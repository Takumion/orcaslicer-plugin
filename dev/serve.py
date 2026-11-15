#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Run the plugin without OrcaSlicer: the tab page in a normal browser and the
real loopback link server, against a simulated plate.

    python dev/serve.py            # page on http://127.0.0.1:8766/

- The page gets a ``window.orca`` shim (POST /bridge, Server-Sent Events on
  /events), so it runs unchanged.
- The link server listens on the first free protocol port (47390–47394), so a
  Takumion editor can pair with it and send to it.
- "Orca" is simulated: a handed-off file lands on the plate after 0.3 s
  (``--orca dialog`` never lands it, ``--orca project`` opens it as the project).
  GET /plate shows the simulated plate; POST /plate/reload?path=… simulates
  *Reload from disk* (send it with ``Content-Type: application/json``; the
  server takes JSON posts only, and only with a 127.0.0.1/localhost Host).
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from link.app import LinkApp  # noqa: E402
from link.handoff import Handoff  # noqa: E402
from link.launcher import Launcher  # noqa: E402
from link.plate import PlateObject  # noqa: E402
from link.store import same_path  # noqa: E402

SHIM = """<script>
(function () {
  var handlers = [];
  window.orca = {
    postMessage: function (data) { fetch('/bridge', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)}); },
    onMessage: function (cb) { handlers.push(cb); }
  };
  new EventSource('/events').onmessage = function (e) {
    var data = JSON.parse(e.data);
    handlers.forEach(function (h) { h(data); });
  };
})();
</script>"""


class SimulatedOrca:
    """Lands handed-off files on a pretend plate, like Orca's loader would."""

    def __init__(self, behaviour: str) -> None:
        self.behaviour = behaviour
        self.objects: list[PlateObject] = []
        self.project = ""
        self.app: LinkApp | None = None
        self._lock = threading.Lock()

    def deliver(self, message: str, data_dir: Path) -> None:
        path = message.split(";", 1)[1].strip('"')
        print(f"[orca] hand-off {path}")
        if self.behaviour != "dialog":
            threading.Timer(0.3, self._land, args=(path,)).start()

    def _land(self, path: str) -> None:
        with self._lock:
            self.objects.append(PlateObject(len(self.objects) + 1, Path(path).stem, path, (12.0, float(Path(path).stat().st_size))))
            if self.behaviour == "project":
                self.project = path
            self._refresh("ObjectAdded")

    def reload(self, path: str) -> None:
        with self._lock:
            size = float(Path(path).stat().st_size)
            self.objects = [PlateObject(o.object_id, o.name, o.input_file, (12.0, size + 0.5))
                            if same_path(o.input_file, path) else o for o in self.objects]
            self._refresh("ObjectChanged")

    def _refresh(self, event: str) -> None:
        if self.app:
            self.app.plate.refresh(self.objects, self.project, event)


class PrintNotifier:
    """Orca's notifications, printed."""

    def notify(self, level: str, text: str) -> None:
        print(f"[orca {level}] {text}")


class Events:
    """Fans posted messages out to every open /events stream."""

    def __init__(self) -> None:
        self._queues: list[queue.Queue] = []
        self._lock = threading.Lock()

    def post(self, message: dict) -> None:
        with self._lock:
            for q in self._queues:
                q.put(message)

    def stream(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._queues.append(q)
        return q

    def drop(self, q: queue.Queue) -> None:
        with self._lock:
            self._queues.remove(q)


def page_handler(app: LinkApp, events: Events, orca: SimulatedOrca) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            pass

        def _send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _host_ok(self) -> bool:
            # Same rule as the link server: no DNS-rebinding page may read /events (it carries the code).
            port = self.server.server_address[1]
            return self.headers.get("Host", "") in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def do_GET(self):  # noqa: N802
            if not self._host_ok():
                self._send(421, b"bad host", "text/plain")
                return
            route = urlsplit(self.path).path
            if route == "/":
                self._send(200, app.page(SHIM).encode(), "text/html; charset=utf-8")
            elif route == "/plate":
                plate = [o.__dict__ for o in orca.objects]
                self._send(200, json.dumps({"objects": plate, "project": orca.project}, indent=1).encode(), "application/json")
            elif route == "/events":
                self._events()
            else:
                self._send(404, b"not found", "text/plain")

        def _events(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            q = events.stream()
            app.tab.push()
            try:
                while True:
                    self.wfile.write(f"data: {json.dumps(q.get())}\n\n".encode())
                    self.wfile.flush()
            except OSError:
                events.drop(q)

        def do_POST(self):  # noqa: N802
            # JSON only: a cross-site form or text/plain fetch cannot reach /bridge without a preflight this server never answers.
            if not self._host_ok() or not self.headers.get("Content-Type", "").startswith("application/json"):
                self._send(403, b"refused", "text/plain")
                return
            parts = urlsplit(self.path)
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if parts.path == "/bridge":
                app.on_page_message(raw.decode())
            elif parts.path == "/plate/reload":
                orca.reload(parse_qs(parts.query)["path"][0])
            self._send(204, b"", "text/plain")

    return Handler


def main(argv: list[str]) -> int:
    """Run until Ctrl+C."""
    parser = argparse.ArgumentParser(description="Run Takumion Link without OrcaSlicer.")
    parser.add_argument("--port", type=int, default=8766, help="port of the tab page")
    parser.add_argument("--data", type=Path, default=REPO / "dev" / ".data", help="stands in for Orca's data folder")
    parser.add_argument("--orca", choices=("add", "dialog", "project"), default="add", help="how the pretend Orca reacts")
    args = parser.parse_args(argv)

    orca = SimulatedOrca(args.orca)
    events = Events()
    app = LinkApp(args.data, notifier=PrintNotifier(),
                  post=events.post, handoff=Handoff(args.data, orca.deliver),
                  launcher=Launcher(opener=lambda url: print(f"[open] {url}")),
                  log=lambda line: print(f"[link] {line}"))
    orca.app = app
    app.service.orca_version = "dev"
    app.plate.refresh([], "", "Start")
    app.start()
    page = ThreadingHTTPServer(("127.0.0.1", args.port), page_handler(app, events, orca))
    page.daemon_threads = True
    print(f"tab page: http://127.0.0.1:{args.port}/   link server: 127.0.0.1:{app.server.port}")
    try:
        page.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
