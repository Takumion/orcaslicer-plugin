"""Where Takumion's mesh files go: a folder the user chooses once, in the
plugin's page (docs/slicer-link-protocol.md, "Files on disk").

OrcaSlicer's sandbox decides how this works:

- A plugin may ask for a whole folder (``<folder>/*``) only while Orca loads
  it, and Orca remembers the answer. So the folder in use (``active``) is the
  one saved when Orca started; a newly chosen one is used after a restart.
- Creating a folder outside Orca's data folder asks the user every time and is
  never remembered, so the plugin creates the chosen folder once, when it is
  saved, and never makes subfolders in it (flat file names, see ``LinkStore``).

The choice is kept in ``takumion/location.json`` in Orca's data folder.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .atomic import read_json, write_json_atomic

LOCATION_FILE = "location.json"
# Orca's sandbox refuses any path with one of these as a whole component
# (outside its own data folder), so such a folder could never be written.
_ORCA_DENIED_COMPONENTS = frozenset({"secret", "secrets", "cert", "certs", "certificate", "certificates", "conf", "config"})


def location_file(data_dir: Path) -> Path:
    """The file that holds the chosen folder."""
    return data_dir / "takumion" / LOCATION_FILE


def saved_folder(data_dir: Path) -> Path | None:
    """The folder chosen so far, or ``None``. Read at load, before Orca grants anything."""
    raw = read_json(location_file(data_dir), {})
    folder = raw.get("folder") if isinstance(raw, dict) else None
    return Path(folder) if isinstance(folder, str) and os.path.isabs(folder) else None


def suggestions(home: Path) -> list[str]:
    """Folders offered next to the field, most likely first."""
    return [str(home / "Documents" / "Takumion"), str(home / "Desktop" / "Takumion"), str(home / "Takumion")]


def normalize_folder(raw: object, home: Path) -> Path:
    """The folder the user typed, as an absolute path; ``~`` is the home
    folder. Raises ``ValueError`` with a message for the user."""
    text = str(raw or "").strip()
    if not text:
        raise ValueError("Type a folder, or pick one of the suggestions.")
    if text == "~" or text.startswith(("~/", "~\\")):
        text = str(home) + text[1:]
    path = Path(text)
    if not path.is_absolute():
        raise ValueError("Use a full path, like " + suggestions(home)[0] + ".")
    path = Path(os.path.normpath(path))
    if path == Path(path.anchor):
        raise ValueError("Choose a folder, not the top of the disk.")
    denied = next((part for part in path.parts if part.lower() in _ORCA_DENIED_COMPONENTS), None)
    if denied:
        raise ValueError(f"OrcaSlicer never lets plugins write under a folder named \"{denied}\". Choose another folder.")
    if path.exists() and not path.is_dir():
        raise ValueError(f"{path} is a file, not a folder.")
    return path


class FilesLocation:
    """The chosen folder: ``active`` (allowed when Orca started, used now)
    and ``saved`` (the latest choice, used from the next start)."""

    def __init__(self, data_dir: Path, home: Path | None = None) -> None:
        self._data_dir = data_dir
        self._home = home or Path.home()
        self.active: Path | None = saved_folder(data_dir)
        self.saved: Path | None = self.active

    @property
    def needs_restart(self) -> bool:
        """A folder was chosen that Orca has not allowed yet."""
        return self.saved is not None and self.saved != self.active

    def choose(self, raw: object) -> Path:
        """Save the user's folder (creating it once if missing: Orca asks
        about that one folder). Raises ``ValueError`` for the user."""
        folder = normalize_folder(raw, self._home)
        if not folder.exists():
            folder.mkdir(parents=True)
        write_json_atomic(location_file(self._data_dir), {"folder": str(folder)})
        self.saved = folder
        return folder

    def state(self) -> dict[str, Any]:
        """What the plugin's page shows about the folder."""
        return {
            "active": str(self.active) if self.active else None,
            "saved": str(self.saved) if self.saved else None,
            "needsRestart": self.needs_restart,
            "suggestions": suggestions(self._home),
        }
