"""The Orca tab's page: ``frontend/`` inlined into one HTML string (Orca's
Pages tab takes HTML, not a folder). The release bundle fills ``BUNDLED``
with the frontend files; a source checkout reads them from disk."""

from __future__ import annotations

from pathlib import Path

BUNDLED: dict[str, str] = {}
FRONTEND = ("index.html", "style.css", "app.js")
_SOURCE = Path(__file__).resolve().parent.parent / "frontend"


def resource(name: str) -> str:
    """One frontend file's text."""
    if name in BUNDLED:
        return BUNDLED[name]
    return (_SOURCE / name).read_text(encoding="utf-8")


def render_page(head_extra: str = "") -> str:
    """``index.html`` with its stylesheet and script inlined; ``head_extra``
    goes first in ``<head>`` (the dev server's ``window.orca`` shim)."""
    html = resource("index.html")
    html = html.replace('<link rel="stylesheet" href="style.css">', f"<style>\n{resource('style.css')}</style>")
    html = html.replace('<script src="app.js"></script>', f"<script>\n{resource('app.js')}</script>")
    return html.replace("<head>", "<head>\n" + head_extra, 1) if head_extra else html
