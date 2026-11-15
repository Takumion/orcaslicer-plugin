"""Builds the plugin's parts from Orca's data folder and wires them together.

Orca-free: the entry file (``takumion_link.py``) gives it a notifier, a way to
post to the tab and, in tests or ``dev/serve.py``, fake hand-off and plate."""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import __version__
from .bridge import Bridge
from .handoff import Handoff
from .launcher import WEB_URL, Launcher
from .location import FilesLocation
from .page import render_page
from .pairing import PairingBook
from .plate import PlateWatch
from .protocol import HANDOFF_WAIT_S, LOOPBACK, PORTS
from .server import LinkServer
from .service import LinkService, Notifier
from .store import LinkStore
from .tab import TabController

Post = Callable[[dict[str, Any]], None]
# Opens the plugin's page in its own Orca window: (html, on_message, on_close) -> a handle
# with post / is_open / close (and show, where the host has it).
PanelHost = Callable[[str, Callable[[Any], None], Callable[[], None]], Any]


class LinkApp:
    """Owns every part for one plugin load; ``start`` / ``stop`` follow Orca's
    ``on_load`` / ``on_unload``."""

    def __init__(self, data_dir: Path, *, notifier: Notifier, post: Post, handoff: Handoff | None = None,
                 launcher: Launcher | None = None, ports: tuple[int, ...] = PORTS,
                 wait_s: float = HANDOFF_WAIT_S, web_url: str = WEB_URL,
                 log: Callable[[str], None] = print, panel_host: PanelHost | None = None) -> None:
        root = data_dir / "takumion"
        self.log = log
        self._panel_host = panel_host
        self._panel: Any = None
        self.store = LinkStore(root)
        self.pairing = PairingBook(root / "pairings.json")
        self.plate = PlateWatch()
        self.location = FilesLocation(data_dir)
        self.service = LinkService(store=self.store, plate=self.plate, handoff=handoff or Handoff(data_dir),
                                   notifier=notifier, plugin_version=__version__, location=self.location, wait_s=wait_s)
        bridge_key = secrets.token_urlsafe(24)
        self.server = LinkServer(self.service, self.pairing, ports, log, page=self.page)
        self.bridge = Bridge(self.service, post, self.tab_url, key=bridge_key, log=log, show_panel=self.show_panel)
        self.tab = TabController(service=self.service, pairing=self.pairing, server=self.server,
                                 launcher=launcher or Launcher(web_url), post=post, bridge_key=bridge_key, log=log,
                                 location=self.location)

    def page(self, head_extra: str = "", open_takumion: bool = False, panel: bool = False) -> str:
        """The tab's page, carrying this load's tab nonce; ``open_takumion``
        makes it go on to Takumion in the tab (the first page Orca shows),
        ``panel`` renders it for the docked panel."""
        return render_page(head_extra, nonce=self.tab.nonce, open_takumion=open_takumion, panel=panel)

    def show_panel(self) -> bool:
        """Open the plugin's page in its own Orca window (UI thread), so
        Takumion stays loaded in the tab; one already open is kept (and shown,
        where the host can). False where Orca has no plugin windows."""
        if self._panel_host is None:
            return False
        if self._panel is not None and self._panel.is_open():
            show = getattr(self._panel, "show", None)
            if callable(show):
                show()
            return True
        self._panel = self._panel_host(self.page(panel=True), self.on_panel_message, self._panel_closed)
        self.tab.attach_panel(self._panel.post)
        return True

    def _panel_closed(self) -> None:
        self._panel = None
        self.tab.detach_panel()

    def on_panel_message(self, raw: Any) -> None:
        """A message from the plugin window's page (UI thread): its buttons only."""
        message = json.loads(raw) if isinstance(raw, str) else raw
        if self.tab.handles(message):
            self.tab.handle(message, source="panel")
        else:
            self.log(f"panel: ignored message {str(message)[:200]}")

    def tab_url(self) -> str | None:
        """The tab page on the link server, while it runs."""
        port = self.server.port
        return f"http://{LOOPBACK}:{port}/tab" if port else None

    def start(self) -> None:
        """Start listening; a failure is shown in the tab, not raised into Orca."""
        try:
            self.server.start()
        except OSError as exc:
            self.log(f"server not started: {exc}")
        self.tab.push()

    def stop(self) -> None:
        """Stop listening, close any open pairing code and the panel."""
        self.server.stop()
        self.pairing.cancel_code()
        panel, self._panel = self._panel, None
        self.tab.detach_panel()
        if panel is not None and panel.is_open():
            panel.close()

    def on_page_message(self, raw: Any) -> None:
        """A message from the tab page (UI thread): the tab's own buttons, or
        Takumion's bridge calls once the tab shows Takumion."""
        message = json.loads(raw) if isinstance(raw, str) else raw
        if self.bridge.handles(message):
            self.bridge.handle(message)
        elif self.tab.handles(message):
            self.tab.handle(message)
        else:
            self.log(f"page: ignored message {str(message)[:200]}")
