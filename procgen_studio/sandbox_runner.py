"""Render worker: runs one drawing program in a separate, locked-down process.

Invoked as ``python -I sandbox_runner.py <job.json>`` by :mod:`procgen_studio.render`.
The job names the program, canvas size, seed, palette and the frame times to
draw. The worker:

1. applies resource limits (address space, CPU seconds, file size),
2. imports the small set of allowed libraries up front,
3. installs an audit hook that refuses network, subprocesses, process control,
   native-library loading, imports outside the allowlist, and any file access
   outside the job directory and the read-only interpreter/font locations,
4. executes the program with a trimmed set of builtins,
5. calls ``draw(t)`` once per frame and writes PNGs into the job directory,
6. prints one JSON line with the result.

This is defence in depth for code the model writes, not a security boundary:
the AST check in :mod:`procgen_studio.render` runs first, and both are
documented in the README.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback

ALLOWED_IMPORTS = {
    "math", "random", "colorsys", "itertools", "functools", "statistics", "cmath",
    "string", "textwrap", "re", "json", "dataclasses", "typing", "collections",
    "heapq", "bisect", "fractions", "decimal", "enum", "copy", "operator",
    "numpy", "PIL", "cairosvg", "noise", "opensimplex", "cairo",
}

# Library code (numpy, PIL, cairosvg) imports whatever stdlib modules it needs at
# run time; the drawing program itself is held to ALLOWED_IMPORTS by the AST check
# and the guarded __import__. These modules are refused for everyone.
_BLOCKED_MODULES = frozenset({
    "subprocess", "_posixsubprocess", "socket", "_socket", "ssl", "_ssl", "multiprocessing",
    "_multiprocessing", "pty", "webbrowser", "ftplib", "smtplib", "poplib", "imaplib", "telnetlib",
    "http", "xmlrpc", "socketserver", "asyncio", "requests", "httpx", "urllib3", "aiohttp", "paramiko",
})

# Attribute names that lead from an ordinary object back to frames, code objects, globals or the
# stdlib. The AST check in procgen_studio.render refuses them as dotted attributes and import names;
# the worker refuses them again when they arrive as strings (getattr, from-imports, star imports).
FORBIDDEN_ATTRS = frozenset({
    "gi_frame", "gi_code", "gi_yieldfrom", "cr_frame", "cr_code", "ag_frame", "ag_code", "f_back",
    "f_globals", "f_locals", "f_builtins", "f_code", "tb_frame", "tb_next", "co_code", "func_globals",
    "system", "popen", "spawn", "fork", "unlink", "rmdir", "rmtree",
    # Library modules re-export the stdlib (PIL.Image.os, numpy.sys, ...).
    "os", "sys", "subprocess", "builtins", "importlib", "shutil", "socket", "ctypes", "modules", "pathlib",
    # operator.attrgetter("os") / methodcaller(...) are getattr by another name.
    "attrgetter", "methodcaller",
})

# Dunders a class body may leave in its namespace. Anything else (__array_interface__, __getattr__,
# __buffer__, __class_getitem__, ...) hooks a library protocol and is refused.
CLASS_DUNDERS = frozenset({
    "__module__", "__qualname__", "__doc__", "__init__", "__post_init__", "__annotations__",
    "__annotate__", "__annotate_func__", "__annotations_cache__", "__firstlineno__",
    "__static_attributes__", "__classcell__", "__orig_bases__", "__dict__", "__weakref__",
    # Added by the stdlib itself for Enum and typing.NamedTuple subclasses.
    "__new__", "__repr__", "__getnewargs__", "__match_args__", "__slots__",
})

_BLOCKED_EVENT_PREFIXES = (
    "socket.", "subprocess.", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork",
    "os.forkpty", "os.kill", "os.killpg", "os.putenv", "os.unsetenv", "os.chmod", "os.chown",
    "os.remove", "os.rmdir", "os.rename", "os.link", "os.symlink", "os.truncate", "os.setxattr",
    "os.removexattr", "os.startfile", "shutil.", "ctypes.dlopen", "ctypes.dlsym", "ctypes.cdata",
    "ctypes.call_function", "ctypes.addressof", "ctypes.string_at", "ctypes.wstring_at",
    "sys.setprofile", "sys.settrace", "sys.addaudithook",
    "urllib.Request", "http.client.", "ftplib.", "smtplib.", "poplib.", "imaplib.", "nntplib.",
    "telnetlib.", "webbrowser.", "winreg.", "msvcrt.", "builtins.input", "pdb.", "code.__new__",
    "function.__new__", "sqlite3.",
)

SAFE_BUILTINS = (
    "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes", "callable", "chr", "complex",
    "dict", "divmod", "enumerate", "filter", "float", "format", "frozenset", "hash", "hex", "int",
    "isinstance", "issubclass", "iter", "len", "list", "map", "max", "min", "next", "object", "oct",
    "ord", "pow", "print", "range", "repr", "reversed", "round", "set", "slice", "sorted", "str", "sum",
    "tuple", "zip", "Exception", "ValueError", "TypeError", "IndexError", "KeyError", "ZeroDivisionError",
    "ArithmeticError", "RuntimeError", "StopIteration", "AssertionError", "NotImplementedError",
    "OverflowError", "AttributeError", "LookupError", "True", "False", "None", "property", "staticmethod",
    "classmethod", "super", "NotImplemented", "Ellipsis", "BaseException", "memoryview",
    "__build_class__", "__name__",
)


def _public_name(name) -> str:
    # Exact str only: a str subclass could override startswith/__eq__/__hash__ to slip past the checks.
    if type(name) is not str or name.startswith("_") or name in FORBIDDEN_ATTRS:
        raise AttributeError(f"attribute {name!r} is not available to drawing programs")
    return name


def _safe_getattr(obj, name, *default):
    return getattr(obj, _public_name(name), *default)


def _safe_hasattr(obj, name) -> bool:
    return hasattr(obj, _public_name(name))


def _safe_setattr(obj, name, value) -> None:
    setattr(obj, _public_name(name), value)


def _emit(payload: dict) -> None:
    sys.stdout.write("\n" + json.dumps(payload) + "\n")
    sys.stdout.flush()


def _limit_resources(mem_mb: int, cpu_s: int, fsize_mb: int) -> None:
    try:
        import resource
    except ImportError:  # Windows: rely on the parent's wall-clock timeout.
        return
    for res, value in (
        (getattr(resource, "RLIMIT_AS", None), mem_mb * 1024 * 1024),
        (getattr(resource, "RLIMIT_CPU", None), cpu_s),
        (getattr(resource, "RLIMIT_FSIZE", None), fsize_mb * 1024 * 1024),
        (getattr(resource, "RLIMIT_CORE", None), 0),
    ):
        if res is None:
            continue
        try:
            soft, hard = resource.getrlimit(res)
            limit = value if hard == resource.RLIM_INFINITY else min(value, hard)
            resource.setrlimit(res, (limit, hard))
        except (ValueError, OSError):
            pass


def _guarded_import_factory(real_import):
    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level != 0:
            raise ImportError("relative imports are not available to drawing programs")
        if type(name) is not str:
            raise ImportError("import names must be plain strings")
        top = name.split(".", 1)[0]
        if top not in ALLOWED_IMPORTS:
            raise ImportError(f"import of '{name}' is not allowed (allowed: {', '.join(sorted(ALLOWED_IMPORTS))})")
        fromlist = tuple(fromlist or ())
        for item in fromlist:
            if item != "*":
                try:
                    _public_name(item)
                except AttributeError:
                    raise ImportError(f"'from {name} import {item}' is not available to drawing programs") from None
        module = real_import(name, globals, locals, fromlist, level)
        if "*" in fromlist:
            exported = getattr(module, "__all__", None)
            if exported is None:
                exported = [k for k in vars(module) if not k.startswith("_")]
            bad = sorted(str(k) for k in exported if type(k) is not str or k.startswith("_") or k in FORBIDDEN_ATTRS)
            if bad:
                raise ImportError(f"'from {name} import *' would expose {', '.join(bad[:5])}; import names explicitly")
        return module
    return guarded_import


def _guarded_build_class_factory(real_build_class):
    def build_class(func, name, *bases, **kwargs):
        cls = real_build_class(func, name, *bases, **kwargs)
        try:
            names = list(vars(cls))
        except TypeError:
            raise TypeError(f"class {name!r} did not produce a class") from None
        bad = sorted(k for k in names if type(k) is not str or
                     (k.startswith("__") and k.endswith("__") and k not in CLASS_DUNDERS))
        if bad:
            raise TypeError(f"class {name!r} may not define {', '.join(map(str, bad[:5]))} in a drawing program")
        return cls
    return build_class


def _install_audit_hook(job_dir: str, read_roots: list[str]) -> None:
    job_dir = os.path.realpath(job_dir)
    roots = tuple(os.path.realpath(r) for r in read_roots if r)

    def within(path: str, bases) -> bool:
        try:
            real = os.path.realpath(path)
        except (TypeError, ValueError):
            return False
        return any(real == b or real.startswith(b.rstrip(os.sep) + os.sep) for b in bases)

    def as_text(path):
        if isinstance(path, (str, bytes)):
            return os.fsdecode(path)
        try:
            return os.fsdecode(os.fspath(path))
        except (TypeError, ValueError):
            return None

    def hook(event: str, args) -> None:
        if event in ("os.listdir", "os.scandir"):
            path = args[0] if args else "."
            if isinstance(path, int):
                return
            text = as_text(path)
            if text is None or not within(text, (job_dir,) + roots):
                raise PermissionError(f"drawing programs may not list {path!r}")
            return
        if event in ("os.mkdir", "os.utime"):
            # os.makedirs goes through os.mkdir; both may only touch the job directory.
            path = args[0] if args else None
            dir_fd = args[-1] if len(args) > 1 else None
            if isinstance(path, int) and event == "os.utime":
                return
            text = as_text(path)
            relative_to_fd = dir_fd not in (None, -1) and text is not None and not os.path.isabs(text)
            if text is None or relative_to_fd or not within(text, (job_dir,)):
                raise PermissionError(f"drawing programs may only {event[3:]} inside the job directory, not {path!r}")
            return
        if event == "open":
            path = args[0]
            mode = args[1] if len(args) > 1 else "r"
            flags = args[2] if len(args) > 2 else 0
            if isinstance(path, int):
                return
            path = as_text(path)
            if path is None:
                raise PermissionError("drawing programs may only open files by path")
            write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
            if isinstance(mode, str):
                writing = any(c in mode for c in "wax+")
            else:
                writing = bool((flags if isinstance(flags, int) else 0) & write_flags)
            if writing and not within(path, (job_dir,)):
                raise PermissionError(f"drawing programs may only write inside the job directory, not {path}")
            if not writing and not within(path, (job_dir,) + roots):
                raise PermissionError(f"drawing programs may not read {path}")
            return
        if event == "import":
            name = args[0] if args else ""
            if isinstance(name, str) and name.split(".", 1)[0] in _BLOCKED_MODULES:
                raise ImportError(f"import of '{name}' is blocked in the render sandbox")
            return
        if event.startswith(_BLOCKED_EVENT_PREFIXES):
            raise PermissionError(f"'{event}' is blocked in the render sandbox")

    sys.addaudithook(hook)


def svg_to_png_bytes(svg: str, width: int, height: int) -> bytes:
    import cairosvg
    # unsafe=False: entities and external XML are refused, and only data: URLs are fetched.
    return cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=width, output_height=height, unsafe=False)


def _helpers(W: int, H: int, seed: int, palette: dict):
    """Small utilities every drawing program can use without importing anything."""
    import math
    import random

    def hex_to_rgb(c: str):
        c = c.lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))

    def rgb_to_hex(rgb) -> str:
        return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in rgb[:3])

    def mix(a: str, b: str, t: float) -> str:
        ra, rb = hex_to_rgb(a), hex_to_rgb(b)
        return rgb_to_hex([ra[i] + (rb[i] - ra[i]) * t for i in range(3)])

    def shade(c: str, k: float) -> str:
        return rgb_to_hex([v * k for v in hex_to_rgb(c)])

    def poly_pts(cx: float, cy: float, r: float, n: int = 6, rot: float = -90.0) -> str:
        return " ".join(
            f"{cx + r * math.cos(math.radians(rot + 360.0 * k / n)):.1f},"
            f"{cy + r * math.sin(math.radians(rot + 360.0 * k / n)):.1f}" for k in range(n))

    def ease(t: float) -> float:
        t = max(0.0, min(1.0, t))
        return 1 - (1 - t) ** 3

    def svg_doc(body: str, defs: str = "", bg: str | None = None) -> str:
        fill = f'<rect width="{W}" height="{H}" fill="{bg}"/>' if bg else ""
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
                f'<defs>{defs}</defs>{fill}{body}</svg>')

    def pixel_grid(rows, colors: dict, scale: int = 1):
        """Rows of single-character keys -> RGBA PIL image. '.' and ' ' are transparent."""
        from PIL import Image
        h = len(rows)
        w = max((len(r) for r in rows), default=0)
        img = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
        px = img.load()
        for y, row in enumerate(rows):
            for x, ch in enumerate(row):
                col = colors.get(ch)
                if ch in ". " or col is None:
                    continue
                rgb = hex_to_rgb(col) if isinstance(col, str) else tuple(col)[:3]
                px[x, y] = tuple(rgb) + (255,)
        if scale > 1:
            img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
        return img

    return {
        "rng": random.Random(seed), "hex_to_rgb": hex_to_rgb, "rgb_to_hex": rgb_to_hex, "mix": mix,
        "shade": shade, "poly_pts": poly_pts, "ease": ease, "svg_doc": svg_doc, "pixel_grid": pixel_grid,
    }


def _to_png(result, W: int, H: int, path: str) -> None:
    from PIL import Image
    if isinstance(result, bytes) and result[:8] == b"\x89PNG\r\n\x1a\n":
        with open(path, "wb") as fh:
            fh.write(result)
        return
    if isinstance(result, str):
        if "<svg" not in result[:2000]:
            raise TypeError("draw() returned a string that is not an SVG document")
        with open(path, "wb") as fh:
            fh.write(svg_to_png_bytes(result, W, H))
        return
    if isinstance(result, Image.Image):
        img = result
    else:
        try:
            import numpy as np
        except ImportError as exc:  # pragma: no cover - numpy is a declared dependency
            raise TypeError(f"unsupported draw() result {type(result).__name__}") from exc
        if not isinstance(result, np.ndarray):
            raise TypeError(f"draw() must return an SVG string, a PIL image or a numpy array, "
                            f"not {type(result).__name__}")
        arr = result
        if arr.dtype != np.uint8:
            arr = np.clip(arr if arr.max() > 1.0 else arr * 255.0, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)
    if img.size != (W, H):
        resample = Image.NEAREST if max(img.size) <= max(W, H) // 4 else Image.LANCZOS
        img = img.resize((W, H), resample)
    img.save(path, "PNG")


def _trim_traceback(exc: BaseException, filename: str, code: str = "") -> str:
    src = code.splitlines()
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == filename]
    lines = [f"line {f.lineno}: {(src[f.lineno - 1].strip() if 0 < (f.lineno or 0) <= len(src) else f.line)}"
             for f in frames[-4:]]
    if isinstance(exc, SyntaxError) and exc.lineno:
        lines.append(f"line {exc.lineno}: {exc.text or ''}".rstrip())
    msg = f"{type(exc).__name__}: {exc}"
    return (msg + ("\n" + "\n".join(lines) if lines else ""))[:2000]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        _emit({"ok": False, "error": "usage: sandbox_runner.py <job.json>"})
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        job = json.load(fh)
    for p in reversed(job.get("sys_path") or []):
        if p and p not in sys.path:
            sys.path.insert(0, p)
    job_dir = job["job_dir"]
    W, H = int(job["width"]), int(job["height"])
    seed = int(job["seed"])
    mode = job["mode"]
    times = [float(t) for t in job.get("times") or [0.0]]
    limits = job.get("limits") or {}
    _limit_resources(int(limits.get("memory_mb", 2048)), int(limits.get("cpu_seconds", 120)),
                     int(limits.get("file_mb", 512)))

    import random as _random  # noqa: F401  (pre-import before the audit hook)
    import math as _math  # noqa: F401
    import colorsys as _colorsys  # noqa: F401
    import cairosvg as _cairosvg  # noqa: F401
    import cairosvg.url as _cairosvg_url  # noqa: F401
    from PIL import Image as _Image, ImageDraw as _ImageDraw, ImageFont as _ImageFont  # noqa: F401
    from PIL import ImageFilter as _ImageFilter, ImageChops as _ImageChops  # noqa: F401
    try:
        import numpy as _np  # noqa: F401
    except ImportError:
        _np = None
    for optional in ("noise", "opensimplex", "cairo"):
        try:
            __import__(optional)
        except Exception:
            pass

    import builtins
    real_import = builtins.__import__
    _install_audit_hook(job_dir, job.get("read_roots") or [])

    code = job["code"]
    filename = "<drawing>"
    started = time.monotonic()
    frames: list[str] = []
    try:
        if mode == "svg":
            for i, _t in enumerate(times):
                out = os.path.join(job_dir, f"frame_{i:04d}.png")
                _to_png(code, W, H, out)
                frames.append(out)
        else:
            safe_builtins = {name: getattr(builtins, name) for name in SAFE_BUILTINS if hasattr(builtins, name)}
            safe_builtins["__import__"] = _guarded_import_factory(real_import)
            safe_builtins["__build_class__"] = _guarded_build_class_factory(builtins.__build_class__)
            safe_builtins.update(getattr=_safe_getattr, hasattr=_safe_hasattr, setattr=_safe_setattr)
            palette = dict(job.get("palette") or {})
            env = {
                "__builtins__": safe_builtins, "__name__": "drawing",
                "W": W, "H": H, "SEED": seed, "PAL": palette, "STYLE": dict(job.get("style") or {}),
                "T": 0.0, "FRAMES": len(times), "FPS": int(job.get("fps") or 24),
            }
            env.update(_helpers(W, H, seed, palette))
            exec(compile(code, filename, "exec"), env)  # noqa: S102 - the point of the worker
            draw = env.get("draw")
            for i, t in enumerate(times):
                env["T"] = t
                env["rng"].seed(seed)
                if callable(draw):
                    try:
                        result = draw(t)
                    except TypeError as exc:
                        if "positional argument" not in str(exc):
                            raise
                        result = draw()
                else:
                    result = next((env[k] for k in ("SVG", "svg", "IMAGE", "image", "img", "result")
                                   if k in env), None)
                    if result is None:
                        raise RuntimeError("the program must define draw(t) returning an SVG string, "
                                           "a PIL image or a numpy array")
                out = os.path.join(job_dir, f"frame_{i:04d}.png")
                _to_png(result, W, H, out)
                frames.append(out)
    except BaseException as exc:  # noqa: BLE001 - report everything, including MemoryError
        _emit({"ok": False, "error": _trim_traceback(exc, filename, code if mode != "svg" else ""),
               "error_type": type(exc).__name__,
               "frames": frames, "elapsed": round(time.monotonic() - started, 3)})
        return 1
    _emit({"ok": True, "frames": frames, "elapsed": round(time.monotonic() - started, 3)})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
