# /// script
# requires-python = ">=3.12"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# name = "Takumion Link"
# description = "Send bodies from Takumion to the plate over a local link; Update keeps them linked for Reload from disk, Add puts a new copy."
# author = "Takumion"
# version = "0.1.0"
# ///
# SPDX-License-Identifier: AGPL-3.0-only
"""Takumion Link for OrcaSlicer 2.5+.

Registers one Pages tab ("Takumion"). The tab's capability owns a ``LinkApp``:
a loopback server Takumion pairs with and sends to, the links between bodies
and plate files, and the hand-off that puts files on the plate. Protocol:
``docs/slicer-link-protocol.md``.
"""

import json
import os
import sys
import time
import traceback
from typing import Any

from link.app import LinkApp
from link.handoff_linux import touch_bus
from link.launcher import WEB_URL
from link.location import saved_folder
from link.orca_host import OrcaNotifier, data_dir_for, event_name, orca_version, refresh_plate
from link.page import render_page
from link.protocol import LOOPBACK, PORTS

try:
    import orca  # type: ignore[import-not-found]  # provided by OrcaSlicer's embedded Python
except ImportError:  # imported outside Orca (tests, tools)
    orca = None

TAB_NAME = "Takumion"


def log(message: str) -> None:
    """One line in Orca's log."""
    print(f"[takumion-link] {message}", flush=True)


def permissions(folder: Any = None) -> dict[str, Any]:
    """What the plugin asks for once, at load: the loopback ports it may
    listen on, threads, and the folder the user chose for Takumion's files
    (the whole folder, ``<folder>/*``: Orca grants a folder only at load, so a
    newly chosen one is asked for at the next start). The Linux session bus is
    not asked for here (see ``touch_bus``): Orca would ask about a socket path
    again on every start."""
    wanted: dict[str, Any] = {"network_socket": [repr((LOOPBACK, port)) for port in PORTS], "threading": True}
    if folder is not None:
        wanted["fs_readwrite"] = [os.path.join(str(folder), "*")]
    return wanted


if orca is not None and hasattr(orca, "pages"):

    class TakumionTab(orca.pages.PagesPluginCapabilityBase):
        """The "Takumion" tab: status, pairing, links, 3MF setup."""

        app: LinkApp | None = None
        bus_touched = False
        last_message_sync = 0.0

        def get_name(self) -> str:
            return TAB_NAME

        def get_icon(self) -> str:
            return ""

        def get_default_config(self) -> dict[str, str]:
            """Orca's Config tab: which Takumion *Open Takumion* opens, and
            whether the tab opens it by itself ("false" keeps the plugin's page)."""
            return {"takumionUrl": WEB_URL, "openTakumionInTab": "true"}

        def _config(self) -> dict[str, Any]:
            try:
                config = json.loads(self.get_config() or "{}")
            except (ValueError, AttributeError):
                return {}
            return config if isinstance(config, dict) else {}

        def _takumion_url(self) -> str:
            return str(self._config().get("takumionUrl") or WEB_URL)

        def _open_takumion_in_tab(self) -> bool:
            return str(self._config().get("openTakumionInTab", "true")).strip().lower() not in ("false", "0", "no", "off")

        def on_load(self) -> None:
            try:
                self.app = LinkApp(data_dir_for(__file__), notifier=OrcaNotifier(orca, log),
                                   post=self.post_message, web_url=self._takumion_url(), log=log,
                                   panel_host=self._panel_host())
                self.app.start()
            except Exception:  # noqa: BLE001 - never take Orca down
                log("load failed:\n" + traceback.format_exc())

        def on_unload(self) -> None:
            if self.app is not None:
                self.app.stop()
                self.app = None

        def on_lifecycle_event(self, event: Any, context: Any) -> None:
            self._sync(event_name(event))

        def get_ui(self) -> str:
            self._sync("PageOpened")
            # The nonce makes the plugin's own page the only one that can drive the tab.
            # The tab shows Takumion by default; the plugin's page stays one click away
            # (Takumion › Settings › Slicer › Back to Takumion Link, which loads /tab).
            if self.app is None:
                return render_page()
            return self.app.page(open_takumion=self._open_takumion_in_tab())

        def _panel_host(self) -> Any:
            """A modeless Orca window for the plugin's page, where this Orca has
            plugin windows. Not a docked panel: those live only on Prepare and
            Preview, and a plugin cannot switch tabs, so one opened from the
            Takumion tab would stay out of sight."""
            ui = getattr(getattr(orca, "host", None), "ui", None)
            if ui is None or not hasattr(ui, "create_window"):
                return None

            def open_panel(html: str, on_message: Any, on_close: Any) -> Any:
                def message(data: Any) -> None:
                    self._sync_throttled()
                    try:
                        on_message(data)
                    except Exception:  # noqa: BLE001
                        log("panel message failed:\n" + traceback.format_exc())

                return ui.create_window(html, title="Takumion Link", width=860, height=700, on_message=message,
                                        on_close=on_close, style=getattr(ui, "WINDOW_MODELESS", 0))

            return open_panel

        def _sync_throttled(self) -> None:
            """At most one plate read a second, however fast a page posts (UI thread)."""
            now = time.monotonic()
            if now - self.last_message_sync >= 1.0:
                self.last_message_sync = now
                self._sync("PageMessage")

        def on_message(self, message: str) -> None:
            if self.app is None:
                return
            self._sync_throttled()
            try:
                self.app.on_page_message(message)
            except Exception:  # noqa: BLE001
                log("page message failed:\n" + traceback.format_exc())

        def _sync(self, event: str) -> None:
            """UI thread: copy the plate and Orca's version for the server thread."""
            if self.app is None:
                return
            self._touch_bus_once()
            try:
                if self.app.service.orca_version == "unknown":
                    self.app.service.orca_version = orca_version(orca)
                refresh_plate(orca, self.app.plate, event)
            except Exception:  # noqa: BLE001
                log(f"plate refresh after {event} failed:\n" + traceback.format_exc())

        def _touch_bus_once(self) -> None:
            """UI thread, Linux: let Orca ask about the session bus inline, once."""
            if self.bus_touched or not sys.platform.startswith("linux"):
                return
            self.bus_touched = True
            try:
                touch_bus()
            except Exception as exc:  # noqa: BLE001 - a refusal shows up when a send needs the bus
                log(f"session bus not reachable: {exc}")

    @orca.plugin
    class TakumionLink(orca.base):
        """The plugin package: asks for its permissions and registers the tab."""

        def register_capabilities(self) -> None:
            orca.request_permissions(**permissions(saved_folder(data_dir_for(__file__))))
            orca.register_capability(TakumionTab)

elif orca is not None:
    log("this OrcaSlicer has no Pages plugin API (orca.pages); Takumion Link needs OrcaSlicer 2.5 or later.")
