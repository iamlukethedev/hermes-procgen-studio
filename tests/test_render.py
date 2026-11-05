import hashlib
import json
from pathlib import Path

import pytest

from procgen_studio.render import (Program, ProgramError, RenderError, check_python, extract_program, image_stats,
                                   run_program)
from procgen_studio.style import load_preset

from conftest import technique

PAL = load_preset("ember").palette

CIRCLE = """
def draw(t):
    r = 80 + rng.random() * 120
    return svg_doc(f'<circle cx="{W/2}" cy="{H/2}" r="{r:.1f}" fill="{PAL["gem"]}"/>', bg=PAL["bg"])
"""


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render(code, tmp_path, *, kind="python", seed=1, size=(256, 256), **kw):
    return run_program(Program(kind, code), width=size[0], height=size[1], seed=seed, palette=PAL, style={},
                       out_dir=tmp_path / f"out-{seed}-{abs(hash(code)) % 10_000}", **kw)


# --- extraction --------------------------------------------------------------------------------

def test_extract_prefers_python_block():
    reply = "notes\n```svg\n<svg></svg>\n```\n```python\ndef draw(t):\n    return ''\n```"
    assert extract_program(reply).kind == "python"


def test_extract_svg_and_pixel_and_bare():
    assert extract_program("```svg\n<svg xmlns='http://www.w3.org/2000/svg'/>\n```").kind == "svg"
    assert extract_program('```pixel\n{"frames": [["K"]], "palette": {"K": "ink"}}\n```').kind == "pixel"
    assert extract_program("<svg width='1' height='1'></svg>").kind == "svg"


def test_extract_unterminated_fence_from_truncated_reply():
    prog = extract_program("```python\ndef draw(t):\n    return svg_doc('')\n")
    assert prog.kind == "python" and "def draw" in prog.code


def test_extract_rejects_prose_and_respects_allow_python():
    with pytest.raises(ProgramError):
        extract_program("I would draw a lovely sword.")
    with pytest.raises(ProgramError, match="disabled"):
        extract_program("```python\ndef draw(t):\n    return ''\n```", allow_python=False)


# --- static safety check -----------------------------------------------------------------------

@pytest.mark.parametrize("code", [
    "import os\ndef draw(t): return ''",
    "import subprocess\ndef draw(t): return ''",
    "from pathlib import Path\ndef draw(t): return ''",
    "def draw(t): return open('/etc/passwd').read()",
    "def draw(t): return eval('1')",
    "def draw(t): return ().__class__.__mro__",
    "def draw(t):\n    g = (x for x in [1])\n    return g.gi_frame.f_back",
    "from PIL import Image\ndef draw(t): return Image.os.system('id')",
    "def draw(t): return __import__('os')",
])
def test_check_python_blocks_escapes(code):
    with pytest.raises(ProgramError):
        check_python(code)


def test_check_python_requires_draw():
    with pytest.raises(ProgramError, match="draw"):
        check_python("x = 1")
    check_python("import math\nimport numpy as np\nfrom PIL import Image\ndef draw(t):\n    return ''")


# --- rendering ---------------------------------------------------------------------------------

def test_python_svg_render(tmp_path):
    res = render(CIRCLE, tmp_path)
    assert res.frames[0].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    stats = image_stats(res.frames[0], PAL)
    assert not stats["looks_blank"]
    assert stats["palette_adherence"] > 0.9


def test_seed_reproducibility(tmp_path):
    a = render(CIRCLE, tmp_path / "a", seed=11)
    b = render(CIRCLE, tmp_path / "b", seed=11)
    c = render(CIRCLE, tmp_path / "c", seed=12)
    assert sha(a.frames[0]) == sha(b.frames[0])
    assert sha(a.frames[0]) != sha(c.frames[0])


