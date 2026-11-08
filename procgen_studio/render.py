"""Turn a model reply into pixels.

Three source kinds are accepted:

* ``python``: a drawing program defining ``draw(t)`` that returns an SVG
  string, a PIL image or a numpy array. Checked with an AST allowlist, then
  executed in :mod:`procgen_studio.sandbox_runner` in a separate process.
* ``svg``: a literal SVG document, rendered by cairosvg in the same worker
  (external resources are refused).
* ``pixel``: a JSON palette-grid sprite spec rendered here with Pillow; no
  code is executed at all.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .sandbox_runner import FORBIDDEN_ATTRS

RUNNER = Path(__file__).resolve().parent / "sandbox_runner.py"

ALLOWED_IMPORTS = frozenset({
    "math", "random", "colorsys", "itertools", "functools", "statistics", "cmath", "string",
    "textwrap", "re", "json", "dataclasses", "typing", "collections", "heapq", "bisect", "fractions",
    "decimal", "enum", "copy", "operator", "numpy", "PIL", "cairosvg", "noise", "opensimplex", "cairo",
})

FORBIDDEN_NAMES = frozenset({
    "eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars", "input", "breakpoint",
    "help", "exit", "quit", "memoryview", "__builtins__", "__loader__", "__spec__", "delattr",
})

# Dunders a program may read as attributes (obj.__init__, ...). Defining, assigning or naming any
# dunder is refused separately: a class body defining __array_interface__ or __getattr__ hooks
# library protocols (numpy can be pointed at raw memory).
ALLOWED_DUNDERS = frozenset({"__init__", "__name__", "__doc__", "__len__", "__iter__", "__next__",
                             "__getitem__", "__repr__", "__str__", "__call__", "__eq__", "__lt__",
                             "__hash__", "__post_init__", "__add__", "__mul__", "__sub__"})

# The only dunder methods a drawing program may define.
DEFINABLE_DUNDERS = frozenset({"__init__", "__post_init__"})

_NAME_LOOKUP_BUILTINS = frozenset({"getattr", "hasattr", "setattr"})

MAX_SOURCE_BYTES = 400 * 1024
_FENCE_RE = re.compile(r"```([A-Za-z0-9_+\-]*)[^\n]*\n(.*?)```", re.DOTALL)


class ProgramError(ValueError):
    """The reply did not contain a usable program, or it failed the safety check."""


class RenderError(RuntimeError):
    """The program ran but did not produce an image."""

    def __init__(self, message: str, *, error_type: str = "render_error"):
        super().__init__(message)
        self.error_type = error_type


@dataclass
class Program:
    kind: str  # "python" | "svg" | "pixel"
    code: str

    @property
    def extension(self) -> str:
        return {"python": ".py", "svg": ".svg", "pixel": ".pixel.json"}[self.kind]


@dataclass
class RenderResult:
    frames: List[Path]
    elapsed: float
    kind: str
    stats: Dict[str, Any] = field(default_factory=dict)


def extract_program(text: str, *, allow_python: bool = True) -> Program:
    """Pick the drawing program out of a model reply."""
    text = (text or "").strip()
    if not text:
        raise ProgramError("the model returned an empty reply")
    blocks = [(lang.lower(), body.strip()) for lang, body in _FENCE_RE.findall(text)]
    if not blocks and "```" in text:
        # Unterminated fence (reply cut off at max_tokens): take what is there.
        lang, _, body = text.partition("```")[2].partition("\n")
        blocks = [(lang.strip().lower(), body.strip())]
    candidates: List[Program] = []
    for lang, body in blocks:
        kind = _classify(lang, body)
        if kind:
            candidates.append(Program(kind, body))
    if not candidates:
        stripped = text.lstrip()
        if stripped.startswith("<svg") or stripped.startswith("<?xml"):
            candidates.append(Program("svg", stripped))
        elif stripped.startswith("{") and ('"frames"' in stripped or '"sprite"' in stripped):
            candidates.append(Program("pixel", stripped))
        elif re.search(r"^def draw\(", text, re.MULTILINE):
            candidates.append(Program("python", text))
    if not candidates:
        raise ProgramError("no ```python, ```svg or ```pixel block found in the reply")
    order = {"python": 0, "pixel": 1, "svg": 2}
    if not allow_python:
        candidates = [c for c in candidates if c.kind != "python"]
        if not candidates:
            raise ProgramError("Python drawing programs are disabled (allow_python: false); reply with ```svg")
    best = min(candidates, key=lambda p: (order[p.kind], -len(p.code)))
    if len(best.code.encode("utf-8", "ignore")) > MAX_SOURCE_BYTES:
        raise ProgramError(f"the program is larger than {MAX_SOURCE_BYTES // 1024} KiB")
    return best


def _classify(lang: str, body: str) -> Optional[str]:
    if lang in {"python", "py", "python3"}:
        return "python"
    if lang in {"svg", "xml", "html"} and "<svg" in body:
        return "svg"
    if lang in {"pixel", "json", "pixeljson"} and ('"frames"' in body or '"sprite"' in body):
        return "pixel"
    if not lang:
        if body.lstrip().startswith("<svg"):
            return "svg"
        if "def draw" in body:
            return "python"
    return None


def _is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def _check_identifier(name: Optional[str], line: Any) -> None:
    if name and _is_dunder(name):
        raise ProgramError(f"line {line}: the name '{name}' is not available to drawing programs "
                           f"(the only dunder methods a program may define are __init__ and __post_init__)")


def check_python(code: str) -> None:
    """Static allowlist check run before the program ever reaches the worker."""
    try:
        tree = ast.parse(code, filename="<drawing>")
    except SyntaxError as exc:
        raise ProgramError(f"SyntaxError: {exc.msg} (line {exc.lineno})") from exc
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_IMPORTS:
                    raise ProgramError(f"line {line}: import of '{alias.name}' is not allowed")
                _check_identifier(alias.asname, line)
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module or node.module.split(".")[0] not in ALLOWED_IMPORTS:
                raise ProgramError(f"line {line}: import from '{node.module or '.'}' is not allowed")
            for alias in node.names:
                if alias.name != "*" and (alias.name in FORBIDDEN_ATTRS or alias.name.startswith("_")):
                    raise ProgramError(f"line {line}: 'from {node.module} import {alias.name}' is not available "
                                       f"to drawing programs")
                _check_identifier(alias.asname, line)
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES:
                raise ProgramError(f"line {line}: '{node.id}' is not available to drawing programs")
            if not (node.id == "__name__" and isinstance(node.ctx, ast.Load)):
                _check_identifier(node.id, line)
        elif isinstance(node, ast.Attribute):
            attr = node.attr
            if attr in FORBIDDEN_ATTRS or (attr.startswith("__") and attr not in ALLOWED_DUNDERS):
                raise ProgramError(f"line {line}: attribute '{attr}' is not available to drawing programs")
            if not isinstance(node.ctx, ast.Load):
                _check_identifier(attr, line)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name not in DEFINABLE_DUNDERS:
                _check_identifier(node.name, line)
        elif isinstance(node, ast.ClassDef):
            _check_identifier(node.name, line)
        elif isinstance(node, ast.arg):
            _check_identifier(node.arg, line)
        elif isinstance(node, ast.keyword):
            _check_identifier(node.arg, line)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                _check_identifier(name, line)
        elif isinstance(node, ast.MatchClass):
            # case object(os=x) looks the attribute up by name, like getattr.
            for attr in node.kwd_attrs:
                if attr in FORBIDDEN_ATTRS or attr.startswith("_"):
                    raise ProgramError(f"line {line}: pattern attribute '{attr}' is not available to drawing programs")
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)):
            _check_identifier(node.name, line)
        elif isinstance(node, ast.MatchMapping):
            _check_identifier(node.rest, line)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _NAME_LOOKUP_BUILTINS:
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
                attr = node.args[1].value
                if attr in FORBIDDEN_ATTRS or attr.startswith("_"):
                    raise ProgramError(f"line {line}: {node.func.id}(..., {attr!r}) is not available to "
                                       f"drawing programs")
    if not any(isinstance(n, ast.FunctionDef) and n.name == "draw" for n in tree.body):
        if not any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in
                   {"SVG", "svg", "IMAGE", "image", "img", "result"} for t in n.targets) for n in tree.body):
            raise ProgramError("the program must define a top-level draw(t) function")


def _read_roots() -> List[str]:
    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix}
    for entry in sys.path:
        if entry and ("site-packages" in entry or "dist-packages" in entry) and os.path.isdir(entry):
            roots.add(entry)
    home = os.path.expanduser("~")
    for fonts in ("/usr/share/fonts", "/usr/local/share/fonts", "/usr/share/texmf/fonts", "/opt/homebrew/share/fonts",
                  "/System/Library/Fonts", "/Library/Fonts", os.path.join(home, ".fonts"),
                  os.path.join(home, ".local", "share", "fonts"), os.path.join(home, "Library", "Fonts"),
                  r"C:\Windows\Fonts"):
        if os.path.isdir(fonts):
            roots.add(fonts)
    return sorted(roots)


def _child_env() -> Dict[str, str]:
    keep = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP", "FONTCONFIG_PATH",
            "FONTCONFIG_FILE", "XDG_DATA_DIRS", "HOME", "USERPROFILE", "DYLD_FALLBACK_LIBRARY_PATH",
            "LD_LIBRARY_PATH")
    env = {k: v for k, v in os.environ.items() if k in keep}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("OMP_NUM_THREADS", "2")
    env.setdefault("OPENBLAS_NUM_THREADS", "2")
    return env


def run_program(
    program: Program, *, width: int, height: int, seed: int, palette: Dict[str, str], style: Dict[str, Any],
    out_dir: Path, times: Sequence[float] = (0.0,), fps: int = 24, timeout: float = 90.0,
    memory_mb: int = 2048,
) -> RenderResult:
    """Render ``program`` into ``out_dir/frame_NNNN.png`` files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    if program.kind == "pixel":
        frames = render_pixel_spec(program.code, width=width, height=height, palette=palette, out_dir=out_dir,
                                   count=len(times))
        return RenderResult(frames, round(time.monotonic() - started, 3), "pixel")
    if program.kind == "python":
        check_python(program.code)
    elif "<svg" not in program.code:
        raise ProgramError("the SVG block does not contain an <svg> element")
    with tempfile.TemporaryDirectory(prefix="procgen-job-") as tmp:
        job_dir = Path(tmp)
        job = {
            "job_dir": str(job_dir), "mode": "svg" if program.kind == "svg" else "python",
            "code": program.code, "width": width, "height": height, "seed": seed,
            "palette": palette, "style": style, "times": list(times), "fps": fps,
            "sys_path": [p for p in sys.path if p and os.path.isdir(p)],
            "read_roots": _read_roots(),
            "limits": {"memory_mb": memory_mb, "cpu_seconds": int(timeout) + 5, "file_mb": 512},
        }
        job_file = job_dir / "job.json"
        job_file.write_text(json.dumps(job), encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, "-I", str(RUNNER), str(job_file)], cwd=str(job_dir), env=_child_env(),
                capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise RenderError(f"the drawing program did not finish within {timeout:.0f}s "
                              f"(keep loops vectorised and element counts under ~20k)", error_type="timeout") from exc
        payload = _last_json(proc.stdout)
        if payload is None:
            tail = (proc.stderr or proc.stdout or "").strip()[-1500:]
            hint = " (out of memory?)" if proc.returncode in (-9, 137) else ""
            raise RenderError(f"the render worker exited with code {proc.returncode}{hint}: {tail}",
                              error_type="worker_crash")
        if not payload.get("ok"):
            raise RenderError(payload.get("error") or "render failed", error_type=payload.get("error_type") or "render_error")
        frames = []
        for i, src in enumerate(payload.get("frames") or []):
            dst = out_dir / f"frame_{i:04d}.png"
            os.replace(src, dst)
            frames.append(dst)
        if not frames:
            raise RenderError("the drawing program produced no frames")
        return RenderResult(frames, float(payload.get("elapsed") or 0.0), program.kind)


