from __future__ import annotations

from unittest.mock import Mock

import pytest
from core.command_pipeline.models import ReceiptStatus
from tools import system


class Endpoint:
    def __init__(self, level=0.4, muted=False):
        self.level = level
        self.muted = muted
        self.calls = []

    def GetMasterVolumeLevelScalar(self):
        return self.level

    def GetMute(self):
        return int(self.muted)

    def SetMasterVolumeLevelScalar(self, level, _context):
        self.calls.append(("level", level))
        self.level = level

    def SetMute(self, muted, _context):
        self.calls.append(("mute", bool(muted)))
        self.muted = bool(muted)


@pytest.fixture
def audio(monkeypatch):
    endpoint = Endpoint()
    monkeypatch.setattr(system, "IS_WINDOWS", True)
    monkeypatch.setattr(system, "_VOL_AVAILABLE", True)
    monkeypatch.setattr(system, "_obtener_control_volumen", lambda: endpoint)
    monkeypatch.setattr(system, "CoInitialize", lambda: None, raising=False)
    monkeypatch.setattr(system, "CoUninitialize", lambda: None, raising=False)
    monkeypatch.setattr(system, "AUDIO_VERIFY_SECONDS", 0, raising=False)
    monkeypatch.setattr(system, "_windows_key_volume", Mock(return_value="key request"))
    monkeypatch.setattr(system, "_windows_key_mute", Mock(return_value="mute toggled"))
    return endpoint


def test_volume_read_failure_does_not_invent_fifty_percent(audio, monkeypatch):
    monkeypatch.setattr(system, "_obtener_control_volumen", Mock(side_effect=OSError("private device")))
    with pytest.raises(RuntimeError):
        system._leer_volumen_actual()


def test_volume_without_native_controller_is_not_fabricated(audio, monkeypatch):
    monkeypatch.setattr(system, "_VOL_AVAILABLE", False)
    with pytest.raises(RuntimeError):
        system._leer_volumen_actual()


@pytest.mark.parametrize("level", [0, 30, 50, 80])
def test_absolute_volume_without_controller_does_not_guess_or_send_keys(audio, monkeypatch, level):
    monkeypatch.setattr(system, "_VOL_AVAILABLE", False)
    result = system._ajustar_volumen_absoluto(level)
    system._windows_key_volume.assert_not_called()
    system._windows_key_mute.assert_not_called()
    assert result.status is ReceiptStatus.UNAVAILABLE
    assert result.verified is False


def test_positive_volume_restores_sound_and_confirms_both_properties(audio):
    audio.muted = True
    result = system._ajustar_volumen_absoluto(30)
    assert audio.muted is False
    assert audio.level == pytest.approx(0.3)
    assert result.verified is True


def test_zero_volume_remains_muted_when_requested_twice(audio):
    system._ajustar_volumen_absoluto(0)
    result = system._ajustar_volumen_absoluto(0)
    assert audio.level == 0
    assert audio.muted is True
    assert result.verified is True
    system._windows_key_mute.assert_not_called()


def test_relative_zero_is_a_noop_even_without_a_controller(audio, monkeypatch):
    monkeypatch.setattr(system, "_VOL_AVAILABLE", False)
    result = system._ajustar_volumen_relativo(0)
    system._windows_key_volume.assert_not_called()
    system._windows_key_mute.assert_not_called()
    assert audio.calls == []
    assert result.diagnostic_code == "audio_no_change_requested"


def test_relative_key_fallback_is_accepted_not_verified(audio, monkeypatch):
    monkeypatch.setattr(system, "_VOL_AVAILABLE", False)
    result = system._ajustar_volumen_relativo(-10)
    system._windows_key_volume.assert_called_once_with(-10.0)
    assert result.accepted is True
    assert result.verified is False


