"""Hermes ``image_generate`` / ``video_generate`` backends."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    normalize_reference_images,
    resolve_aspect_ratio,
    success_response,
)

from . import store
from .engine import Engine, GenerationError, ffmpeg_path
from .render import ProgramError, RenderError
from .style import StyleError

logger = logging.getLogger("procgen_studio")

PROVIDER_NAME = "procgen-studio"

MODELS: List[Dict[str, Any]] = [
    {"id": "procgen-studio", "display": "Procgen Studio (critic + redraws)", "speed": "~30-120s",
     "strengths": "Icons, game assets, logos, flat scenes, patterns; editable source + seed",
     "price": "$0 image cost (uses your chat model tokens)"},
    {"id": "procgen-fast", "display": "Procgen Studio Fast (no critic)", "speed": "~15-60s",
     "strengths": "Quick drafts, one model call when the first render works",
     "price": "$0 image cost (uses your chat model tokens)"},
    {"id": "procgen-careful", "display": "Procgen Studio Careful (3+ redraws)", "speed": "~1-4 min",
     "strengths": "Final assets: stricter critic, more redraw rounds",
     "price": "$0 image cost (uses your chat model tokens)"},
]


def _deps_missing() -> Optional[str]:
    try:
        import cairosvg  # noqa: F401
    except OSError as exc:  # cairocffi could not find the system cairo library
        return f"cairosvg needs the cairo system library ({exc}); install libcairo2 / brew install cairo"
    except ImportError:
        return "cairosvg is not installed (it is a declared dependency of procgen-studio)"
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        return "Pillow is not installed"
    return None


def _summary_extra(outcome) -> Dict[str, Any]:
    rec = outcome.record
    verdict = outcome.verdict
    published = Path(rec.published) if rec.published else Path(rec.output)
    stem = published.name.split(".")[0]
    ext = "".join(Path(rec.source).suffixes)
    return {
        "render_id": rec.id,
        "seed": rec.seed,
        "size": f"{rec.width}x{rec.height}",
        "style": rec.style.get("name"),
        "palette": rec.style.get("palette"),
        "renderer": rec.kind,
        "source": str(published.with_name(stem + ext)),
        "metadata": str(published.with_name(stem + ".procgen.json")),
        "record": str(rec.dir),
        "critic": verdict.as_dict() if verdict else None,
        "attempts": len(outcome.history),
        "llm_calls": rec.llm.get("calls", 0),
        "set": rec.set or None,
        "next_steps": (f"Re-roll locally with no model call: /procgen reroll {rec.id} --seed {rec.seed + 1}. "
                       f"Edit: pass this image path as image_url with an instruction. "
                       f"Source code: {published.with_name(stem + ext)}"),
    }


class ProcgenImageProvider(ImageGenProvider):
    """Images drawn as code by the user's own model and rendered locally."""

    def __init__(self, engine: Engine):
        self.engine = engine

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def display_name(self) -> str:
        return "Procgen Studio"

    def is_available(self) -> bool:
        return _deps_missing() is None

    def list_models(self) -> List[Dict[str, Any]]:
        return [dict(m) for m in MODELS]

    def default_model(self) -> Optional[str]:
        return "procgen-studio"

    def get_setup_schema(self) -> Dict[str, Any]:
        return {"name": "Procgen Studio", "badge": "free",
                "tag": "Your chat model writes SVG/Python drawing code, rendered locally. No image API key.",
                "env_vars": []}

    def capabilities(self) -> Dict[str, Any]:
        return {"modalities": ["text", "image"], "max_reference_images": 1,
                "creative_controls": ["complexity", "thinking", "resolution"]}

    def generate(self, prompt: str, aspect_ratio: str = DEFAULT_ASPECT_RATIO, *, image_url: Optional[str] = None,
                 reference_image_urls: Optional[List[str]] = None, **kwargs: Any) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)
        model = kwargs.get("model") or "procgen-studio"
        if not prompt:
            return error_response(error="Prompt is required", error_type="invalid_input", provider=self.name,
                                  model=model, prompt="", aspect_ratio=aspect)
        missing = _deps_missing()
        if missing:
            return error_response(error=missing, error_type="missing_dependency", provider=self.name, model=model,
                                  prompt=prompt, aspect_ratio=aspect)
        sources = ([image_url] if isinstance(image_url, str) and image_url.strip() else [])
        sources += normalize_reference_images(reference_image_urls) or []
        modality = "image" if sources else "text"
        try:
            base = None
            reference = None
            if sources:
                try:
                    base = store.resolve_ref(sources[0])
                except KeyError:
                    reference = sources[0]
            if base is not None:
                outcome = self.engine.edit(base, prompt, thinking=kwargs.get("thinking"), model=model)
            else:
                outcome = self.engine.generate(prompt, aspect_ratio=aspect, resolution=kwargs.get("resolution"),
                                               complexity=kwargs.get("complexity"), thinking=kwargs.get("thinking"),
                                               model=model, reference_image=reference)
        except GenerationError as exc:
            return error_response(error=str(exc), error_type=exc.error_type, provider=self.name, model=model,
                                  prompt=prompt, aspect_ratio=aspect)
        except (ProgramError, RenderError, StyleError) as exc:
            return error_response(error=str(exc), error_type=type(exc).__name__, provider=self.name, model=model,
                                  prompt=prompt, aspect_ratio=aspect)
        except Exception as exc:  # model/provider errors surface to the agent as a tool error
            logger.warning("procgen-studio image generation failed: %s", exc, exc_info=True)
            return error_response(error=f"{type(exc).__name__}: {exc}", error_type="provider_error",
                                  provider=self.name, model=model, prompt=prompt, aspect_ratio=aspect)
        rec = outcome.record
        return success_response(image=rec.published or rec.output, model=model, prompt=prompt, aspect_ratio=aspect,
                                provider=self.name, modality=modality, extra=_summary_extra(outcome))