def _last_json(stdout: str) -> Optional[dict]:
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


# --- pixel grid DSL ---------------------------------------------------------------------------

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _hex_to_rgb(value: str) -> tuple:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def parse_pixel_spec(text: str, palette: Dict[str, str]) -> Dict[str, Any]:
    try:
        spec = json.loads(text)
    except ValueError as exc:
        raise ProgramError(f"pixel spec is not valid JSON: {exc}") from exc
    if not isinstance(spec, dict):
        raise ProgramError("pixel spec must be a JSON object")
    frames = spec.get("frames") or ([spec["sprite"]] if spec.get("sprite") else None)
    if not isinstance(frames, list) or not frames:
        raise ProgramError("pixel spec needs a non-empty 'frames' list (each frame is a list of row strings)")
    if len(frames) > 16:
        raise ProgramError("pixel spec supports at most 16 frames")
    keys = spec.get("palette") or {}
    if not isinstance(keys, dict):
        raise ProgramError("pixel 'palette' must map single characters to palette roles or hex colours")
    colours: Dict[str, tuple] = {}
    for ch, value in keys.items():
        if not isinstance(ch, str) or len(ch) != 1:
            raise ProgramError(f"palette key {ch!r} must be a single character")
        if isinstance(value, str) and value in palette:
            colours[ch] = _hex_to_rgb(palette[value])
        elif isinstance(value, str) and _HEX.match(value):
            colours[ch] = _hex_to_rgb(value)
        elif value is None:
            continue
        else:
            raise ProgramError(f"palette entry {ch!r}: {value!r} is neither a palette role nor a hex colour")
    norm_frames = []
    for f_index, frame in enumerate(frames):
        if not isinstance(frame, list) or not all(isinstance(r, str) for r in frame):
            raise ProgramError(f"frame {f_index + 1} must be a list of row strings")
        if len(frame) > 128 or any(len(r) > 128 for r in frame):
            raise ProgramError("pixel frames are limited to 128x128")
        for row in frame:
            for ch in row:
                if ch not in ". " and ch not in colours:
                    raise ProgramError(f"frame {f_index + 1} uses '{ch}' which is not in the pixel palette")
        norm_frames.append(frame)
    return {"frames": norm_frames, "colours": colours, "layout": str(spec.get("layout") or
            ("sheet" if len(norm_frames) > 1 else "single")), "title": str(spec.get("title") or "")[:40],
            "background": str(spec.get("background") or "checker"), "labels": bool(spec.get("labels", True))}


