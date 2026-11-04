"""``/procgen`` (in-session) and ``hermes procgen`` (terminal) share this implementation."""

from __future__ import annotations

import argparse
import shlex
import shutil
from pathlib import Path
from typing import List, Optional

from . import store
from .engine import Engine, GenerationError, ffmpeg_path, working_dir
from .render import Program, ProgramError, RenderError, run_program
from .style import StyleError, list_presets, load_preset, load_style_file, preset_text

HELP = """\
Procgen Studio: images drawn as code by your own model, rendered locally.

  /procgen status                      what is active (style, provider, settings, ffmpeg)
  /procgen styles                      list built-in style presets
  /procgen style <preset>|show|clear   pick the active preset, or print the style contract in effect
  /procgen init [preset] [--user]      write a STYLE.md into the working directory (or your user style)
  /procgen set <name>|off              group the next generations into an asset set
  /procgen list [n] [--set name]       recent renders
  /procgen show <id|last>              paths, seed, critic log
  /procgen source <id|last>            print the drawing program
  /procgen reroll <id|last> [--seed n] re-render the same program with a new seed (no model call)
  /procgen restyle <id|last|--set s|--all> [--style p]
                                       re-render with the current STYLE palette (no model call)
  /procgen regen <id|last|--set s> [--style p]
                                       ask the model again under the current STYLE.md
  /procgen edit <id|last> <instruction>
                                       change an existing drawing ("make the gem green")
  /procgen render <file> [--seed n] [--style p] [--size WxH]
                                       render a local .py / .svg / .pixel.json source
  /procgen gallery [--set s] [n]       contact sheet of recent renders

Prompt directives for image_generate: [style=frost] [seed=42] [set=dungeon-icons] [renderer=pixel]
"""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # never sys.exit inside a chat session
        raise ValueError(message)


def _parser() -> _Parser:
    p = _Parser(prog="procgen", add_help=False)
    sub = p.add_subparsers(dest="cmd")
    for name in ("help", "status", "styles", "presets"):
        sub.add_parser(name, add_help=False)
    s = sub.add_parser("style", add_help=False)
    s.add_argument("name", nargs="?", default="show")
    s = sub.add_parser("init", add_help=False)
    s.add_argument("preset", nargs="?", default="")
    s.add_argument("--user", action="store_true")
    s.add_argument("--force", action="store_true")
    s = sub.add_parser("set", add_help=False)
    s.add_argument("name", nargs="*", default=[])
    s = sub.add_parser("list", aliases=["ls"], add_help=False)
    s.add_argument("n", nargs="?", type=int, default=10)
    s.add_argument("--set", dest="set_name", default="")
    for name in ("show", "source"):
        s = sub.add_parser(name, add_help=False)
        s.add_argument("ref", nargs="?", default="last")
    s = sub.add_parser("reroll", add_help=False)
    s.add_argument("ref", nargs="?", default="last")
    s.add_argument("--seed", type=int, default=None)
    for name in ("restyle", "regen"):
        s = sub.add_parser(name, add_help=False)
        s.add_argument("ref", nargs="?", default="")
        s.add_argument("--set", dest="set_name", default="")
        s.add_argument("--all", action="store_true")
        s.add_argument("--style", default=None)
    s = sub.add_parser("edit", add_help=False)
    s.add_argument("ref")
    s.add_argument("instruction", nargs=argparse.REMAINDER)
    s = sub.add_parser("render", add_help=False)
    s.add_argument("file")
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--style", default=None)
    s.add_argument("--size", default="1024x1024")
    s.add_argument("--out", default="")
    s = sub.add_parser("gallery", add_help=False)
    s.add_argument("n", nargs="?", type=int, default=12)
    s.add_argument("--set", dest="set_name", default="")
    s.add_argument("--out", default="")
    return p


