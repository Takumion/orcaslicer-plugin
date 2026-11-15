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

import sys
import traceback
from typing import Any

from link.app import LinkApp
from link.handoff_linux import touch_bus
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


def permissions() -> dict[str, Any]:
    """What the plugin asks for once, at load: the loopback ports it may
    listen on, and threads. The Linux session bus is not asked for here (see
    ``touch_bus``): Orca would ask about a socket path again on every start."""
    return {"network_socket": [repr((LOOPBACK, port)) for port in PORTS], "threading": True}


if orca is not None and hasattr(orca, "pages"):

    class TakumionTab(orca.pages.PagesPluginCapabilityBase):
        """The "Takumion" tab: status, pairing, links, 3MF setup."""

        app: LinkApp | None = None
        bus_touched = False

        def get_name(self) -> str:
            return TAB_NAME

        def get_icon(self) -> str:
            return ""

        def on_load(self) -> None:
            try:
                self.app = LinkApp(data_dir_for(__file__), notifier=OrcaNotifier(orca, log),
                                   post=self.post_message, log=log)
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
            return render_page()

        def on_message(self, message: str) -> None:
            if self.app is None:
                return
            self._sync("PageMessage")
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
            orca.request_permissions(**permissions())
            orca.register_capability(TakumionTab)

elif orca is not None:
    log("this OrcaSlicer has no Pages plugin API (orca.pages); Takumion Link needs OrcaSlicer 2.5 or later.")
