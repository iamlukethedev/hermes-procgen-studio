from pathlib import Path

import pytest

from procgen_studio.style import (PALETTE_ROLES, StyleError, find_project_style, list_presets, load_preset,
                                  load_style_file, parse_style_text, resolve_style)

SAMPLE = """# STYLE.md - shared art contract
palette: ember
outline: 10px ink #1a1020, round joins
lighting: top-left key light, soft rim on lower-right
shading: two-tone cel + one gradient, one specular highlight per object
background: rounded tile, radial vignette
"""


def test_builtin_presets_are_complete():
    names = list_presets()
    for expected in ("ember", "frost", "flat-illustration", "dark-fantasy-lowpoly", "brand-minimal", "pixel-arcade"):
        assert expected in names
    for name in names:
        style = load_preset(name)
        assert set(style.palette) == set(PALETTE_ROLES), name
        assert all(v.startswith("#") and len(v) == 7 for v in style.palette.values()), name
        assert style.description, name


def test_frost_inherits_ember_rules_with_its_own_palette():
    ember, frost = load_preset("ember"), load_preset("frost")
    assert frost.rules["outline"] == ember.rules["outline"]
    assert frost.rules["lighting"] != ember.rules["lighting"]
    assert frost.palette["gem"] != ember.palette["gem"]
    assert frost.palette_name == "frost"


def test_one_line_palette_change_restyles(tmp_path):
    f = tmp_path / "STYLE.md"
    f.write_text(SAMPLE, encoding="utf-8")
    a = load_style_file(f)
    f.write_text(SAMPLE.replace("palette: ember", "palette: frost"), encoding="utf-8")
    b = load_style_file(f)
    assert a.palette == load_preset("ember").palette
    assert b.palette == load_preset("frost").palette
    assert a.rules == b.rules
    assert a.fingerprint() != b.fingerprint()


def test_hex_in_values_is_not_treated_as_comment():
    parsed = parse_style_text(SAMPLE)
    assert parsed.rules["outline"] == "10px ink #1a1020, round joins"


def test_palette_role_override_and_validation(tmp_path):
    f = tmp_path / "STYLE.md"
    f.write_text("preset: ember\npalette.gem: #0f0\n", encoding="utf-8")
    style = load_style_file(f)
    assert style.palette["gem"] == "#00ff00"
    assert style.palette["bg"] == load_preset("ember").palette["bg"]
    with pytest.raises(StyleError):
        parse_style_text("palette.sparkle: #ffffff")
    with pytest.raises(StyleError):
        parse_style_text("palette.gem: red")


def test_unknown_preset_is_a_clear_error():
    with pytest.raises(StyleError, match="unknown preset"):
        load_preset("vaporwave-9000")
    with pytest.raises(StyleError):
        load_preset("../etc/passwd")


def test_project_style_requires_a_procgen_marker(tmp_path):
    (tmp_path / "STYLE.md").write_text("# Writing style\nUse the active voice.\n", encoding="utf-8")
    assert find_project_style(str(tmp_path)) is None
    (tmp_path / "STYLE.md").write_text(SAMPLE, encoding="utf-8")
    assert find_project_style(str(tmp_path)) == tmp_path / "STYLE.md"


def test_resolution_precedence(tmp_path):
    user = tmp_path / "user_STYLE.md"
    user.write_text("preset: brand-minimal\n", encoding="utf-8")
    assert resolve_style(cwd=str(tmp_path), user_style=user).name == "brand-minimal"
    (tmp_path / "STYLE.md").write_text(SAMPLE, encoding="utf-8")
    assert resolve_style(cwd=str(tmp_path), user_style=user).palette_name == "ember"
    assert resolve_style(override="frost", cwd=str(tmp_path), user_style=user).palette_name == "frost"
    other = tmp_path / "other.md"
    other.write_text("preset: pixel-arcade\n", encoding="utf-8")
    assert resolve_style(style_file=str(other), cwd=str(tmp_path)).name == "pixel-arcade"
    empty = tmp_path / "empty"
    empty.mkdir()
    assert resolve_style(cwd=str(empty), active_preset="frost").palette_name == "frost"
    assert resolve_style(cwd=str(empty)).name == "flat-illustration"


def test_style_prompt_lists_every_role():
    text = load_preset("ember").as_prompt()
    for role in PALETTE_ROLES:
        assert f"PAL['{role}']" in text
