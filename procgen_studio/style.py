"""STYLE.md: the shared art contract injected into every generation.

A STYLE.md is a small, human-editable file of ``key: value`` lines plus free
prose. It can extend a built-in preset (``preset: ember``), pull a whole
palette from another preset (``palette: frost``) and override single palette
roles (``palette.gem: #56e0ff``). Everything else (outline, lighting, shading,
shape language, ...) is passed to the model verbatim, so a one-line change
restyles every asset on the next regenerate.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PRESETS_DIR = Path(__file__).resolve().parent / "presets"

# Every palette defines the same roles so a drawing program written against
# PAL["gem"] keeps working when the palette is swapped.
PALETTE_ROLES: Tuple[str, ...] = (
    "bg", "bg2", "ink", "base", "base2", "metal", "metal2", "accent", "accent2",
    "wood", "wood2", "liquid", "liquid2", "gem", "glow", "highlight", "shadow",
    "sky", "sky2", "ground", "foliage", "light",
)

ROLE_HINTS: Dict[str, str] = {
    "bg": "background, light end of the gradient",
    "bg2": "background, dark end / vignette",
    "ink": "outlines and darkest line work",
    "base": "main subject material, lit side",
    "base2": "main subject material, shadow side",
    "metal": "steel / silver, lit",
    "metal2": "steel / silver, shadow",
    "accent": "trim, gold, brand accent, lit",
    "accent2": "trim, gold, brand accent, shadow",
    "wood": "wood / leather, lit",
    "wood2": "wood / leather, shadow",
    "liquid": "potions, water, energy, lit",
    "liquid2": "potions, water, energy, deep",
    "gem": "gems, eyes, small hot details",
    "glow": "glows, halos, rim light",
    "highlight": "specular highlights",
    "shadow": "cast shadows, ambient occlusion",
    "sky": "sky, top",
    "sky2": "sky, horizon",
    "ground": "ground plane, far terrain",
    "foliage": "trees, grass, vegetation",
    "light": "lamps, moons, windows, emissive",
}

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_.\-]*)\s*:\s*(.*)$")
_INLINE_COMMENT_RE = re.compile(r"\s+#\s.*$")
_PRESET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")
MAX_STYLE_BYTES = 64 * 1024

# Keys that carry structure rather than prose.
_STRUCTURAL_KEYS = {"preset", "palette", "name", "renderer", "description"}


class StyleError(ValueError):
    """Raised for an unknown preset or a malformed STYLE.md."""


@dataclass
class Style:
    """A resolved style contract."""

    name: str
    palette_name: str
    palette: Dict[str, str]
    rules: Dict[str, str] = field(default_factory=dict)
    notes: str = ""
    renderer: str = ""
    description: str = ""
    source: str = "preset"
    path: Optional[str] = None
    raw: str = ""

    def fingerprint(self) -> str:
        """Stable hash of everything that affects the drawing."""
        h = hashlib.sha256()
        h.update(self.palette_name.encode())
        for role in sorted(self.palette):
            h.update(f"{role}={self.palette[role]};".encode())
        for key in sorted(self.rules):
            h.update(f"{key}={self.rules[key]};".encode())
        h.update(self.notes.encode())
        h.update(self.renderer.encode())
        return h.hexdigest()[:12]

    def as_prompt(self) -> str:
        """Render the contract the way the drawing model sees it."""
        lines = [f"STYLE CONTRACT: {self.name}" + (f" ({self.description})" if self.description else "")]
        for key, value in self.rules.items():
            lines.append(f"- {key.replace('_', ' ')}: {value}")
        if self.renderer:
            lines.append(f"- preferred renderer: {self.renderer}")
        lines.append(f"- palette ({self.palette_name}), read colours from PAL[role], never hard-code them:")
        for role in PALETTE_ROLES:
            if role in self.palette:
                lines.append(f"    PAL['{role}'] = {self.palette[role]}   # {ROLE_HINTS.get(role, '')}")
        if self.notes.strip():
            lines.append("- notes:")
            lines.extend(f"    {ln}" for ln in self.notes.strip().splitlines())
        return "\n".join(lines)

    def summary(self) -> Dict[str, object]:
        return {
            "name": self.name, "palette": self.palette_name, "source": self.source,
            "path": self.path, "fingerprint": self.fingerprint(), "renderer": self.renderer or "auto",
        }


@dataclass
class _Parsed:
    preset: Optional[str]
    palette: Optional[str]
    palette_overrides: Dict[str, str]
    rules: Dict[str, str]
    notes: str
    name: Optional[str]
    renderer: Optional[str]
    description: Optional[str]


def _clean_value(value: str) -> str:
    return _INLINE_COMMENT_RE.sub("", value).strip().strip('"').strip("'").strip()


def parse_style_text(text: str) -> _Parsed:
    """Parse STYLE.md text. Unknown keys are kept as free-form rules."""
    if len(text.encode("utf-8", "ignore")) > MAX_STYLE_BYTES:
        raise StyleError(f"STYLE.md is larger than {MAX_STYLE_BYTES // 1024} KiB")
    preset = palette = name = renderer = description = None
    overrides: Dict[str, str] = {}
    rules: Dict[str, str] = {}
    notes: List[str] = []
    in_fence = False
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        stripped = line.strip().lstrip("-*").strip()
        if not stripped or stripped.startswith("<!--"):
            continue
        if stripped.startswith("#") and not in_fence:
            continue  # markdown headings and comment lines
        m = _KEY_RE.match(stripped) if not in_fence else None
        if not m:
            notes.append(stripped)
            continue
        key, value = m.group(1).lower().replace("-", "_"), _clean_value(m.group(2))
        if key.startswith("palette."):
            role = key.split(".", 1)[1]
            if role not in PALETTE_ROLES:
                raise StyleError(f"unknown palette role '{role}' (roles: {', '.join(PALETTE_ROLES)})")
            if not _HEX_RE.match(value):
                raise StyleError(f"palette.{role} must be a hex colour like #1a1020, got '{value}'")
            overrides[role] = _expand_hex(value)
        elif key == "preset":
            preset = value.lower()
        elif key == "palette":
            palette = value.lower()
        elif key == "name":
            name = value
        elif key == "renderer":
            renderer = value.lower()
        elif key == "description":
            description = value
        elif value:
            rules[key] = value
    return _Parsed(preset, palette, overrides, rules, "\n".join(notes), name, renderer, description)


def _expand_hex(value: str) -> str:
    value = value.lower()
    if len(value) == 4:
        value = "#" + "".join(c * 2 for c in value[1:])
    return value


def list_presets() -> List[str]:
    return sorted(p.stem for p in PRESETS_DIR.glob("*.md"))


def _preset_path(name: str) -> Path:
    if not _PRESET_NAME_RE.match(name or ""):
        raise StyleError(f"invalid preset name '{name}'")
    path = PRESETS_DIR / f"{name}.md"
    if not path.is_file():
        raise StyleError(f"unknown preset '{name}' (available: {', '.join(list_presets())})")
    return path


def _resolve(parsed: _Parsed, *, depth: int = 0,
             name: Optional[str] = None) -> Tuple[Dict[str, str], str, Dict[str, str], str, str, str]:
    """Return (palette, palette_name, rules, notes, renderer, description) with preset inheritance."""
    if depth > 4:
        raise StyleError("preset inheritance is nested too deeply")
    palette: Dict[str, str] = {}
    palette_name = ""
    rules: Dict[str, str] = {}
    notes, renderer, description = "", "", ""
    if parsed.preset:
        base = parse_style_text(_preset_path(parsed.preset).read_text(encoding="utf-8"))
        palette, palette_name, rules, notes, renderer, description = _resolve(base, depth=depth + 1,
                                                                              name=parsed.preset)
        palette_name = palette_name or parsed.preset
    if parsed.palette:
        if parsed.palette != palette_name:
            pal_src = parse_style_text(_preset_path(parsed.palette).read_text(encoding="utf-8"))
            pal, _n, _r, _no, _re, _d = _resolve(pal_src, depth=depth + 1)
            palette = dict(pal)
        palette_name = parsed.palette
    palette.update(parsed.palette_overrides)
    if name and parsed.palette_overrides and not parsed.palette:
        palette_name = name  # a preset file that defines its own colours owns its palette name
    rules.update(parsed.rules)
    if parsed.notes:
        notes = (notes + "\n" + parsed.notes).strip() if notes else parsed.notes
    renderer = parsed.renderer or renderer
    description = parsed.description or description
    return palette, palette_name, rules, notes, renderer, description


def _finalize(palette: Dict[str, str]) -> Dict[str, str]:
    """Fill any role a custom palette left out from its nearest sibling role."""
    fallback = {
        "bg2": "bg", "base2": "base", "metal2": "metal", "accent2": "accent", "wood2": "wood",
        "liquid2": "liquid", "sky2": "sky", "glow": "accent", "gem": "accent", "light": "glow",
        "highlight": "light", "shadow": "ink", "ground": "bg2", "foliage": "base2", "sky": "bg",
        "metal": "highlight", "wood": "base2", "liquid": "accent", "base": "accent", "accent": "base",
    }
    out = dict(palette)
    defaults = {"bg": "#14121c", "ink": "#0d0b12", "highlight": "#ffffff", "base": "#8a7cff"}
    for role, value in defaults.items():
        out.setdefault(role, value)
    for _ in range(3):
        for role in PALETTE_ROLES:
            if role not in out and fallback.get(role) in out:
                out[role] = out[fallback[role]]
    for role in PALETTE_ROLES:
        out.setdefault(role, out["base"])
    return {role: out[role] for role in PALETTE_ROLES}


def load_preset(name: str) -> Style:
    path = _preset_path(name)
    text = path.read_text(encoding="utf-8")
    parsed = parse_style_text(text)
    palette, palette_name, rules, notes, renderer, description = _resolve(parsed, name=name)
    palette_name = palette_name or name
    return Style(name=parsed.name or name, palette_name=palette_name, palette=_finalize(palette), rules=rules,
                 notes=notes, renderer=renderer, description=description, source="preset", path=None, raw=text)


def load_style_file(path: os.PathLike | str) -> Style:
    p = Path(path).expanduser()
    if not p.is_file():
        raise StyleError(f"style file not found: {p}")
    text = p.read_text(encoding="utf-8")
    parsed = parse_style_text(text)
    palette, palette_name, rules, notes, renderer, description = _resolve(parsed)
    if not palette and not parsed.palette_overrides:
        raise StyleError(f"{p}: STYLE.md needs a 'preset:' or 'palette:' line (or palette.<role> colours)")
    return Style(name=parsed.name or parsed.preset or "project",
                 palette_name=palette_name or "custom",
                 palette=_finalize(palette), rules=rules, notes=notes, renderer=renderer,
                 description=description, source="file", path=str(p.resolve()), raw=text)


def looks_like_procgen_style(path: Path) -> bool:
    """A repo may already have an unrelated STYLE.md (a writing guide); only claim ours."""
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:8192]
    except OSError:
        return False
    return bool(re.search(r"^\s*[-*]?\s*(preset|palette(\.[a-z0-9]+)?)\s*:", head, re.MULTILINE | re.IGNORECASE))


def find_project_style(cwd: Optional[str]) -> Optional[Path]:
    """``./.procgen/STYLE.md`` then ``./STYLE.md`` (only if it is a procgen style)."""
    if not cwd:
        return None
    base = Path(cwd)
    for candidate in (base / ".procgen" / "STYLE.md", base / "STYLE.md"):
        if candidate.is_file() and looks_like_procgen_style(candidate):
            return candidate
    return None


def resolve_style(
    *, override: Optional[str] = None, style_file: Optional[str] = None, cwd: Optional[str] = None,
    user_style: Optional[Path] = None, active_preset: Optional[str] = None, default_preset: str = "flat-illustration",
) -> Style:
    """Pick the style for one generation.

    Precedence: an explicit override (``[style=frost]`` in the prompt, a preset
    name or a path) > the ``style_file`` setting > a project STYLE.md in the
    working directory > the user STYLE.md in the plugin data dir > the preset
    chosen with ``/procgen style`` > the default preset.
    """
    if override:
        candidate = Path(override).expanduser()
        if override.endswith(".md") or os.sep in override:
            return load_style_file(candidate if candidate.is_absolute() or not cwd else Path(cwd) / candidate)
        return load_preset(override.lower())
    if style_file:
        p = Path(style_file).expanduser()
        if not p.is_absolute() and cwd:
            p = Path(cwd) / p
        return load_style_file(p)
    project = find_project_style(cwd)
    if project is not None:
        return load_style_file(project)
    if user_style is not None and user_style.is_file():
        return load_style_file(user_style)
    return load_preset(active_preset or default_preset)


def preset_text(name: str) -> str:
    return _preset_path(name).read_text(encoding="utf-8")
