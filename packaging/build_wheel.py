#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Pattern check: no GoF pattern (-) — rejected — one build script beside bundle.py, plain functions writing a zip; no family or state.
"""Build ``dist/takumion_link-<version>-py3-none-any.whl``: the plugin as a
wheel, the form Orca Cloud lists without flagging it — the modules are plain
files, so nothing is ``exec``'d from strings as in the single-file bundle.

Orca extracts a wheel plugin to ``orca_plugins/<file>/__whl_extracted__/<pkg>/``,
puts that folder on ``sys.path`` and imports the package that ``Import-Name``
(and ``top_level.txt``) name. Inside the wheel:

- ``takumion_link/__init__.py``: ``takumion_link.py`` with ``from link.`` as ``from .``,
  and ``__version__`` first, since ``app`` reads it while the package imports;
- ``takumion_link/<module>.py``: every module of ``link`` (they import relatively);
- ``frontend/``: where ``page`` looks, two levels above itself.

Usage: ``python packaging/build_wheel.py [--out-dir dist]``
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
import sys
import tomllib
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACKAGE = "takumion_link"
DIST = "takumion_link"
FRONTEND = ("index.html", "style.css", "app.js")
_MANIFEST_END = "# ///\n"


def project(repo: Path) -> dict:
    """The ``[project]`` table of pyproject.toml."""
    return tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def entry_module(repo: Path, version: str) -> str:
    """``takumion_link.py`` as the package's ``__init__``."""
    entry = (repo / "takumion_link.py").read_text(encoding="utf-8")
    end = entry.index(_MANIFEST_END) + len(_MANIFEST_END)
    body = re.sub(r"^from link\.", "from .", entry[end:], flags=re.MULTILINE)
    if re.search(r"^\s*(from|import) link\b", body, flags=re.MULTILINE):
        raise SystemExit("takumion_link.py imports link in a form the wheel builder cannot rewrite")
    return entry[:end] + f"__version__ = {version!r}\n" + body


def wheel_files(repo: Path, version: str) -> dict[str, bytes]:
    """Every file of the wheel but its RECORD, by archive path."""
    meta = project(repo)
    files = {f"{PACKAGE}/__init__.py": entry_module(repo, version).encode("utf-8")}
    for path in sorted((repo / "link").glob("*.py")):
        if path.stem != "__init__":
            files[f"{PACKAGE}/{path.name}"] = path.read_bytes()
    for name in FRONTEND:
        files[f"frontend/{name}"] = (repo / "frontend" / name).read_bytes()
    info = f"{DIST}-{version}.dist-info"
    files[f"{info}/METADATA"] = (
        "Metadata-Version: 2.4\n"
        "Name: Takumion Link\n"
        f"Version: {version}\n"
        f"Summary: {meta['description']}\n"
        "Author: Takumion\n"
        f"License-Expression: {meta['license']}\n"
        f"Requires-Python: {meta['requires-python']}\n"
        f"Import-Name: {PACKAGE}\n"
    ).encode("utf-8")
    files[f"{info}/WHEEL"] = b"Wheel-Version: 1.0\nGenerator: takumion-orca-link packaging/build_wheel.py\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    files[f"{info}/top_level.txt"] = f"{PACKAGE}\n".encode("utf-8")
    files[f"{info}/LICENSE"] = (repo / "LICENSE").read_bytes()
    return files


def record(files: dict[str, bytes], record_path: str) -> bytes:
    """RECORD: each file's urlsafe SHA-256 and size; RECORD itself unhashed."""
    def digest(data: bytes) -> str:
        return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
    lines = [f"{name},sha256={digest(data)},{len(data)}" for name, data in files.items()]
    return ("\n".join([*lines, f"{record_path},,"]) + "\n").encode("utf-8")


def build(repo: Path = REPO, out_dir: Path | None = None) -> Path:
    """Write the wheel; returns its path. Entries carry a fixed date, so the same sources give the same bytes."""
    version = project(repo)["version"]
    files = wheel_files(repo, version)
    record_path = f"{DIST}-{version}.dist-info/RECORD"
    files[record_path] = record(files, record_path)
    out = (out_dir or repo / "dist") / f"{DIST}-{version}-py3-none-any.whl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), data, zipfile.ZIP_DEFLATED)
    return out


def main(argv: list[str]) -> int:
    """CLI entry."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    out = build(REPO, args.out_dir)
    print(f"{out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
