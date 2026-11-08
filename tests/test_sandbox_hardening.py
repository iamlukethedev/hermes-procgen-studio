"""Regression tests for the catalog sandbox review (NousResearch/hermes-agent#150293)."""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import procgen_studio.render as render_mod
import procgen_studio.sandbox_runner as runner_mod
from procgen_studio.engine import ReferenceImageError, reference_data_url
from procgen_studio.render import Program, ProgramError, RenderError, check_python, run_program

from conftest import FakeLlm, fenced, technique

PASS = {"score": 9, "matches_prompt": True, "issues": []}


def render(code, tmp_path, *, size=(64, 64)):
    return run_program(Program("python", code), width=size[0], height=size[1], seed=1, palette={}, style={},
                       out_dir=tmp_path / f"out-{abs(hash(code)) % 100_000}")


@pytest.fixture
def no_static_check(monkeypatch):
    monkeypatch.setattr(render_mod, "check_python", lambda code: None)


def test_static_and_runtime_share_forbidden_attrs():
    assert render_mod.FORBIDDEN_ATTRS is runner_mod.FORBIDDEN_ATTRS
    assert {"os", "sys", "subprocess", "builtins"} <= runner_mod.FORBIDDEN_ATTRS


# --- 1. getattr/hasattr/setattr and import names refuse FORBIDDEN_ATTRS ------------------------

@pytest.mark.parametrize("code", [
    "from PIL import Image\ndef draw(t):\n    return getattr(Image, 'os')",
    "import numpy as np\ndef draw(t):\n    return hasattr(np, 'sys')",
    "from PIL import Image\ndef draw(t):\n    setattr(Image, 'subprocess', 1)\n    return ''",
    "from PIL.Image import os\ndef draw(t):\n    return ''",
    "from PIL.Image import sys as s\ndef draw(t):\n    return ''",
    "import operator\nfrom PIL import Image\ndef draw(t):\n    return operator.attrgetter('os')(Image)",
    "from operator import attrgetter\ndef draw(t):\n    return ''",
    "from PIL import Image\ndef draw(t):\n    match Image:\n        case object(os=o):\n            return o\n    return ''",
])
def test_static_check_refuses_forbidden_names_by_string(code):
    with pytest.raises(ProgramError, match="not available"):
        check_python(code)


@pytest.mark.parametrize("code", [
    "from PIL import Image\ndef draw(t):\n    o = getattr(Image, 'os')\n    return svg_doc(str(o.getpid()))",
    "from PIL import Image\ndef draw(t):\n    return getattr(Image, 'o' + 's')",
    "import numpy as np\ndef draw(t):\n    return svg_doc(str(hasattr(np, ''.join(['s', 'y', 's']))))",
    "from PIL import Image\ndef draw(t):\n    setattr(Image, 'o' + 's', None)\n    return svg_doc('')",
    "def draw(t):\n    return getattr(draw, '__glo' + 'bals__')",
])
def test_runtime_getattr_refuses_forbidden_names(tmp_path, no_static_check, code):
    with pytest.raises(RenderError, match="not available"):
        render(code, tmp_path)


def test_runtime_getattr_refuses_str_subclass_tricks(tmp_path, no_static_check):
    code = ("class S(str):\n    def startswith(self, *a):\n        return False\n"
            "from PIL import Image\ndef draw(t):\n    return getattr(Image, S('os'))")
    with pytest.raises(RenderError, match="not available"):
        render(code, tmp_path)


@pytest.mark.parametrize("code,needle", [
    ("from PIL.Image import os\ndef draw(t):\n    return svg_doc(str(os.getpid()))", "not available"),
    ("from PIL.Image import *\ndef draw(t):\n    return svg_doc(str(os.getpid()))", "would expose"),
])
def test_runtime_import_refuses_forbidden_names(tmp_path, no_static_check, code, needle):
    with pytest.raises(RenderError, match=needle):
        render(code, tmp_path)


def test_ordinary_getattr_and_star_imports_still_work(tmp_path):
    code = ("from math import *\nfrom PIL import Image\n"
            "def draw(t):\n    mode = getattr(Image, 'NEAREST', 0)\n    ok = hasattr(Image, 'new')\n"
            "    return svg_doc(f'<circle cx=\"{W/2}\" cy=\"{H/2}\" r=\"{20 + sin(pi / 2) * 4 + mode}\" '\n"
            "                   f'fill=\"{\"#ff0000\" if ok else \"#000\"}\"/>', bg='#000000')")
    check_python(code)
    assert render(code, tmp_path).frames[0].is_file()


