"""Putting a file on Orca's plate.

Orca's plugin API cannot add objects, so the plugin hands the file to Orca the
way a second Orca process would: through Orca's single-instance message
(``OtherInstanceMessageHandler``), which calls Orca's own loader. The message
is Orca's ``escape_strings_cstyle([exe, file])``; Orca skips the first entry.

One OS-specific deliver function per platform (``DELIVERERS``). Rules:

- **one file per message** — several files in one message become ONE object
  with several parts;
- the receiver may not exist yet right after Orca starts, so ``Handoff``
  retries until it does;
- worker threads only: delivering waits on Orca's UI thread.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path

DeliverFn = Callable[[str, Path], None]

RETRY_FOR_S = 10.0
RETRY_EVERY_S = 0.5
_FIRST_ARG = "takumion-link"


class HandoffUnavailable(Exception):
    """Orca's single-instance receiver cannot be reached (yet)."""


def escape_strings_cstyle(values: list[str]) -> str:
    """Orca's ``escape_strings_cstyle`` (libslic3r/Config.cpp): ``;``-joined,
    a value quoted when it holds a space, tab, ``;``, ``\\``, ``"``, CR or LF."""
    specials = set(' \t;\\"\r\n')
    out = []
    for value in values:
        quote = (len(values) == 1 and not value) or any(c in specials for c in value)
        if not quote:
            out.append(value)
            continue
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r").replace("\n", "\\n")
        out.append(f'"{escaped}"')
    return ";".join(out)


def orca_message(path: str) -> str:
    """The single-instance message that loads one file."""
    return escape_strings_cstyle([_FIRST_ARG, path])


def instance_hashes(data_dir: Path) -> list[str]:
    """Orca's instance hashes, newest first, from the lock files Orca keeps in
    ``{data_dir}/cache/<hash>.lock`` while it runs."""
    try:
        locks = sorted((data_dir / "cache").glob("*.lock"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    return [lock.stem for lock in locks if lock.stem.isdigit()]


def _unsupported(message: str, data_dir: Path) -> None:
    raise HandoffUnavailable(f"Handing files to Orca is not supported on {sys.platform}.")


def _deliverer(platform: str) -> DeliverFn:
    if platform.startswith("linux"):
        from .handoff_linux import deliver
    elif platform == "win32":
        from .handoff_windows import deliver
    elif platform == "darwin":
        from .handoff_macos import deliver
    else:
        return _unsupported
    return deliver


class Handoff:
    """Hands one file at a time to the running Orca, retrying while its
    receiver is missing."""

    def __init__(self, data_dir: Path, deliver: DeliverFn | None = None, *,
                 retry_for: float = RETRY_FOR_S, retry_every: float = RETRY_EVERY_S,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._data_dir = data_dir
        self._deliver = deliver or _deliverer(sys.platform)
        self._retry_for = retry_for
        self._retry_every = retry_every
        self._sleep = sleep

    def deliver(self, path: str) -> None:
        """Put ``path`` on the plate, or raise ``HandoffUnavailable`` once the
        receiver stayed unreachable for the whole retry window."""
        deadline = time.monotonic() + self._retry_for
        message = orca_message(path)
        while True:
            try:
                self._deliver(message, self._data_dir)
                return
            except (HandoffUnavailable, OSError) as exc:
                if time.monotonic() >= deadline:
                    raise HandoffUnavailable(str(exc)) from exc
            self._sleep(self._retry_every)
