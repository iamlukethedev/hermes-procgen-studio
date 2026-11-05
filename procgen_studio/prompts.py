"""Prompts for the drawing model, the reviser and the critic."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .style import Style

TECHNIQUES_DIR = Path(__file__).resolve().parent / "techniques"

# (technique file, keywords that select it). Order is the tie-break.
_TECHNIQUES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("icon_tiles.py", ("icon", "icons", "inventory", "item", "items", "badge", "badges", "sword", "potion",
                       "shield", "key", "loot", "pickup", "ui", "button", "achievement", "emoji", "set of")),
    ("brand_mark.py", ("logo", "logotype", "wordmark", "brand", "mark", "monogram", "app icon", "favicon",
                       "emblem", "crest", "startup", "company", "product")),
    ("logo_motion.py", ("animate", "animated", "animation", "motion", "intro", "reveal", "loader", "loop",
                        "spinner")),
    ("lowpoly_creature.py", ("monster", "creature", "character", "golem", "demon", "beast", "boss", "dragon",
                             "low-poly", "lowpoly", "low poly", "faceted", "crystal", "warrior", "robot", "mascot")),
    ("layered_scene.py", ("scene", "landscape", "forest", "city", "skyline", "mountain", "night", "sunset",
                          "hero image", "wallpaper", "background", "cabin", "village", "island", "illustration")),
    ("topo_pattern.py", ("pattern", "texture", "topo", "topographic", "contour", "noise", "generative", "tile",
                         "seamless", "abstract", "flow field", "terrain", "marble", "voronoi")),
    ("pixel_sprite.pixel.json", ("pixel", "sprite", "8-bit", "16-bit", "retro", "nes", "snes", "gameboy",
                                 "tileset", "spritesheet", "sprite sheet")),
)

_KIND_FENCE = {"python": "python", "pixel": "pixel"}

# What each reference draws: text it bakes in, and the subject words a request would use for it.
# Used to catch a model that pastes a reference instead of drawing the request.
REFERENCE_FINGERPRINTS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "icon_tiles.py": ((), ("sword", "potion", "shield", "key")),
    "brand_mark.py": (("forgeline", "SHIP AGENTS"), ("forgeline",)),
    "logo_motion.py": (("forgeline", "SHIP AGENTS"), ("forgeline",)),
    "lowpoly_creature.py": (("HOLLOW WARDEN",), ("warden", "golem", "monster", "creature", "demon", "beast")),
    "layered_scene.py": ((), ("cabin", "forest", "moon", "night", "pine")),
    "topo_pattern.py": ((), ("topo", "topographic", "contour", "map", "terrain")),
    "pixel_sprite.pixel.json": (("KNIGHT - WALK",), ("knight",)),
}


def copied_reference(code: str, request: str, *, threshold: float = 0.8) -> Optional[str]:
    """Return an issue if the program is a reference pasted in place of the request, else None."""
    import difflib
    low_code, low_req = code.lower(), request.lower()
    for name, (literals, subject) in REFERENCE_FINGERPRINTS.items():
        words = tuple(w.lower() for w in literals + subject)
        if any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", low_req) for w in words):
            continue  # the request is about this reference's own subject
        for literal in literals:
            if literal.lower() in low_code:
                return (f"the drawing contains '{literal}' from the {name} technique reference; the request never "
                        f"asked for it. Draw the requested subject and text, not the reference's")
        ref = _technique_text(name, max_chars=10**6)
        a = [ln.strip() for ln in code.splitlines() if ln.strip()]
        b = [ln.strip() for ln in ref.splitlines() if ln.strip()]
        if len(a) >= 8 and difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= threshold:
            return (f"the program is a near copy of the {name} technique reference instead of a drawing of the "
                    f"request; design the requested subject from scratch")
    return None


def _technique_text(name: str, max_chars: int = 9000) -> str:
    text = (TECHNIQUES_DIR / name).read_text(encoding="utf-8")
    if len(text) > max_chars:
        text = text[:max_chars] + "\n# ... (truncated reference)"
    return text


def pick_techniques(prompt: str, style: Optional[Style] = None, *, animated: bool = False, limit: int = 2) -> List[str]:
    """Choose the reference programs most relevant to this request."""
    text = prompt.lower()
    scores: Dict[str, int] = {}
    for name, words in _TECHNIQUES:
        score = sum(2 if " " in w else 1 for w in words if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text))
        if score:
            scores[name] = score
    if style is not None and style.renderer == "pixel":
        scores["pixel_sprite.pixel.json"] = scores.get("pixel_sprite.pixel.json", 0) + 3
    if animated:
        scores["logo_motion.py"] = scores.get("logo_motion.py", 0) + 2
    order = [n for n, _ in _TECHNIQUES]
    ranked = sorted(scores, key=lambda n: (-scores[n], order.index(n)))
    if not ranked:
        ranked = ["layered_scene.py"]
    return ranked[:limit]


CONTRACT = """\
You are Procgen Studio, an illustrator who draws by writing code. You never describe an image in prose:
you reply with ONE fenced code block that renders it. The code is executed locally and rasterised to PNG.