def test_numpy_and_pil_outputs(tmp_path):
    np_code = "import numpy as np\ndef draw(t):\n    a = np.zeros((H, W, 3), np.uint8)\n    a[:, :W//2] = 200\n    return a"
    pil_code = ("from PIL import Image, ImageDraw\ndef draw(t):\n    im = Image.new('RGB', (W, H), PAL['bg'])\n"
                "    ImageDraw.Draw(im).ellipse([10, 10, W-10, H-10], fill=PAL['gem'])\n    return im")
    for code in (np_code, pil_code):
        res = render(code, tmp_path)
        assert not image_stats(res.frames[0])["looks_blank"]


def test_literal_svg_render(tmp_path):
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" '
           'fill="#123456"/><circle cx="50" cy="50" r="30" fill="#ffcc00"/></svg>')
    res = render(svg, tmp_path, kind="svg", size=(200, 200))
    from PIL import Image
    with Image.open(res.frames[0]) as im:
        assert im.size == (200, 200)


def test_svg_external_resources_are_not_fetched(tmp_path):
    secret = tmp_path / "secret.svg"
    secret.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" '
                      'height="10" fill="#ff0000"/></svg>', encoding="utf-8")
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="64" '
           f'height="64"><rect width="64" height="64" fill="#000000"/><image href="file://{secret}" '
           f'width="64" height="64"/></svg>')
    res = render(svg, tmp_path, kind="svg", size=(64, 64))
    from PIL import Image
    with Image.open(res.frames[0]) as im:
        assert im.convert("RGB").getpixel((32, 32)) == (0, 0, 0)


def test_pixel_spec_render(tmp_path):
    res = render(technique("pixel_sprite.pixel.json"), tmp_path, kind="pixel", size=(512, 512))
    stats = image_stats(res.frames[0], load_preset("pixel-arcade").palette)
    assert not stats["looks_blank"]


def test_pixel_spec_validation(tmp_path):
    with pytest.raises(ProgramError, match="not in the pixel palette"):
        render(json.dumps({"frames": [["KX"]], "palette": {"K": "ink"}}), tmp_path, kind="pixel")
    with pytest.raises(ProgramError):
        render(json.dumps({"frames": [], "palette": {}}), tmp_path, kind="pixel")


def test_animation_frames(tmp_path):
    code = "def draw(t):\n    return svg_doc(f'<circle cx=\"{W*t:.1f}\" cy=\"{H/2}\" r=\"20\" fill=\"#fff\"/>', bg='#000')"
    res = render(code, tmp_path, times=(0.0, 0.5, 1.0))
    assert len(res.frames) == 3
    assert len({sha(f) for f in res.frames}) == 3


# --- runtime sandbox (bypassing the static check on purpose) -----------------------------------

@pytest.fixture
def no_static_check(monkeypatch):
    import procgen_studio.render as r
    monkeypatch.setattr(r, "check_python", lambda code: None)


@pytest.mark.parametrize("code,needle", [
    ("import numpy as np\ndef draw(t):\n    np.save('/tmp/procgen_escape.npy', np.zeros(2))\n    return svg_doc('')",
     "only write inside"),
    ("from PIL import Image\ndef draw(t):\n    return Image.open('/etc/hostname')", "may not read"),
    ("import socket\ndef draw(t): return ''", "not allowed"),
    ("def draw(t):\n    return getattr(draw, '__globals__')", "not available"),
])
def test_runtime_sandbox_blocks(tmp_path, no_static_check, code, needle):
    with pytest.raises(RenderError, match=needle):
        render(code, tmp_path)
    assert not Path("/tmp/procgen_escape.npy").exists()


def test_render_timeout(tmp_path):
    code = "def draw(t):\n    while True:\n        pass"
    with pytest.raises(RenderError) as exc:
        render(code, tmp_path, timeout=3)
    assert exc.value.error_type in ("timeout", "worker_crash")


def test_render_error_reports_line(tmp_path):
    code = "def draw(t):\n    x = 1\n    return undefined_name"
    with pytest.raises(RenderError, match="line 3"):
        render(code, tmp_path)


def test_blank_detection(tmp_path):
    res = render("def draw(t):\n    return svg_doc('', bg=PAL['bg'])", tmp_path)
    assert image_stats(res.frames[0])["looks_blank"]
