"""Immutable models and data contracts for Computer Use actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class MouseButton(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class ScrollDirection(StrEnum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


class CoordinateSpace(StrEnum):
    NORMALIZED = "normalized"
    PIXEL = "pixel"


@dataclass(frozen=True, slots=True)
class ScreenCoordinate:
    """Exact signed virtual-screen pixel coordinates."""

    x: int
    y: int


@dataclass(frozen=True, slots=True)
class VirtualScreenBounds:
    """Signed bounds for the complete Windows virtual desktop."""

    left: int
    top: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("virtual_screen_dimensions_must_be_positive")

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def clamp(self, x: int | float, y: int | float) -> ScreenCoordinate:
        return ScreenCoordinate(
            x=max(self.left, min(int(x), self.right - 1)),
            y=max(self.top, min(int(y), self.bottom - 1)),
        )


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Rectangular area in pixel space."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def center(self) -> ScreenCoordinate:
        return ScreenCoordinate(
            x=self.left + self.width // 2,
            y=self.top + self.height // 2,
        )


@dataclass(frozen=True, slots=True)
class UIElement:
    """Detected interactive element from UIA or Vision Grounding."""

    name: str
    control_type: str
    bounding_box: BoundingBox | None = None
    handle: int = 0
    automation_id: str = ""
    is_enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ComputerActionResult:
    """Auditable result of an OS input operation."""

    success: bool
    action: str
    message: str
    target: str = ""
    coordinate: ScreenCoordinate | None = None
    details: dict[str, Any] = field(default_factory=dict)