def run(engine: Engine, argv: List[str]) -> str:
    """Execute one procgen command and return the text to show the user."""
    try:
        args = _parser().parse_args(argv or ["help"])
    except ValueError as exc:
        return f"procgen: {exc}\n\n{HELP}"
    cmd = args.cmd or "help"
    try:
        return _dispatch(engine, cmd, args)
    except (KeyError, StyleError, ProgramError, RenderError, GenerationError, FileNotFoundError) as exc:
        msg = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
        return f"procgen {cmd}: {msg}"


def run_text(engine: Engine, raw: str) -> str:
    try:
        argv = shlex.split(raw or "")
    except ValueError as exc:
        return f"procgen: {exc}"
    return run(engine, argv)


def _describe(rec: store.Record) -> str:
    crit = rec.critic[-1] if rec.critic else {}
    score = next((h.get("score") for h in reversed(rec.critic) if h.get("ok")), None)
    lines = [
        f"{rec.id}  {rec.operation}  {rec.media}  {rec.width}x{rec.height}  seed={rec.seed}  "
        f"style={rec.style.get('name')}/{rec.style.get('palette')}  renderer={rec.kind}"
        + (f"  set={rec.set}" if rec.set else "") + (f"  parent={rec.parent}" if rec.parent else ""),
        f"  prompt:  {rec.prompt}",
        f"  output:  {rec.published or rec.output}",
        f"  source:  {rec.source}",
        f"  record:  {rec.dir / 'meta.json'}",
    ]
    if rec.critic:
        lines.append(f"  critic:  {len(rec.critic)} attempt(s), final score {score}, mode {crit.get('mode', '-')}")
        for h in rec.critic:
            if h.get("ok"):
                issues = "; ".join(h.get("issues") or [])[:160]
                lines.append(f"    #{h['attempt']}: score {h.get('score')} {'pass' if h.get('passed') else 'redraw'}"
                             + (f" ({issues})" if issues else ""))
            else:
                lines.append(f"    #{h['attempt']}: render error: {str(h.get('error'))[:160]}")
    if rec.llm.get("calls"):
        lines.append(f"  model:   {', '.join(rec.llm.get('models') or ['?'])}  calls={rec.llm.get('calls')} "
                     f"tokens={rec.llm.get('tokens')}")
    if rec.extra.get("gif"):
        lines.append(f"  gif:     {rec.extra['gif']}")
    return "\n".join(lines)


def _targets(args) -> List[store.Record]:
    if getattr(args, "all", False):
        seen, out = set(), []
        for rec in store.iter_records():
            key = (rec.prompt, rec.media)
            if rec.operation in ("generate", "edit", "regen", "animate") and key not in seen:
                seen.add(key)
                out.append(rec)
        return list(reversed(out))
    if args.set_name:
        seen, out = set(), []
        for rec in store.iter_records():
            if rec.set == args.set_name and rec.prompt not in seen and rec.operation != "reroll":
                seen.add(rec.prompt)
                out.append(rec)
        if not out:
            raise KeyError(f"no renders in set '{args.set_name}'")
        return list(reversed(out))
    return [store.resolve_ref(args.ref or "last")]


