"""The loopback HTTP transport (docs/slicer-link-protocol.md, "Discovery",
"Security", "Messages"): ``127.0.0.1`` only, first free port of 47390–47394,
``Host`` check, pairing tokens bound to ``Origin``, CORS for paired origins
and Chrome's private-network preflight."""

from __future__ import annotations

import json
import socket
import socketserver
import sys
import threading
import traceback
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .pairing import PairingBook
from .protocol import LOOPBACK, MAX_BODY_BYTES, PORTS, UNPAIRED_MESSAGE, LinkError, parse_pair, parse_send
from .service import LinkService

_OPEN_PATHS = frozenset({"/v1/hello", "/v1/pair"})
_ALLOW_HEADERS = "Authorization, Content-Type"
_ALLOW_METHODS = "GET, POST, DELETE, OPTIONS"


class _LoopbackHTTPServer(ThreadingHTTPServer):
    """A threading server that never resolves host names (Orca's sandbox would
    ask the user about ``gethostbyaddr``) and, on Windows, owns its port
    exclusively."""

    daemon_threads = True
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self) -> None:
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = str(host), int(port)


class LinkServer:
    """Owns the listening socket and its thread: ``start`` in Orca's
    ``on_load``, ``stop`` in ``on_unload``."""

    def __init__(self, service: LinkService, pairing: PairingBook, ports: tuple[int, ...] = PORTS,
                 log: Callable[[str], None] = print) -> None:
        self.service = service
        self.pairing = pairing
        self._ports = ports
        self._log = log
        self._httpd: _LoopbackHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    @property
    def port(self) -> int | None:
        """The port being served, or ``None`` while stopped."""
        return self._httpd.server_port if self._httpd else None

    def start(self) -> int:
        """Bind the first free port and serve on a daemon thread; returns the port."""
        if self._httpd:
            return self._httpd.server_port
        handler = _make_handler(self)
        for port in self._ports:
            try:
                httpd = _LoopbackHTTPServer((LOOPBACK, port), handler)
            except OSError as exc:
                self._log(f"port {port} busy: {exc}")
                continue
            self._httpd = httpd
            self._thread = threading.Thread(target=httpd.serve_forever, name="takumion-link-http", daemon=True)
            self._thread.start()
            self.error = None
            self._log(f"listening on {LOOPBACK}:{port}")
            return port
        self.error = f"Ports {self._ports[0]}–{self._ports[-1]} are all in use."
        raise OSError(self.error)

    def stop(self) -> None:
        """Stop serving and release the port."""
        httpd, self._httpd = self._httpd, None
        if httpd is None:
            return
        httpd.shutdown()
        httpd.server_close()
        if self._thread:
            self._thread.join(timeout=5)
        self._log("server stopped")


def _make_handler(server: LinkServer) -> type[BaseHTTPRequestHandler]:
    class Handler(_LinkHandler):
        link = server
    return Handler


class _LinkHandler(BaseHTTPRequestHandler):
    """One request: host check → preflight → route → auth → body → reply."""

    link: LinkServer
    protocol_version = "HTTP/1.1"
    server_version = "TakumionLink"
    timeout = 60

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        self.link._log("http " + format % args)

    # -------------------------------------------------------------- helpers

    @property
    def _origin(self) -> str | None:
        return self.headers.get("Origin") or None

    @property
    def _route(self) -> str:
        return urlsplit(self.path).path

    def _host_ok(self) -> bool:
        port = self.link.port
        return self.headers.get("Host", "") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _cors_origin(self, status: int = 200) -> str | None:
        """The origin to echo: any origin for ``hello`` / ``pair``, for a
        preflight and for an error reply (a refusal reveals nothing, and the
        client must be able to read "pair again"); a paired origin otherwise."""
        origin = self._origin
        if not origin:
            return None
        if self._route in _OPEN_PATHS or status >= 400 or self.command == "OPTIONS":
            return origin
        return origin if self.link.pairing.is_paired_origin(origin) else None

    def _token(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        return auth[7:].strip() if auth.startswith("Bearer ") else None

    def _authorized(self) -> bool:
        return self.link.pairing.check(self._token(), self._origin)

    def _reply(self, status: int, body: Any | None = None, extra: dict[str, str] | None = None) -> None:
        data = b"" if body is None else json.dumps(body).encode("utf-8")
        self.send_response(status)
        allow = self._cors_origin(status)
        if allow:
            self.send_header("Access-Control-Allow-Origin", allow)
        self.send_header("Vary", "Origin")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        if body is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, error: LinkError) -> None:
        # A refused request's body may be unread; never parse it as the next request.
        self.close_connection = True
        self._reply(error.status, error.to_json(), {"Connection": "close"})

    def _read_json(self) -> Any:
        length_header = self.headers.get("Content-Length")
        if length_header is None or not length_header.isdigit():
            raise LinkError("bad-request", "Content-Length is required.")
        length = int(length_header)
        if length > MAX_BODY_BYTES:
            raise LinkError("too-large", "The request is larger than 384 MB.")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, ValueError) as exc:
            raise LinkError("bad-request", f"The body is not JSON: {exc}") from None

    # ------------------------------------------------------------ endpoints

    def _hello(self) -> None:
        self._reply(200, self.link.service.hello(self._authorized()))

    def _pair(self) -> None:
        code, client = parse_pair(self._read_json())
        self._reply(200, {"token": self.link.pairing.pair(code, self._origin, client)})

    def _unpair(self) -> None:
        self.link.pairing.revoke_token(self._token() or "")
        self._reply(204)

    def _send(self) -> None:
        self._reply(200, self.link.service.send(parse_send(self._read_json())))

    def _links(self) -> None:
        doc = parse_qs(urlsplit(self.path).query).get("doc", [None])[0]
        self._reply(200, self.link.service.links(doc))

    _ROUTES: dict[tuple[str, str], Callable[[_LinkHandler], None]] = {
        ("GET", "/v1/hello"): _hello,
        ("POST", "/v1/pair"): _pair,
        ("DELETE", "/v1/pair"): _unpair,
        ("POST", "/v1/send"): _send,
        ("GET", "/v1/links"): _links,
    }

    # -------------------------------------------------------------- methods

    def _handle(self, method: str) -> None:
        try:
            if not self._host_ok():
                raise LinkError("bad-host", "Only 127.0.0.1 or localhost may be used as Host.")
            route = self._ROUTES.get((method, self._route))
            if route is None:
                self._reply(404, {"error": {"code": "bad-request", "message": f"No {method} {self._route}."}})
                return
            if self._route not in _OPEN_PATHS and not self._authorized():
                raise LinkError("unauthorized", UNPAIRED_MESSAGE)
            route(self)
        except LinkError as error:
            self._error(error)
        except Exception as exc:  # noqa: BLE001 - every failure must reach the client as `internal`
            self.link._log("internal error: " + traceback.format_exc())
            self._error(LinkError("internal", f"{type(exc).__name__}: {exc}"))

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._error(LinkError("bad-host", "Only 127.0.0.1 or localhost may be used as Host."))
            return
        extra = {}
        if self._cors_origin():
            extra = {"Access-Control-Allow-Methods": _ALLOW_METHODS,
                     "Access-Control-Allow-Headers": _ALLOW_HEADERS,
                     "Access-Control-Max-Age": "600"}
            if self.headers.get("Access-Control-Request-Private-Network"):
                extra["Access-Control-Allow-Private-Network"] = "true"
        self._reply(204, extra=extra)
