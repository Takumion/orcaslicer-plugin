#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Build ``dist/takumion_link_any.py``: the whole plugin as ONE file, which is
what OrcaSlicer installs (``{data_dir}/orca_plugins/<file>/<file>``).

The ``link`` package is embedded as source behind a small import hook under a
private package name, so it cannot clash with another plugin's modules in
Orca's shared interpreter, and a reloaded plugin never sees stale modules.
The frontend files are embedded for ``link.page``.

Usage: ``python packaging/bundle.py [--out dist/takumion_link_any.py]``
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACKAGE = "takumion_link_bundle"
OUT = REPO / "dist" / "takumion_link_any.py"
MAX_BUNDLE_BYTES = 1_000_000
MAX_PAGE_BYTES = 300_000
FRONTEND = ("index.html", "style.css", "app.js")

BOOTSTRAP = '''
# ---------------------------------------------------------------------------
# Bundled modules (built by packaging/bundle.py from the takumion-orca-link
# repository; the readable sources live there).
import importlib.abc as _tl_abc
import importlib.util as _tl_util
import linecache as _tl_linecache
import sys as _tl_sys

_TL_PACKAGE = {package!r}
_TL_SOURCES = {sources}
_TL_RESOURCES = {resources}


def _tl_key(name):
    if name == _TL_PACKAGE:
        return "link"
    if name.startswith(_TL_PACKAGE + "."):
        key = "link." + name[len(_TL_PACKAGE) + 1:]
        return key if key in _TL_SOURCES else None
    return None


class _TakumionLinkImporter(_tl_abc.MetaPathFinder, _tl_abc.Loader):
    """Imports the embedded link package."""

    def find_spec(self, name, path=None, target=None):
        key = _tl_key(name)
        if key is None:
            return None
        return _tl_util.spec_from_loader(name, self, is_package=key == "link")

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        key = _tl_key(module.__name__)
        filename = "<takumion_link_any:" + key + ">"
        source = _TL_SOURCES[key]
        _tl_linecache.cache[filename] = (len(source), None, source.splitlines(True), filename)
        module.__file__ = filename
        exec(compile(source, filename, "exec"), module.__dict__)


for _tl_name in [n for n in _tl_sys.modules if n == _TL_PACKAGE or n.startswith(_TL_PACKAGE + ".")]:
    del _tl_sys.modules[_tl_name]
_tl_sys.meta_path[:] = [f for f in _tl_sys.meta_path if type(f).__name__ != "_TakumionLinkImporter"]
_tl_sys.meta_path.insert(0, _TakumionLinkImporter())

import {package}.page as _tl_page  # noqa: E402

_tl_page.BUNDLED.update(_TL_RESOURCES)
# ---------------------------------------------------------------------------
'''

_MANIFEST_END = "# ///\n"


def split_entry(entry: str) -> tuple[str, str]:
    """The PEP 723 manifest block (Orca reads it from the top of the file) and
    the rest of the entry file."""
    end = entry.index(_MANIFEST_END) + len(_MANIFEST_END)
    return entry[:end], entry[end:]


def module_sources(repo: Path) -> dict[str, str]:
    """``{"link": …, "link.server": …}`` for every module of the package."""
    sources = {}
    for path in sorted((repo / "link").glob("*.py")):
        name = "link" if path.stem == "__init__" else f"link.{path.stem}"
        sources[name] = path.read_text(encoding="utf-8")
    return sources


def build(repo: Path = REPO, out: Path = OUT) -> Path:
    """Write the bundle and enforce the size limits; returns its path."""
    manifest, body = split_entry((repo / "takumion_link.py").read_text(encoding="utf-8"))
    body = re.sub(r"^from link(\.|\s)", rf"from {PACKAGE}\1", body, flags=re.MULTILINE)
    if re.search(r"^\s*(from|import) link\b", body, flags=re.MULTILINE):
        raise SystemExit("takumion_link.py imports link in a form the bundler cannot rewrite")
    resources = {name: (repo / "frontend" / name).read_text(encoding="utf-8") for name in FRONTEND}
    page_bytes = sum(len(text.encode("utf-8")) for text in resources.values())
    if page_bytes > MAX_PAGE_BYTES:
        raise SystemExit(f"frontend is {page_bytes} bytes; the limit is {MAX_PAGE_BYTES}")
    bootstrap = BOOTSTRAP.format(package=PACKAGE, sources=repr(module_sources(repo)), resources=repr(resources))
    text = manifest + "# Built by packaging/bundle.py; do not edit.\n" + bootstrap + body
    data = text.encode("utf-8")
    if len(data) > MAX_BUNDLE_BYTES:
        raise SystemExit(f"bundle is {len(data)} bytes; the limit is {MAX_BUNDLE_BYTES}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out


def main(argv: list[str]) -> int:
    """CLI entry."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    out = build(REPO, args.out)
    print(f"{out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
