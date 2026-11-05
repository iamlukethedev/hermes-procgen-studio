"""Critic pass: does the render match the prompt and the STYLE contract?

Three layers, cheapest first:

1. ``auto``: free numeric checks on the PNG (blank canvas, single colour,
   palette drift). A blank render fails without spending a model call.
2. ``vision``: the PNG plus the request and style go to the model (or the
   ``procgen_critic`` slot, which inherits Hermes's vision slot). If the
   provider refuses images, the critic falls back to layer 3 and says so.
3. ``structure``: the model reads the program and the measured facts instead
   of the pixels.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import prompts
from .render import image_stats

logger = logging.getLogger(__name__)

CRITIC_MODES = ("auto", "vision", "structure", "off")


@dataclass
class Verdict:
    score: int
    passed: bool
    mode: str
    issues: List[str] = field(default_factory=list)
    fixes: List[str] = field(default_factory=list)
    note: str = ""
    stats: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"score": self.score, "passed": self.passed, "mode": self.mode, "issues": self.issues,
                "fixes": self.fixes, "note": self.note}


def _png_for_critic(path: Path, max_side: int = 1024) -> bytes:
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB")
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        return buf.getvalue()


def automatic_checks(stats: Dict[str, Any]) -> List[str]:
    issues = []
    if stats.get("looks_blank"):
        issues.append("the render is blank or a single flat colour; nothing from the request is visible")
    elif float(stats.get("contrast_std") or 0) < 10:
        issues.append("the render has almost no contrast; the subject does not read against the background")
    adherence = stats.get("palette_adherence")
    if adherence is not None and float(adherence) < 0.35:
        issues.append(f"only {float(adherence):.0%} of pixels are near the style palette; colours are hard-coded "
                      f"instead of taken from PAL")
    return issues


def review(llm, req: "prompts.DrawRequest", *, image: Path, code: str, kind: str, mode: str = "auto",
           min_score: int = 7, frames_note: str = "") -> Verdict:
    """Return a verdict for one render. Never raises for model failures."""
    stats = image_stats(image, req.style.palette)
    hard = automatic_checks(stats)
    copied = prompts.copied_reference(code, req.prompt)
    if copied:
        return Verdict(score=1, passed=False, mode="auto", issues=[copied] + hard,
                       fixes=["start from the REQUEST: list its subjects, then design shapes for them"], stats=stats)
    if stats.get("looks_blank"):
        return Verdict(score=0, passed=False, mode="auto", issues=hard,
                       fixes=["make sure draw(t) returns the full composed document; check coordinates use W and H"],
                       stats=stats)
    if mode == "off":
        return Verdict(score=10, passed=True, mode="off", issues=hard, stats=stats, note="critic disabled")

    note = ""
    parsed: Optional[Dict[str, Any]] = None
    used = mode
    if mode in ("auto", "vision"):
        try:
            parsed = llm.critique(
                instructions=prompts.CRITIC_VISION,
                text=prompts.critic_input_text(req, stats, frames_note=frames_note),
                image_png=_png_for_critic(image), schema=prompts.CRITIC_SCHEMA, purpose="critic.vision",
            )
            used = "vision"
        except Exception as exc:  # a text-only model, or a provider without image input
            note = f"vision critique unavailable ({type(exc).__name__}: {str(exc)[:160]}); used structure critique"
            logger.info("procgen critic: %s", note)
            parsed = None
            used = "structure"
    if parsed is None and (used == "structure" or mode == "structure"):
        try:
            parsed = llm.critique(
                instructions=prompts.CRITIC_STRUCTURE,
                text=prompts.critic_input_text(req, stats, code=code, kind=kind, frames_note=frames_note),
                image_png=None, schema=prompts.CRITIC_SCHEMA, purpose="critic.structure",
            )
            used = "structure"
        except Exception as exc:
            note = (note + "; " if note else "") + f"structure critique failed ({type(exc).__name__}); accepted render"
            logger.warning("procgen critic: %s", note)
            return Verdict(score=min_score, passed=not hard, mode="auto", issues=hard, note=note, stats=stats)
    if not isinstance(parsed, dict):
        return Verdict(score=min_score, passed=not hard, mode=used, issues=hard,
                       note=(note + "; " if note else "") + "critic reply was not valid JSON; accepted render",
                       stats=stats)
    try:
        score = int(parsed.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(10, score))
    issues = [str(i) for i in (parsed.get("issues") or []) if str(i).strip()][:8]
    fixes = [str(f) for f in (parsed.get("fixes") or []) if str(f).strip()][:8]
    issues = hard + [i for i in issues if i not in hard]
    matches = parsed.get("matches_prompt", True) is not False
    passed = score >= min_score and matches and not hard
    return Verdict(score=score, passed=passed, mode=used, issues=issues, fixes=fixes, note=note, stats=stats)
