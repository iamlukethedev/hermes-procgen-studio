"""Backend routes for the Procgen Studio Desktop pane (mounted at /api/plugins/procgen-studio/).

Read-only views over the render store plus one action, a local re-roll, which re-runs a
stored drawing program with a new seed in the sandboxed renderer. No route calls a model.
"""

from __future__ import annotations

import base64
import io
import random
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from procgen_studio import store  # noqa: E402
from procgen_studio.engine import Engine, JsonState, Settings  # noqa: E402

router = APIRouter()

_MAX_SOURCE_CHARS = 200_000


def _settings() -> Settings:
    try:
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly() or {}
    except Exception:
        cfg = {}
    entry = (((cfg.get("plugins") or {}).get("entries") or {}).get("procgen-studio") or {})
    values = entry.get("settings") or {}
    return Settings.load(lambda key, default: values.get(key, default))


def _no_model(_settings: Settings):
    raise RuntimeError("the Desktop pane never calls a model; use /procgen regen in a session")


def _engine() -> Engine:
    return Engine(llm_factory=_no_model, settings=_settings, state=JsonState())


def _data_url(path: Optional[str], max_side: int) -> Optional[str]:
    if not path or not Path(path).is_file():
        return None
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG" if max_side <= 320 else "PNG", quality=88)
    mime = "image/jpeg" if max_side <= 320 else "image/png"
    return f"data:{mime};base64," + base64.b64encode(buf.getvalue()).decode()


def _still(rec: store.Record) -> Optional[str]:
    if rec.media == "video":
        return rec.extra.get("poster")
    return rec.output


def _summary(rec: store.Record, *, thumb: bool = True) -> Dict[str, Any]:
    score = next((h.get("score") for h in reversed(rec.critic) if h.get("ok")), None)
    return {
        "id": rec.id, "prompt": rec.prompt, "seed": rec.seed, "size": [rec.width, rec.height],
        "style": rec.style.get("name"), "palette": rec.style.get("palette"), "kind": rec.kind,
        "media": rec.media, "set": rec.set, "parent": rec.parent, "operation": rec.operation,
        "created": rec.created, "score": score, "attempts": len(rec.critic),
        "thumb": _data_url(_still(rec), 256) if thumb else None,
    }


def _record(render_id: str) -> store.Record:
    try:
        return store.load_record(render_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/renders")
def list_renders(limit: int = 24, set: str = "") -> Dict[str, Any]:  # noqa: A002 - query name
    recs = store.latest(max(1, min(96, limit)), set_name=set)
    return {"renders": [_summary(r) for r in recs]}


@router.get("/renders/{render_id}")
def get_render(render_id: str) -> Dict[str, Any]:
    rec = _record(render_id)
    source = ""
    if rec.source and Path(rec.source).is_file():
        source = Path(rec.source).read_text(encoding="utf-8", errors="replace")[:_MAX_SOURCE_CHARS]
    return {
        **_summary(rec, thumb=False),
        "image": _data_url(_still(rec), 1024),
        "source": source,
        "paths": {"output": rec.published or rec.output, "source": rec.source, "record": str(rec.dir / "meta.json"),
                  "gif": rec.extra.get("gif")},
        "critic": rec.critic,
        "llm": {k: v for k, v in (rec.llm or {}).items() if k != "log"},
    }


@router.post("/renders/{render_id}/reroll")
def reroll(render_id: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base = _record(render_id)
    raw = (body or {}).get("seed")
    try:
        seed = int(raw) if raw is not None and str(raw).strip() != "" else random.randint(1, 999_999)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="seed must be an integer") from exc
    try:
        rec = _engine().rerender(base, seed=seed, operation="reroll")
    except Exception as exc:  # render errors are shown in the pane, not raised as 500s
        raise HTTPException(status_code=422, detail=f"{type(exc).__name__}: {exc}") from exc
    return _summary(rec)


@router.get("/status")
def status() -> Dict[str, Any]:
    engine = _engine()
    try:
        style = engine.current_style()
        style_info: Dict[str, Any] = style.summary()
    except Exception as exc:
        style_info = {"error": str(exc)}
    return {"style": style_info, "renders": len(store.latest(1000)), "data_dir": str(store.data_dir())}
