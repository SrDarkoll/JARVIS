"""UI Element locator and Visual Coordinate Grounding resolver."""

from __future__ import annotations

import sys

from modules.computer_use.models import (
    BoundingBox,
    CoordinateSpace,
    ScreenCoordinate,
    UIElement,
    VirtualScreenBounds,
)

IS_WINDOWS = sys.platform == "win32"


def normalize_visual_coordinates(
    x: int | float,
    y: int | float,
    screen_bounds: VirtualScreenBounds,
    *,
    coordinate_space: CoordinateSpace | str = CoordinateSpace.NORMALIZED,
) -> ScreenCoordinate:
    """Resolve an explicitly declared coordinate space into virtual pixels."""
    raw_x = float(x)
    raw_y = float(y)
    try:
        space = (
            coordinate_space
            if isinstance(coordinate_space, CoordinateSpace)
            else CoordinateSpace(str(coordinate_space or "").strip().lower())
        )
    except ValueError as exc:
        raise ValueError("invalid_coordinate_space") from exc

    if space is CoordinateSpace.NORMALIZED:
        normalized_x = max(0.0, min(raw_x, 1000.0))
        normalized_y = max(0.0, min(raw_y, 1000.0))
        px_x = screen_bounds.left + int((normalized_x / 1000.0) * screen_bounds.width)
        px_y = screen_bounds.top + int((normalized_y / 1000.0) * screen_bounds.height)
    else:
        px_x = int(raw_x)
        px_y = int(raw_y)

    return screen_bounds.clamp(px_x, px_y)


def _extract_pywinauto_elements(window_obj, max_items: int = 30) -> list[UIElement]:
    elements: list[UIElement] = []
    try:
        descendants = window_obj.descendants()
        for ctrl in descendants[:max_items * 2]:
            if len(elements) >= max_items:
                break
            try:
                if not ctrl.is_visible() or not ctrl.is_enabled():
                    continue
                name = str(ctrl.window_text() or "").strip()
                type_name = str(ctrl.friendly_class_name() or "").strip()
                rect = ctrl.rectangle()
                if rect.width() <= 0 or rect.height() <= 0:
                    continue

                bbox = BoundingBox(
                    left=int(rect.left),
                    top=int(rect.top),
                    right=int(rect.right),
                    bottom=int(rect.bottom),
                )
                elements.append(
                    UIElement(
                        name=name or f"{type_name}_{len(elements)}",
                        control_type=type_name,
                        bounding_box=bbox,
                        handle=int(getattr(ctrl, "handle", 0) or 0),
                        automation_id=str(getattr(ctrl.element_info, "automation_id", "") or ""),
                        is_enabled=True,
                    )
                )
            except Exception:
                continue
    except Exception:
        pass
    return elements


class UIGroundingResolver:
    """Discovers and resolves UI targets via Windows UIA or screen layout."""

    def __init__(self) -> None:
        self._uia_available = False
        if IS_WINDOWS:
            try:
                import pywinauto  # noqa: F401
                self._uia_available = True
            except ImportError:
                self._uia_available = False

    def list_visible_elements(self, max_items: int = 25) -> list[UIElement]:
        if not IS_WINDOWS or not self._uia_available:
            return []

        try:
            from pywinauto import Desktop

            desktop = Desktop(backend="uia")
            active_window = desktop.active()
            if not active_window:
                return []
            return _extract_pywinauto_elements(active_window, max_items=max_items)
        except Exception:
            return []

    def find_element_by_name(self, target_name: str) -> UIElement | None:
        if not IS_WINDOWS or not self._uia_available:
            return None

        needle = str(target_name or "").strip().lower()
        if not needle:
            return None

        try:
            from pywinauto import Desktop

            desktop = Desktop(backend="uia")
            active_window = desktop.active()
            if not active_window:
                return None

            candidates = _extract_pywinauto_elements(active_window, max_items=50)
            # Exact match first
            for el in candidates:
                if el.name.lower() == needle:
                    return el

            # Partial match
            for el in candidates:
                if needle in el.name.lower():
                    return el

            return None
        except Exception:
            return None