# --- 2. dunder names in class bodies and elsewhere ---------------------------------------------

ARRAY_INTERFACE = ("import numpy as np\nclass R:\n    __array_interface__ = {'data': (4096, True), 'shape': (64,), "
                   "'typestr': '|u1', 'version': 3}\ndef draw(t):\n    return np.asarray(R())")


@pytest.mark.parametrize("code", [
    ARRAY_INTERFACE,
    "class P:\n    def __getattr__(self, name):\n        return 1\ndef draw(t):\n    return ''",
    "class P:\n    def __getattribute__(self, name):\n        return 1\ndef draw(t):\n    return ''",
    "class B:\n    def __buffer__(self, flags):\n        return None\ndef draw(t):\n    return ''",
    "class F:\n    def __fspath__(self):\n        return '/etc/passwd'\ndef draw(t):\n    return ''",
    "class Q:\n    __array_struct__ = 0\ndef draw(t):\n    return ''",
    "class Q:\n    def __class_getitem__(cls, k):\n        return k\ndef draw(t):\n    return ''",
    "class Q:\n    __slots__ = ()\ndef draw(t):\n    return ''",
    "class Q:\n    pass\nQ.__init__ = None\ndef draw(t):\n    return ''",
    "def draw(t):\n    return __build_class__",
    "def draw(__array__):\n    return ''",
    "def draw(t):\n    return dict(__array_interface__=1)",
    "__array_priority__ = 1\ndef draw(t):\n    return ''",
])
def test_static_check_refuses_dunder_identifiers(code):
    with pytest.raises(ProgramError, match="not available"):
        check_python(code)


@pytest.mark.parametrize("code", [
    ARRAY_INTERFACE,
    "class P:\n    def __getattr__(self, name):\n        return 1\ndef draw(t):\n    return svg_doc('')",
    ("class F:\n    def __fspath__(self):\n        return '/etc/hostname'\nfrom PIL import Image\n"
     "def draw(t):\n    return Image.open(F())"),
])
def test_runtime_refuses_classes_with_protocol_dunders(tmp_path, no_static_check, code):
    with pytest.raises(RenderError, match="may not define"):
        render(code, tmp_path)


def test_dataclass_with_post_init_passes_the_static_check():
    check_python("import dataclasses\n@dataclasses.dataclass\nclass Dot:\n    r: float = 4.0\n"
                 "    def __post_init__(self):\n        self.r = max(1.0, self.r)\ndef draw(t):\n    return ''")


def test_classes_with_init_enum_and_namedtuple_still_work(tmp_path):
    code = '''
import enum
from typing import NamedTuple

class Kind(enum.Enum):
    DOT = 1

class Point(NamedTuple):
    x: float
    y: float

class Dot:
    def __init__(self, p, r=4.0):
        self.p = p
        self.r = max(1.0, r)

class Scene:
    def __init__(self, dots):
        self.dots = dots
    def svg(self):
        return "".join(f'<circle cx="{d.p.x}" cy="{d.p.y}" r="{d.r}" fill="#ffffff"/>' for d in self.dots)

def draw(t):
    if __name__ == "drawing" and Kind.DOT.value == 1:
        return svg_doc(Scene([Dot(Point(W / 2, H / 2), 12)]).svg(), bg="#000000")
    return ""
'''
    check_python(code)
    assert render(code, tmp_path).frames[0].is_file()


# --- 3. model-supplied image_url reads ---------------------------------------------------------

def _png(path: Path) -> Path:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (10, 200, 10)).save(path, "PNG")
    return path


def test_reference_from_working_dir_cache_and_data_dir(tmp_path, hermes_home):
    from procgen_studio import store
    for p in (tmp_path / "work" / "ref.png", hermes_home / "cache" / "images" / "ref.png",
              store.data_dir() / "refs" / "ref.png"):
        url = reference_data_url(str(_png(p)))
        assert url.startswith("data:image/png;base64,")
        assert base64.b64decode(url.split(",", 1)[1]) == p.read_bytes()


