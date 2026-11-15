"""The ``window.orca`` bridge transport (docs/slicer-link-protocol.md, "Bridge
transport"): Takumion running inside the plugin's own Orca tab sends
``{"type": "takumion-link", "id", "method", "params"}`` and gets the reply on
``window.orca.onMessage``. No pairing: the tab only ever shows Takumion."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from .protocol import LinkError, parse_send
from .service import LinkService

BRIDGE_TYPE = "takumion-link"

Post = Callable[[dict[str, Any]], None]


class Bridge:
    """Answers bridge messages; ``send`` runs on a worker thread because it
    waits for Orca's UI thread to show the new objects."""

    def __init__(self, service: LinkService, post: Post) -> None:
        self._service = service
        self._post = post
        self._methods: dict[str, Callable[[Any], dict[str, Any]]] = {
            "hello": lambda params: service.hello(True),
            "links": lambda params: service.links((params or {}).get("doc")),
            "send": lambda params: service.send(parse_send(params)),
        }

    @staticmethod
    def handles(message: Any) -> bool:
        """Whether ``message`` is a bridge call."""
        return isinstance(message, dict) and message.get("type") == BRIDGE_TYPE

    def handle(self, message: dict[str, Any]) -> None:
        """Answer one call (UI thread); never blocks on the plate."""
        if message.get("method") == "send":
            threading.Thread(target=self._answer, args=(message,), name="takumion-link-bridge", daemon=True).start()
        else:
            self._answer(message)

    def _answer(self, message: dict[str, Any]) -> None:
        reply: dict[str, Any] = {"type": BRIDGE_TYPE, "id": message.get("id")}
        try:
            method = self._methods.get(str(message.get("method")))
            if method is None:
                raise LinkError("bad-request", f"Unknown method {message.get('method')!r}.")
            reply["result"] = method(message.get("params"))
        except LinkError as error:
            reply["error"] = error.to_json()["error"]
        except Exception as exc:  # noqa: BLE001 - the page must always get an answer
            reply["error"] = {"code": "internal", "message": f"{type(exc).__name__}: {exc}"}
        self._post(reply)
