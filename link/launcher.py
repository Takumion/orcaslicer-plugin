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
# Plain http is accepted only for a Takumion served on this computer (a dev
# server set in Orca's Config tab): anything else must be https.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})
# The fragment that carries the bridge key into the tab (never sent to a server).
KEY_FRAGMENT = "orcaLinkKey"


def open_url(url: str) -> None:
    """Hand ``url`` to the operating system's default handler."""
    if sys.platform == "win32":
        os.startfile(url)  # type: ignore[attr-defined]  # noqa: S606 - fixed URL
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    # No stdio redirection: opening /dev/null is a file access Orca's sandbox asks about.
    subprocess.Popen([opener, url], start_new_session=True)  # noqa: S603 - fixed argv


def checked_web_url(url: object) -> str:
    """``url`` when it is an https URL with a host (or http on this computer),
    else the default web app."""
    parts = urlsplit(url) if isinstance(url, str) else None
    if parts is None or not parts.netloc:
        return WEB_URL
    if parts.scheme == "https" or (parts.scheme == "http" and (parts.hostname or "") in _LOCAL_HOSTS):
        return url  # type: ignore[return-value]  # checked str above
    return WEB_URL


def hosted_url(url: str, key: str = "") -> str:
    """The web app as a page inside Orca's tab: ``url`` with ``host=orca`` and,
    when given, the bridge ``key`` in the fragment — fragments never reach a
    server or a ``Referer``, so only the page the plugin opened learns it."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "host"] + [("host", "orca")]
    fragment = f"{KEY_FRAGMENT}={key}" if key else ""
    return urlunsplit(parts._replace(query=urlencode(query), fragment=fragment))


class Launcher:
    """Knows the Takumion targets: the desktop app, the web app in the
    browser, and the web app inside Orca's own tab."""

    def __init__(self, web_url: str = WEB_URL, opener: Callable[[str], None] = open_url) -> None:
        self.web_url = checked_web_url(web_url)
        self.targets = {"web": self.web_url, "desktop": DESKTOP_URL}
        self._opener = opener

    def here_url(self, key: str = "") -> str:
        """Where *Open Takumion › In this tab* navigates the tab, carrying the bridge ``key``."""
        return hosted_url(self.web_url, key)

    def open(self, target: str) -> str:
        """Open ``"web"`` or ``"desktop"``; returns the URL opened."""
        url = self.targets.get(target)
        if url is None:
            raise ValueError(f"unknown Takumion target {target!r}")
        self._opener(url)
        return url
