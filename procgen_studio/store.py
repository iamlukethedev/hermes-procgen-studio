"""Where renders live.

Every render gets a durable record in the plugin data directory::

    <HERMES_HOME>/plugin-data/procgen-studio/renders/<id>/
        image.png | video.mp4 | video.gif   the output
        source.py | source.svg | source.pixel.json
        STYLE.md                             the style contract snapshot used
        meta.json                            prompt, seed, size, style, critic log, model calls
        attempts/NN.*                        every draft the critic loop produced

The PNG handed back to Hermes is a copy in Hermes's media cache, with the
source and metadata written next to it under the same file stem, so whoever
receives the image path can find the code that drew it.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

_ID_RE = re.compile(r"^pg-[0-9a-f]{8}$")
_lock = threading.Lock()


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def data_dir() -> Path:
    try:
        from plugins.plugin_storage import plugin_data_dir
        return Path(plugin_data_dir("procgen-studio"))
    except Exception:
        root = hermes_home() / "plugin-data" / "procgen-studio"
        root.mkdir(parents=True, exist_ok=True)
        return root


def slugify(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (slug[:limit].rstrip("-") or "render")


@dataclass
class Record:
    id: str
    created: float
    prompt: str
    seed: int
    width: int
    height: int
    aspect_ratio: str
    kind: str
    style: Dict[str, Any]
    output: str = ""
    source: str = ""
    published: str = ""
    media: str = "image"
    set: str = ""
    parent: str = ""
    operation: str = "generate"
    frames: int = 1
    fps: int = 0
    critic: List[Dict[str, Any]] = field(default_factory=list)
    llm: Dict[str, Any] = field(default_factory=dict)
    timings: Dict[str, float] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def dir(self) -> Path:
        return renders_dir() / self.id

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


def renders_dir() -> Path:
    d = data_dir() / "renders"
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_id() -> str:
    return f"pg-{secrets.token_hex(4)}"


def valid_id(value: str) -> bool:
    return bool(_ID_RE.match(value or ""))


def save_record(rec: Record) -> Path:
    rec.dir.mkdir(parents=True, exist_ok=True)
    path = rec.dir / "meta.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec.to_json(), indent=2), encoding="utf-8")
    os.replace(tmp, path)
    with _lock:
        with open(data_dir() / "index.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": rec.id, "created": rec.created, "prompt": rec.prompt[:200],
                                 "set": rec.set, "media": rec.media, "parent": rec.parent,
                                 "operation": rec.operation}) + "\n")
    return path


def load_record(render_id: str) -> Record:
    if not valid_id(render_id):
        raise KeyError(f"not a render id: {render_id!r} (ids look like pg-1a2b3c4d)")
    path = renders_dir() / render_id / "meta.json"
    if not path.is_file():
        raise KeyError(f"no render {render_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    known = {f for f in Record.__dataclass_fields__}
    return Record(**{k: v for k, v in data.items() if k in known})


def iter_records(*, newest_first: bool = True) -> Iterator[Record]:
    dirs = [d for d in renders_dir().iterdir() if d.is_dir() and valid_id(d.name) and (d / "meta.json").is_file()]
    dirs.sort(key=lambda d: (d / "meta.json").stat().st_mtime, reverse=newest_first)
    for d in dirs:
        try:
            yield load_record(d.name)
        except (KeyError, ValueError):
            continue


def latest(n: int = 10, *, set_name: str = "", media: str = "") -> List[Record]:
    out = []
    for rec in iter_records():
        if set_name and rec.set != set_name:
            continue
        if media and rec.media != media:
            continue
        out.append(rec)
        if len(out) >= n:
            break
    return out


def resolve_ref(ref: str) -> Record:
    """Accept an id, ``last``, or the path of a published PNG/MP4 (or its sidecar)."""
    ref = (ref or "").strip()
    if not ref or ref in ("last", "latest", "-"):
        recs = latest(1)
        if not recs:
            raise KeyError("no renders yet")
        return recs[0]
    if valid_id(ref):
        return load_record(ref)
    p = Path(ref).expanduser()
    meta = None
    if p.name == "meta.json" and p.is_file():
        meta = p
    elif p.parent.name and valid_id(p.parent.name) and (p.parent / "meta.json").is_file():
        meta = p.parent / "meta.json"
    else:
        stem = p.name.split(".")[0]
        side = p.with_name(stem + ".procgen.json")
        if side.is_file():
            try:
                rid = json.loads(side.read_text(encoding="utf-8")).get("id", "")
            except ValueError:
                rid = ""
            if valid_id(rid):
                return load_record(rid)
    if meta is not None:
        rid = json.loads(meta.read_text(encoding="utf-8")).get("id", "")
        return load_record(rid)
    m = re.search(r"pg-[0-9a-f]{8}", ref)
    if m:
        return load_record(m.group(0))
    raise KeyError(f"'{ref}' is not a Procgen Studio render (use an id like pg-1a2b3c4d, 'last', or an output path)")


def publish(rec: Record, *, output_dir: Optional[str] = None) -> str:
    """Copy the output into Hermes's media cache with sidecars; optional project copy."""
    src = Path(rec.output)
    suffix = src.suffix
    stem = f"procgen_{slugify(rec.prompt, 32)}_{rec.id}"
    cache_kind = "videos" if rec.media == "video" else "images"
    cache = hermes_home() / "cache" / "generated" / cache_kind
    cache.mkdir(parents=True, exist_ok=True)
    published = cache / f"{stem}{suffix}"
    shutil.copyfile(src, published)
    _write_sidecars(rec, published.parent, stem)
    for extra_key in ("gif",):
        extra = rec.extra.get(extra_key)
        if extra and Path(extra).is_file():
            shutil.copyfile(extra, cache / f"{stem}{Path(extra).suffix}")
    rec.published = str(published)
    if output_dir:
        out = Path(output_dir).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        project_stem = f"{slugify(rec.prompt, 40)}-{rec.id}"
        shutil.copyfile(src, out / f"{project_stem}{suffix}")
        _write_sidecars(rec, out, project_stem)
        rec.extra["project_copy"] = str(out / f"{project_stem}{suffix}")
    return str(published)


def _write_sidecars(rec: Record, folder: Path, stem: str) -> None:
    source = Path(rec.source)
    if source.is_file():
        ext = "".join(source.suffixes)
        shutil.copyfile(source, folder / f"{stem}{ext}")
    meta = {
        "id": rec.id, "prompt": rec.prompt, "seed": rec.seed, "width": rec.width, "height": rec.height,
        "kind": rec.kind, "style": rec.style, "media": rec.media, "source": f"{stem}{''.join(source.suffixes)}",
        "record": str(rec.dir / "meta.json"), "created": rec.created,
        "reroll": f"/procgen reroll {rec.id} --seed <n>",
    }
    (folder / f"{stem}.procgen.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def now() -> float:
    return round(time.time(), 3)
