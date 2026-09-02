"""High-level Computer Use Service coordinating OS automation and UI grounding."""

from __future__ import annotations

import logging

from modules.computer_use.engine import OSInputEngine
from modules.computer_use.grounding import UIGroundingResolver, normalize_visual_coordinates
from modules.computer_use.models import ComputerActionResult, CoordinateSpace

logger = logging.getLogger(__name__)


class ComputerUseService:
    """Unified service for desktop computer use and UI navigation."""

    def __init__(
        self,
        engine: OSInputEngine | None = None,
        grounding: UIGroundingResolver | None = None,
    ) -> None:
        self.engine = engine or OSInputEngine(failsafe=True)
        self.grounding = grounding or UIGroundingResolver()

    def click(
        self,
        target: str = "",
        x: int | float | None = None,
        y: int | float | None = None,
        coordinate_space: CoordinateSpace | str = CoordinateSpace.NORMALIZED,
        button: str = "left",
        clicks: int = 1,
    ) -> ComputerActionResult:
        """Click on screen coordinate or named UI element."""
        if (x is None) != (y is None):
            return ComputerActionResult(
                success=False,
                action="click",
                message="Se requieren ambas coordenadas X e Y.",
            )

        # 1. Direct coordinate specified
        if x is not None and y is not None:
            norm_coord = normalize_visual_coordinates(
                x,
                y,
                self.engine.get_screen_bounds(),
                coordinate_space=coordinate_space,
            )
            res = self.engine.click(norm_coord.x, norm_coord.y, button=button, clicks=clicks)
            return res

        # 2. Named target specified -> resolve via UIA
        target_clean = str(target or "").strip()
        if target_clean:
            element = self.grounding.find_element_by_name(target_clean)
            if element and element.is_enabled and element.bounding_box:
                center = element.bounding_box.center
                res = self.engine.click(center.x, center.y, button=button, clicks=clicks)
                return ComputerActionResult(
                    success=res.success,
                    action="click",
                    message=f"Clic en elemento '{element.name}' ({center.x}, {center.y}).",
                    target=element.name,
                    coordinate=center,
                )
            return ComputerActionResult(
                success=False,
                action="click",
                message=f"No se encontro ningun elemento interactivo llamado '{target_clean}'.",
                target=target_clean,
            )

        return ComputerActionResult(
            success=False,
            action="click",
            message="Especifique un objetivo o ambas coordenadas para hacer clic.",
        )

    def type_text(
        self,
        text: str,
        target: str = "",
        clear_first: bool = False,
        press_enter: bool = False,
    ) -> ComputerActionResult:
        """Type text, optionally focusing on target first."""
        target_clean = str(target or "").strip()
        if target_clean:
            el = self.grounding.find_element_by_name(target_clean)
            if not el or not el.is_enabled or not el.bounding_box:
                return ComputerActionResult(
                    success=False,
                    action="type",
                    message=f"No se encontro un campo habilitado llamado '{target_clean}'.",
                    target=target_clean,
                )
            focus_result = self.engine.click(el.bounding_box.center.x, el.bounding_box.center.y)
            if not focus_result.success:
                return ComputerActionResult(
                    success=False,
                    action="type",
                    message=f"No se pudo enfocar el campo '{target_clean}'.",
                    target=target_clean,
                )

        return self.engine.type_text(
            text=text,
            clear_first=clear_first,
            press_enter=press_enter,
        )

    def press_hotkey(self, keys: str) -> ComputerActionResult:
        """Execute key combination."""
        return self.engine.press_hotkey(keys)

    def scroll(
        self,
        direction: str = "down",
        amount: int = 5,
        target: str = "",
    ) -> ComputerActionResult:
        """Scroll in specified direction."""
        target_clean = str(target or "").strip()
        if target_clean:
            el = self.grounding.find_element_by_name(target_clean)
            if not el or not el.is_enabled or not el.bounding_box:
                return ComputerActionResult(
                    success=False,
                    action="scroll",
                    message=f"No se encontro un elemento habilitado llamado '{target_clean}'.",
                    target=target_clean,
                )
            self.engine.move_to(el.bounding_box.center.x, el.bounding_box.center.y)

        return self.engine.scroll(amount=amount, direction=direction)

    def drag_and_drop(
        self,
        start_x: int | float,
        start_y: int | float,
        end_x: int | float,
        end_y: int | float,
        coordinate_space: CoordinateSpace | str = CoordinateSpace.NORMALIZED,
    ) -> ComputerActionResult:
        """Drag and drop between coordinates."""
        bounds = self.engine.get_screen_bounds()
        c1 = normalize_visual_coordinates(
            start_x,
            start_y,
            bounds,
            coordinate_space=coordinate_space,
        )
        c2 = normalize_visual_coordinates(
            end_x,
            end_y,
            bounds,
            coordinate_space=coordinate_space,
        )
        return self.engine.drag_and_drop(c1.x, c1.y, c2.x, c2.y)

    def inspect_screen(self, query: str = "") -> str:
        """Inspect visible UI interactive elements."""
        elements = self.grounding.list_visible_elements(max_items=30)
        if not elements:
            w, h = self.engine.get_screen_size()
            cursor = self.engine.get_cursor_position()
            return f"Pantalla activa ({w}x{h} px). Cursor en ({cursor.x}, {cursor.y}). No se detecto arbol UIA detallado."

        q = str(query or "").strip().lower()
        if q:
            matched = [e for e in elements if q in e.name.lower() or q in e.control_type.lower()]
            if not matched:
                return f"No se detectaron elementos interactivos coincidentes con '{query}'."
            elements = matched

        lines = [f"Elementos interactivos detectados ({len(elements)}):"]
        for el in elements[:20]:
            center_info = f"({el.bounding_box.center.x}, {el.bounding_box.center.y})" if el.bounding_box else ""
            lines.append(f"- [{el.control_type}] '{el.name}' {center_info}".strip())
        return "\n".join(lines)


# Global singleton instance
computer_service = ComputerUseService()