def _dispatch(engine: Engine, cmd: str, args) -> str:
    if cmd == "help":
        return HELP
    if cmd == "status":
        try:
            style = engine.current_style()
            style_line = f"{style.name} (palette {style.palette_name}, from {style.source}" + \
                         (f": {style.path}" if style.path else "") + ")"
        except StyleError as exc:
            style_line = f"error: {exc}"
        s = engine._settings()
        count = sum(1 for _ in store.renders_dir().iterdir())
        return "\n".join([
            "Procgen Studio",
            f"  style in effect: {style_line}",
            f"  active set:      {engine.state.get('set') or '-'}",
            f"  critic:          {s.critic}, max redraws {s.max_redraws}, pass score {s.min_score}",
            f"  python programs: {'allowed (sandboxed)' if s.allow_python else 'disabled (SVG/pixel only)'}",
            f"  ffmpeg (video):  {ffmpeg_path() or 'not found'}",
            f"  renders stored:  {count} in {store.renders_dir()}",
            "  select me:       hermes config set image_gen.provider procgen-studio",
        ])
    if cmd in ("styles", "presets"):
        active = engine.state.get("preset") or engine._settings().preset
        out = ["Built-in style presets:"]
        for name in list_presets():
            st = load_preset(name)
            out.append(f"  {'*' if name == active else ' '} {name:22s} {st.description}")
        out.append("Use: /procgen style <name>, or put 'preset: <name>' in a STYLE.md")
        return "\n".join(out)
    if cmd == "style":
        name = args.name
        if name == "show":
            return engine.current_style().as_prompt()
        if name in ("clear", "reset", "default"):
            engine.state.set("preset", None)
            return f"Active preset cleared; using {engine.current_style().name}."
        load_preset(name)
        engine.state.set("preset", name)
        cur = engine.current_style()
        note = "" if cur.name == name or cur.palette_name == name else \
            f" (note: {cur.source} style {cur.path or cur.name} takes precedence over the active preset)"
        return f"Active preset: {name}{note}"
    if cmd == "init":
        preset = args.preset or "ember"
        load_preset(preset)
        target = engine.user_style_path() if args.user else Path(working_dir()) / "STYLE.md"
        if target.exists() and not args.force:
            return f"{target} already exists (use --force to overwrite)"
        target.write_text(_starter_style(preset), encoding="utf-8")
        return f"Wrote {target}. Edit any line (try changing 'palette: {preset}') and regenerate."
    if cmd == "set":
        name = " ".join(args.name).strip()
        if not name or name in ("off", "none", "clear"):
            engine.state.set("set", None)
            return "Asset set cleared."
        engine.state.set("set", store.slugify(name, 48))
        return f"New generations go into set '{store.slugify(name, 48)}'. Restyle or regenerate it with --set."
    if cmd in ("list", "ls"):
        recs = store.latest(max(1, min(100, args.n)), set_name=args.set_name)
        if not recs:
            return "No renders yet. Ask for an image with image_generate while procgen-studio is the provider."
        return "\n".join(f"{r.id}  {r.operation:8s} {r.media:5s} seed={r.seed:<7d} "
                         f"{(r.set or '-'):14s} {r.prompt[:70]}" for r in recs)
    if cmd == "show":
        return _describe(store.resolve_ref(args.ref))
    if cmd == "source":
        rec = store.resolve_ref(args.ref)
        code = Path(rec.source).read_text(encoding="utf-8")
        return f"# {rec.source}\n{code}"
    if cmd == "reroll":
        base = store.resolve_ref(args.ref)
        seed = args.seed if args.seed is not None else base.seed + 1
        rec = engine.rerender(base, seed=seed, operation="reroll")
        note = "" if base.kind != "svg" else "\n  (static SVG source: the seed does not change it)"
        return f"Re-rolled {base.id} with seed {seed} (no model call):\n{_describe(rec)}{note}"
    if cmd == "restyle":
        style = engine.current_style(args.style)
        outs = []
        for base in _targets(args):
            rec = engine.rerender(base, style=style, operation="restyle")
            outs.append(f"{base.id} -> {rec.id}  {rec.published}")
        return (f"Restyled {len(outs)} render(s) with palette '{style.palette_name}' locally (no model call). "
                f"Rule changes such as lighting or outline need /procgen regen.\n" + "\n".join(outs))
    if cmd == "regen":
        outs = []
        for base in _targets(args):
            outcome = engine.regenerate(base, style_override=args.style)
            r = outcome.record
            score = outcome.verdict.score if outcome.verdict else "-"
            outs.append(f"{base.id} -> {r.id}  score {score}  {r.published}")
        return f"Regenerated {len(outs)} render(s) under the current style:\n" + "\n".join(outs)
    if cmd == "edit":
        instruction = " ".join(args.instruction).strip()
        if not instruction:
            return "procgen edit: say what to change, e.g. /procgen edit last make the gem emerald green"
        outcome = engine.edit(store.resolve_ref(args.ref), instruction)
        return f"Edited:\n{_describe(outcome.record)}"
    if cmd == "render":
        return _render_file(engine, args)
    if cmd == "gallery":
        return _gallery(args)
    return HELP


