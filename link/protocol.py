"""Protocol v1 constants, the typed error every endpoint answers with, and
validation of incoming messages (docs/slicer-link-protocol.md, "Messages")."""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Any

APP = "takumion-orca-link"
PROTOCOL = 1
PORTS = (47390, 47391, 47392, 47393, 47394)
LOOPBACK = "127.0.0.1"
MAX_BODY_BYTES = 384 * 1024 * 1024
HANDOFF_WAIT_S = 5.0
MODES = ("update", "add")
FORMATS = ("3mf", "stl")

_KEY = re.compile(r"[A-Za-z0-9_-]{1,128}")

_STATUS = {
    "bad-request": 400,
    "unauthorized": 401,
    "forbidden": 403,
    "too-large": 413,
    "bad-host": 421,
    "internal": 500,
    "orca-busy": 503,
}

UNPAIRED_MESSAGE = "Pair this app in Orca's Takumion tab first."


class LinkError(Exception):
    """A refusal with a protocol error code; the transport turns it into the
    HTTP status of the errors table or a bridge ``error`` reply."""

    def __init__(self, code: str, message: str) -> None:
        if code not in _STATUS:
            raise ValueError(f"unknown protocol error code {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def status(self) -> int:
        """The HTTP status the errors table gives this code."""
        return _STATUS[self.code]

    def to_json(self) -> dict[str, Any]:
        """The error body: ``{"error": {"code", "message"}}``."""
        return {"error": {"code": self.code, "message": self.message}}


@dataclass(frozen=True)
class Part:
    """One body of a send: its key, display name and decoded file bytes."""

    body_key: str
    name: str
    data: bytes


@dataclass(frozen=True)
class SendRequest:
    """A validated ``POST /v1/send``."""

    doc_key: str
    doc_name: str
    mode: str
    format: str
    parts: tuple[Part, ...]


def check_key(value: Any, what: str) -> str:
    """Return ``value`` when it is a protocol key (``[A-Za-z0-9_-]{1,128}``),
    else refuse with ``bad-request``."""
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise LinkError("bad-request", f"{what} must match [A-Za-z0-9_-]{{1,128}}.")
    return value


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise LinkError("bad-request", f"{what} must be a string.")
    return value


def _choice(value: Any, allowed: tuple[str, ...], what: str) -> str:
    if value not in allowed:
        raise LinkError("bad-request", f"{what} must be one of {', '.join(allowed)}.")
    return value


def _part(raw: Any) -> Part:
    if not isinstance(raw, dict):
        raise LinkError("bad-request", "Each part must be an object.")
    try:
        data = base64.b64decode(_text(raw.get("data"), "part.data"), validate=True)
    except binascii.Error as exc:
        raise LinkError("bad-request", f"part.data is not base64: {exc}") from None
    if not data:
        raise LinkError("bad-request", "part.data is empty.")
    return Part(
        body_key=check_key(raw.get("bodyKey"), "part.bodyKey"),
        name=_text(raw.get("name"), "part.name"),
        data=data,
    )


def parse_send(body: Any) -> SendRequest:
    """Validate a send body and decode its parts; any flaw is ``bad-request``."""
    if not isinstance(body, dict):
        raise LinkError("bad-request", "The body must be a JSON object.")
    doc = body.get("doc")
    if not isinstance(doc, dict):
        raise LinkError("bad-request", "doc must be an object.")
    raw_parts = body.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
        raise LinkError("bad-request", "parts must be a non-empty list.")
    parts = tuple(_part(raw) for raw in raw_parts)
    if len({p.body_key for p in parts}) != len(parts):
        raise LinkError("bad-request", "Each bodyKey may appear once per send.")
    return SendRequest(
        doc_key=check_key(doc.get("key"), "doc.key"),
        doc_name=_text(doc.get("name", ""), "doc.name"),
        mode=_choice(body.get("mode"), MODES, "mode"),
        format=_choice(body.get("format"), FORMATS, "format"),
        parts=parts,
    )


def parse_pair(body: Any) -> tuple[str, str]:
    """Validate a pair body; returns ``(code, clientName)``."""
    if not isinstance(body, dict):
        raise LinkError("bad-request", "The body must be a JSON object.")
    code = _text(body.get("code"), "code").strip()
    client = _text(body.get("clientName", "Unnamed app"), "clientName").strip()
    return code, client[:80] or "Unnamed app"
