"""The plugin's Orca tab: what it shows (``state``) and what its buttons do.

Page → plugin: ``{"type": "tab", "action": …}``. ``refresh`` comes every 3 s
from the page: Orca's *Reload from disk* fires no event, so the page's own
message is the UI-thread moment that re-reads the plate.
Plugin → page: ``{"type": "tab-state", "state": {…}}``, pushed after every change."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .launcher import Launcher
from .pairing import PairingBook
from .server import LinkServer
from .service import LinkService

TAB_TYPE = "tab"
STATE_TYPE = "tab-state"

Post = Callable[[dict[str, Any]], None]


class TabController:
    """Turns tab button presses into pairing / launcher / service calls."""

    def __init__(self, *, service: LinkService, pairing: PairingBook, server: LinkServer,
                 launcher: Launcher, post: Post, log: Callable[[str], None] = print) -> None:
        self._service = service
        self._pairing = pairing
        self._server = server
        self._launcher = launcher
        self._post = post
        self._log = log
        self._notice = ""
        self._actions: dict[str, Callable[[dict[str, Any]], None]] = {
            "ready": lambda msg: None,
            "refresh": lambda msg: None,
            "pair": lambda msg: self._pairing.start_code(),
            "cancel-pair": lambda msg: self._pairing.cancel_code(),
            "revoke": lambda msg: self._pairing.revoke_id(str(msg.get("id", ""))),
            "open-takumion": self._open,
            "dismiss-setup": lambda msg: self._service.dismiss_setup_card(),
        }
        pairing.subscribe(self.push)
        service.subscribe(self.push)

    @staticmethod
    def handles(message: Any) -> bool:
        """Whether ``message`` comes from the tab's own page."""
        return isinstance(message, dict) and message.get("type") == TAB_TYPE

    def handle(self, message: dict[str, Any]) -> None:
        """Run one button press, then push the new state."""
        action = self._actions.get(str(message.get("action")))
        if action is None:
            self._log(f"tab: unknown action {message.get('action')!r}")
            return
        action(message)
        if message.get("action") != "refresh":
            self.push()

    def _open(self, message: dict[str, Any]) -> None:
        try:
            self._notice = f"Opened {self._launcher.open(str(message.get('target', 'web')))}"
        except (OSError, ValueError) as exc:
            self._notice = f"Could not open Takumion: {exc}"

    def state(self) -> dict[str, Any]:
        """Everything the tab shows."""
        code = self._pairing.live_code()
        return {
            "plugin": self._service.plugin_version,
            "orca": self._service.orca_version,
            "server": {"port": self._server.port, "error": self._server.error},
            "code": {"value": code[0], "secondsLeft": round(code[1])} if code else None,
            "pairings": self._pairing.pairings(),
            "docs": self._service.docs(),
            "setup": self._service.setup(),
            "plateSeen": self._service.plate.seen,
            "notice": self._notice,
            "takumionHere": self._launcher.here_url,
            "takumionDev": self._launcher.dev_targets,
        }

    def push(self) -> None:
        """Send the current state to the page (any thread)."""
        try:
            self._post({"type": STATE_TYPE, "state": self.state()})
        except Exception as exc:  # noqa: BLE001 - a closed tab must not break a send
            self._log(f"tab: could not push state: {exc}")
