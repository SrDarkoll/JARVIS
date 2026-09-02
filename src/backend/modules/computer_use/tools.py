"""LangChain tool adapters for Computer Use and desktop navigation."""

from __future__ import annotations

from langchain_core.tools import tool

from modules.computer_use.service import computer_service


@tool
def computer_click(
    target: str = "",
    x: float | None = None,
    y: float | None = None,
    coordinate_space: str = "normalized",
    button: str = "left",
    clicks: int = 1,
) -> str:
    """Hace clic en un elemento por nombre o en coordenadas X/Y. coordinate_space debe ser 'normalized' (0..1000 para grounding visual) o 'pixel' (escritorio virtual)."""
    try:
        res = computer_service.click(
            target=target,
            x=x,
            y=y,
            coordinate_space=coordinate_space,
            button=button,
            clicks=clicks,
        )
        return res.message
    except Exception as exc:
        return f"Error ejecutando clic en computadora: {exc}"


@tool
def computer_type(
    text: str,
    target: str = "",
    clear_first: bool = False,
    press_enter: bool = False,
) -> str:
    """Escribe texto en el campo o ventana activa, o en el elemento indicado por target. Opcionalmente borra el contenido previo o presiona Enter al finalizar."""
    try:
        res = computer_service.type_text(
            text=text,
            target=target,
            clear_first=clear_first,
            press_enter=press_enter,
        )
        return res.message
    except Exception as exc:
        return f"Error escribiendo texto en computadora: {exc}"


@tool
def computer_hotkey(keys: str) -> str:
    """Ejecuta una combinacion o atajo de teclado en la computadora, como 'ctrl+c', 'ctrl+v', 'ctrl+s', 'alt+tab', 'win+d', 'enter', 'esc'."""
    try:
        res = computer_service.press_hotkey(keys=keys)
        return res.message
    except Exception as exc:
        return f"Error ejecutando atajo de teclado: {exc}"


@tool
def computer_scroll(
    direction: str = "down",
    amount: int = 5,
    target: str = "",
) -> str:
    """Realiza scroll vertical u horizontal en la ventana o sobre el elemento indicado. Direcciones: 'down', 'up', 'left', 'right'."""
    try:
        res = computer_service.scroll(direction=direction, amount=amount, target=target)
        return res.message
    except Exception as exc:
        return f"Error realizando scroll: {exc}"


@tool
def computer_inspect_screen(query: str = "") -> str:
    """Inspecciona la pantalla e identifica elementos interactivos visibles (botones, campos de texto, pestanas, controles)."""
    try:
        return computer_service.inspect_screen(query=query)
    except Exception as exc:
        return f"Error inspeccionando pantalla: {exc}"


@tool
def computer_drag_and_drop(
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    coordinate_space: str = "normalized",
) -> str:
    """Arrastra entre coordenadas en espacio 'normalized' (0..1000) o 'pixel' del escritorio virtual."""
    try:
        res = computer_service.drag_and_drop(
            start_x=start_x,
            start_y=start_y,
            end_x=end_x,
            end_y=end_y,
            coordinate_space=coordinate_space,
        )
        return res.message
    except Exception as exc:
        return f"Error ejecutando arrastrar y soltar: {exc}"
