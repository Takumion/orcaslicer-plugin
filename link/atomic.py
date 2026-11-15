"""Crash-safe file writes: a reader (Orca's *Reload from disk*, or the plugin
after a restart) sees the old file or the new one, never half of either."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to a sibling temp file, flush it to disk, then rename it
    over ``path`` in one step."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_json_atomic(path: Path, value: Any) -> None:
    """Atomically store ``value`` as pretty, UTF-8 JSON."""
    text = json.dumps(value, indent=1, ensure_ascii=False, sort_keys=True)
    write_bytes_atomic(path, text.encode("utf-8"))


def read_json(path: Path, default: Any) -> Any:
    """Load JSON from ``path``; a missing file gives ``default``. A corrupt file
    is moved aside (``.corrupt``) so the next write does not lose the evidence."""
    try:
        with open(path, "rb") as handle:
            return json.loads(handle.read().decode("utf-8"))
    except FileNotFoundError:
        return default
    except (ValueError, UnicodeDecodeError):
        os.replace(path, path.with_name(path.name + ".corrupt"))
        return default
