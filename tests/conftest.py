"""Shared fixtures: free loopback ports, a simulated Orca plate, a service."""

from __future__ import annotations

import socket
import sys
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from link.handoff import Handoff, HandoffUnavailable  # noqa: E402
from link.plate import PlateObject, PlateWatch  # noqa: E402
from link.service import LinkService  # noqa: E402
from link.store import LinkStore, same_path  # noqa: E402


def free_ports(count: int) -> tuple[int, ...]:
    """Ports nothing listens on right now."""
    socks = [socket.socket() for _ in range(count)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = tuple(s.getsockname()[1] for s in socks)
    for s in socks:
        s.close()
    return ports


class Notes:
    """A ``Notifier`` that records."""

    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []

    def notify(self, level: str, text: str) -> None:
        self.items.append((level, text))


class FakeOrca:
    """Simulates Orca receiving hand-offs: each delivered file lands on the
    plate (``ObjectAdded``) shortly after, unless told otherwise."""

    def __init__(self, plate: PlateWatch) -> None:
        self.plate = plate
        self.objects: list[PlateObject] = []
        self.project = ""
        self.delivered: list[str] = []
        self.behaviour = "add"  # "add" | "ignore" (dialog up) | "project" | "unavailable"
        self.delay = 0.05
        self._next_id = 1

    def deliver(self, message: str, data_dir: Path) -> None:
        if self.behaviour == "unavailable":
            raise HandoffUnavailable("no Orca on the bus")
        path = message.split(";", 1)[1].strip('"')
        self.delivered.append(path)
        if self.behaviour == "ignore":
            return
        threading.Timer(self.delay, self._land, args=(path,)).start()

    def _land(self, path: str) -> None:
        self.objects.append(PlateObject(self._next_id, Path(path).stem, path, (12.0, 1000.0)))
        self._next_id += 1
        if self.behaviour == "project":
            self.project = path
            self.plate.refresh(self.objects, "", "ObjectAdded")
            self.plate.refresh(self.objects, self.project, "ProjectOpened")
            return
        self.plate.refresh(self.objects, self.project, "ObjectAdded")

    def reload(self, path: str, sig: tuple[float, ...]) -> None:
        """The user's *Reload from disk*: same object, new mesh."""
        self.objects = [PlateObject(o.object_id, o.name, o.input_file, sig) if same_path(o.input_file, path) else o
                        for o in self.objects]
        self.plate.refresh(self.objects, self.project, "ObjectChanged")

    def delete(self, path: str) -> None:
        self.objects = [o for o in self.objects if not same_path(o.input_file, path)]
        self.plate.refresh(self.objects, self.project, "ObjectDeleted")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "OrcaSlicer"


@pytest.fixture
def world(data_dir: Path):
    """A service wired to a fake Orca."""
    plate = PlateWatch()
    fake = FakeOrca(plate)
    notes = Notes()
    store = LinkStore(data_dir / "takumion")
    handoff = Handoff(data_dir, fake.deliver, retry_for=0.2, retry_every=0.05)
    service = LinkService(store=store, plate=plate, handoff=handoff, notifier=notes, plugin_version="0.1.0",
                          wait_s=1.0, now=lambda: datetime(2026, 11, 15, 10, 50, 30))
    return SimpleNamespace(service=service, store=store, plate=plate, orca=fake, notes=notes)
