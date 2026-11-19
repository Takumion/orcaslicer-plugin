# /// script
# requires-python = ">=3.12"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# name = "Takumion Link"
# description = "Send bodies from Takumion to the plate over a local link; Update keeps them linked for Reload from disk, Add puts a new copy."
# author = "Takumion"
# version = "0.1.2"
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
from link.location import folder_at_load
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
            """Orca's Config tab: which Takumion *Open Takumion* opens, whether
            the tab opens it by itself ("false" keeps the plugin's page), and
            ``filesFolder``, where Takumion's files go (the folder in use, by
            default ``~/Documents/Takumion``; another one is used after a restart)."""
            return {"takumionUrl": WEB_URL, "openTakumionInTab": "true",
                    "filesFolder": str(folder_at_load(data_dir_for(__file__)))}

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
                self._follow_config_folder()
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

        def _follow_config_folder(self) -> None:
            """``filesFolder`` from the Config tab, when edited there, becomes the
            chosen folder; a folder chosen on the plugin's page is written back
            to the Config tab, so both always name the same one."""
            location = self.app.location
            try:
                location.apply(self._config().get("filesFolder"))
            except (ValueError, OSError) as exc:
                log(f"filesFolder in the Config tab is not used: {exc}")
            location.on_choose = self._save_config_folder

        def _save_config_folder(self, folder: Any) -> None:
            try:
                self.save_config(json.dumps({**self._config(), "filesFolder": str(folder)}))
            except Exception:  # noqa: BLE001 - the page's choice stands without it
                log("could not write filesFolder to the Config tab:\n" + traceback.format_exc())

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
            orca.request_permissions(**permissions(folder_at_load(data_dir_for(__file__))))
            orca.register_capability(TakumionTab)

elif orca is not None:
    log("this OrcaSlicer has no Pages plugin API (orca.pages); Takumion Link needs OrcaSlicer 2.5 or later.")
