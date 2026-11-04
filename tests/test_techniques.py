"""Every technique reference must render under every preset: they are few-shot examples and fixtures."""

from pathlib import Path

import pytest

from procgen_studio.prompts import DrawRequest, pick_techniques, system_prompt, user_prompt
from procgen_studio.render import Program, image_stats, run_program
from procgen_studio.style import list_presets, load_preset

from conftest import TECHNIQUES

FILES = sorted(p.name for p in TECHNIQUES.iterdir() if p.suffix in (".py", ".json"))


@pytest.mark.parametrize("name", FILES)
@pytest.mark.parametrize("preset", list_presets())
def test_technique_renders_with_preset_palette(tmp_path, name, preset):
    style = load_preset(preset)
    kind = "pixel" if name.endswith(".json") else "python"
    res = run_program(Program(kind, (TECHNIQUES / name).read_text()), width=320, height=240, seed=3,
                      palette=style.palette, style=style.rules, out_dir=tmp_path,
                      times=(1.0,) if "motion" in name else (0.0,))
    stats = image_stats(res.frames[0], style.palette)
    assert not stats["looks_blank"]
    assert stats["palette_adherence"] > 0.5, "techniques must take their colours from PAL"


@pytest.mark.parametrize("prompt,expected", [
    ("a set of 4 game icons: sword, potion, shield, key", "icon_tiles.py"),
    ("a logo and wordmark for a dev tools startup", "brand_mark.py"),
    ("a moonlit forest scene with a cabin", "layered_scene.py"),
    ("low-poly dark fantasy monster", "lowpoly_creature.py"),
    ("seamless topographic pattern", "topo_pattern.py"),
    ("pixel art knight sprite sheet", "pixel_sprite.pixel.json"),
])
def test_technique_selection(prompt, expected):
    assert pick_techniques(prompt)[0] == expected


def test_prompts_carry_the_contract():
    style = load_preset("ember")
    req = DrawRequest(prompt="a sword icon", width=1024, height=1024, seed=7, style=style)
    sys_text = system_prompt(req)
    assert "NO blur" in sys_text and "draw(t)" in sys_text and "icon_tiles.py" in sys_text
    user = user_prompt(req)
    assert "SEED=7" in user and "W=1024" in user and "PAL['gem']" in user
