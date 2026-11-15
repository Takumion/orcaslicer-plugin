"""The protocol's behaviour behind both transports (loopback HTTP and the
``window.orca`` bridge): ``hello``, ``send`` and ``links``, plus the 3MF guards
and the *reload needed* bookkeeping (docs/slicer-link-protocol.md)."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .atomic import read_json, write_bytes_atomic, write_json_atomic
from .handoff import Handoff, HandoffUnavailable
from .plate import ARRIVED, OPENED_AS_PROJECT, TIMED_OUT, PlateWatch
from .protocol import APP, HANDOFF_WAIT_S, PROTOCOL, LinkError, Part, SendRequest, check_key
from .store import Link, LinkStore

PROJECT_GRACE_S = 0.75
SETUP_HINT = "Set Preferences › Load behavior to \"Load geometry only\", so a 3MF from Takumion is added to the plate."


class Notifier(Protocol):
    """Where the plugin tells the user something inside Orca."""

    def notify(self, level: str, text: str) -> None:
        """Show ``text``; ``level`` is ``"info"`` or ``"warning"``."""


@dataclass
class _Handed:
    part: Part
    path: Path
    result: dict[str, Any] | None = None


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class LinkService:
    """Sends bodies to the plate and keeps their links; thread-safe."""

    def __init__(self, *, store: LinkStore, plate: PlateWatch, handoff: Handoff, notifier: Notifier,
                 plugin_version: str, wait_s: float = HANDOFF_WAIT_S,
                 now: Callable[[], datetime] = lambda: datetime.now()) -> None:
        self.store = store
        self.plate = plate
        self._handoff = handoff
        self._notifier = notifier
        self.plugin_version = plugin_version
        self.orca_version = "unknown"
        self._wait_s = wait_s
        self._now = now
        self._send_lock = threading.Lock()
        self._state_file = store.root / "state.json"
        state = read_json(self._state_file, {})
        self._setup = state.get("loadGeometryOnly", "unknown") if isinstance(state, dict) else "unknown"
        self._card_dismissed = bool(state.get("setupCardDismissed")) if isinstance(state, dict) else False
        self._listeners: list[Callable[[], None]] = []
        plate.subscribe(self._on_plate)

    # ---------------------------------------------------------------- state

    def subscribe(self, listener: Callable[[], None]) -> None:
        """Call ``listener`` after anything the Orca tab shows changed."""
        self._listeners.append(listener)

    def _changed(self) -> None:
        for listener in list(self._listeners):
            listener()

    def _save_state(self) -> None:
        write_json_atomic(self._state_file, {"loadGeometryOnly": self._setup,
                                             "setupCardDismissed": self._card_dismissed})

    def _set_setup(self, value: str) -> None:
        if value == self._setup:
            return
        self._setup = value
        if value == "missing":
            self._card_dismissed = False
        self._save_state()

    def setup(self) -> dict[str, Any]:
        """What the tab's 3MF setup card needs. Takumion sends STL by default,
        so the card appears only once a 3MF failed to land as plain geometry."""
        return {"loadGeometryOnly": self._setup,
                "showCard": self._setup == "missing" and not self._card_dismissed}

    def dismiss_setup_card(self) -> None:
        """The user closed the setup card; it returns if a 3MF fails to land."""
        self._card_dismissed = True
        self._save_state()
        self._changed()

    # ------------------------------------------------------------- messages

    def hello(self, paired: bool) -> dict[str, Any]:
        """``GET /v1/hello``."""
        return {
            "app": APP,
            "protocol": PROTOCOL,
            "plugin": {"version": self.plugin_version},
            "orca": {"version": self.orca_version},
            "paired": paired,
            "setup": {"loadGeometryOnly": self._setup},
        }

    def links(self, doc_key: Any) -> dict[str, Any]:
        """``GET /v1/links?doc=<key>``."""
        key = check_key(doc_key, "doc")
        return {"doc": {"key": key, "name": self.store.doc_name(key)},
                "bodies": [self._body_view(link) for link in self.store.bodies(key)]}

    def docs(self) -> list[dict[str, Any]]:
        """Every linked document with its bodies, for the Orca tab."""
        keys = dict.fromkeys(link.doc_key for link in self.store.all_links())
        return [self.links(key) | {"key": key} for key in keys]

    def _body_view(self, link: Link) -> dict[str, Any]:
        return {"bodyKey": link.body_key, "name": link.name, "format": link.format,
                "lastSent": link.last_sent, "onPlate": self.plate.on_plate(link.path),
                "reloadNeeded": link.reload_needed}

    def send(self, request: SendRequest) -> dict[str, Any]:
        """``POST /v1/send``: write every part, hand the new ones to Orca one
        file per message, then wait (shared 5 s) to see them arrive."""
        with self._send_lock:
            handed: list[_Handed] = []
            results: dict[str, dict[str, Any]] = {}
            for part in request.parts:
                try:
                    outcome = self._prepare(request, part)
                except OSError as exc:
                    outcome = _failed(part, f"Could not write the file: {exc}")
                if isinstance(outcome, _Handed):
                    handed.append(outcome)
                else:
                    results[part.body_key] = outcome
            needs_setup = self._hand_off(request, handed, results)
        self._changed()
        return {"results": [results[p.body_key] for p in request.parts], "needsOrcaSetup": needs_setup}

    # ----------------------------------------------------------------- send

    def _prepare(self, request: SendRequest, part: Part) -> dict[str, Any] | _Handed:
        """Write one part; return its finished result, or what still needs handing off."""
        if request.mode == "add":
            path = self.store.copy_path(request.doc_key, part.name, request.format, self._now())
            write_bytes_atomic(path, part.data)
            return _Handed(part, path)
        return self._prepare_update(request, part)

    def _prepare_update(self, request: SendRequest, part: Part) -> dict[str, Any] | _Handed:
        link = self.store.get(request.doc_key, part.body_key)
        if link is not None and link.format != request.format:
            link = None  # a different file type needs its own file
        path = Path(link.path) if link else self.store.link_path(request.doc_key, part.body_key, part.name, request.format)
        digest = hashlib.sha256(part.data).hexdigest()
        changed = link is None or link.sha256 != digest or not path.exists()
        if changed:
            write_bytes_atomic(path, part.data)
        on_plate = self.plate.find(str(path))
        if link is None or on_plate is None:
            fresh = Link(request.doc_key, part.body_key, part.name, request.format, str(path), digest, _iso_now())
            self.store.put(fresh, request.doc_name)
            return _Handed(part, path)
        reload_needed = link.reload_needed or changed
        sig = on_plate.mesh_sig if changed else link.mesh_sig
        self.store.put(Link(link.doc_key, link.body_key, link.name, link.format, link.path, digest,
                            _iso_now(), reload_needed, sig), request.doc_name)
        if changed:
            self._notifier.notify("info", f"Takumion: {part.name} updated — right-click it › Reload from disk.")
        return {"bodyKey": part.body_key, "action": "updated", "onPlate": None, "reloadNeeded": reload_needed}

    def _hand_off(self, request: SendRequest, handed: list[_Handed], results: dict[str, dict[str, Any]]) -> bool:
        """Deliver each file, wait for the plate, and fill ``results``; returns
        ``needsOrcaSetup``."""
        delivered: list[_Handed] = []
        for item in handed:
            try:
                self._handoff.deliver(str(item.path))
                delivered.append(item)
            except HandoffUnavailable as exc:
                if not delivered and not results:
                    raise LinkError("orca-busy", f"OrcaSlicer is not ready to take files yet: {exc}") from exc
                results[item.part.body_key] = _failed(item.part, f"OrcaSlicer did not take the file: {exc}")
        deadline = time.monotonic() + self._wait_s
        outcomes = {item.part.body_key: self.plate.wait_for(str(item.path), deadline) for item in delivered}
        if request.format == "3mf" and delivered:
            outcomes = self._project_grace(delivered, outcomes)
        for item in delivered:
            outcome = outcomes[item.part.body_key]
            results[item.part.body_key] = {"bodyKey": item.part.body_key, "action": "added",
                                           "onPlate": outcome != TIMED_OUT, "reloadNeeded": False}
        return self._judge_3mf(request, delivered, outcomes)

    def _project_grace(self, delivered: list[_Handed], outcomes: dict[str, str]) -> dict[str, str]:
        """A 3MF opened as the project can show up as an added object before
        Orca names the project; look again for a moment."""
        grace = time.monotonic() + PROJECT_GRACE_S
        out = dict(outcomes)
        for item in delivered:
            if out[item.part.body_key] == ARRIVED and self.plate.wait_project(str(item.path), grace):
                out[item.part.body_key] = OPENED_AS_PROJECT
        return out

    def _judge_3mf(self, request: SendRequest, delivered: list[_Handed], outcomes: dict[str, str]) -> bool:
        if request.format != "3mf" or not delivered:
            return False
        if all(outcomes[i.part.body_key] == ARRIVED for i in delivered):
            self._set_setup("confirmed")
            return False
        self._set_setup("missing")
        self._notifier.notify("warning", f"Takumion: a 3MF did not land on the plate as plain geometry. {SETUP_HINT}")
        return True

    # ---------------------------------------------------------------- plate

    def _on_plate(self, event: str) -> None:
        """UI thread, after each plate refresh: run the project guard and
        clear *reload needed* where Orca now shows new geometry."""
        self._guard_project(event)
        for link in self.store.all_links():
            if not link.reload_needed:
                continue
            seen = self.plate.find(link.path)
            if seen is not None and seen.mesh_sig != link.mesh_sig:
                self.store.update(link, reload_needed=False, mesh_sig=seen.mesh_sig)
        self._changed()

    def _guard_project(self, event: str) -> None:
        project = self.plate.project_path
        if not project:
            return
        dropped = self.store.drop_path(project)
        if dropped:
            self._set_setup("missing")
            names = ", ".join(link.name for link in dropped)
            self._notifier.notify("warning", f"Takumion: Orca opened {names} as its project. Use File › Save Project As "
                                             f"to keep it; the next send from Takumion writes a new file. {SETUP_HINT}")
        elif event == "ProjectBeforeSave" and _inside(project, self.store.root):
            self._notifier.notify("warning", "Takumion: this project is a Takumion link file. Use File › Save Project As "
                                             "so the next send does not overwrite it.")


def _failed(part: Part, message: str) -> dict[str, Any]:
    return {"bodyKey": part.body_key, "action": "failed", "onPlate": False, "reloadNeeded": False, "message": message}


def _inside(path: str, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True
