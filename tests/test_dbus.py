"""The D-Bus client against a small fake session bus on a UNIX socket."""

import socket
import struct
import threading

import pytest

from link.dbus import decode_string_array, encode_call, reply_serial, session_bus_address
from link.handoff import orca_message
from link.handoff_linux import NAME_PREFIX, deliver
from link.handoff import HandoffUnavailable


def _fields(raw: bytes) -> dict[int, object]:
    out, i = {}, 0
    while i < len(raw):
        i += -i % 8
        code, sig_len = raw[i], raw[i + 1]
        sig = raw[i + 2:i + 2 + sig_len].decode()
        j = i + 3 + sig_len
        if sig == "u":
            j += -j % 4
            out[code] = struct.unpack_from("<I", raw, j)[0]
            j += 4
        elif sig in "so":
            j += -j % 4
            n = struct.unpack_from("<I", raw, j)[0]
            out[code] = raw[j + 4:j + 4 + n].decode()
            j += 4 + n + 1
        else:
            n = raw[j]
            out[code] = raw[j + 1:j + 1 + n].decode()
            j += 1 + n + 1
        i = j
    return out


def _string_body(values: list[str]) -> bytes:
    inner = bytearray()
    for v in values:
        inner.extend(b"\0" * (-(len(inner) + 4) % 4))
        data = v.encode()
        inner.extend(struct.pack("<I", len(data)) + data + b"\0")
    return struct.pack("<I", len(inner)) + bytes(inner)


def _reply(serial: int, to: int, body: bytes, sig: str) -> bytes:
    fields = bytearray()
    for code, s, value in ((5, "u", to), (8, "g", sig)) if sig else ((5, "u", to),):
        fields.extend(b"\0" * (-len(fields) % 8))
        fields.extend(bytes([code, 1]) + s.encode() + b"\0")
        if s == "u":
            fields.extend(b"\0" * (-len(fields) % 4))
            fields.extend(struct.pack("<I", value))
        else:
            fields.extend(bytes([len(value)]) + value.encode() + b"\0")
    head = bytearray(b"l\x02\x00\x01") + struct.pack("<III", len(body), serial, len(fields)) + fields
    head.extend(b"\0" * (-len(head) % 8))
    return bytes(head) + body


class FakeBus:
    """Accepts one client at a time and records its calls."""

    def __init__(self, path: str, names: list[str]) -> None:
        self.names = names
        self.calls: list[tuple[str, str | None, int]] = []
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen(4)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                self._session(conn)

    def _recv(self, conn, n):
        data = b""
        while len(data) < n:
            chunk = conn.recv(n - len(data))
            if not chunk:
                raise EOFError
            data += chunk
        return data

    def _session(self, conn) -> None:
        line = b""
        while not line.endswith(b"\r\n"):
            line += conn.recv(1)
        assert line.startswith(b"\0AUTH EXTERNAL ")
        conn.sendall(b"OK 1234deadbeef\r\n")
        assert self._recv(conn, 7) == b"BEGIN\r\n"
        serial = 100
        try:
            while True:
                fixed = self._recv(conn, 16)
                body_len, msg_serial, fields_len = struct.unpack_from("<III", fixed, 4)
                fields = _fields(self._recv(conn, fields_len + (-(16 + fields_len) % 8))[:fields_len])
                body = self._recv(conn, body_len)
                member = fields[3]
                arg = decode_string_array(struct.pack("<I", len(body)) + body)[0] if body else None
                self.calls.append((member, arg, fixed[2]))
                serial += 1
                if member == "ListNames":
                    conn.sendall(_reply(serial, msg_serial, _string_body(self.names), "as"))
                elif member in ("Hello", "GetId"):
                    conn.sendall(_reply(serial, msg_serial, _string_body([":1.42"])[4:], "s"))
        except EOFError:
            return

    def close(self) -> None:
        self.server.close()


@pytest.fixture
def bus(tmp_path):
    fake = FakeBus(str(tmp_path / "bus"), ["org.freedesktop.DBus", NAME_PREFIX + "7325096244605402762"])
    yield fake, str(tmp_path / "bus")
    fake.close()


def test_one_another_instance_call_then_a_flush(bus, tmp_path):
    fake, address = bus
    deliver(orca_message("/data/takumion/links/p/b/Base.3mf"), tmp_path, address)
    members = [m for m, _, _ in fake.calls]
    assert members == ["Hello", "ListNames", "AnotherInstance", "GetId"]
    member, arg, flags = fake.calls[2]
    assert arg == "takumion-link;/data/takumion/links/p/b/Base.3mf"
    assert flags & 1, "AnotherInstance is sent with NO_REPLY_EXPECTED"


def test_missing_orca_name_is_unavailable(tmp_path):
    fake = FakeBus(str(tmp_path / "bus2"), ["org.freedesktop.DBus"])
    try:
        with pytest.raises(HandoffUnavailable):
            deliver(orca_message("/x.stl"), tmp_path, str(tmp_path / "bus2"))
    finally:
        fake.close()


def test_encoding_basics():
    msg = encode_call(3, destination="d.e", path="/a/b", interface="i.f", member="M", args=("x",), no_reply=True)
    assert msg[:4] == b"l\x01\x01\x01"
    body_len, serial, _ = struct.unpack_from("<III", msg, 4)
    assert (body_len, serial) == (6, 3)
    assert reply_serial(_reply_fields(9)) == 9


def _reply_fields(serial: int) -> bytes:
    raw = _reply(1, serial, b"", "")
    fields_len = struct.unpack_from("<I", raw, 12)[0]
    return raw[16:16 + fields_len]


def test_bus_address_from_environment():
    assert session_bus_address({"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/flatpak/bus"}) == "/run/flatpak/bus"
    assert session_bus_address({"DBUS_SESSION_BUS_ADDRESS": "unix:abstract=/tmp/dbus-x,guid=1"}) == "\0/tmp/dbus-x"
    assert session_bus_address({"DBUS_SESSION_BUS_ADDRESS": "unix:path=/a%20b"}) == "/a b"