def _render_file(engine: Engine, args) -> str:
    path = Path(args.file).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {path}")
    name = path.name.lower()
    kind = "pixel" if name.endswith(".json") else "svg" if name.endswith(".svg") else "python"
    w, _, h = args.size.partition("x")
    width, height = int(w), int(h or w)
    if args.style and (args.style.endswith(".md") or "/" in args.style):
        style = load_style_file(args.style)
    else:
        style = engine.current_style(args.style)
    out = Path(args.out).expanduser() if args.out else path.with_suffix(".png")
    if name.endswith(".pixel.json") and not args.out:
        out = path.with_name(path.name[: -len(".pixel.json")] + ".png")
    s = engine._settings()
    result = run_program(Program(kind, path.read_text(encoding="utf-8")), width=width, height=height, seed=args.seed,
                         palette=style.palette, style=style.rules, out_dir=out.parent / ".procgen-render",
                         timeout=s.render_timeout)
    shutil.copyfile(result.frames[0], out)
    shutil.rmtree(out.parent / ".procgen-render", ignore_errors=True)
    return f"Rendered {path} with style {style.name}/{style.palette_name}, seed {args.seed} -> {out} " \
           f"({result.elapsed:.2f}s)"


def _gallery(args) -> str:
    from PIL import Image, ImageDraw
    recs = [r for r in store.latest(max(1, min(48, args.n)), set_name=args.set_name) if r.media == "image"]
    if not recs:
        return "No image renders to show yet."
    cols = min(4, len(recs))
    rows = (len(recs) + cols - 1) // cols
    cell = 320
    sheet = Image.new("RGB", (cols * cell, rows * (cell + 28)), (16, 16, 20))
    d = ImageDraw.Draw(sheet)
    for i, rec in enumerate(reversed(recs)):
        with Image.open(rec.output) as im:
            im = im.convert("RGB")
            im.thumbnail((cell - 12, cell - 12))
            x, y = (i % cols) * cell, (i // cols) * (cell + 28)
            sheet.paste(im, (x + (cell - im.width) // 2, y + 6 + (cell - 12 - im.height) // 2))
            d.text((x + 8, y + cell), f"{rec.id} seed={rec.seed} {rec.style.get('palette')}", fill=(190, 190, 205))
    out = Path(args.out).expanduser() if args.out else store.data_dir() / "gallery.png"
    sheet.save(out, "PNG")
    return f"Gallery of {len(recs)} render(s): {out}"


def _starter_style(preset: str) -> str:
    base = load_preset(preset)
    rules = "\n".join(f"{k}: {v}" for k, v in base.rules.items())
    return f"""# STYLE.md: the art contract for every Procgen Studio render in this project.
# Change one line and regenerate (or /procgen restyle --all for palette-only changes).
# Palettes: {', '.join(list_presets())}. Override single colours with palette.<role>: #hex.

preset: {preset}
palette: {base.palette_name}
{rules}

# palette.gem: #56e0ff
# Free-form notes below are passed to the model as-is.
"""


def cli_setup(subparser) -> None:
    subparser.add_argument("args", nargs=argparse.REMAINDER, help="procgen subcommand (try: help)")


def cli_handler_factory(engine_getter):
    def handler(ns) -> int:
        print(run(engine_getter(), list(getattr(ns, "args", []) or [])))
        return 0
    return handler


def preset_doc(name: str) -> Optional[str]:
    try:
        return preset_text(name)
    except StyleError:
        return None