def _sprite_image(frame: List[str], colours: Dict[str, tuple]):
    from PIL import Image
    h = len(frame)
    w = max((len(r) for r in frame), default=1)
    img = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    px = img.load()
    for y, row in enumerate(frame):
        for x, ch in enumerate(row):
            if ch in colours:
                px[x, y] = colours[ch] + (255,)
    return img


def _pixel_font(size: int):
    from PIL import ImageFont
    for name in ("PressStart2P-Regular.ttf", "DejaVuSansMono-Bold.ttf", "DejaVuSans-Bold.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_pixel_spec(text: str, *, width: int, height: int, palette: Dict[str, str], out_dir: Path,
                      count: int = 1) -> List[Path]:
    """Render a pixel spec to one PNG (sheet/single) or one PNG per frame (animation)."""
    from PIL import Image, ImageDraw
    spec = parse_pixel_spec(text, palette)
    sprites = [_sprite_image(f, spec["colours"]) for f in spec["frames"]]
    bg, bg2 = _hex_to_rgb(palette.get("bg", "#1c1730")), _hex_to_rgb(palette.get("bg2", "#16122a"))
    accent = _hex_to_rgb(palette.get("accent", "#ffd25a"))
    muted = _hex_to_rgb(palette.get("metal2", "#8f8ac0"))
    ink = _hex_to_rgb(palette.get("shadow", "#0c0a16"))

    def canvas():
        img = Image.new("RGB", (width, height), bg)
        if spec["background"] == "checker":
            d = ImageDraw.Draw(img)
            cell = max(8, min(width, height) // 32)
            for y in range(0, height, cell):
                for x in range(0, width, cell):
                    if (x // cell + y // cell) % 2:
                        d.rectangle([x, y, x + cell - 1, y + cell - 1], fill=bg2)
        return img

    def paste_scaled(img, sprite, box):
        x0, y0, bw, bh = box
        scale = max(1, min(bw // sprite.width, bh // sprite.height))
        big = sprite.resize((sprite.width * scale, sprite.height * scale), Image.NEAREST)
        x, y = x0 + (bw - big.width) // 2, y0 + (bh - big.height) // 2
        d = ImageDraw.Draw(img)
        d.ellipse([x + big.width * 0.2, y + big.height - scale, x + big.width * 0.8, y + big.height + scale * 1.5],
                  fill=ink)
        img.paste(big, (x, y), big)

    out_dir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    if count > 1:
        for i in range(count):
            img = canvas()
            sprite = sprites[i % len(sprites)]
            pad = min(width, height) // 8
            paste_scaled(img, sprite, (pad, pad, width - 2 * pad, height - 2 * pad))
            p = out_dir / f"frame_{i:04d}.png"
            img.save(p, "PNG")
            paths.append(p)
        return paths

    img = canvas()
    d = ImageDraw.Draw(img)
    font = _pixel_font(max(12, height // 36))
    top = 0
    if spec["title"]:
        d.text((width // 2, height // 9), spec["title"], font=font, fill=accent, anchor="mm")
        top = height // 6
    n = len(sprites)
    if spec["layout"] == "single" or n == 1:
        pad = min(width, height) // 8
        paste_scaled(img, sprites[0], (pad, top + pad // 2, width - 2 * pad, height - top - pad))
    else:
        margin = width // 64
        cell_w = (width - margin * (n + 1)) // n
        cell_h = min(cell_w, int(height * 0.3))
        y0 = top + height // 24
        for i, sprite in enumerate(sprites):
            x0 = margin + i * (cell_w + margin)
            d.rectangle([x0, y0, x0 + cell_w, y0 + cell_h], outline=muted, width=max(2, width // 400))
            paste_scaled(img, sprite, (x0 + 6, y0 + 6, cell_w - 12, cell_h - 12))
            if spec["labels"]:
                d.text((x0 + cell_w // 2, y0 + cell_h + height // 30), str(i + 1), font=font, fill=muted, anchor="mm")
        hero_top = y0 + cell_h + height // 12
        hero_h = height - hero_top - height // 20
        if hero_h > height // 6:
            paste_scaled(img, sprites[0], (width // 3, hero_top, width // 3, hero_h))
    p = out_dir / "frame_0000.png"
    img.save(p, "PNG")
    return [p]


# --- image statistics for the structural critic -----------------------------------------------

def image_stats(path: Path, palette: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Cheap numeric facts about a render: blankness, contrast, colour use, palette adherence."""
    from PIL import Image, ImageStat
    with Image.open(path) as im:
        rgb = im.convert("RGB")
        small = rgb.resize((128, 128))
    stat = ImageStat.Stat(small)
    mean = [round(v, 1) for v in stat.mean]
    std = round(sum(stat.stddev) / 3.0, 2)
    colours = small.getcolors(128 * 128) or []
    colours.sort(reverse=True)
    total = 128 * 128
    dominant = colours[0][0] / total if colours else 1.0
    quant = small.quantize(colors=8)
    qpal = quant.getpalette()[: 8 * 3]
    counts = sorted(quant.getcolors() or [], reverse=True)
    top = [("#%02x%02x%02x" % tuple(qpal[i * 3: i * 3 + 3]), round(c / total, 3)) for c, i in counts[:6]]
    stats: Dict[str, Any] = {
        "size": list(rgb.size), "mean_rgb": mean, "contrast_std": std, "distinct_colours": len(colours),
        "dominant_colour_share": round(dominant, 3), "top_colours": top,
        "looks_blank": std < 4.0 or dominant > 0.97,
    }
    if palette:
        pal = [_hex_to_rgb(v) for v in palette.values()]
        near = 0
        for count, colour in colours:
            if min(sum((a - b) ** 2 for a, b in zip(colour, p)) for p in pal) <= 48 ** 2:
                near += count
        stats["palette_adherence"] = round(near / total, 3)
    return stats


def contact_strip(frames: Sequence[Path], out: Path, *, height: int = 384) -> Path:
    """Side-by-side strip of a few frames (critic input for animations)."""
    from PIL import Image
    imgs = []
    for f in frames:
        with Image.open(f) as im:
            im = im.convert("RGB")
            w = int(im.width * height / im.height)
            imgs.append(im.resize((w, height)))
    strip = Image.new("RGB", (sum(i.width for i in imgs) + 8 * (len(imgs) - 1), height), (0, 0, 0))
    x = 0
    for im in imgs:
        strip.paste(im, (x, 0))
        x += im.width + 8
    strip.save(out, "PNG")
    return out