OUTPUT FORMAT (pick one):
1. ```python  (preferred) a program that defines draw(t) and returns ONE of:
     - an SVG document string (rendered with cairosvg at exactly W x H), or
     - a PIL.Image.Image, or
     - a numpy uint8 array of shape (H, W, 3|4).
2. ```svg     a literal SVG document (only for simple static marks).
3. ```pixel   JSON sprite spec for pixel art (see PIXEL ART below). Never hand-place pixels in Python.

PREDEFINED NAMES (do not import or redefine them):
  W, H        canvas size in pixels. Lay everything out relative to W and H; never assume 1024.
  SEED        integer seed for this render. rng = random.Random(SEED) is already created as `rng`.
              Use rng (or numpy.random.default_rng(SEED)) for EVERY random choice so the image is reproducible
              and re-rolling the seed gives a meaningful variation (star fields, scatter, jitter, terrain).
  PAL         dict of role -> hex colour from the style contract. Take every colour from PAL (or derive it
              with mix/shade). Never hard-code hex colours: the user restyles by swapping the palette.
  t           draw(t) receives t in [0, 1]. Still images get t = 0. Animations get one call per frame.
  helpers     svg_doc(body, defs="", bg=None) -> full <svg> string sized W x H
              mix(hex_a, hex_b, k) -> hex     shade(hex, k) -> hex (k<1 darker, k>1 lighter)
              hex_to_rgb(hex) -> (r,g,b)      rgb_to_hex((r,g,b)) -> hex
              poly_pts(cx, cy, r, n=6, rot=-90) -> "x,y x,y ..." for regular polygons
              ease(x) -> cubic ease-out clamped to [0, 1]
              pixel_grid(rows, colors, scale=1) -> RGBA PIL image from character rows

ALLOWED IMPORTS: math, random, colorsys, itertools, functools, statistics, cmath, re, json, dataclasses,
typing, collections, heapq, bisect, copy, operator, string, textwrap, numpy, PIL. Nothing else: no os, sys,
pathlib, subprocess, open(), eval/exec, network or file access. Dunder attributes are unavailable.

RENDERER FACTS (cairosvg):
- NO blur: feGaussianBlur and other filters are ignored. Make glows, halos, soft shadows and fog with
  radialGradient / linearGradient stops fading to stop-opacity 0, or stacked semi-transparent shapes.
- Supported well: paths, polygons, circles, ellipses, rects with rx, linear/radial gradients (with
  gradientTransform), opacity, stroke-linejoin/linecap, stroke-dasharray, clipPath, transforms, <text>.
- Gradient and clip ids must be unique per document (suffix them when you build repeated parts).
- Text: use font stacks such as "Inter, DejaVu Sans, sans-serif", "JetBrains Mono, DejaVu Sans Mono,
  monospace" or "Cinzel, Liberation Serif, DejaVu Serif, serif". Keep text short; measure nothing.
- Keep documents under ~20k elements and programs fast (< 10 s). Vectorise numpy work; no per-pixel loops.

QUALITY BAR: polished, intentional, finished art, never clip art. Clear silhouette and focal point,
deliberate light direction, two-tone shading plus one gradient per material, a specular highlight,
a grounded cast shadow, depth through value and atmosphere, generous padding, consistent stroke weights.
Fill the whole canvas with a designed background. Match the subject count the user asked for exactly.

GOOD FITS: icons, game assets, logos, flat illustrations, scenes, patterns, diagrams, low-poly creatures,
motion graphics, pixel sprites (via the pixel spec). NOT photorealism: if asked for a photo, produce the
best stylised illustration of it instead.

PIXEL ART: reply with ```pixel JSON:
  {"title": "KNIGHT - WALK", "layout": "sheet" | "single", "background": "checker" | "none",
   "palette": {"K": "ink", "h": "base", "v": "#28c8f0"},      # char -> PAL role or hex
   "frames": [["....KK....", "...KhhK...", ...], ...]}          # 1-16 frames, rows of equal length, <=128px
  '.' is transparent. 16-32 px sprites, 1 px ink outline, 3 shades per material, top-left light.

