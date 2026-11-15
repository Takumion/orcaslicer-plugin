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

WEB_URL = "https://app.takumion.com/"
DESKTOP_URL = "takumion://open"


def open_url(url: str) -> None:
    """Hand ``url`` to the operating system's default handler."""
    if sys.platform == "win32":
        os.startfile(url)  # type: ignore[attr-defined]  # noqa: S606 - fixed URL
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,  # noqa: S603
                     stderr=subprocess.DEVNULL, start_new_session=True)


class Launcher:
    """Knows the two Takumion targets and opens one."""

    def __init__(self, web_url: str = WEB_URL, opener: Callable[[str], None] = open_url) -> None:
        self.targets = {"web": web_url, "desktop": DESKTOP_URL}
        self._opener = opener

    def open(self, target: str) -> str:
        """Open ``"web"`` or ``"desktop"``; returns the URL opened."""
        url = self.targets.get(target)
        if url is None:
            raise ValueError(f"unknown Takumion target {target!r}")
        self._opener(url)
        return url
