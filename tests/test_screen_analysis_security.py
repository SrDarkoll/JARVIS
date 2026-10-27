from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from core import llm_providers
from core.brain import llm_engine
from tools import utilities


class _FakeImage:
    size = (16, 12)
    mode = "RGB"

    def convert(self, _mode: str):
        return self

    def filter(self, _filter):
        return self

    def histogram(self):
        return [0] * 256

    def thumbnail(self, _size):
        return None

    def save(self, path):
        Path(path).write_bytes(b"fake-png")


def _install_fake_desktop_modules(monkeypatch, saved_paths: list[Path]) -> None:
    pyautogui = ModuleType("pyautogui")

    class _Screenshot:
        @staticmethod
        def save(path):
            target = Path(path)
            saved_paths.append(target)
            target.write_bytes(b"fake-png")

    pyautogui.screenshot = _Screenshot
    monkeypatch.setitem(sys.modules, "pyautogui", pyautogui)

    pil = ModuleType("PIL")
    pil.Image = SimpleNamespace(open=lambda _path: _FakeImage())
    pil.ImageFilter = SimpleNamespace(FIND_EDGES=object())
    pil.ImageStat = SimpleNamespace(Stat=lambda _image: SimpleNamespace(mean=[100.0]))
    monkeypatch.setitem(sys.modules, "PIL", pil)


def _prepare_screen_tool(monkeypatch, tmp_path, model):
    saved_paths: list[Path] = []
    _install_fake_desktop_modules(monkeypatch, saved_paths)
    monkeypatch.setattr(
        utilities,
        "RUNTIME_PATHS",
        SimpleNamespace(temp=tmp_path),
        raising=False,
    )
    monkeypatch.setattr(utilities, "_bloqueo_si_no_autorizado", lambda: None)
    monkeypatch.setattr(utilities.services, "llm_vision", model)
    return saved_paths


def test_screen_capture_uses_private_runtime_temp_and_is_always_removed(
    monkeypatch,
    tmp_path,
):
    class VisionModel:
        @staticmethod
        def invoke(_messages):
            return SimpleNamespace(content="Todo estable")

    saved_paths = _prepare_screen_tool(monkeypatch, tmp_path, VisionModel())

    result = utilities.analizar_pantalla.invoke({})

    assert "Todo estable" in result
    assert len(saved_paths) == 1
    assert saved_paths[0].parent == tmp_path
    assert not saved_paths[0].exists()
    assert list(tmp_path.rglob("*.png")) == []


def test_screen_analysis_fallback_uses_real_provider_module(monkeypatch, tmp_path):
    saved_paths = _prepare_screen_tool(monkeypatch, tmp_path, None)

    class VisionModel:
        @staticmethod
        def invoke(_messages):
            return SimpleNamespace(content="Fallback visual activo")

    class FakeChatOpenAI:
        def __new__(cls, **_kwargs):
            return VisionModel()

    monkeypatch.setattr(llm_engine, "_load_chat_openai", lambda: FakeChatOpenAI)
    monkeypatch.setattr(llm_providers, "resolve_gemini_api_key", lambda: "test-key")
    monkeypatch.setattr(llm_providers, "resolve_groq_api_key", lambda: "")
    monkeypatch.setattr(
        llm_providers,
        "provider_base_url",
        lambda _provider: "https://example.invalid/v1",
    )

    result = utilities.analizar_pantalla.invoke({})

    assert "Fallback visual activo" in result
    assert len(saved_paths) == 1
    assert not saved_paths[0].exists()


def test_screen_analysis_does_not_expose_provider_error_details(monkeypatch, tmp_path):
    class FailingVisionModel:
        @staticmethod
        def invoke(_messages):
            raise RuntimeError("api_key=super-secret")

    _prepare_screen_tool(monkeypatch, tmp_path, FailingVisionModel())

    result = utilities.analizar_pantalla.invoke({})

    assert "super-secret" not in result
    assert "api_key" not in result
    assert list(tmp_path.rglob("*.png")) == []