Reply with the code block only. No explanation before or after it.
"""

REVISE_INSTRUCTIONS = """\
Revise the drawing program below so the render fixes the reviewer's issues while keeping everything
that already works. Keep the same output format and the same predefined names (W, H, SEED, PAL, rng, t).
Reply with ONE complete fenced code block (not a diff)."""

REPAIR_INSTRUCTIONS = """\
The drawing program below failed to render. Fix the error and reply with ONE complete fenced code block.
Do not change the design unless the error requires it. Remember: no imports outside the allowlist,
no dunder attributes, no filters (blur is unsupported), draw(t) must return SVG text, a PIL image
or a uint8 numpy array."""

EDIT_INSTRUCTIONS = """\
Edit the existing drawing program below according to the user's request. Keep the composition, palette
roles and everything the request does not mention. Reply with ONE complete fenced code block."""

ANIMATE_INSTRUCTIONS = """\
This is an ANIMATION. draw(t) is called once per frame with t from 0 to 1 ({frames} frames at {fps} fps,
{seconds:.1f} s). Move things with smooth eased curves, stagger elements, and make sure every frame is a
complete, finished image. {loop_hint} Keep each frame fast to render (< 0.5 s)."""


@dataclass
class DrawRequest:
    prompt: str
    width: int
    height: int
    seed: int
    style: Style
    complexity: int = 0
    animated: bool = False
    frames: int = 1
    fps: int = 24
    loop: bool = False
    renderer_hint: str = ""
    allow_python: bool = True


def _complexity_hint(value: int) -> str:
    if value <= -50:
        return "Keep it minimal: few shapes, lots of negative space."
    if value < -10:
        return "Lean towards simple shapes and restraint."
    if value >= 50:
        return "Go dense and richly detailed: secondary details, texture and particles everywhere they help."
    if value > 10:
        return "Add a little more secondary detail than usual."
    return ""


def system_prompt(req: DrawRequest, *, techniques: Optional[Sequence[str]] = None) -> str:
    parts = [CONTRACT]
    if not req.allow_python:
        parts.append("PYTHON IS DISABLED for this install: reply with ```svg or ```pixel only.")
    names = list(techniques) if techniques is not None else pick_techniques(req.prompt, req.style, animated=req.animated)
    if names:
        parts.append("TECHNIQUE REFERENCES: study HOW they are built (layering, gradients, shading, use of PAL and rng). "
                     "They are NOT the request. Never reuse their subjects, names, text, shapes or layout; "
                     "a reply that pastes a reference is rejected automatically.")
        for name in names:
            fence = "pixel" if name.endswith(".json") else "python"
            parts.append(f"--- {name} ---\n```{fence}\n{_technique_text(name)}\n```")
    return "\n\n".join(parts)


def user_prompt(req: DrawRequest) -> str:
    orient = "square" if req.width == req.height else ("landscape" if req.width > req.height else "portrait")
    lines = [
        f"REQUEST: {req.prompt.strip()}",
        f"CANVAS: W={req.width}, H={req.height} ({orient}). SEED={req.seed}.",
        "",
        req.style.as_prompt(),
    ]
    hint = _complexity_hint(req.complexity)
    if hint:
        lines += ["", hint]
    if req.renderer_hint:
        lines += ["", f"Use the ```{req.renderer_hint} output format."]
    elif req.style.renderer in _KIND_FENCE and not req.animated:
        if req.style.renderer == "pixel" or re.search(r"pixel|sprite|8-bit|16-bit", req.prompt, re.I):
            lines += ["", f"Use the ```{_KIND_FENCE[req.style.renderer]} output format."]
    lines += ["", "Draw exactly the REQUEST above: its subjects, its count, its text. The technique references "
                  "only show how to build things; none of their content belongs in this drawing."]
    if req.animated:
        loop_hint = "The last frame must match the first so it loops seamlessly." if req.loop else \
            "End on a clean, finished hero frame and hold it."
        lines += ["", ANIMATE_INSTRUCTIONS.format(frames=req.frames, fps=req.fps, seconds=req.frames / max(1, req.fps),
                                                  loop_hint=loop_hint),
                  "Use the ```python output format (draw(t) must depend on t)."]
    return "\n".join(lines)


def revise_prompt(req: DrawRequest, code: str, kind: str, issues: Sequence[str], fixes: Sequence[str]) -> str:
    bullet = "\n".join(f"- {i}" for i in list(issues)[:8]) or "- (no specific issues listed)"
    fix = "\n".join(f"- {f}" for f in list(fixes)[:8])
    return (f"{REVISE_INSTRUCTIONS}\n\nORIGINAL REQUEST: {req.prompt}\n\n{req.style.as_prompt()}\n\n"
            f"REVIEWER ISSUES:\n{bullet}\n" + (f"SUGGESTED FIXES:\n{fix}\n" if fix else "") +
            f"\nCURRENT PROGRAM:\n```{_fence(kind)}\n{code}\n```")


def repair_prompt(req: DrawRequest, code: str, kind: str, error: str) -> str:
    return (f"{REPAIR_INSTRUCTIONS}\n\nREQUEST: {req.prompt}\nCANVAS: W={req.width}, H={req.height}\n\n"
            f"ERROR:\n{error.strip()[:2000]}\n\nPROGRAM:\n```{_fence(kind)}\n{code}\n```")


def edit_prompt(req: DrawRequest, code: str, kind: str, instruction: str) -> str:
    return (f"{EDIT_INSTRUCTIONS}\n\nUSER REQUEST: {instruction.strip()}\nCANVAS: W={req.width}, H={req.height}. "
            f"SEED={req.seed}.\n\n{req.style.as_prompt()}\n\nEXISTING PROGRAM:\n```{_fence(kind)}\n{code}\n```")


def _fence(kind: str) -> str:
    return {"python": "python", "svg": "svg", "pixel": "pixel"}.get(kind, "python")


CRITIC_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 10,
                  "description": "0 = unusable, 7 = shippable, 10 = excellent"},
        "matches_prompt": {"type": "boolean"},
        "follows_style": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "fixes": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
    },
    "required": ["score", "matches_prompt", "issues"],
}

CRITIC_VISION = """\
You are an exacting art director reviewing a render produced by a drawing program.
Judge the IMAGE against the request and the style contract. Check: every requested subject present and
the exact count; readable silhouettes; composition and padding; the style rules (outline, lighting,
shading, background); palette use; visible defects (elements cut off by the canvas edge, overlapping
mess, empty canvas, unreadable or clipped text, broken shapes, flat lifeless lighting).
Score 0-10 where 7 means "shippable as-is". Issues must be concrete and visual ("the key's teeth are
cut off at the right edge"). Fixes must be concrete code-level instructions."""

CRITIC_STRUCTURE = """\
You are an exacting art director, but you cannot see the image. You get the drawing program and
measured facts about its render. Judge whether the program, as written, satisfies the request and the
style contract: requested subjects and count present, coordinates inside the W x H canvas, colours taken
from PAL, style rules implemented (outline widths, lighting direction, gradients, highlights, shadows),
background designed, nothing obviously broken. Use the measured facts: a blank or single-colour render,
very low contrast or low palette adherence are real problems.
Score 0-10 where 7 means "shippable as-is". Give concrete issues and code-level fixes."""


def critic_input_text(req: DrawRequest, stats: Dict[str, object], code: Optional[str] = None,
                      kind: str = "python", frames_note: str = "") -> str:
    parts = [f"REQUEST: {req.prompt}", f"CANVAS: {req.width}x{req.height}", req.style.as_prompt(),
             f"MEASURED RENDER FACTS: {stats}"]
    if frames_note:
        parts.append(frames_note)
    if code is not None:
        parts.append(f"PROGRAM:\n```{_fence(kind)}\n{code[:24000]}\n```")
    return "\n\n".join(parts)
