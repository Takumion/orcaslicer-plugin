"""The ``window.orca`` bridge transport (docs/slicer-link-protocol.md, "Bridge
transport"): Takumion running inside the plugin's own Orca tab sends
``{"type": "takumion-link", "id", "key", "method", "params"}`` and gets the
reply on ``window.orca.onMessage``. No pairing, but a ``key``: Orca loads any
page into the tab with ``window.orca`` (a link Takumion opens, a sign-in
redirect), so a call counts only when it carries the key the plugin put in the
URL fragment of *Open Takumion › In this tab* — and only that page learned it."""

from __future__ import annotations

import hmac
import threading
import traceback
from collections.abc import Callable
from typing import Any

from .protocol import LinkError, parse_send
from .service import LinkService

BRIDGE_TYPE = "takumion-link"

Post = Callable[[dict[str, Any]], None]


class Bridge:
    """Answers bridge messages; ``send`` runs on a worker thread because it
    waits for Orca's UI thread to show the new objects."""

    def __init__(self, service: LinkService, post: Post, tab_url: Callable[[], str | None] = lambda: None,
                 key: str = "", log: Callable[[str], None] = print,
                 show_panel: Callable[[], bool] = lambda: False) -> None:
        self._service = service
        self._post = post
        self._tab_url = tab_url
        self._key = key
        self._log = log
        self._sending = threading.Lock()
        self._methods: dict[str, Callable[[Any], dict[str, Any]]] = {
            "hello": lambda params: self._hello(),
            "links": lambda params: service.links((params or {}).get("doc")),
            "send": lambda params: service.send(parse_send(params)),
            # Bridge only: open the plugin's page in its own Orca window, Takumion stays in the tab.
            "show-panel": lambda params: {"shown": show_panel()},
        }

    def _hello(self) -> dict[str, Any]:
        """``hello`` plus, when the link server runs, where the tab's own page
        is served — Takumion navigates there to get back to it."""
        url = self._tab_url()
        return self._service.hello(True) | ({"tab": {"url": url}} if url else {})

    @staticmethod
    def handles(message: Any) -> bool:
        """Whether ``message`` is a bridge call."""
        return isinstance(message, dict) and message.get("type") == BRIDGE_TYPE

    def handle(self, message: dict[str, Any]) -> None:
        """Answer one call (UI thread); never blocks on the plate. One send
        at a time: a second one while the first runs is ``orca-busy``."""
        if not self._key or not hmac.compare_digest(str(message.get("key", "")).encode(), self._key.encode()):
            self._refuse(message, LinkError("forbidden", "This page was not opened by the Takumion Link plugin."))
            return
        if message.get("method") != "send":
            self._answer(message)
            return
        if not self._sending.acquire(blocking=False):
            self._refuse(message, LinkError("orca-busy", "A send to OrcaSlicer is still running; try again when it ends."))
            return
        threading.Thread(target=self._send_then_release, args=(message,), name="takumion-link-bridge", daemon=True).start()

    def _send_then_release(self, message: dict[str, Any]) -> None:
        try:
            self._answer(message)
        finally:
            self._sending.release()

    def _refuse(self, message: dict[str, Any], error: LinkError) -> None:
        self._post({"type": BRIDGE_TYPE, "id": message.get("id"), "error": error.to_json()["error"]})

    def _answer(self, message: dict[str, Any]) -> None:
        reply: dict[str, Any] = {"type": BRIDGE_TYPE, "id": message.get("id")}
        try:
            method = self._methods.get(str(message.get("method")))
            if method is None:
                raise LinkError("bad-request", f"Unknown method {message.get('method')!r}.")
            reply["result"] = method(message.get("params"))
        except LinkError as error:
            reply["error"] = error.to_json()["error"]
        except Exception:  # noqa: BLE001 - the page must always get an answer; details stay in Orca's log
            self._log("bridge: internal error:\n" + traceback.format_exc())
            reply["error"] = {"code": "internal", "message": "The Takumion Link plugin failed; OrcaSlicer's log has the details."}
        self._post(reply)
