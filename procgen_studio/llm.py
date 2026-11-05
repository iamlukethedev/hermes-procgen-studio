"""Thin wrapper around the host-owned ``ctx.llm`` facade.

Procgen Studio never holds credentials: every call goes through Hermes, on the
user's active model, or on the ``procgen_draw`` / ``procgen_critic`` auxiliary
slots when the user has pinned a model to them (``hermes model`` -> auxiliary).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DRAW_TASK = "procgen_draw"
CRITIC_TASK = "procgen_critic"


@dataclass
class LlmCall:
    purpose: str
    provider: str = ""
    model: str = ""
    tokens: int = 0
    seconds: float = 0.0
    ok: bool = True
    error: str = ""


@dataclass
class Llm:
    """Adapter so the engine can be tested with a fake (anything with ``complete``)."""

    facade: Any
    draw_task: Optional[str] = None
    critic_task: Optional[str] = None
    max_tokens: int = 16000
    timeout: float = 300.0
    calls: List[LlmCall] = field(default_factory=list)

    def draw(self, system: str, user: str, *, purpose: str, temperature: Optional[float] = None) -> str:
        import time
        started = time.monotonic()
        call = LlmCall(purpose=purpose)
        try:
            result = self.facade.complete(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=temperature, max_tokens=self.max_tokens, timeout=self.timeout,
                purpose=f"procgen-studio.{purpose}", task=self.draw_task,
            )
        except Exception as exc:
            call.ok, call.error = False, f"{type(exc).__name__}: {exc}"[:300]
            call.seconds = round(time.monotonic() - started, 2)
            self.calls.append(call)
            raise
        call.provider, call.model = getattr(result, "provider", ""), getattr(result, "model", "")
        call.tokens = int(getattr(getattr(result, "usage", None), "total_tokens", 0) or 0)
        call.seconds = round(time.monotonic() - started, 2)
        self.calls.append(call)
        return getattr(result, "text", "") or ""

    def critique(self, *, instructions: str, text: str, image_png: Optional[bytes], schema: Dict[str, Any],
                 purpose: str) -> Optional[Dict[str, Any]]:
        import time
        started = time.monotonic()
        call = LlmCall(purpose=purpose)
        blocks: List[Dict[str, Any]] = [{"type": "text", "text": text}]
        if image_png is not None:
            blocks.append({"type": "image", "data": image_png, "mime_type": "image/png"})
        try:
            result = self.facade.complete_structured(
                instructions=instructions, input=blocks, json_schema=schema, schema_name="procgen.critique",
                temperature=0.0, max_tokens=1200, timeout=min(self.timeout, 180.0),
                purpose=f"procgen-studio.{purpose}", task=self.critic_task,
            )
        except Exception as exc:
            call.ok, call.error = False, f"{type(exc).__name__}: {exc}"[:300]
            call.seconds = round(time.monotonic() - started, 2)
            self.calls.append(call)
            raise
        call.provider, call.model = getattr(result, "provider", ""), getattr(result, "model", "")
        call.tokens = int(getattr(getattr(result, "usage", None), "total_tokens", 0) or 0)
        call.seconds = round(time.monotonic() - started, 2)
        self.calls.append(call)
        parsed = getattr(result, "parsed", None)
        if isinstance(parsed, dict):
            return parsed
        return _salvage_json(getattr(result, "text", "") or "")

    def summary(self) -> Dict[str, Any]:
        return {
            "calls": len(self.calls),
            "tokens": sum(c.tokens for c in self.calls),
            "models": sorted({f"{c.provider}/{c.model}".strip("/") for c in self.calls if c.ok and (c.provider or c.model)}),
            "log": [c.__dict__ for c in self.calls],
        }


def _salvage_json(text: str) -> Optional[Dict[str, Any]]:
    import json
    import re
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        value = json.loads(m.group(0))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None