def make_video_provider(engine: Engine):
    """Build the video backend lazily so older Hermes builds without video_gen still load the image half."""
    from agent.video_gen_provider import VideoGenProvider
    from agent.video_gen_provider import error_response as video_error
    from agent.video_gen_provider import success_response as video_success

    class ProcgenVideoProvider(VideoGenProvider):
        """Animated drawing programs encoded to MP4 (and GIF) with the system ffmpeg."""

        def __init__(self):
            self.engine = engine

        @property
        def name(self) -> str:
            return PROVIDER_NAME

        @property
        def display_name(self) -> str:
            return "Procgen Studio (motion)"

        def is_available(self) -> bool:
            return _deps_missing() is None and ffmpeg_path() is not None

        def list_models(self) -> List[Dict[str, Any]]:
            return [{"id": "procgen-motion", "display": "Procgen Motion", "speed": "~1-3 min",
                     "strengths": "Animated logos, loaders, loops, motion graphics", "price": "$0 render cost",
                     "modalities": ["text", "image"]}]

        def default_model(self) -> Optional[str]:
            return "procgen-motion"

        def get_setup_schema(self) -> Dict[str, Any]:
            return {"name": "Procgen Studio (motion)", "badge": "free",
                    "tag": "Animated drawing code rendered locally; needs ffmpeg on PATH", "env_vars": []}

        def capabilities(self) -> Dict[str, Any]:
            return {"modalities": ["text", "image"], "aspect_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4"],
                    "resolutions": ["480p", "540p", "720p", "1080p"], "min_duration": 1, "max_duration": 15,
                    "supports_audio": False, "supports_negative_prompt": False, "max_reference_images": 0}

        def generate(self, prompt: str, *, model: Optional[str] = None, image_url: Optional[str] = None,
                     reference_image_urls: Optional[List[str]] = None, duration: Optional[int] = None,
                     aspect_ratio: str = "16:9", resolution: str = "720p", negative_prompt: Optional[str] = None,
                     audio: Optional[bool] = None, seed: Optional[int] = None, **kwargs: Any) -> Dict[str, Any]:
            prompt = (prompt or "").strip()
            model_id = model or "procgen-motion"
            aspect = aspect_ratio if aspect_ratio in ("16:9", "9:16", "1:1", "4:3", "3:4") else "16:9"
            if not prompt:
                return video_error(error="Prompt is required", error_type="invalid_input", provider=self.name,
                                   model=model_id, prompt="", aspect_ratio=aspect)
            missing = _deps_missing() or (None if ffmpeg_path() else "ffmpeg was not found on PATH")
            if missing:
                return video_error(error=missing, error_type="missing_dependency", provider=self.name,
                                   model=model_id, prompt=prompt, aspect_ratio=aspect)
            source_record = None
            if isinstance(image_url, str) and image_url.strip():
                try:
                    source_record = store.resolve_ref(image_url)
                except KeyError:
                    source_record = None
            try:
                outcome = self.engine.generate_video(prompt, aspect_ratio=aspect, resolution=resolution or "720p",
                                                     duration=duration, seed=seed, source_record=source_record)
            except GenerationError as exc:
                return video_error(error=str(exc), error_type=exc.error_type, provider=self.name, model=model_id,
                                   prompt=prompt, aspect_ratio=aspect)
            except Exception as exc:
                logger.warning("procgen-studio video generation failed: %s", exc, exc_info=True)
                return video_error(error=f"{type(exc).__name__}: {exc}", error_type="provider_error",
                                   provider=self.name, model=model_id, prompt=prompt, aspect_ratio=aspect)
            rec = outcome.record
            extra = _summary_extra(outcome)
            published = Path(rec.published)
            extra["gif"] = str(published.with_suffix(".gif")) if rec.extra.get("gif") else None
            extra["fps"], extra["frames"] = rec.fps, rec.frames
            return video_success(video=rec.published or rec.output, model=model_id, prompt=prompt,
                                 modality="image" if source_record else "text", aspect_ratio=aspect,
                                 duration=round(rec.frames / max(1, rec.fps)), provider=self.name, extra=extra)

    return ProcgenVideoProvider()
