"""Which plate file belongs to which body (docs/slicer-link-protocol.md,
"Files on disk").

The bookkeeping lives under ``{data_dir}/takumion/`` (``links.json``: doc ->
body -> link). The mesh files go straight into the folder the user chose
(``FilesLocation``), flat — Orca asks about every folder a plugin creates
outside its data folder, so there are no subfolders::

    <Project> - <Body>.<fmt>                   update mode, path fixed at first send
    <Project> - <Body> <YYYY-MM-DD HHMMSS>.<fmt>   add mode
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

from .atomic import read_json, write_json_atomic

# Windows refuses these as a file or folder name, with or without an extension.
_WINDOWS_RESERVED = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))})
# A readable file-name part: letters (any script), digits, space and _.-() kept.
_UNREADABLE = re.compile(r"[^\w .()-]+")


def display_name(name: str, fallback: str) -> str:
    """A project or body name as part of a file name a person reads: path and
    shell characters become ``_``, at most 60 characters, never empty, hidden
    or a Windows device name."""
    part = _UNREADABLE.sub("_", name).strip(" ._")[:60].strip() or fallback
    return f"_{part}" if part.split(".")[0].strip().lower() in _WINDOWS_RESERVED else part


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

    def link_path(self, folder: Path, doc_key: str, body_key: str, doc_name: str, name: str, fmt: str) -> Path:
        """Where a body's update-mode file lives in ``folder``:
        ``<Project> - <Body>.<fmt>``, with the body key's start added only when
        another body (or another document of the same name) already has that
        name. Decided once, at the body's first send."""
        stem = f"{display_name(doc_name, 'Takumion')} - {display_name(name, 'Body')}"
        candidate = folder / f"{stem}.{fmt}"
        if self._taken(candidate, doc_key, body_key):
            candidate = folder / f"{stem} ({body_key[:8]}).{fmt}"
        return candidate

    def copy_path(self, folder: Path, doc_name: str, name: str, fmt: str, now: datetime) -> Path:
        """A fresh, time-stamped add-mode file in ``folder`` that does not exist yet."""
        stem = f"{display_name(doc_name, 'Takumion')} - {display_name(name, 'Body')} {now:%Y-%m-%d %H%M%S}"
        candidate = folder / f"{stem}.{fmt}"
        counter = 2
        while candidate.exists():
            candidate = folder / f"{stem} {counter}.{fmt}"
            counter += 1
        return candidate

    def _taken(self, path: Path, doc_key: str, body_key: str) -> bool:
        """Whether ``path`` belongs to another body's link (case-insensitive, as Windows and macOS see it)."""
        return any(same_path(link.path, path) or str(link.path).lower() == str(path).lower()
                   for link in self.all_links() if (link.doc_key, link.body_key) != (doc_key, body_key))

    def project_path(self, folder: Path, doc_key: str, doc_name: str) -> Path:
        """Where the document's ``.cad`` lives in ``folder``: ``<Project>.cad``,
        decided at its first send (a renamed project keeps its file); the key's
        start is added when another document already has that name."""
        with self._lock:
            known = self._docs.get(doc_key, {}).get("project", {})
            others = [str(doc.get("project", {}).get("path", "")).lower()
                      for key, doc in self._docs.items() if key != doc_key]
        path = known.get("path") if isinstance(known, dict) else None
        if isinstance(path, str) and Path(path).parent == folder:
            return Path(path)
        candidate = folder / f"{display_name(doc_name, 'Takumion')}.cad"
        if str(candidate).lower() in others:
            candidate = folder / f"{display_name(doc_name, 'Takumion')} ({doc_key[:8]}).cad"
        return candidate

    def project_sha256(self, doc_key: str) -> str:
        """The hash of the ``.cad`` last written for the document, or ``""``."""
        with self._lock:
            known = self._docs.get(doc_key, {}).get("project", {})
        return str(known.get("sha256", "")) if isinstance(known, dict) else ""

    def put_project(self, doc_key: str, doc_name: str, path: Path, sha256: str) -> None:
        """Remember the document's ``.cad`` file and what was written to it."""
        with self._lock:
            doc = self._docs.setdefault(doc_key, {"name": "", "bodies": {}})
            if doc_name:
                doc["name"] = doc_name
            doc["project"] = {"path": str(path), "sha256": sha256}
            self._save()

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
