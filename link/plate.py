"""What is on Orca's plate, as last seen on the UI thread.

Orca's model may only be read on the UI thread, and the plugin's lifecycle
events are the UI-thread moments it gets. ``PlateWatch.refresh`` copies the
plate into plain values there; the server thread only ever reads that copy
and waits on it (``wait_for``) — it never touches Orca's model.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .store import same_path

ARRIVED = "arrived"
OPENED_AS_PROJECT = "project"
TIMED_OUT = "timeout"


@dataclass(frozen=True)
class PlateObject:
    """One model object: Orca's id, name, source file and mesh signature."""

    object_id: int
    name: str
    input_file: str
    mesh_sig: tuple[float, ...]


def mesh_signature(model_object: Any) -> tuple[float, ...]:
    """Facet count and total volume (mm³) of an Orca ``ModelObject``: changes
    when *Reload from disk* brings new geometry, not when the user moves it."""
    volume = sum(v.volume() for v in model_object.volumes())
    return (float(model_object.facets_count()), round(float(volume), 3))


def read_plate(model: Any) -> list[PlateObject]:
    """Copy an Orca ``Model`` into ``PlateObject`` values. UI thread only."""
    return [
        PlateObject(int(o.id()), str(o.name), str(o.input_file or ""), mesh_signature(o))
        for o in model.objects()
    ]


class PlateWatch:
    """The UI thread's latest copy of the plate, shared with worker threads."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._objects: tuple[PlateObject, ...] = ()
        self._project_path = ""
        self._seen = False
        self._listeners: list[Callable[[str], None]] = []

    def subscribe(self, listener: Callable[[str], None]) -> None:
        """Call ``listener(event_name)`` on the UI thread after each refresh."""
        self._listeners.append(listener)

    def refresh(self, objects: list[PlateObject], project_path: str, event: str) -> None:
        """Replace the copy (UI thread), wake any waiter, then tell listeners."""
        with self._cond:
            self._objects = tuple(objects)
            self._project_path = project_path
            self._seen = True
            self._cond.notify_all()
        for listener in list(self._listeners):
            listener(event)

    @property
    def seen(self) -> bool:
        """Whether the plate has been read at least once since the plugin loaded."""
        with self._cond:
            return self._seen

    @property
    def project_path(self) -> str:
        """Orca's project file as of the last refresh (``""`` if never saved)."""
        with self._cond:
            return self._project_path

    def objects(self) -> tuple[PlateObject, ...]:
        """The plate's objects as of the last refresh."""
        with self._cond:
            return self._objects

    def find(self, path: str) -> PlateObject | None:
        """The first plate object loaded from ``path``, if any."""
        with self._cond:
            return next((o for o in self._objects if same_path(o.input_file, path)), None) if path else None

    def on_plate(self, path: str) -> bool:
        """Whether an object loaded from ``path`` is on the plate."""
        return self.find(path) is not None

    def wait_for(self, path: str, deadline: float) -> str:
        """Block (worker thread) until an object from ``path`` is on the plate
        (``ARRIVED``), Orca opened ``path`` as its project (``OPENED_AS_PROJECT``),
        or ``deadline`` (``time.monotonic``) passes (``TIMED_OUT``)."""
        with self._cond:
            while True:
                if self._project_path and same_path(self._project_path, path):
                    return OPENED_AS_PROJECT
                if any(same_path(o.input_file, path) for o in self._objects):
                    return ARRIVED
                left = deadline - time.monotonic()
                if left <= 0:
                    return TIMED_OUT
                self._cond.wait(left)

    def wait_project(self, path: str, deadline: float) -> bool:
        """Block until Orca's project is ``path`` (``True``) or ``deadline`` passes."""
        with self._cond:
            while not (self._project_path and same_path(self._project_path, path)):
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(left)
            return True
