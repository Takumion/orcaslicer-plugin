"""Windows hand-off: ``WM_COPYDATA`` (``dwData = 1``, UTF-16 text) to Orca's
main window, the way ``InstanceCheck.cpp`` does it from a second Orca.

The plugin runs inside Orca, so the target is this process's own top-level
window that carries Orca's ``Instance_Hash_Minor`` property.

Not yet checked on a real Windows Orca; the message format follows Orca's
``GUI_App.cpp`` handler (``handle_message(narrow((LPCWSTR) lpData))``)."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from .handoff import HandoffUnavailable

WM_COPYDATA = 0x004A
_HASH_PROP = "Instance_Hash_Minor"


class COPYDATASTRUCT(ctypes.Structure):
    """Win32 ``COPYDATASTRUCT``."""

    _fields_ = [("dwData", ctypes.c_size_t), ("cbData", wintypes.DWORD), ("lpData", ctypes.c_void_p)]


def _orca_window(user32: ctypes.WinDLL) -> int | None:
    found: list[int] = []
    pid = os.getpid()
    owner = wintypes.DWORD()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd: int, _lparam: int) -> bool:
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.GetPropW(hwnd, _HASH_PROP):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(visit, 0)
    return found[0] if found else None


def deliver(message: str, data_dir: Path) -> None:
    """Send ``message`` to Orca's main window with ``WM_COPYDATA``."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetPropW.restype = wintypes.HANDLE
    user32.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    hwnd = _orca_window(user32)
    if hwnd is None:
        raise HandoffUnavailable("OrcaSlicer's main window is not up yet.")
    text = ctypes.create_unicode_buffer(message)
    data = COPYDATASTRUCT(1, ctypes.sizeof(text), ctypes.cast(text, ctypes.c_void_p))
    user32.SendMessageW(hwnd, WM_COPYDATA, 0, ctypes.addressof(data))
