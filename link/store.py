"""Which plate file belongs to which body (docs/slicer-link-protocol.md,
"Files on disk").

Layout under ``{data_dir}/takumion/``::

    links.json                                     doc -> body -> link
    links/<docKey>/<bodyKey>/<name>.<fmt>          update mode, path fixed at first send
    copies/<docKey>/<name>-<YYYYMMDD-HHMMSS>.<fmt> add mode
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

from .atomic import read_json, write_json_atomic

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")
# Orca's sandbox refuses any path with one of these as a whole component.
_ORCA_DENIED_COMPONENTS = frozenset({"secret", "secrets", "cert", "certs", "certificate", "certificates", "conf", "config"})


# Windows refuses these as a file or folder name, with or without an extension.
_WINDOWS_RESERVED = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))})


def folder_name(key: str) -> str:
    """A protocol key as a folder name, prefixed with ``_`` when Orca's
    sandbox would refuse it (``config``, ``secret``, …) or Windows would (``CON``, ``COM1``, …)."""
    lowered = key.lower()
    return f"_{key}" if lowered in _ORCA_DENIED_COMPONENTS or lowered in _WINDOWS_RESERVED else key


def safe_name(name: str) -> str:
    """A file-name stem from a body name: ASCII letters, digits, ``_.-`` only,
    at most 60 characters, never empty, hidden or a Windows device name."""
    stem = _UNSAFE.sub("_", name).strip("._")[:60] or "body"
    return f"_{stem}" if stem.split(".")[0].lower() in _WINDOWS_RESERVED else stem


def same_path(a: str | os.PathLike[str], b: str | os.PathLike[str]) -> bool:
    """Whether two paths name the same file as Orca would report it."""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


@dataclass(frozen=True)
class Link:
    """One linked body: its fixed file and what was last sent to it.

    ``mesh_sig`` is the plate object's mesh signature when ``reload_needed``
    was raised; a different signature later means Orca reloaded the file."""

    doc_key: str
    body_key: str
    name: str
    format: str
    path: str
    sha256: str
    last_sent: str
    reload_needed: bool = False
    mesh_sig: tuple[float, ...] | None = None


class LinkStore:
    """Owns ``links.json`` and the file layout; every method is thread-safe."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._file = root / "links.json"
        self._lock = threading.Lock()
        self._docs: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        raw = read_json(self._file, {})
        docs = raw.get("docs", {}) if isinstance(raw, dict) else {}
        return {key: doc for key, doc in docs.items() if isinstance(doc, dict)}

    def _save(self) -> None:
        write_json_atomic(self._file, {"version": 1, "docs": self._docs})

    def link_path(self, doc_key: str, body_key: str, name: str, fmt: str) -> Path:
        """Where a body's update-mode file lives; decided once, at its first send."""
        return self.root / "links" / folder_name(doc_key) / folder_name(body_key) / f"{safe_name(name)}.{fmt}"

    def copy_path(self, doc_key: str, name: str, fmt: str, now: datetime) -> Path:
        """A fresh, time-stamped add-mode file that does not exist yet."""
        folder = self.root / "copies" / folder_name(doc_key)
        stem = f"{safe_name(name)}-{now:%Y%m%d-%H%M%S}"
        candidate = folder / f"{stem}.{fmt}"
        counter = 2
        while candidate.exists():
            candidate = folder / f"{stem}-{counter}.{fmt}"
            counter += 1
        return candidate

    def get(self, doc_key: str, body_key: str) -> Link | None:
        """The body's link, if it has one."""
        with self._lock:
            raw = self._docs.get(doc_key, {}).get("bodies", {}).get(body_key)
        return _to_link(doc_key, body_key, raw)

    def put(self, link: Link, doc_name: str | None = None) -> None:
        """Store or replace a link (and the document's display name)."""
        with self._lock:
            doc = self._docs.setdefault(link.doc_key, {"name": "", "bodies": {}})
            if doc_name:
                doc["name"] = doc_name
            record = asdict(link)
            del record["doc_key"], record["body_key"]
            record["mesh_sig"] = list(link.mesh_sig) if link.mesh_sig is not None else None
            doc.setdefault("bodies", {})[link.body_key] = record
            self._save()

    def update(self, link: Link, **changes: object) -> Link:
        """Store a changed copy of ``link`` and return it."""
        changed = replace(link, **changes)
        self.put(changed)
        return changed

    def doc_name(self, doc_key: str) -> str:
        """The name the document was last sent under, or ``""``."""
        with self._lock:
            return str(self._docs.get(doc_key, {}).get("name", ""))

    def bodies(self, doc_key: str) -> list[Link]:
        """Every link of one document, in first-send order."""
        with self._lock:
            raw = dict(self._docs.get(doc_key, {}).get("bodies", {}))
        return [link for key, rec in raw.items() if (link := _to_link(doc_key, key, rec))]

    def all_links(self) -> list[Link]:
        """Every link of every document."""
        with self._lock:
            keys = list(self._docs)
        return [link for key in keys for link in self.bodies(key)]

    def drop_path(self, path: str) -> list[Link]:
        """Forget every link whose file is ``path`` (it was opened as Orca's
        project); the next send of those bodies writes a fresh file."""
        dropped = [link for link in self.all_links() if same_path(link.path, path)]
        if not dropped:
            return []
        with self._lock:
            for link in dropped:
                self._docs.get(link.doc_key, {}).get("bodies", {}).pop(link.body_key, None)
            self._save()
        return dropped


def _to_link(doc_key: str, body_key: str, raw: object) -> Link | None:
    if not isinstance(raw, dict):
        return None
    try:
        sig = raw.get("mesh_sig")
        return Link(
            doc_key=doc_key,
            body_key=body_key,
            name=str(raw["name"]),
            format=str(raw["format"]),
            path=str(raw["path"]),
            sha256=str(raw["sha256"]),
            last_sent=str(raw["last_sent"]),
            reload_needed=bool(raw.get("reload_needed", False)),
            mesh_sig=tuple(sig) if isinstance(sig, list) else None,
        )
    except KeyError:
        return None
