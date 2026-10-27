"""Tests for Computer Use module, OS input engine, and UI grounding."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from modules.computer_use import engine as engine_module
from modules.computer_use.engine import FailsafeTriggeredError, OSInputEngine
from modules.computer_use.grounding import (
    UIGroundingResolver,
    normalize_visual_coordinates,
)
from modules.computer_use.models import (
    BoundingBox,
    CoordinateSpace,
    MouseButton,
    ScreenCoordinate,
    ScrollDirection,
    UIElement,
    VirtualScreenBounds,
)
from modules.computer_use.service import ComputerUseService
from modules.computer_use.tools import (
    computer_click,
    computer_drag_and_drop,
    computer_hotkey,
    computer_inspect_screen,
    computer_scroll,
    computer_type,
)


@pytest.fixture(autouse=True)
def fake_native_input(monkeypatch):
    """Make every test in this module incapable of sending real Windows input."""
    fake_user32 = MagicMock(name="fake_user32")
    monkeypatch.setattr(engine_module, "IS_WINDOWS", True)
    monkeypatch.setattr(engine_module, "user32", fake_user32)
    monkeypatch.setattr(engine_module.time, "sleep", lambda _seconds: None)
    fake_user32.GetAsyncKeyState.return_value = 0
    fake_user32.SendInput.return_value = 1
    return fake_user32


def test_models_screen_coordinate_and_bounding_box():
    coord = ScreenCoordinate(100, 200)
    assert coord.x == 100
    assert coord.y == 200

    assert ScreenCoordinate(-1, 50) == ScreenCoordinate(-1, 50)

    bbox = BoundingBox(left=100, top=200, right=300, bottom=400)
    assert bbox.width == 200
    assert bbox.height == 200
    assert bbox.center == ScreenCoordinate(200, 300)


def test_coordinate_normalization():
    primary = VirtualScreenBounds(left=0, top=0, width=1920, height=1080)

    # Explicit normalized 0..1000 coordinate space to 1920x1080 screen
    coord = normalize_visual_coordinates(
        500,
        500,
        primary,
        coordinate_space=CoordinateSpace.NORMALIZED,
    )
    assert coord.x == 960
    assert coord.y == 540

    # Top-left corner
    top_left = normalize_visual_coordinates(
        0,
        0,
        primary,
        coordinate_space=CoordinateSpace.NORMALIZED,
    )
    assert top_left.x == 0
    assert top_left.y == 0

    # Bottom-right corner normalized
    bottom_right = normalize_visual_coordinates(
        1000,
        1000,
        primary,
        coordinate_space=CoordinateSpace.NORMALIZED,
    )
    assert bottom_right.x == 1919
    assert bottom_right.y == 1079

    # Pixel coordinates are never guessed as normalized.
    raw_pixels = normalize_visual_coordinates(
        500,
        500,
        primary,
        coordinate_space=CoordinateSpace.PIXEL,
    )
    assert raw_pixels == ScreenCoordinate(500, 500)

    virtual = VirtualScreenBounds(left=-1920, top=-200, width=3840, height=1280)
    virtual_left = normalize_visual_coordinates(
        0,
        0,
        virtual,
        coordinate_space=CoordinateSpace.NORMALIZED,
    )
    assert virtual_left == ScreenCoordinate(-1920, -200)
    signed_pixel = normalize_visual_coordinates(
        -500,
        -100,
        virtual,
        coordinate_space=CoordinateSpace.PIXEL,
    )
    assert signed_pixel == ScreenCoordinate(-500, -100)


def test_os_input_engine_simulated(fake_native_input):
    engine = OSInputEngine(failsafe=False)

    with patch.object(engine, "get_screen_size", return_value=(1920, 1080)), \
         patch.object(engine, "get_cursor_position", return_value=ScreenCoordinate(500, 500)), \
         patch.object(engine, "move_to") as mock_move:

        # Test click
        res = engine.click(100, 200, button=MouseButton.LEFT)
        assert res.success is True
        mock_move.assert_called_with(100, 200)

        # Test drag and drop
        res_drag = engine.drag_and_drop(100, 200, 300, 400)
        assert res_drag.success is True
        assert res_drag.coordinate == ScreenCoordinate(300, 400)

        # Test scroll
        res_scroll = engine.scroll(amount=3, direction=ScrollDirection.DOWN)
        assert res_scroll.success is True

        # Test text typing
        res_type = engine.type_text("Hola JARVIS", press_enter=True)
        assert res_type.success is True
        assert "Hola JARVIS" in res_type.message

        # Test hotkey
        res_hotkey = engine.press_hotkey("ctrl+s")
        assert res_hotkey.success is True
        assert "CTRL + S" in res_hotkey.message

        assert fake_native_input.mouse_event.call_count >= 5
        assert fake_native_input.SendInput.call_count >= 2
        assert fake_native_input.keybd_event.call_count == 0


def test_failsafe_trigger():
    engine = OSInputEngine(failsafe=True)
    with patch.object(
        engine,
        "get_screen_bounds",
        return_value=VirtualScreenBounds(0, 0, 1920, 1080),
    ), \
         patch.object(engine, "get_cursor_position", return_value=ScreenCoordinate(0, 0)):
        with pytest.raises(FailsafeTriggeredError):
            engine._check_failsafe()


@pytest.mark.parametrize(
    "cursor",
    [
        ScreenCoordinate(-1920, -200),
        ScreenCoordinate(1919, -200),
        ScreenCoordinate(-1920, 1079),
        ScreenCoordinate(1919, 1079),
    ],
)
def test_failsafe_covers_all_virtual_screen_corners(cursor):
    engine = OSInputEngine(failsafe=True)
    bounds = VirtualScreenBounds(-1920, -200, 3840, 1280)
    with patch.object(engine, "get_screen_bounds", return_value=bounds), patch.object(
        engine,
        "get_cursor_position",
        return_value=cursor,
    ):
        with pytest.raises(FailsafeTriggeredError):
            engine._check_failsafe()


def test_failsafe_triggers_when_escape_is_held(fake_native_input):
    fake_native_input.GetAsyncKeyState.return_value = 0x8000
    engine = OSInputEngine(failsafe=True)
    with pytest.raises(FailsafeTriggeredError, match="Escape"):
        engine._check_failsafe()


def test_unicode_typing_uses_utf16_send_input_units(fake_native_input):
    engine = OSInputEngine(failsafe=False)
    with patch.object(engine, "_send_keyboard_input") as send_keyboard:
        result = engine.type_text("😀")

    assert result.success is True
    assert [call.kwargs["scan_code"] for call in send_keyboard.call_args_list] == [
        0xD83D,
        0xD83D,
        0xDE00,
        0xDE00,
    ]
    assert fake_native_input.keybd_event.call_count == 0
    assert engine_module.ctypes.sizeof(engine_module.INPUT) in {28, 40}


def test_engine_uses_virtual_screen_metrics_and_signed_clamping(fake_native_input):
    metrics = {
        engine_module.SM_XVIRTUALSCREEN: -1920,
        engine_module.SM_YVIRTUALSCREEN: -200,
        engine_module.SM_CXVIRTUALSCREEN: 3840,
        engine_module.SM_CYVIRTUALSCREEN: 1280,
    }
    fake_native_input.GetSystemMetrics.side_effect = lambda metric: metrics.get(metric, 0)
    engine = OSInputEngine(failsafe=False)

    assert engine.get_screen_bounds() == VirtualScreenBounds(-1920, -200, 3840, 1280)

    engine.move_to(-5000, -5000)
    fake_native_input.SetCursorPos.assert_called_with(-1920, -200)


def test_computer_service_fails_closed_for_missing_coordinates_and_targets():
    mock_engine = MagicMock(spec=OSInputEngine)
    mock_grounding = MagicMock(spec=UIGroundingResolver)
    mock_grounding.find_element_by_name.return_value = None
    service = ComputerUseService(engine=mock_engine, grounding=mock_grounding)

    assert service.click().success is False
    assert service.click(x=100, y=None).success is False
    assert service.type_text("secret", target="Missing field").success is False
    assert service.scroll(target="Missing panel").success is False

    mock_engine.click.assert_not_called()
    mock_engine.type_text.assert_not_called()
    mock_engine.scroll.assert_not_called()


def test_computer_use_service_and_tools():
    mock_engine = MagicMock(spec=OSInputEngine)
    mock_engine.get_screen_size.return_value = (1920, 1080)
    mock_engine.get_screen_bounds.return_value = VirtualScreenBounds(0, 0, 1920, 1080)
    mock_engine.get_cursor_position.return_value = ScreenCoordinate(100, 100)

    from modules.computer_use.models import ComputerActionResult
    mock_engine.click.return_value = ComputerActionResult(True, "click", "Clic simulado OK")
    mock_engine.type_text.return_value = ComputerActionResult(True, "type", "Escritura simulada OK")
    mock_engine.press_hotkey.return_value = ComputerActionResult(True, "hotkey", "Hotkey simulado OK")
    mock_engine.scroll.return_value = ComputerActionResult(True, "scroll", "Scroll simulado OK")
    mock_engine.drag_and_drop.return_value = ComputerActionResult(True, "drag_and_drop", "Drag simulado OK")

    mock_grounding = MagicMock(spec=UIGroundingResolver)
    bbox = BoundingBox(50, 50, 150, 150)
    test_element = UIElement(name="Boton Buscar", control_type="Button", bounding_box=bbox)
    mock_grounding.find_element_by_name.return_value = test_element
    mock_grounding.list_visible_elements.return_value = [test_element]

    service = ComputerUseService(engine=mock_engine, grounding=mock_grounding)

    # Click by target name
    res_click_name = service.click(target="Boton Buscar")
    assert res_click_name.success is True
    assert "Boton Buscar" in res_click_name.message

    # Click by coords
    res_click_coord = service.click(x=500, y=500, coordinate_space="pixel")
    assert res_click_coord.success is True

    # Type with target focus
    res_type = service.type_text("prueba", target="Boton Buscar", press_enter=True)
    assert res_type.success is True

    # Inspect screen
    inspection = service.inspect_screen()
    assert "Boton Buscar" in inspection

    # Test LangChain tool adapters
    with patch("modules.computer_use.tools.computer_service", service):
        out_click = computer_click.invoke({"target": "Boton Buscar"})
        assert "Boton Buscar" in out_click

        out_type = computer_type.invoke({"text": "hola"})
        assert "simulada OK" in out_type

        out_hotkey = computer_hotkey.invoke({"keys": "ctrl+c"})
        assert "simulado OK" in out_hotkey

        out_scroll = computer_scroll.invoke({"direction": "down"})
        assert "simulado OK" in out_scroll

        out_inspect = computer_inspect_screen.invoke({})
        assert "Boton Buscar" in out_inspect

        out_drag = computer_drag_and_drop.invoke({"start_x": 100, "start_y": 100, "end_x": 200, "end_y": 200})
        assert "Drag simulado OK" in out_drag


def test_computer_use_tools_in_base_catalog(monkeypatch):
    from core import core_tools

    monkeypatch.setenv("JARVIS_COMPUTER_USE_ENABLED", "true")
    tools = core_tools.get_base_tools()
    tool_names = {t.name for t in tools}

    assert "computer_click" in tool_names
    assert "computer_type" in tool_names
    assert "computer_hotkey" in tool_names
    assert "computer_scroll" in tool_names
    assert "computer_inspect_screen" in tool_names
    assert "computer_drag_and_drop" in tool_names


def test_sensitive_computer_use_tools_require_confirmation():
    from core.security.tool_policy import get_tool_policy

    for tool_name in (
        "computer_click",
        "computer_type",
        "computer_hotkey",
        "computer_drag_and_drop",
    ):
        policy = get_tool_policy(tool_name)
        assert policy.risk_level == "critical"
        assert policy.requires_confirmation is True
        assert policy.allowed_profiles == ("admin",)

    assert get_tool_policy("computer_scroll").requires_confirmation is False
    assert get_tool_policy("computer_inspect_screen").requires_confirmation is False
