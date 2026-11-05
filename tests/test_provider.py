import json
import shutil
from pathlib import Path

import pytest

from procgen_studio.plugin import register
from procgen_studio.provider import PROVIDER_NAME

from conftest import HERMES_REAL, FakeCtx, FakeLlm, fenced, technique

PASS = {"score": 9, "matches_prompt": True, "issues": []}


def registered(llm=None, **settings):
    ctx = FakeCtx(llm or FakeLlm([fenced(technique("brand_mark.py"))], [PASS]), settings)
    register(ctx)
    return ctx


def test_register_wires_every_surface():
    ctx = registered()
    assert [p.name for p in ctx.image_providers] == [PROVIDER_NAME]
    assert [p.name for p in ctx.video_providers] == [PROVIDER_NAME]
    assert "procgen" in ctx.commands and "procgen" in ctx.cli
    assert ctx.aux_tasks["procgen_critic"]["inherit_from"] == "vision"
    assert "procgen_draw" in ctx.aux_tasks
    assert ctx.skills["procgen-art-direction"].is_file()


def test_image_provider_metadata():
    from agent.image_gen_provider import ImageGenProvider
    p = registered().image_providers[0]
    assert isinstance(p, ImageGenProvider)
    assert p.display_name == "Procgen Studio"
    assert p.is_available()
    ids = [m["id"] for m in p.list_models()]
    assert p.default_model() in ids and {"procgen-fast", "procgen-careful"} <= set(ids)
    caps = p.capabilities()
    assert caps["modalities"] == ["text", "image"]
    assert set(caps["creative_controls"]) <= {"creativity", "intensity", "complexity", "movement", "thinking",
                                              "resolution"}
    assert p.get_setup_schema()["env_vars"] == []


def test_generate_follows_the_success_contract():
    p = registered().image_providers[0]
    res = p.generate("a logo for forgeline", "square", model="procgen-studio", ignored_future_kwarg=True)
    assert res["success"] is True
    for key in ("image", "model", "prompt", "aspect_ratio", "modality", "provider"):
        assert key in res
    assert res["provider"] == PROVIDER_NAME and res["modality"] == "text" and res["aspect_ratio"] == "square"
    image = Path(res["image"])
    assert image.is_absolute() and image.is_file() and image.read_bytes()[:4] == b"\x89PNG"
    assert "cache/generated/images" in image.as_posix()
    from PIL import Image
    with Image.open(image) as im:
        assert im.size == (1024, 1024)
    source, meta = Path(res["source"]), Path(res["metadata"])
    assert source.parent == image.parent and source.suffix == ".py" and "def draw" in source.read_text()
    side = json.loads(meta.read_text())
    assert side["id"] == res["render_id"] and side["seed"] == res["seed"]
    json.dumps(res)  # the tool layer JSON-serialises the dict


def test_aspect_ratio_sizes_and_2k():
    p = registered().image_providers[0]
    from PIL import Image
    for aspect, size in (("landscape", (1536, 1024)), ("portrait", (1024, 1536)), ("nonsense", (1536, 1024))):
        res = p.generate("a logo for forgeline", aspect)
        with Image.open(res["image"]) as im:
            assert im.size == size
    res = p.generate("a logo for forgeline", "square", resolution="2K")
    with Image.open(res["image"]) as im:
        assert im.size == (2048, 2048)


def test_error_contract():
    p = registered().image_providers[0]
    res = p.generate("   ", "square")
    assert res["success"] is False and res["image"] is None and res["error_type"] == "invalid_input"
    bad = registered(FakeLlm([fenced("def draw(t):\n    return nope")], [PASS]), max_redraws=1).image_providers[0]
    res = bad.generate("a logo", "square")
    assert res["success"] is False and res["error_type"] == "render_failed" and "nope" in res["error"]
    style_err = registered().image_providers[0].generate("a logo for forgeline [style=does-not-exist]", "square")
    assert style_err["success"] is False and style_err["error_type"] == "invalid_style"


def test_llm_exception_becomes_provider_error():
    class Boom(FakeLlm):
        def complete(self, messages, **kw):
            raise RuntimeError("no credentials configured")
    res = registered(Boom([""])).image_providers[0].generate("a logo", "square")
    assert res["success"] is False and res["error_type"] == "provider_error" and "credentials" in res["error"]


def test_image_url_of_a_render_edits_its_source():
    llm = FakeLlm([fenced(technique("brand_mark.py")), fenced(technique("brand_mark.py").replace("forgeline", "emberline"))],
                  [PASS])
    p = registered(llm).image_providers[0]
    first = p.generate("a logo for forgeline", "square")
    second = p.generate("rename the brand to emberline", "square", image_url=first["image"])
    assert second["success"] and second["modality"] == "image"
    edit_user = llm.complete_calls[1]["messages"][1]["content"]
    assert "EXISTING PROGRAM" in edit_user and "forgeline" in edit_user and "emberline" in edit_user
    assert "emberline" in Path(second["source"]).read_text()


def test_foreign_image_url_is_sent_as_a_reference(tmp_path):
    from PIL import Image
    ref = tmp_path / "photo.png"
    Image.new("RGB", (32, 32), (200, 10, 10)).save(ref)
    llm = FakeLlm([fenced(technique("brand_mark.py"))], [PASS])
    res = registered(llm).image_providers[0].generate("redraw this as a logo", "square", image_url=str(ref))
    assert res["success"]
    content = llm.complete_calls[0]["messages"][1]["content"]
    assert isinstance(content, list) and content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_output_dir_receives_a_project_copy(tmp_path):
    out_dir = tmp_path / "assets"
    p = registered(output_dir=str(out_dir)).image_providers[0]
    res = p.generate("a logo for forgeline", "square")
    pngs = list(out_dir.glob("*.png"))
    assert len(pngs) == 1 and pngs[0].with_suffix(".py").exists()
    assert res["render_id"] in pngs[0].name


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_video_provider_contract():
    llm = FakeLlm([fenced(technique("logo_motion.py"))], [PASS])
    ctx = registered(llm, fps=8, gif=True)
    v = ctx.video_providers[0]
    assert v.is_available()
    caps = v.capabilities()
    assert "16:9" in caps["aspect_ratios"] and caps["max_duration"] >= 1
    res = v.generate("animated logo reveal for forgeline", duration=1, aspect_ratio="1:1", resolution="480p",
                     model="procgen-motion")
    assert res["success"] is True, res
    video = Path(res["video"])
    assert video.suffix == ".mp4" and video.stat().st_size > 1000 and "cache/generated/videos" in video.as_posix()
    assert res["duration"] == 1 and res["frames"] == 8
    assert res["gif"] and Path(res["gif"]).is_file()
    crit = llm.structured_calls[0]
    assert crit["has_image"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_video_can_animate_an_existing_render():
    llm = FakeLlm([fenced(technique("brand_mark.py")), fenced(technique("logo_motion.py"))], [PASS])
    ctx = registered(llm, fps=6)
    still = ctx.image_providers[0].generate("a logo for forgeline", "square")
    res = ctx.video_providers[0].generate("animate a reveal", image_url=still["image"], duration=1,
                                          aspect_ratio="1:1", resolution="480p")
    assert res["success"] and res["modality"] == "image"
    assert "EXISTING PROGRAM" in llm.complete_calls[1]["messages"][1]["content"]


def test_hermes_abcs_in_use():
    """Report which ABC flavour ran (real Hermes checkout or the stand-in)."""
    import agent.image_gen_provider as m
    assert hasattr(m, "ImageGenProvider")
    print("real Hermes ABCs" if HERMES_REAL else "stand-in ABCs")
