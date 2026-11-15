"""Linux hand-off: Orca's ``AnotherInstance`` D-Bus method
(``InstanceCheck.cpp``, ``com.orcaslicer.OrcaSlicer.InstanceCheck.Object<hash>``).

The ``<hash>`` is Orca's hash of its executable path, which a plugin cannot
compute, so the name is found on the bus; when several Orcas run, the one
whose lock file is newest is preferred."""

from __future__ import annotations

import socket
from pathlib import Path

from .dbus import BusError, SessionBus, session_bus_address
from .handoff import HandoffUnavailable, instance_hashes

NAME_PREFIX = "com.orcaslicer.OrcaSlicer.InstanceCheck.Object"
PATH_PREFIX = "/com/orcaslicer/OrcaSlicer/InstanceCheck/Object"
METHOD = "AnotherInstance"


def pick_name(names: list[str], hashes: list[str]) -> str | None:
    """Orca's bus name: the one matching the newest instance hash, else the
    first Orca name in sorted order, else ``None``."""
    orca = sorted(n for n in names if n.startswith(NAME_PREFIX))
    for digest in hashes:
        if NAME_PREFIX + digest in orca:
            return NAME_PREFIX + digest
    return orca[0] if orca else None


def deliver(message: str, data_dir: Path, address: str | None = None) -> None:
    """Send ``message`` to Orca's ``AnotherInstance`` and flush it through the bus."""
    try:
        with SessionBus.connect(address or session_bus_address()) as bus:
            name = pick_name(bus.list_names(), instance_hashes(data_dir))
            if name is None:
                raise HandoffUnavailable("OrcaSlicer's single-instance name is not on the session bus yet.")
            suffix = name[len(NAME_PREFIX):]
            bus.send_no_reply(destination=name, path=PATH_PREFIX + suffix, interface=name,
                              member=METHOD, args=(message,))
            bus.flush()
    except BusError as exc:
        raise HandoffUnavailable(f"D-Bus: {exc}") from exc


def touch_bus(address: str | None = None) -> None:
    """Open and close the session-bus socket once, on Orca's UI thread.

    Orca checks the grants a plugin asks for at load by host name, and a socket
    path has none, so asking for the bus there prompts on every start. Touched
    here instead, Orca asks inline the first time only and remembers the exact
    path; the hand-off's later connects (worker threads) then match it."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(2)
        sock.connect(address or session_bus_address())
    finally:
        sock.close()
