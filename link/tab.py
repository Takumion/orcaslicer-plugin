"""The plugin's Orca tab: what it shows (``state``) and what its buttons do.

Page → plugin: ``{"type": "tab", "nonce", "action": …}``. ``refresh`` comes
every 3 s from the page: Orca's *Reload from disk* fires no event, so the
page's own message is the UI-thread moment that re-reads the plate.
Plugin → page: ``{"type": "tab-state", "state": {…}}``, pushed after every change.

Orca loads ANY page in this tab with ``window.orca`` — Takumion after *In this
tab*, and whatever a link from there opens — and tells the plugin nothing
about which page sent a message. So the tab's own page carries ``nonce`` (put
in it by ``render_page``): a message without it is ignored, and the state
(which holds the live pairing code) is pushed only while the plugin's own page
is the one showing — from its first message until it says ``leave``.

The same page also runs in its own Orca window (``attach_panel``), so Takumion
can stay in the tab: that window shows the state whenever it is open."""

from __future__ import annotations

import hmac
import secrets
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
                 launcher: Launcher, post: Post, bridge_key: str = "", log: Callable[[str], None] = print) -> None:
        self.nonce = secrets.token_urlsafe(24)
        self._bridge_key = bridge_key
        self._present = False
        self._panel_post: Post | None = None
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
            "leave": self._leave,
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

    def attach_panel(self, post: Post) -> None:
        """The plugin window opened: push the state there too while it is open."""
        self._panel_post = post

    def detach_panel(self) -> None:
        """The plugin window closed."""
        self._panel_post = None

    def handle(self, message: dict[str, Any], source: str = "tab") -> None:
        """Run one button press from the plugin's own page (in the ``"tab"`` or
        the ``"panel"``), then push the new state."""
        if not hmac.compare_digest(str(message.get("nonce", "")).encode(), self.nonce.encode()):
            self._log(f"{source}: ignored a message from a page the plugin did not render")
            return
        if source == "tab":
            self._present = message.get("action") != "leave"
        action = self._actions.get(str(message.get("action")))
        if action is None:
            self._log(f"tab: unknown action {message.get('action')!r}")
            return
        action(message)
        if message.get("action") not in ("refresh", "leave"):
            self.push()

    def _leave(self, message: dict[str, Any]) -> None:
        """The page is about to navigate the tab away (to Takumion): stop pushing state."""

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
            "takumionHere": self._launcher.here_url(self._bridge_key),
        }

    def push(self) -> None:
        """Send the current state (any thread) to the panel while it is open,
        and to the tab only while the plugin's own page shows there: another
        page must never see the code."""
        targets = [post for post in (self._post if self._present else None, self._panel_post) if post is not None]
        if not targets:
            return
        message = {"type": STATE_TYPE, "state": self.state()}
        for post in targets:
            try:
                post(message)
            except Exception as exc:  # noqa: BLE001 - a closed view must not break a send
                self._log(f"tab: could not push state: {exc}")
