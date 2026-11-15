"""*Open Takumion* from the Orca tab: the desktop app through its
``takumion://`` link, or the web app in the default browser.

Opening a URL starts the platform's opener (``xdg-open`` / ``open`` /
``os.startfile``). Orca's sandbox asks the user once per command line; the
command line is fixed per target, so the answer is remembered."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

WEB_URL = "https://app.takumion.com/"
DESKTOP_URL = "takumion://open"
# Takumion's local dev servers. The tab offers them in *Open Takumion › In
# this tab* only when one answers (probed from the page, so the plugin itself
# asks Orca for no extra network access).
DEV_URLS = ("http://localhost:5174/", "http://localhost:5180/")


def open_url(url: str) -> None:
    """Hand ``url`` to the operating system's default handler."""
    if sys.platform == "win32":
        os.startfile(url)  # type: ignore[attr-defined]  # noqa: S606 - fixed URL
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    # No stdio redirection: opening /dev/null is a file access Orca's sandbox asks about.
    subprocess.Popen([opener, url], start_new_session=True)  # noqa: S603 - fixed argv


def checked_web_url(url: object) -> str:
    """``url`` when it is an http(s) URL with a host, else the default web app."""
    parts = urlsplit(url) if isinstance(url, str) else None
    return url if parts and parts.scheme in ("http", "https") and parts.netloc else WEB_URL


def hosted_url(url: str) -> str:
    """The web app as a page inside Orca's tab: ``url`` with ``host=orca``."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "host"] + [("host", "orca")]
    return urlunsplit(parts._replace(query=urlencode(query)))


class Launcher:
    """Knows the Takumion targets: the desktop app, the web app in the
    browser, and the web app inside Orca's own tab."""

    def __init__(self, web_url: str = WEB_URL, opener: Callable[[str], None] = open_url) -> None:
        self.web_url = checked_web_url(web_url)
        self.targets = {"web": self.web_url, "desktop": DESKTOP_URL}
        self._opener = opener

    @property
    def here_url(self) -> str:
        """Where *Open Takumion here* navigates the tab."""
        return hosted_url(self.web_url)

    @property
    def dev_targets(self) -> list[dict[str, str]]:
        """Local dev servers the tab may offer next to the web app: ``probe``
        is what the page checks, ``url`` where it then navigates."""
        return [{"label": urlsplit(url).netloc, "probe": url, "url": hosted_url(url)}
                for url in DEV_URLS if url.rstrip("/") != self.web_url.rstrip("/")]

    def open(self, target: str) -> str:
        """Open ``"web"`` or ``"desktop"``; returns the URL opened."""
        url = self.targets.get(target)
        if url is None:
            raise ValueError(f"unknown Takumion target {target!r}")
        self._opener(url)
        return url
