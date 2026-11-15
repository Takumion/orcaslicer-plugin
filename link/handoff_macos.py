"""macOS hand-off: the distributed notification
``OtherOrcaSlicerInstanceMessage<hash>`` with ``userInfo = {"data": message}``
(``InstanceCheckMac.mm``). The hash comes from Orca's lock file in
``{data_dir}/cache``.

Not yet checked on a real macOS Orca. Uses the Objective-C runtime through
ctypes, so no PyObjC is needed."""

from __future__ import annotations

import ctypes
from pathlib import Path

from .handoff import HandoffUnavailable, instance_hashes

_OBJC = "/usr/lib/libobjc.A.dylib"
_FOUNDATION = "/System/Library/Frameworks/Foundation.framework/Foundation"
NOTIFICATION_PREFIX = "OtherOrcaSlicerInstanceMessage"


class _ObjC:
    """Just enough of the Objective-C runtime to post one notification."""

    def __init__(self) -> None:
        self.lib = ctypes.cdll.LoadLibrary(_OBJC)
        ctypes.cdll.LoadLibrary(_FOUNDATION)
        self.lib.objc_getClass.restype = ctypes.c_void_p
        self.lib.objc_getClass.argtypes = [ctypes.c_char_p]
        self.lib.sel_registerName.restype = ctypes.c_void_p
        self.lib.sel_registerName.argtypes = [ctypes.c_char_p]
        self._send = ctypes.cast(self.lib.objc_msgSend, ctypes.c_void_p).value

    def send(self, receiver: int, selector: str, *args: tuple[type, object]) -> int:
        """``[receiver selector args…]`` with typed arguments; returns an id."""
        proto = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, *(t for t, _ in args))
        fn = proto(self._send)
        return fn(receiver, self.lib.sel_registerName(selector.encode()), *(v for _, v in args))

    def cls(self, name: str) -> int:
        """An Objective-C class object."""
        return self.lib.objc_getClass(name.encode())

    def string(self, text: str) -> int:
        """An autoreleased ``NSString``."""
        return self.send(self.cls("NSString"), "stringWithUTF8String:", (ctypes.c_char_p, text.encode("utf-8")))


def deliver(message: str, data_dir: Path) -> None:
    """Post Orca's single-instance notification for the newest running instance."""
    hashes = instance_hashes(data_dir)
    if not hashes:
        raise HandoffUnavailable("No OrcaSlicer instance lock found yet.")
    objc = _ObjC()
    pool = objc.send(objc.send(objc.cls("NSAutoreleasePool"), "alloc"), "init")
    try:
        center = objc.send(objc.cls("NSDistributedNotificationCenter"), "defaultCenter")
        info = objc.send(objc.cls("NSDictionary"), "dictionaryWithObject:forKey:",
                         (ctypes.c_void_p, objc.string(message)), (ctypes.c_void_p, objc.string("data")))
        objc.send(center, "postNotificationName:object:userInfo:deliverImmediately:",
                  (ctypes.c_void_p, objc.string(NOTIFICATION_PREFIX + hashes[0])),
                  (ctypes.c_void_p, None), (ctypes.c_void_p, info), (ctypes.c_bool, True))
    finally:
        objc.send(pool, "drain")
