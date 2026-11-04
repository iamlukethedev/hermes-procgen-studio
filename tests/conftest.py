import os
import sys
import types
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Provider tests subclass Hermes's ABCs. Use a real Hermes checkout when one is importable
# (HERMES_AGENT_DIR, or the default install location), otherwise a minimal stand-in that
# mirrors agent/image_gen_provider.py and agent/video_gen_provider.py.
_hermes_dir = Path(os.environ.get("HERMES_AGENT_DIR", Path.home() / ".hermes" / "hermes-agent"))
if _hermes_dir.is_dir() and str(_hermes_dir) not in sys.path:
    sys.path.append(str(_hermes_dir))

try:
    import agent.image_gen_provider  # noqa: F401
    import agent.video_gen_provider  # noqa: F401
    HERMES_REAL = True
except Exception:
    HERMES_REAL = False
    for name in [m for m in list(sys.modules) if m == "agent" or m.startswith("agent.")]:
        del sys.modules[name]
    agent_pkg = types.ModuleType("agent")
    agent_pkg.__path__ = []

    class _Base:
        def is_available(self):
            return True

        def list_models(self):
            return []

        def default_model(self):
            models = self.list_models()
            return models[0]["id"] if models else None

        @property
        def display_name(self):
            return self.name.title()

    img = types.ModuleType("agent.image_gen_provider")
    img.DEFAULT_ASPECT_RATIO = "landscape"
    img.VALID_ASPECT_RATIOS = ("landscape", "square", "portrait")

    class ImageGenProvider(_Base):
        def capabilities(self):
            return {"modalities": ["text"], "max_reference_images": 0}

    def resolve_aspect_ratio(value):
        v = value.strip().lower() if isinstance(value, str) else ""
        return v if v in img.VALID_ASPECT_RATIOS else "landscape"

    def normalize_reference_images(value):
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return None
        return [i.strip() for i in value if isinstance(i, str) and i.strip()] or None

    def success_response(*, image, model, prompt, aspect_ratio, provider, modality="text", extra=None):
        payload = {"success": True, "image": image, "model": model, "prompt": prompt,
                   "aspect_ratio": aspect_ratio, "modality": modality, "provider": provider}
        for k, v in (extra or {}).items():
            payload.setdefault(k, v)
        return payload

    def error_response(*, error, error_type="provider_error", provider="", model="", prompt="",
                       aspect_ratio="landscape"):
        return {"success": False, "image": None, "error": error, "error_type": error_type, "model": model,
                "prompt": prompt, "aspect_ratio": aspect_ratio, "provider": provider}

    img.ImageGenProvider, img.resolve_aspect_ratio = ImageGenProvider, resolve_aspect_ratio
    img.normalize_reference_images, img.success_response, img.error_response = (
        normalize_reference_images, success_response, error_response)

    vid = types.ModuleType("agent.video_gen_provider")

    class VideoGenProvider(_Base):
        def capabilities(self):
            return {}

    def v_success(*, video, model, prompt, modality="text", aspect_ratio="", duration=0, provider, extra=None):
        payload = {"success": True, "video": video, "model": model, "prompt": prompt, "modality": modality,
                   "aspect_ratio": aspect_ratio, "duration": int(duration) if duration else 0, "provider": provider}
        for k, v in (extra or {}).items():
            payload.setdefault(k, v)
        return payload

    def v_error(*, error, error_type="provider_error", provider="", model="", prompt="", aspect_ratio=""):
        return {"success": False, "video": None, "error": error, "error_type": error_type, "model": model,
                "prompt": prompt, "aspect_ratio": aspect_ratio, "provider": provider}

    vid.VideoGenProvider, vid.success_response, vid.error_response = VideoGenProvider, v_success, v_error
    sys.modules.update({"agent": agent_pkg, "agent.image_gen_provider": img, "agent.video_gen_provider": vid})

TECHNIQUES = ROOT / "procgen_studio" / "techniques"


def technique(name: str) -> str:
    return (TECHNIQUES / name).read_text(encoding="utf-8")


def fenced(code: str, kind: str = "python") -> str:
    return f"Here you go.\n```{kind}\n{code}\n```\n"


class _Usage:
    total_tokens = 100


class _Result:
    def __init__(self, text: str, parsed: Any = None):
        self.text = text
        self.parsed = parsed
        self.provider = "fake"
        self.model = "fake-model"
        self.usage = _Usage()
        self.content_type = "json" if parsed is not None else "text"


class FakeLlm:
    """Stands in for ctx.llm: scripted drawing replies and scripted critic verdicts."""

    def __init__(self, drawings: List[str], critiques: Optional[List[Any]] = None, *, vision_error: bool = False):
        self.drawings = list(drawings)
        self.critiques = list(critiques or [])
        self.vision_error = vision_error
        self.complete_calls: List[Dict[str, Any]] = []
        self.structured_calls: List[Dict[str, Any]] = []

    def complete(self, messages, **kw):
        self.complete_calls.append({"messages": messages, **kw})
        if not self.drawings:
            raise AssertionError("FakeLlm ran out of scripted drawings (unbounded loop?)")
        text = self.drawings.pop(0) if len(self.drawings) > 1 else self.drawings[0]
        return _Result(text)

    def complete_structured(self, *, instructions, input, **kw):
        has_image = any(isinstance(b, dict) and b.get("type") == "image" for b in input)
        self.structured_calls.append({"instructions": instructions, "has_image": has_image, **kw})
        if has_image and self.vision_error:
            raise RuntimeError("model does not support image input")
        verdict = self.critiques.pop(0) if len(self.critiques) > 1 else (self.critiques[0] if self.critiques else
                                                                          {"score": 9, "matches_prompt": True,
                                                                           "issues": []})
        if isinstance(verdict, Exception):
            raise verdict
        return _Result("{}", parsed=verdict)


class FakeCtx:
    """The subset of PluginContext that Procgen Studio uses."""

    def __init__(self, llm: Optional[FakeLlm] = None, settings: Optional[Dict[str, Any]] = None):
        self.llm = llm or FakeLlm([fenced(technique("brand_mark.py"))])
        self.settings = dict(settings or {})
        self.image_providers: list = []
        self.video_providers: list = []
        self.commands: Dict[str, Any] = {}
        self.cli: Dict[str, Any] = {}
        self.aux_tasks: Dict[str, Any] = {}
        self.skills: Dict[str, Path] = {}
        self._state: Dict[str, Any] = {}
        ctx = self

        class _State:
            def get(self, key, default=None):
                return ctx._state.get(key, default)

            def set(self, key, value):
                ctx._state[key] = value

        self.state = _State()

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_image_gen_provider(self, provider):
        self.image_providers.append(provider)

    def register_video_gen_provider(self, provider):
        self.video_providers.append(provider)

    def register_command(self, name, handler, description="", args_hint="", argument_mode=None):
        self.commands[name] = handler

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
        self.cli[name] = (setup_fn, handler_fn)

    def register_auxiliary_task(self, key, *, display_name, description, defaults=None, inherit_from=None):
        self.aux_tasks[key] = {"display_name": display_name, "inherit_from": inherit_from, "defaults": defaults}

    def register_skill(self, name, path, description="", frontmatter=None):
        self.skills[name] = Path(path)


@pytest.fixture(autouse=True)
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("TERMINAL_CWD", str(work))
    monkeypatch.chdir(work)
    return home


@pytest.fixture
def make_engine():
    from procgen_studio.plugin import build_engine

    def factory(llm: FakeLlm, **settings):
        ctx = FakeCtx(llm, settings)
        return build_engine(ctx), ctx

    return factory
