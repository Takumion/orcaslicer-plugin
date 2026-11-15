"""Pairing: one-time codes shown in the Orca tab, and the tokens they buy
(docs/slicer-link-protocol.md, "Security").

- A code exists only after the user clicks *Pair an app*: 6 digits, 120 s,
  dead after 5 wrong tries, used once.
- A token is 32 random bytes (base64url). Only its SHA-256 is kept, with the
  origin it is bound to, the client's name, and created / last-used times.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from .atomic import read_json, write_json_atomic
from .protocol import LinkError

CODE_TTL_S = 120.0
CODE_TRIES = 5
_LAST_USED_SAVE_S = 60.0


def bound_origin(origin: str | None, client_name: str) -> str:
    """The origin a token is bound to: the request's ``Origin``, or
    ``native:<clientName>`` for a client that sends none."""
    return origin if origin else f"native:{client_name}"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", "replace")).hexdigest()


def _iso(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


@dataclass
class Pairing:
    """A stored pairing; ``token_hash`` doubles as its id in the tab."""

    token_hash: str
    origin: str
    client_name: str
    created: float
    last_used: float

    def public(self) -> dict[str, str]:
        """What the Orca tab shows: no hash, only a short id to revoke by."""
        return {
            "id": self.token_hash[:16],
            "origin": self.origin,
            "clientName": self.client_name,
            "created": _iso(self.created),
            "lastUsed": _iso(self.last_used),
        }


@dataclass
class _Code:
    value: str
    expires: float
    tries_left: int


class PairingBook:
    """Owns the live pairing code and the stored pairings (``pairings.json``)."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._code: _Code | None = None
        self._pairings: dict[str, Pairing] = self._load()
        self._saved_at = clock()
        self._listeners: list[Callable[[], None]] = []

    def _load(self) -> dict[str, Pairing]:
        raw = read_json(self._path, [])
        records = raw if isinstance(raw, list) else []
        out: dict[str, Pairing] = {}
        for record in records:
            try:
                pairing = Pairing(**record)
            except TypeError:
                continue
            out[pairing.token_hash] = pairing
        return out

    def _save(self) -> None:
        write_json_atomic(self._path, [asdict(p) for p in self._pairings.values()])
        self._saved_at = self._clock()

    def subscribe(self, listener: Callable[[], None]) -> None:
        """Call ``listener`` after any change the tab should show."""
        self._listeners.append(listener)

    def _changed(self) -> None:
        for listener in list(self._listeners):
            listener()

    def start_code(self) -> str:
        """*Pair an app*: a new code replaces any live one."""
        with self._lock:
            value = f"{secrets.randbelow(1_000_000):06d}"
            self._code = _Code(value, self._clock() + CODE_TTL_S, CODE_TRIES)
        self._changed()
        return value

    def cancel_code(self) -> None:
        """Drop the live code, if any."""
        with self._lock:
            self._code = None
        self._changed()

    def live_code(self) -> tuple[str, float] | None:
        """The live code and its seconds left, or ``None``."""
        with self._lock:
            code = self._code
            if code is None:
                return None
            left = code.expires - self._clock()
            if left <= 0:
                self._code = None
                return None
            return code.value, left

    def pair(self, code: str, origin: str | None, client_name: str) -> str:
        """Trade a code for a token bound to ``origin``; a wrong, expired or
        missing code is ``forbidden`` and costs one of the code's tries."""
        with self._lock:
            live = self._code
            if live is None or live.expires <= self._clock():
                self._code = None
                raise LinkError("forbidden", "No pairing code is open, or it expired. Click Pair an app in Orca's Takumion tab.")
            if not hmac.compare_digest(live.value.encode(), code.encode("ascii", "replace")):
                live.tries_left -= 1
                if live.tries_left <= 0:
                    self._code = None
                raise LinkError("forbidden", "Wrong pairing code.")
            self._code = None
            token = secrets.token_urlsafe(32)
            now = self._clock()
            pairing = Pairing(_hash(token), bound_origin(origin, client_name), client_name, now, now)
            self._pairings[pairing.token_hash] = pairing
            self._save()
        self._changed()
        return token

    def check(self, token: str | None, origin: str | None) -> bool:
        """Whether ``token`` is valid for this request's origin. A request with
        no ``Origin`` may only use a ``native:`` token."""
        if not token:
            return False
        with self._lock:
            pairing = self._pairings.get(_hash(token))
            if pairing is None or not _origin_matches(pairing.origin, origin):
                return False
            pairing.last_used = self._clock()
            if pairing.last_used - self._saved_at > _LAST_USED_SAVE_S:
                self._save()
        return True

    def is_paired_origin(self, origin: str | None) -> bool:
        """Whether any token is bound to this browser origin (for CORS)."""
        if not origin:
            return False
        with self._lock:
            return any(p.origin == origin for p in self._pairings.values())

    def revoke_token(self, token: str) -> None:
        """``DELETE /v1/pair``: the caller drops its own token."""
        self._revoke(lambda p: p.token_hash == _hash(token))

    def revoke_id(self, pairing_id: str) -> None:
        """The tab's *Revoke*: drop the pairing whose public id is given."""
        if pairing_id:
            self._revoke(lambda p: p.token_hash.startswith(pairing_id))

    def _revoke(self, match: Callable[[Pairing], bool]) -> None:
        with self._lock:
            gone = [key for key, pairing in self._pairings.items() if match(pairing)]
            for key in gone:
                del self._pairings[key]
            if gone:
                self._save()
        if gone:
            self._changed()

    def pairings(self) -> list[dict[str, str]]:
        """Every pairing, oldest first, as the tab shows it."""
        with self._lock:
            ordered = sorted(self._pairings.values(), key=lambda p: p.created)
        return [p.public() for p in ordered]


def _origin_matches(bound: str, origin: str | None) -> bool:
    if origin:
        return bound == origin
    return bound.startswith("native:")
