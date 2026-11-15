"""Builds the plugin's parts from Orca's data folder and wires them together.

Orca-free: the entry file (``takumion_link.py``) gives it a notifier, a way to
post to the tab and, in tests or ``dev/serve.py``, fake hand-off and plate."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import __version__
from .bridge import Bridge
from .handoff import Handoff
from .launcher import WEB_URL, Launcher
from .pairing import PairingBook
from .plate import PlateWatch
from .protocol import HANDOFF_WAIT_S, LOOPBACK, PORTS
from .server import LinkServer
from .service import LinkService, Notifier
from .store import LinkStore
from .tab import TabController

Post = Callable[[dict[str, Any]], None]


class LinkApp:
    """Owns every part for one plugin load; ``start`` / ``stop`` follow Orca's
    ``on_load`` / ``on_unload``."""

    def __init__(self, data_dir: Path, *, notifier: Notifier, post: Post, handoff: Handoff | None = None,
                 launcher: Launcher | None = None, ports: tuple[int, ...] = PORTS,
                 wait_s: float = HANDOFF_WAIT_S, web_url: str = WEB_URL,
                 log: Callable[[str], None] = print) -> None:
        root = data_dir / "takumion"
        self.log = log
        self.store = LinkStore(root)
        self.pairing = PairingBook(root / "pairings.json")
        self.plate = PlateWatch()
        self.service = LinkService(store=self.store, plate=self.plate, handoff=handoff or Handoff(data_dir),
                                   notifier=notifier, plugin_version=__version__, wait_s=wait_s)
        self.server = LinkServer(self.service, self.pairing, ports, log)
        self.bridge = Bridge(self.service, post, self.tab_url)
        self.tab = TabController(service=self.service, pairing=self.pairing, server=self.server,
                                 launcher=launcher or Launcher(web_url), post=post, log=log)

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
        """Stop listening and close any open pairing code."""
        self.server.stop()
        self.pairing.cancel_code()

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
