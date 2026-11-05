"""Generation pipeline: prompt -> drawing program -> render -> critic -> (redraw) -> record."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import critic as critic_mod
from . import prompts, store
from .llm import Llm
from .render import (Program, ProgramError, RenderError, contact_strip, extract_program, run_program)
from .style import Style, StyleError, resolve_style

logger = logging.getLogger("procgen_studio")

IMAGE_SIZES = {"square": (1024, 1024), "landscape": (1536, 1024), "portrait": (1024, 1536)}
VIDEO_ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:3": (4, 3), "3:4": (3, 4)}
VIDEO_HEIGHTS = {"480p": 480, "540p": 540, "720p": 720, "1080p": 1080}

_DIRECTIVE_RE = re.compile(r"\[(?:procgen[:\s]+)?((?:\s*[a-z_]+\s*=\s*[^\]\s]+\s*)+)\]", re.IGNORECASE)
_DIRECTIVE_KEYS = {"style", "seed", "set", "renderer", "critic", "redraws", "size", "fps", "loop"}

# Cost guesses (seconds) before a call of that kind has been measured in the current loop.
DRAW_COST_GUESS = 60.0
CRITIC_COST_GUESS = 30.0
ROUND_MARGIN = 10.0


class GenerationError(RuntimeError):
    def __init__(self, message: str, *, error_type: str = "generation_failed", history: Optional[list] = None):
        super().__init__(message)
        self.error_type = error_type
        self.history = history or []


@dataclass
class Settings:
    """User settings under ``plugins.entries.procgen-studio.settings`` (see plugin.yaml)."""

    preset: str = "flat-illustration"
    style_file: str = ""
    critic: str = "auto"
    max_redraws: int = 2
    min_score: int = 7
    allow_python: bool = True
    output_dir: str = ""
    render_timeout: int = 90
    llm_timeout: int = 300
    time_budget: int = 360
    max_tokens: int = 16000
    fps: int = 24
    video_seconds: int = 4
    gif: bool = True

    @classmethod
    def load(cls, getter: Optional[Callable[[str, Any], Any]]) -> "Settings":
        s = cls()
        if getter is None:
            return s
        for name, default in list(s.__dict__.items()):
            try:
                value = getter(name, default)
            except Exception:
                value = default
            if value is None:
                continue
            try:
                if isinstance(default, bool):
                    value = value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on")
                elif isinstance(default, int):
                    value = int(value)
                else:
                    value = str(value)
            except (TypeError, ValueError):
                value = default
            setattr(s, name, value)
        s.critic = s.critic if s.critic in critic_mod.CRITIC_MODES else "auto"
        s.max_redraws = max(0, min(6, s.max_redraws))
        s.min_score = max(0, min(10, s.min_score))
        s.render_timeout = max(5, min(600, s.render_timeout))
        s.time_budget = 0 if s.time_budget <= 0 else max(5, s.time_budget)
        s.fps = max(4, min(60, s.fps))
        s.video_seconds = max(1, min(15, s.video_seconds))
        return s


@dataclass
class Directives:
    prompt: str
    values: Dict[str, str] = field(default_factory=dict)


def parse_directives(prompt: str) -> Directives:
    """``[style=frost seed=42 set=icons]`` anywhere in the prompt; unknown keys are left alone."""
    values: Dict[str, str] = {}

    def take(m: re.Match) -> str:
        pairs = re.findall(r"([a-z_]+)\s*=\s*([^\]\s]+)", m.group(1), re.IGNORECASE)
        if not pairs or any(k.lower() not in _DIRECTIVE_KEYS for k, _ in pairs):
            return m.group(0)
        for k, v in pairs:
            values[k.lower()] = v.strip().strip("'\"")
        return " "
    cleaned = _DIRECTIVE_RE.sub(take, prompt or "")
    return Directives(re.sub(r"\s{2,}", " ", cleaned).strip(), values)


def derive_seed(prompt: str, style: Style) -> int:
    digest = hashlib.sha256(f"{prompt.strip().lower()}|{style.palette_name}".encode()).hexdigest()
    return int(digest[:8], 16) % 1_000_000


def image_size(aspect_ratio: str, resolution: Optional[str] = None, size: Optional[str] = None) -> Tuple[int, int]:
    if size:
        m = re.match(r"^(\d{2,4})x(\d{2,4})$", size)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            return max(64, min(4096, w)), max(64, min(4096, h))
    w, h = IMAGE_SIZES.get(aspect_ratio, IMAGE_SIZES["landscape"])
    if (resolution or "").upper() == "2K":
        w, h = w * 2, h * 2
    return w, h


def video_size(aspect_ratio: str, resolution: str) -> Tuple[int, int]:
    aw, ah = VIDEO_ASPECTS.get(aspect_ratio or "16:9", (16, 9))
    short = VIDEO_HEIGHTS.get(resolution or "720p", 720)
    if aw >= ah:
        h = short
        w = int(round(short * aw / ah))
    else:
        w = short
        h = int(round(short * ah / aw))
    return w - w % 2, h - h % 2


def working_dir() -> str:
    """The conversation's workspace (one gateway serves many sessions, so not the process cwd)."""
    try:
        from agent.runtime_cwd import resolve_agent_cwd
        return str(resolve_agent_cwd())
    except Exception:
        return os.environ.get("TERMINAL_CWD") or os.getcwd()


