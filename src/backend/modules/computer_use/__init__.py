"""Computer Use module for autonomous OS navigation and interaction."""

from modules.computer_use.engine import OSInputEngine
from modules.computer_use.grounding import UIGroundingResolver, normalize_visual_coordinates
from modules.computer_use.models import (
    BoundingBox,
    ComputerActionResult,
    MouseButton,
    ScreenCoordinate,
    ScrollDirection,
    UIElement,
)
from modules.computer_use.service import ComputerUseService, computer_service
from modules.computer_use.tools import (
    computer_click,
    computer_drag_and_drop,
    computer_hotkey,
    computer_inspect_screen,
    computer_scroll,
    computer_type,
)

__all__ = [
    "BoundingBox",
    "ComputerActionResult",
    "ComputerUseService",
    "MouseButton",
    "OSInputEngine",
    "ScreenCoordinate",
    "ScrollDirection",
    "UIElement",
    "UIGroundingResolver",
    "computer_click",
    "computer_drag_and_drop",
    "computer_hotkey",
    "computer_inspect_screen",
    "computer_scroll",
    "computer_service",
    "computer_type",
    "normalize_visual_coordinates",
]
