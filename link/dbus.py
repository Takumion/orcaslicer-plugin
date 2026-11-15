"""A minimal D-Bus client over the session bus socket: just what the Linux
hand-off needs (``Hello``, ``ListNames``, ``GetId`` and one no-reply call with
string arguments), in pure Python so it works inside Orca's embedded
interpreter and its Flatpak sandbox without extra packages.

Wire format: little-endian messages as in the D-Bus specification,
"Message Protocol"."""

from __future__ import annotations

import os
import socket
import struct

_BUS = "org.freedesktop.DBus"
_BUS_PATH = "/org/freedesktop/DBus"
_METHOD_CALL = 1
_METHOD_RETURN = 2
_ERROR = 3
_NO_REPLY_EXPECTED = 0x1


class BusError(Exception):
    """The bus refused, closed, or answered with an error."""


def session_bus_address(environ: dict[str, str] | None = None) -> str:
    """The socket of the session bus: a filesystem path, or ``"\\0name"`` for
    an abstract socket. Inside Flatpak this is the proxy's ``/run/flatpak/bus``."""
    env = os.environ if environ is None else environ
    for entry in env.get("DBUS_SESSION_BUS_ADDRESS", "").split(";"):
        transport, _, params = entry.partition(":")
        if transport != "unix":
            continue
        options = dict(p.split("=", 1) for p in params.split(",") if "=" in p)
        if "path" in options:
            return _unescape_address(options["path"])
        if "abstract" in options:
            return "\0" + _unescape_address(options["abstract"])
    return f"/run/user/{os.getuid()}/bus"


def _unescape_address(value: str) -> str:
    out, i = bytearray(), 0
    raw = value.encode()
    while i < len(raw):
        if raw[i] == ord("%") and i + 2 < len(raw):
            out.append(int(raw[i + 1:i + 3], 16))
            i += 3
        else:
            out.append(raw[i])
            i += 1
    return out.decode()


def _pad(buf: bytearray, align: int) -> None:
    buf.extend(b"\0" * (-len(buf) % align))


def _put_string(buf: bytearray, value: str, signature: str = "s") -> None:
    data = value.encode("utf-8")
    if signature == "g":
        buf.append(len(data))
    else:
        _pad(buf, 4)
        buf.extend(struct.pack("<I", len(data)))
    buf.extend(data + b"\0")


def encode_call(serial: int, *, destination: str, path: str, interface: str, member: str,
                args: tuple[str, ...] = (), no_reply: bool = False) -> bytes:
    """A METHOD_CALL message whose arguments are all strings."""
    body = bytearray()
    for arg in args:
        _put_string(body, arg)
    fields = [(1, "o", path), (2, "s", interface), (3, "s", member), (6, "s", destination)]
    if args:
        fields.append((8, "g", "s" * len(args)))
    header_fields = bytearray()
    for code, sig, value in fields:
        _pad(header_fields, 8)
        header_fields.append(code)
        _put_string(header_fields, sig, "g")
        _put_string(header_fields, value, sig)
    flags = _NO_REPLY_EXPECTED if no_reply else 0
    head = bytearray(b"l" + bytes([_METHOD_CALL, flags, 1]))
    head.extend(struct.pack("<III", len(body), serial, len(header_fields)))
    head.extend(header_fields)
    _pad(head, 8)
    return bytes(head + body)


def _skip_field_value(fields: bytes, sig: str, j: int) -> tuple[int, int | None]:
    """Step over one header-field value; returns (next index, uint32 value)."""
    if sig == "u":
        j += -j % 4
        return j + 4, struct.unpack_from("<I", fields, j)[0]
    if sig in ("s", "o"):
        j += -j % 4
        length = struct.unpack_from("<I", fields, j)[0]
        return j + 4 + length + 1, None
    if sig == "g":
        return j + 1 + fields[j] + 1, None
    raise BusError(f"unexpected header field type {sig!r}")


def reply_serial(fields: bytes) -> int | None:
    """The REPLY_SERIAL header field (code 5) of a message, if present."""
    i, found = 0, None
    while i < len(fields):
        i += -i % 8
        code, sig_len = fields[i], fields[i + 1]
        sig = fields[i + 2:i + 2 + sig_len].decode()
        i, value = _skip_field_value(fields, sig, i + 3 + sig_len)
        if code == 5:
            found = value
    return found


def decode_string_array(body: bytes) -> list[str]:
    """The ``as`` body of a ``ListNames`` reply."""
    total = struct.unpack_from("<I", body, 0)[0]
    out, j = [], 4
    while j < 4 + total:
        j += -j % 4
        length = struct.unpack_from("<I", body, j)[0]
        out.append(body[j + 4:j + 4 + length].decode("utf-8"))
        j += 4 + length + 1
    return out


class SessionBus:
    """One authenticated connection to the session bus; close it when done."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._serial = 0

    @classmethod
    def connect(cls, address: str, timeout: float = 5.0) -> SessionBus:
        """Connect, authenticate (EXTERNAL, our uid) and say ``Hello``."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(address)
            sock.sendall(b"\0AUTH EXTERNAL " + str(os.getuid()).encode().hex().encode() + b"\r\n")
            answer = sock.recv(512)
            if not answer.startswith(b"OK"):
                raise BusError(f"bus refused authentication: {answer!r}")
            sock.sendall(b"BEGIN\r\n")
        except BaseException:
            sock.close()
            raise
        bus = cls(sock)
        bus.call_bus("Hello")
        return bus

    def __enter__(self) -> SessionBus:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the socket."""
        self._sock.close()

    def _next_serial(self) -> int:
        self._serial += 1
        return self._serial

    def call_bus(self, member: str) -> bytes:
        """Call a no-argument method of the bus itself and return the reply body."""
        serial = self._next_serial()
        self._sock.sendall(encode_call(serial, destination=_BUS, path=_BUS_PATH, interface=_BUS, member=member))
        return self._await_reply(serial)

    def list_names(self) -> list[str]:
        """Every name on the bus."""
        return decode_string_array(self.call_bus("ListNames"))

    def send_no_reply(self, *, destination: str, path: str, interface: str, member: str,
                      args: tuple[str, ...]) -> None:
        """A method call that expects no answer (Orca's ``AnotherInstance``)."""
        self._sock.sendall(encode_call(self._next_serial(), destination=destination, path=path,
                                       interface=interface, member=member, args=args, no_reply=True))

    def flush(self) -> None:
        """A round trip (``GetId``) so everything sent before has been taken
        by the bus. Inside Flatpak, closing right after a no-reply call can make
        the bus proxy drop that call."""
        self.call_bus("GetId")

    def _recv_exact(self, count: int) -> bytes:
        chunks, left = [], count
        while left:
            chunk = self._sock.recv(left)
            if not chunk:
                raise BusError("the bus closed the connection")
            chunks.append(chunk)
            left -= len(chunk)
        return b"".join(chunks)

    def _await_reply(self, serial: int) -> bytes:
        while True:
            fixed = self._recv_exact(16)
            if fixed[:1] != b"l":
                raise BusError("big-endian bus messages are not supported")
            mtype = fixed[1]
            body_len, _, fields_len = struct.unpack_from("<III", fixed, 4)
            fields = self._recv_exact(fields_len + (-(16 + fields_len) % 8))[:fields_len]
            body = self._recv_exact(body_len)
            if reply_serial(fields) != serial:
                continue  # a signal or an unrelated message
            if mtype == _ERROR:
                raise BusError(f"bus error replying to call {serial}")
            if mtype == _METHOD_RETURN:
                return body