class JsonState:
    """Fallback for ``ctx.state`` (tests, older Hermes): one JSON file in the data dir."""

    def __init__(self, path: Optional[Path] = None):
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or store.data_dir() / "state.json"

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")).get(key, default)
        except (OSError, ValueError):
            return default

    def set(self, key: str, value: Any) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data[key] = value
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


@dataclass
class Outcome:
    record: store.Record
    program: Program
    verdict: Optional[critic_mod.Verdict]
    history: List[Dict[str, Any]]


class Engine:
    def __init__(self, *, llm_factory: Callable[[Settings], Llm], settings: Callable[[], Settings],
                 state: Any = None):
        self._llm_factory = llm_factory
        self._settings = settings
        self.state = state if state is not None else JsonState()

    # --- style -------------------------------------------------------------------------------

    def user_style_path(self) -> Path:
        return store.data_dir() / "STYLE.md"

    def current_style(self, override: Optional[str] = None, settings: Optional[Settings] = None) -> Style:
        s = settings or self._settings()
        active = None
        try:
            active = self.state.get("preset")
        except Exception:
            active = None
        return resolve_style(override=override, style_file=s.style_file or None, cwd=working_dir(),
                             user_style=self.user_style_path(), active_preset=active, default_preset=s.preset)

    # --- public operations -------------------------------------------------------------------

    def generate(self, prompt: str, *, aspect_ratio: str = "landscape", resolution: Optional[str] = None,
                 complexity: Optional[int] = None, thinking: Optional[str] = None, model: Optional[str] = None,
                 reference_image: Optional[str] = None) -> Outcome:
        d = parse_directives(prompt)
        if not d.prompt:
            raise GenerationError("Prompt is required", error_type="invalid_input")
        s = self._settings()
        style = self._style_for(d, s)
        width, height = image_size(aspect_ratio, resolution, d.values.get("size"))
        seed = self._seed(d, style)
        policy = self._policy(s, thinking=thinking, model=model, directives=d)
        req = prompts.DrawRequest(prompt=d.prompt, width=width, height=height, seed=seed, style=style,
                                  complexity=int(complexity or 0), renderer_hint=self._renderer_hint(d),
                                  allow_python=s.allow_python)
        rec = self._new_record(req, aspect_ratio, d, media="image", operation="generate")
        llm = self._llm_factory(s)
        system = prompts.system_prompt(req)
        user: Any = prompts.user_prompt(req)
        if reference_image:
            user = self._with_reference(user, reference_image)
            rec.extra["reference_image"] = reference_image
        return self._run(rec, req, llm, s, policy, system=system, first_user=user)

    def edit(self, base: store.Record, instruction: str, *, thinking: Optional[str] = None,
             model: Optional[str] = None) -> Outcome:
        d = parse_directives(instruction)
        s = self._settings()
        style = self._style_for(d, s, fallback=base)
        source = Path(base.source).read_text(encoding="utf-8")
        seed = self._seed(d, style) if "seed" in d.values else base.seed
        video = base.media == "video"
        req = prompts.DrawRequest(prompt=f"{base.prompt} -- edit: {d.prompt}", width=base.width, height=base.height,
                                  seed=seed, style=style, allow_python=s.allow_python, animated=video,
                                  frames=base.frames if video else 1, fps=base.fps or s.fps)
        policy = self._policy(s, thinking=thinking, model=model, directives=d)
        rec = self._new_record(req, base.aspect_ratio, d, media=base.media, operation="edit", parent=base)
        rec.extra["instruction"] = d.prompt
        if video:
            rec.fps, rec.frames = base.fps, base.frames
            rec.extra.update({k: v for k, v in base.extra.items() if k.startswith("video_")})
        llm = self._llm_factory(s)
        system = prompts.system_prompt(req)
        user = prompts.edit_prompt(req, source, base.kind, d.prompt)
        if base.media == "video":
            return self._run_video(rec, req, llm, s, policy, system=system, first_user=user)
        return self._run(rec, req, llm, s, policy, system=system, first_user=user)

    def regenerate(self, base: store.Record, *, style_override: Optional[str] = None) -> Outcome:
        """Ask the model again with the same prompt, size and seed under the current style."""
        prompt = base.prompt
        directive = f" [seed={base.seed}]" + (f" [style={style_override}]" if style_override else "")
        directive += f" [set={base.set}]" if base.set else ""
        if base.media == "video":
            return self.generate_video(prompt + directive, aspect_ratio=base.extra.get("video_aspect", "16:9"),
                                       resolution=base.extra.get("video_resolution", "720p"),
                                       duration=max(1, round(base.frames / max(1, base.fps))), parent=base)
        outcome = self.generate(prompt + directive, aspect_ratio=base.aspect_ratio,
                                resolution="2K" if base.width > 1600 or base.height > 1600 else None)
        outcome.record.parent = base.id
        outcome.record.operation = "regen"
        store.save_record(outcome.record)
        return outcome

    def rerender(self, base: store.Record, *, seed: Optional[int] = None, style: Optional[Style] = None,
                 operation: str = "reroll") -> store.Record:
        """Re-run the stored program locally (no model call): a new seed, a new palette, or both."""
        s = self._settings()
        style = style or Style(name=base.style.get("name", "?"), palette_name=base.style.get("palette", "?"),
                               palette=dict(base.style.get("colors") or {}))
        if not style.palette:
            style = self.current_style()
        program = Program(base.kind, Path(base.source).read_text(encoding="utf-8"))
        new_seed = base.seed if seed is None else int(seed)
        req = prompts.DrawRequest(prompt=base.prompt, width=base.width, height=base.height, seed=new_seed,
                                  style=style, allow_python=s.allow_python)
        rec = self._new_record(req, base.aspect_ratio, Directives(base.prompt, {"set": base.set} if base.set else {}),
                               media=base.media, operation=operation, parent=base)
        rec.fps, rec.frames, rec.kind = base.fps, base.frames, program.kind
        rec.extra.update({k: v for k, v in base.extra.items() if k.startswith("video_")})
        started = time.monotonic()
        if base.media == "video":
            self._encode_video(rec, req, program, s)
        else:
            result = run_program(program, width=req.width, height=req.height, seed=req.seed, palette=style.palette,
                                 style=style.rules, out_dir=rec.dir / "work", timeout=s.render_timeout)
            shutil.copyfile(result.frames[0], rec.dir / "image.png")
            rec.output = str(rec.dir / "image.png")
            shutil.rmtree(rec.dir / "work", ignore_errors=True)
        source = rec.dir / f"source{program.extension}"
        source.write_text(program.code, encoding="utf-8")
        rec.source = str(source)
        rec.timings["render"] = round(time.monotonic() - started, 3)
        rec.llm = {"calls": 0, "tokens": 0, "models": [], "log": []}
        store.publish(rec, output_dir=s.output_dir or None)
        store.save_record(rec)
        return rec

    def generate_video(self, prompt: str, *, aspect_ratio: str = "16:9", resolution: str = "720p",
                       duration: Optional[int] = None, seed: Optional[int] = None,
                       source_record: Optional[store.Record] = None, parent: Optional[store.Record] = None,
                       loop: Optional[bool] = None) -> Outcome:
        d = parse_directives(prompt)
        if not d.prompt:
            raise GenerationError("Prompt is required", error_type="invalid_input")
        s = self._settings()
        style = self._style_for(d, s, fallback=source_record)
        width, height = video_size(aspect_ratio, resolution)
        try:
            fps = max(4, min(60, int(d.values.get("fps", s.fps))))
        except ValueError:
            fps = s.fps
        seconds = max(1, min(15, int(duration or s.video_seconds)))
        frames = fps * seconds
        if seed is not None and "seed" not in d.values:
            d.values["seed"] = str(seed)
        seed_value = self._seed(d, style)
        want_loop = loop if loop is not None else (d.values.get("loop", "").lower() in ("1", "true", "yes") or
                                                   bool(re.search(r"\b(loop|looping|seamless)\b", d.prompt, re.I)))
        req = prompts.DrawRequest(prompt=d.prompt, width=width, height=height, seed=seed_value, style=style,
                                  animated=True, frames=frames, fps=fps, loop=want_loop, allow_python=True)
        if not s.allow_python:
            raise GenerationError("Animations need Python drawing programs; enable allow_python", error_type="disabled")
        policy = self._policy(s, thinking=None, model=None, directives=d)
        rec = self._new_record(req, aspect_ratio, d, media="video", operation="generate", parent=parent)
        rec.fps, rec.frames = fps, frames
        rec.extra.update(video_aspect=aspect_ratio, video_resolution=resolution, video_seconds=seconds)
        llm = self._llm_factory(s)
        system = prompts.system_prompt(req)
        if source_record is not None:
            code = Path(source_record.source).read_text(encoding="utf-8")
            user = prompts.edit_prompt(req, code, source_record.kind,
                                       f"Animate this drawing: {d.prompt}\n\n" + prompts.user_prompt(req))
            rec.parent = source_record.id
            rec.operation = "animate"
        else:
            user = prompts.user_prompt(req)
        return self._run_video(rec, req, llm, s, policy, system=system, first_user=user)

    # --- internals -----------------------------------------------------------------------------

    def _style_for(self, d: Directives, s: Settings, fallback: Optional[store.Record] = None) -> Style:
        try:
            return self.current_style(d.values.get("style"), s)
        except StyleError as exc:
            raise GenerationError(str(exc), error_type="invalid_style") from exc

    def _seed(self, d: Directives, style: Style) -> int:
        raw = d.values.get("seed")
        if raw is not None:
            try:
                return int(raw) % 2_147_483_647
            except ValueError:
                return int(hashlib.sha256(raw.encode()).hexdigest()[:8], 16) % 1_000_000
        return derive_seed(d.prompt, style)

    @staticmethod
    def _renderer_hint(d: Directives) -> str:
        r = (d.values.get("renderer") or "").lower()
        return r if r in ("python", "svg", "pixel") else ""

    @staticmethod
    def _policy(s: Settings, *, thinking: Optional[str], model: Optional[str], directives: Directives) -> Dict[str, Any]:
        mode, redraws, min_score = s.critic, s.max_redraws, s.min_score
        model = (model or "").lower()
        if model.endswith("fast") or thinking == "fast":
            mode, redraws = "off", min(1, redraws)
        if model.endswith("careful") or thinking == "high":
            redraws, min_score = max(redraws, 3), min(10, min_score + 1)
        if "critic" in directives.values:
            v = directives.values["critic"].lower()
            mode = v if v in critic_mod.CRITIC_MODES else mode
        if "redraws" in directives.values:
            try:
                redraws = max(0, min(6, int(directives.values["redraws"])))
            except ValueError:
                pass
        return {"critic": mode, "max_redraws": redraws, "min_score": min_score}

    def _new_record(self, req: prompts.DrawRequest, aspect_ratio: str, d: Directives, *, media: str,
                    operation: str, parent: Optional[store.Record] = None) -> store.Record:
        set_name = d.values.get("set") or (parent.set if parent else "") or (self.state.get("set") or "")
        rec = store.Record(
            id=store.new_id(), created=store.now(), prompt=req.prompt, seed=req.seed, width=req.width,
            height=req.height, aspect_ratio=aspect_ratio, kind="", media=media, set=str(set_name or ""),
            style={**req.style.summary(), "colors": dict(req.style.palette)}, operation=operation,
            parent=parent.id if parent else "",
        )
        rec.dir.mkdir(parents=True, exist_ok=True)
        (rec.dir / "STYLE.md").write_text(req.style.raw or req.style.as_prompt(), encoding="utf-8")
        return rec

    @staticmethod
    def _extract(text: str, allow_python: bool) -> Tuple[Optional[Program], Optional[str]]:
        try:
            return extract_program(text, allow_python=allow_python), None
        except ProgramError as exc:
            return None, str(exc)

    @staticmethod
    def _with_reference(user_text: str, ref: str) -> Any:
        url = ref
        if not ref.lower().startswith(("http://", "https://", "data:")):
            p = Path(ref).expanduser()
            if not p.is_file() or p.stat().st_size > 8 * 1024 * 1024:
                return user_text + f"\n\n(Reference image {ref} could not be read; ignore it.)"
            mime = mimetypes.guess_type(p.name)[0] or "image/png"
            url = f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()
        return [{"type": "text", "text": user_text + "\n\nThe attached image is a REFERENCE: match its composition "
                 "and subject, but draw it in the style contract above."},
                {"type": "image_url", "image_url": {"url": url}}]

    def _call_draw(self, llm: Llm, system: str, user: Any, purpose: str) -> str:
        if isinstance(user, list):
            try:
                return llm.draw(system, user, purpose=purpose)  # type: ignore[arg-type]
            except Exception as exc:
                logger.info("procgen: reference image refused by the model (%s); retrying text-only", exc)
                user = next((b["text"] for b in user if b.get("type") == "text"), "")
        return llm.draw(system, user, purpose=purpose)

    def _loop(self, rec: store.Record, req: prompts.DrawRequest, llm: Llm, s: Settings, policy: Dict[str, Any], *,
              system: str, first_user: Any, times: Tuple[float, ...], strip: bool) -> Tuple[Program, Any, Any, list]:
        """The bounded draw -> render -> critique -> redraw loop. Returns the best attempt."""
        budget = int(policy["max_redraws"])
        attempts_dir = rec.dir / "attempts"
        attempts_dir.mkdir(parents=True, exist_ok=True)
        history: List[Dict[str, Any]] = []
        t0 = time.monotonic()
        deadline = t0 + s.time_budget if s.time_budget > 0 else None
        critic_on = policy["critic"] != "off"

        def remaining() -> float:
            return float("inf") if deadline is None else deadline - time.monotonic()

        def fit_timeout() -> None:
            llm.timeout = float(s.llm_timeout) if deadline is None else max(20.0, min(float(s.llm_timeout),
                                                                                   remaining() - 5.0))

        def cost(prefixes: Tuple[str, ...], fallback: float) -> float:
            spent = [c.seconds for c in llm.calls if c.purpose.startswith(prefixes)]
            return max(spent) if spent else fallback

        def can_redraw() -> bool:
            # Hermes cuts a tool call off at timeouts.tools.sequential_call (420 s by default); stop
            # early with the best attempt rather than lose everything to that deadline.
            need = cost(("draw", "repair", "redraw"), DRAW_COST_GUESS) + ROUND_MARGIN + \
                (cost(("critic",), CRITIC_COST_GUESS) if critic_on else 0.0)
            if remaining() > need:
                return True
            rec.extra["stopped_early"] = f"time budget: {max(0.0, remaining()):.0f}s left, next round needs ~{need:.0f}s"
            logger.info("procgen %s: %s", rec.id, rec.extra["stopped_early"])
            return False

        fit_timeout()
        text = self._call_draw(llm, system, first_user, "draw")
        program, error = self._extract(text, s.allow_python)
        best: Optional[Tuple[Program, Any, Any]] = None
        seen: set = set()
        for n in range(1, budget + 2):
            started = time.monotonic()
            result = None
            if program is not None and n > 1 and hash(program.code.strip()) in seen:
                rec.extra["stopped_early"] = f"attempt {n} repeated an earlier program unchanged"
                logger.info("procgen %s: %s", rec.id, rec.extra["stopped_early"])
                break
            if program is not None:
                seen.add(hash(program.code.strip()))
            if program is not None:
                try:
                    result = run_program(program, width=req.width, height=req.height, seed=req.seed,
                                         palette=req.style.palette, style=req.style.rules,
                                         out_dir=attempts_dir / f"{n:02d}", times=times, fps=req.fps,
                                         timeout=s.render_timeout)
                    error = None
                except (ProgramError, RenderError) as exc:
                    error = f"{exc}"
                (attempts_dir / f"{n:02d}{program.extension}").write_text(program.code, encoding="utf-8")
            if error is not None or result is None:
                entry = {"attempt": n, "ok": False, "error": (error or "no program")[:600],
                         "render_s": round(time.monotonic() - started, 2)}
                history.append(entry)
                logger.info("procgen %s attempt %d failed to render: %s", rec.id, n, entry["error"][:200])
                if n > budget or not can_redraw():
                    break
                broken = program.code if program is not None else text[:6000]
                kind = program.kind if program is not None else "python"
                fit_timeout()
                text = llm.draw(system, prompts.repair_prompt(req, broken, kind, error or "no code block"),
                                purpose=f"repair.{n}")
                program, error = self._extract(text, s.allow_python)
                continue
            image = result.frames[0]
            note = ""
            if strip and len(result.frames) > 1:
                image = contact_strip(result.frames, attempts_dir / f"{n:02d}" / "strip.png")
                note = (f"The image is a strip of {len(result.frames)} frames of the animation at "
                        f"t = {', '.join(f'{t:.2f}' for t in times)}. Judge motion and the final frame.")
            mode = policy["critic"]
            skipped = critic_on and remaining() < cost(("critic",), CRITIC_COST_GUESS) + ROUND_MARGIN / 2
            if skipped:
                mode = "off"
                rec.extra["critic_skipped"] = "time budget"
            fit_timeout()
            verdict = critic_mod.review(llm, req, image=image, code=program.code, kind=program.kind,
                                        mode=mode, min_score=policy["min_score"], frames_note=note)
            if skipped and verdict.mode == "off":
                verdict.mode, verdict.score, verdict.note = "skipped", policy["min_score"], \
                    "critic skipped: not enough time budget left"
            entry = {"attempt": n, "ok": True, **verdict.as_dict(), "render_s": round(time.monotonic() - started, 2)}
            history.append(entry)
            logger.info("procgen %s attempt %d: score=%s passed=%s mode=%s issues=%s", rec.id, n, verdict.score,
                        verdict.passed, verdict.mode, "; ".join(verdict.issues)[:300])
            if best is None or verdict.score >= best[2].score:
                best = (program, result, verdict)
            if verdict.passed or n > budget or not can_redraw():
                break
            fit_timeout()
            text = llm.draw(system, prompts.revise_prompt(req, program.code, program.kind, verdict.issues,
                                                          verdict.fixes), purpose=f"redraw.{n}")
            program, error = self._extract(text, s.allow_python)
        rec.timings["loop"] = round(time.monotonic() - t0, 3)
        rec.critic = history
        rec.llm = llm.summary()
        if best is None:
            store.save_record(rec)
            last = next((h.get("error") for h in reversed(history) if not h.get("ok")), "unknown error")
            raise GenerationError(f"no attempt rendered after {len(history)} tries: {last}",
                                  error_type="render_failed", history=history)
        return best[0], best[1], best[2], history

    def _run(self, rec: store.Record, req: prompts.DrawRequest, llm: Llm, s: Settings, policy: Dict[str, Any], *,
             system: str, first_user: Any) -> Outcome:
        program, result, verdict, history = self._loop(rec, req, llm, s, policy, system=system,
                                                       first_user=first_user, times=(0.0,), strip=False)
        rec.kind = program.kind
        source = rec.dir / f"source{program.extension}"
        source.write_text(program.code, encoding="utf-8")
        shutil.copyfile(result.frames[0], rec.dir / "image.png")
        rec.source, rec.output = str(source), str(rec.dir / "image.png")
        rec.extra["policy"] = policy
        store.publish(rec, output_dir=s.output_dir or None)
        store.save_record(rec)
        return Outcome(rec, program, verdict, history)

    def _run_video(self, rec: store.Record, req: prompts.DrawRequest, llm: Llm, s: Settings, policy: Dict[str, Any],
                   *, system: str, first_user: Any) -> Outcome:
        if not ffmpeg_path():
            raise GenerationError("ffmpeg was not found on PATH; install it to render video "
                                  "(apt install ffmpeg / brew install ffmpeg / winget install ffmpeg)",
                                  error_type="missing_dependency")
        preview = (0.0, 0.5, 1.0)
        program, _result, verdict, history = self._loop(rec, req, llm, s, policy, system=system,
                                                        first_user=first_user, times=preview, strip=True)
        rec.kind = program.kind
        source = rec.dir / f"source{program.extension}"
        source.write_text(program.code, encoding="utf-8")
        rec.source = str(source)
        rec.extra["policy"] = policy
        self._encode_video(rec, req, program, s)
        store.publish(rec, output_dir=s.output_dir or None)
        store.save_record(rec)
        return Outcome(rec, program, verdict, history)

    def _encode_video(self, rec: store.Record, req: prompts.DrawRequest, program: Program, s: Settings) -> None:
        ffmpeg = ffmpeg_path()
        if not ffmpeg:
            raise GenerationError("ffmpeg was not found on PATH", error_type="missing_dependency")
        n = max(2, rec.frames)
        times = tuple(i / (n - 1) for i in range(n))
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="procgen-frames-") as tmp:
            frames_dir = Path(tmp)
            timeout = max(s.render_timeout, min(900, 2 * s.render_timeout + n * 1.5))
            result = run_program(program, width=req.width, height=req.height, seed=req.seed, palette=req.style.palette,
                                 style=req.style.rules, out_dir=frames_dir, times=times, fps=rec.fps or s.fps,
                                 timeout=timeout)
            rec.timings["frames"] = round(time.monotonic() - started, 3)
            mp4 = rec.dir / "video.mp4"
            encode_mp4(ffmpeg, frames_dir, rec.fps or s.fps, mp4)
            rec.output = str(mp4)
            shutil.copyfile(result.frames[-1], rec.dir / "poster.png")
            rec.extra["poster"] = str(rec.dir / "poster.png")
            if s.gif:
                gif = rec.dir / "video.gif"
                encode_gif(ffmpeg, mp4, gif, fps=min(15, rec.fps or s.fps), width=min(512, req.width))
                rec.extra["gif"] = str(gif)
        rec.timings["encode"] = round(time.monotonic() - started, 3)


def ffmpeg_path() -> Optional[str]:
    return shutil.which("ffmpeg")


def encode_mp4(ffmpeg: str, frames_dir: Path, fps: int, out: Path) -> None:
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(frames_dir / "frame_%04d.png"),
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-crf", "18", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise GenerationError(f"ffmpeg failed: {proc.stderr.strip()[-400:]}", error_type="encode_failed")


def encode_gif(ffmpeg: str, mp4: Path, out: Path, *, fps: int = 15, width: int = 512) -> None:
    vf = f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse"
    proc = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(mp4), "-vf", vf, str(out)],
                          capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise GenerationError(f"ffmpeg gif encode failed: {proc.stderr.strip()[-400:]}", error_type="encode_failed")