def test_failed_native_write_does_not_send_a_second_unrequested_change(audio, monkeypatch):
    monkeypatch.setattr(audio, "SetMasterVolumeLevelScalar", Mock(side_effect=OSError("secret endpoint path")))
    result = system._ajustar_volumen_absoluto(80)
    system._windows_key_volume.assert_not_called()
    system._windows_key_mute.assert_not_called()
    assert result.status is ReceiptStatus.FAILED
    assert "secret endpoint" not in result.user_message


def test_unconfirmed_postcondition_is_not_verified(audio, monkeypatch):
    monkeypatch.setattr(audio, "SetMasterVolumeLevelScalar", lambda *_args: None)
    result = system._ajustar_volumen_absoluto(80)
    assert result.accepted is True
    assert result.verified is False


def test_unmute_is_not_parsed_as_mute(audio):
    audio.muted = True
    result = system.ajustar_volumen.invoke({"nivel": "unmute"})
    assert audio.muted is False
    assert result.verified is True


def test_audio_mute_does_not_enable_notification_quiet_mode(audio, monkeypatch):
    quiet_tool = Mock()
    monkeypatch.setattr(system, "modo_no_molestar", quiet_tool)
    system.ajustar_volumen.invoke({"nivel": "silenciar"})
    quiet_tool.invoke.assert_not_called()
    assert audio.muted is True


def test_volume_autocure_does_not_set_an_arbitrary_fifty_percent(audio, monkeypatch):
    from core import core_tools
    from core.brain import tool_manager

    setter = Mock()
    monkeypatch.setattr(core_tools, "_ajustar_volumen_absoluto", setter)
    result = tool_manager._intentar_autocuracion("ajustar_volumen", {"nivel": "+10"}, "sube el volumen 10")
    setter.assert_not_called()
    assert result is None


@pytest.mark.parametrize("level", [float("nan"), float("inf"), -float("inf")])
def test_non_finite_volume_does_not_write(audio, level):
    result = system._ajustar_volumen_absoluto(level)
    assert result.status is ReceiptStatus.FAILED
    assert audio.calls == []


def test_legacy_volume_preflight_returns_a_human_message(audio, monkeypatch):
    from core import core_tools
    from core.brain import processor
    from core.command_pipeline.models import ToolExecutionResult

    monkeypatch.setattr(processor, "_cargar_contexto_perfil", lambda profile: profile)
    monkeypatch.setattr(processor, "_resolve_pending_auth_confirmation", lambda *_: (None, False))
    monkeypatch.setattr(processor, "_resolve_pending_spotify_selection", lambda *_: (None, False))
    monkeypatch.setattr(processor.social_engine, "_respuesta_rapida_social", lambda *_: None)
    monkeypatch.setattr(processor.social_engine, "_respuesta_seguimiento_contextual", lambda *_: None)
    monkeypatch.setattr(processor.brain_utils, "parse_reminder", lambda _: (None, None))
    monkeypatch.setattr(processor.brain_utils, "parsear_comando_volumen", lambda _: ("absolute", 30))
    monkeypatch.setattr(
        core_tools,
        "_ajustar_volumen_absoluto",
        lambda _: ToolExecutionResult(
            result={"level": 30},
            user_message="Volumen comprobado al 30%.",
            verified=True,
        ),
    )
    reply, _ = processor._preflight("volumen 30", "admin", allow_compound=False)
    assert reply == "Volumen comprobado al 30%."


@pytest.mark.parametrize(
    ("command", "muted"),
    [
        ("mute", True),
        ("silencia el volumen", True),
        ("unmute", False),
        ("desactiva el silencio", False),
    ],
)
def test_audio_mute_routing_preserves_the_volume_level(audio, command, muted):
    from core.brain import router
    from core.command_pipeline.models import CommandRequest

    audio.level = 0.4
    audio.muted = not muted
    plan = router.plan_hybrid(CommandRequest.create(text=command, profile_id="admin", channel="chat"))
    assert plan is not None
    assert plan.steps[0].tool_name == "ajustar_volumen"
    result = system.ajustar_volumen.invoke(dict(plan.steps[0].arguments))
    assert audio.level == 0.4
    assert audio.muted is muted
    assert result.verified is True
