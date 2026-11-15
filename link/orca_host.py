"""Adapters from Orca's ``orca`` module to what the link modules need.

The ``orca`` module is passed in, never imported here, so tests run these
against a fake. Everything that reads Orca's model or app info runs on the UI
thread (lifecycle events, ``get_ui``, page messages)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .plate import PlateWatch, read_plate

_LEVELS = {"info": "RegularNotificationLevel", "warning": "WarningNotificationLevel"}


class OrcaNotifier:
    """``Notifier`` over Orca's plater notifications."""

    def __init__(self, orca: Any, log: Any = print) -> None:
        self._ui = orca.host.ui
        self._log = log

    def notify(self, level: str, text: str) -> None:
        """Push a plater notification; a closing Orca only gets a log line."""
        try:
            kind = getattr(self._ui.NotificationLevel, _LEVELS.get(level, _LEVELS["info"]))
            self._ui.push_notification(kind, text)
        except Exception as exc:  # noqa: BLE001 - a notice must never fail a send
            self._log(f"notification failed ({exc}): {text}")


def refresh_plate(orca: Any, plate: PlateWatch, event: str) -> None:
    """Copy Orca's plate and project path into ``plate``. UI thread only."""
    plater = orca.host.plater()
    plate.refresh(read_plate(plater.model()), str(plater.project_path() or ""), event)


def orca_version(orca: Any) -> str:
    """Orca's version string. UI thread only."""
    return str(orca.host.app_info().get("version", "unknown"))


def event_name(event: Any) -> str:
    """``orca.LifecycleEvent.ObjectAdded`` → ``"ObjectAdded"``."""
    return str(getattr(event, "name", event)).rsplit(".", 1)[-1]


def data_dir_for(plugin_file: str) -> Path:
    """Orca's data folder from the installed plugin's path
    (``{data_dir}/orca_plugins/…/<file>``)."""
    path = Path(plugin_file).resolve()
    for parent in path.parents:
        if parent.name == "orca_plugins":
            return parent.parent
    raise RuntimeError(f"{path} is not inside Orca's orca_plugins folder")
