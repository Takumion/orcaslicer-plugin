"""Our own stand-in for OrcaSlicer's embedded ``orca`` module: just the parts
the plugin touches, recording what it was asked to do."""

from __future__ import annotations

import enum
import types
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeVolume:
    mm3: float

    def volume(self) -> float:
        return self.mm3


@dataclass
class FakeObject:
    """An Orca ``ModelObject``."""

    oid: int
    name: str
    input_file: str
    facets: int = 12
    mm3: float = 1000.0

    def id(self) -> int:
        return self.oid

    def facets_count(self) -> int:
        return self.facets

    def volumes(self) -> list[FakeVolume]:
        return [FakeVolume(self.mm3)]


@dataclass
class FakeModel:
    items: list[FakeObject] = field(default_factory=list)

    def objects(self) -> list[FakeObject]:
        return list(self.items)


@dataclass
class FakePlater:
    the_model: FakeModel = field(default_factory=FakeModel)
    project: str = ""

    def model(self) -> FakeModel:
        return self.the_model

    def project_path(self) -> str:
        return self.project


class NotificationLevel(enum.Enum):
    RegularNotificationLevel = 1
    WarningNotificationLevel = 2


class LifecycleEvent(enum.Enum):
    ObjectAdded = 1
    ObjectDeleted = 2
    ProjectOpened = 3
    ProjectBeforeSave = 4


def make_fake_orca(version: str = "2.5.1") -> types.ModuleType:
    """A fresh fake ``orca`` module; inspect ``orca.calls`` / ``orca.notifications``."""
    orca = types.ModuleType("orca")
    orca.calls = {"permissions": [], "capabilities": [], "packages": []}
    orca.notifications = []
    orca.plater_obj = FakePlater()
    orca.LifecycleEvent = LifecycleEvent

    class PagesPluginCapabilityBase:
        def __init__(self) -> None:
            self.posted: list[Any] = []

        def post_message(self, data: Any) -> None:
            self.posted.append(data)

    class base:  # noqa: N801 - Orca's name
        def register_capabilities(self) -> None:
            raise NotImplementedError

    def plugin(cls: type) -> type:
        orca.calls["packages"].append(cls)
        return cls

    def push_notification(level: NotificationLevel, text: str, hyper_text: str = "", on_click: Any = None) -> None:
        orca.notifications.append((level.name, text))

    orca.pages = types.SimpleNamespace(PagesPluginCapabilityBase=PagesPluginCapabilityBase)
    orca.base = base
    orca.plugin = plugin
    orca.register_capability = lambda cls: orca.calls["capabilities"].append(cls)
    orca.request_permissions = lambda **kwargs: orca.calls["permissions"].append(kwargs)
    orca.host = types.SimpleNamespace(
        plater=lambda: orca.plater_obj,
        model=lambda: orca.plater_obj.model(),
        app_info=lambda: {"version": version},
        ui=types.SimpleNamespace(NotificationLevel=NotificationLevel, push_notification=push_notification),
    )
    return orca