def test_reference_mime_comes_from_decoded_format(tmp_path):
    from PIL import Image
    jpeg = tmp_path / "work" / "looks-like.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(jpeg, "JPEG")
    assert reference_data_url(str(jpeg)).startswith("data:image/jpeg;base64,")
    renamed = _png(tmp_path / "work" / "notes.txt")
    assert reference_data_url(str(renamed)).startswith("data:image/png;base64,")


@pytest.mark.parametrize("name,content", [
    (".env", b"OPENAI_API_KEY=sk-secret\n"),
    ("id_ed25519.png", b"-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n"),
    ("fake.png", b"\x89PNG\r\n\x1a\nnot really a png"),
])
def test_reference_refuses_files_that_do_not_decode(tmp_path, name, content):
    p = tmp_path / "work" / name
    p.write_bytes(content)
    with pytest.raises(ReferenceImageError, match="not a decodable image"):
        reference_data_url(str(p))


def test_reference_refuses_paths_outside_allowed_roots(tmp_path, hermes_home):
    outside = _png(tmp_path / "elsewhere" / "ref.png")
    env_file = hermes_home / ".env"
    env_file.write_text("OPENAI_API_KEY=sk-secret\n")
    link = tmp_path / "work" / "link.png"
    link.symlink_to(outside)
    for ref in (outside, env_file, link, tmp_path / "work" / ".." / "elsewhere" / "ref.png"):
        with pytest.raises(ReferenceImageError, match="outside"):
            reference_data_url(str(ref))


def test_reference_refuses_other_formats_and_oversize(tmp_path, monkeypatch):
    from PIL import Image
    import procgen_studio.engine as engine_mod
    bmp = tmp_path / "work" / "ref.bmp"
    Image.new("RGB", (8, 8)).save(bmp, "BMP")
    with pytest.raises(ReferenceImageError, match="BMP"):
        reference_data_url(str(bmp))
    big = _png(tmp_path / "work" / "big.png")
    monkeypatch.setattr(engine_mod, "MAX_REFERENCE_BYTES", 10)
    with pytest.raises(ReferenceImageError, match="larger"):
        reference_data_url(str(big))


def test_refused_reference_is_not_sent_to_the_model(hermes_home):
    from procgen_studio.plugin import register
    from conftest import FakeCtx
    secret = hermes_home / ".env"
    secret.write_text("OPENAI_API_KEY=sk-very-secret\n")
    llm = FakeLlm([fenced(technique("brand_mark.py"))], [PASS])
    ctx = FakeCtx(llm)
    register(ctx)
    res = ctx.image_providers[0].generate("redraw this as a logo", "square", image_url=str(secret))
    assert res["success"]
    content = llm.complete_calls[0]["messages"][1]["content"]
    assert isinstance(content, str) and "could not be read" in content
    blob = json.dumps([c["messages"] for c in llm.complete_calls])
    assert "sk-very-secret" not in blob
    assert base64.b64encode(secret.read_bytes()).decode() not in blob


# --- 4. os.mkdir / os.makedirs / os.utime only inside the job dir ------------------------------

def test_audit_hook_confines_mkdir_makedirs_utime(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    script = f"""
import json, os, sys
sys.path.insert(0, {str(Path(runner_mod.__file__).parent)!r})
import sandbox_runner
sandbox_runner._install_audit_hook({str(job)!r}, [])
results = {{}}
def attempt(name, fn):
    try:
        fn()
        results[name] = "ok"
    except PermissionError as exc:
        results[name] = "refused"
attempt("mkdir_out", lambda: os.mkdir({str(outside / "a")!r}))
attempt("makedirs_out", lambda: os.makedirs({str(outside / "b" / "c")!r}))
attempt("utime_out", lambda: os.utime({str(outside)!r}))
attempt("dotdot_out", lambda: os.mkdir({str(job / ".." / "outside" / "d")!r}))
attempt("mkdir_in", lambda: os.mkdir({str(job / "a")!r}))
attempt("makedirs_in", lambda: os.makedirs({str(job / "b" / "c")!r}))
attempt("utime_in", lambda: os.utime({str(job / "a")!r}))
print(json.dumps(results))
"""
    out = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=60)
    results = json.loads(out.stdout.strip().splitlines()[-1])
    assert results == {"mkdir_out": "refused", "makedirs_out": "refused", "utime_out": "refused",
                       "dotdot_out": "refused", "mkdir_in": "ok", "makedirs_in": "ok", "utime_in": "ok"}
    assert os.listdir(outside) == []
    assert (job / "b" / "c").is_dir()
