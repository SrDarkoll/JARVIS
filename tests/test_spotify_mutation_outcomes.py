"""Spotify receipts retain native outcomes without real provider or UI effects."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from core.command_pipeline.execution import ToolExecutionService
from core.command_pipeline.models import ActionStep, CommandRequest, ReceiptStatus, ToolExecutionResult
from modules.spotify import messages, service, tools
from modules.spotify.api.playback import SpotifyAPIPlaybackResult
from modules.spotify.desktop import (
    AutomationState,
    DesktopResultStatus,
    SpotifyCandidate,
    SpotifyDesktopResult,
    windows,
)

OPERATIONS = [
    pytest.param("queue", tools.agregar_a_cola_spotify, {"cancion": "Test Track"}, "queue", id="queue"),
    pytest.param("like", tools.dar_like_spotify, {"cancion": "Test Track"}, "library", id="like"),
    pytest.param("unlike", tools.quitar_like_spotify, {"cancion": "Test Track"}, "library", id="unlike"),
    pytest.param("control", tools.controlar_reproduccion, {"accion": "pause"}, "control", id="control"),
]
CHOICES = (
    SpotifyCandidate("candidate-1", "Test Track", "First Artist"),
    SpotifyCandidate("candidate-2", "Test Track", "Second Artist"),
)


@pytest.fixture
def providers(monkeypatch):
    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "desktop")
    monkeypatch.setattr(service, "_spotify_api_blocked_until", {})
    monkeypatch.setattr(service, "_spotify_has_valid_cached_token", Mock(return_value=True))
    monkeypatch.setattr(service, "pending_spotify_selections", Mock())
    monkeypatch.setattr(service, "set_last_media_source", Mock())
    monkeypatch.setattr(service, "set_last_requested_track", Mock())
    monkeypatch.setattr(messages, "get_current_language", lambda: "en")
    controller = Mock(spec=["play", "queue", "library", "control", "current"])
    monkeypatch.setattr(service, "_get_desktop_controller", Mock(return_value=controller))
    monkeypatch.setattr(windows, "send_media_key_event", Mock(return_value=False))
    for name in ("play", "control", "add_to_queue", "like_track", "unlike_track", "current_track"):
        monkeypatch.setattr(service, f"_spotify_{name}_api", Mock(side_effect=AssertionError("Unexpected API call")))
    return controller


@pytest.mark.parametrize("operation,adapter,arguments,method", OPERATIONS)
@pytest.mark.parametrize("status,expected,verified,accepted", [
    (DesktopResultStatus.SUCCESS, ReceiptStatus.SUCCEEDED, True, False),
    (DesktopResultStatus.UNVERIFIED, ReceiptStatus.ACCEPTED, False, True),
    (DesktopResultStatus.AMBIGUOUS, ReceiptStatus.PARTIAL, False, False),
    (DesktopResultStatus.UNAVAILABLE, ReceiptStatus.UNAVAILABLE, False, False),
    (DesktopResultStatus.FAILED, ReceiptStatus.FAILED, False, False),
    (DesktopResultStatus.NOT_FOUND, ReceiptStatus.FAILED, False, False),
    (DesktopResultStatus.RESTRICTED, ReceiptStatus.FAILED, False, False),
    (DesktopResultStatus.CANCELLED, ReceiptStatus.FAILED, False, False),
])
def test_native_mutation_status_survives_adapter(
    providers, operation, adapter, arguments, method, status, expected, verified, accepted
):
    native = SpotifyDesktopResult(
        status=status, title="Test Track", artist="Test Artist",
        message_key=f"spotify_{operation}_{status.value}", choices=CHOICES if status is DesktopResultStatus.AMBIGUOUS else (),
    )
    getattr(providers, method).return_value = native

    result = adapter.invoke(arguments)

    assert isinstance(result, ToolExecutionResult)
    assert result.status is expected
    assert result.verified is verified
    assert result.accepted is accepted
    assert result.diagnostic_code == native.message_key
    assert result.result["provider"] == "desktop"
    assert result.result["status"] == status.value
    assert result.result["requires_follow_up"] is (status is DesktopResultStatus.AMBIGUOUS)
    assert result.result["choices"] == [
        {"title": choice.title, "artist": choice.artist, "kind": choice.kind} for choice in native.choices
    ]
    getattr(providers, method).assert_called_once()
    providers.play.assert_not_called()
    if method == "library":
        assert providers.library.call_args.args[1] == operation
        providers.control.assert_not_called()


@pytest.mark.parametrize("operation,adapter,arguments,method", OPERATIONS)
@pytest.mark.parametrize("ok,capability_failure,expected", [
    (True, False, ReceiptStatus.ACCEPTED),
    (False, False, ReceiptStatus.FAILED),
    (False, True, ReceiptStatus.UNAVAILABLE),
])
def test_api_mutation_uses_typed_flags_instead_of_message(
    monkeypatch, providers, operation, adapter, arguments, method, ok, capability_failure, expected
):
    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "api")
    api_name = {"queue": "add_to_queue", "like": "like_track", "unlike": "unlike_track", "control": "control"}[operation]
    api = Mock(return_value=SpotifyAPIPlaybackResult(
        ok=ok, capability_failure=capability_failure, message="Playback paused. Saved. Added to queue."
    ))
    monkeypatch.setattr(service, f"_spotify_{api_name}_api", api)

    result = adapter.invoke(arguments)

    assert isinstance(result, ToolExecutionResult)
    assert result.status is expected
    assert result.accepted is ok
    assert result.verified is False
    assert result.result["provider"] == "api"
    assert result.diagnostic_code == f"spotify_api_{operation}_{'accepted' if ok else 'unavailable' if capability_failure else 'failed'}"
    api.assert_called_once()
    assert providers.mock_calls == []


@pytest.mark.parametrize("operation,adapter,arguments,method", OPERATIONS)
def test_capability_fallback_executes_each_provider_once(monkeypatch, providers, operation, adapter, arguments, method):
    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "auto")
    api_name = {"queue": "add_to_queue", "like": "like_track", "unlike": "unlike_track", "control": "control"}[operation]
    api = Mock(return_value=SpotifyAPIPlaybackResult(ok=False, message="Unavailable", capability_failure=True))
    monkeypatch.setattr(service, f"_spotify_{api_name}_api", api)
    getattr(providers, method).return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.UNVERIFIED, message_key=f"spotify_{operation}_sent_unverified"
    )

    result = adapter.invoke(arguments)

    assert result.status is ReceiptStatus.ACCEPTED
    assert result.result["provider"] == "desktop"
    api.assert_called_once()
    getattr(providers, method).assert_called_once()


@pytest.mark.parametrize("operation,adapter,arguments,method", OPERATIONS)
def test_auto_without_cached_token_skips_api(monkeypatch, providers, operation, adapter, arguments, method):
    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "auto")
    monkeypatch.setattr(service, "_spotify_has_valid_cached_token", Mock(return_value=False))
    getattr(providers, method).return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.UNVERIFIED, message_key=f"spotify_{operation}_sent_unverified"
    )

    result = adapter.invoke(arguments)

    assert result.status is ReceiptStatus.ACCEPTED
    getattr(providers, method).assert_called_once()


@pytest.mark.parametrize("operation,adapter,arguments,method", OPERATIONS)
@pytest.mark.parametrize("ok,expected", [(True, ReceiptStatus.ACCEPTED), (False, ReceiptStatus.FAILED)])
def test_auto_does_not_repeat_acknowledged_or_non_capability_result(
    monkeypatch, providers, operation, adapter, arguments, method, ok, expected
):
    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "auto")
    api_name = {"queue": "add_to_queue", "like": "like_track", "unlike": "unlike_track", "control": "control"}[operation]
    api = Mock(return_value=SpotifyAPIPlaybackResult(ok=ok, message="Synthetic provider response"))
    monkeypatch.setattr(service, f"_spotify_{api_name}_api", api)

    result = adapter.invoke(arguments)

    assert result.status is expected
    api.assert_called_once()
    assert providers.mock_calls == []


@pytest.mark.parametrize("operation,adapter", [
    ("like", tools.dar_like_spotify),
    ("unlike", tools.quitar_like_spotify),
])
@pytest.mark.parametrize("value", ["", "current"])
def test_current_favorite_uses_control_once(providers, operation, adapter, value):
    providers.control.return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.UNVERIFIED, message_key="spotify_control_sent_unverified"
    )

    result = adapter.invoke({"cancion": value})

    assert result.status is ReceiptStatus.ACCEPTED
    providers.control.assert_called_once_with(operation)
    providers.library.assert_not_called()
    providers.play.assert_not_called()


@pytest.mark.parametrize("status,code,states,expected", [
    (DesktopResultStatus.UNVERIFIED, "spotify_playback_not_verified", (), ReceiptStatus.ACCEPTED),
    (DesktopResultStatus.FAILED, "spotify_playback_not_verified", (), ReceiptStatus.ACCEPTED),
    (DesktopResultStatus.FAILED, "spotify_playback_not_verified",
     (AutomationState.PLAYING, AutomationState.VERIFYING, AutomationState.FAILED), ReceiptStatus.ACCEPTED),
    (DesktopResultStatus.FAILED, "spotify_playback_not_verified",
     (AutomationState.FOCUSING, AutomationState.FAILED), ReceiptStatus.FAILED),
    (DesktopResultStatus.FAILED, "spotify_focus_lost", (), ReceiptStatus.FAILED),
    (DesktopResultStatus.UNAVAILABLE, "spotify_start_timeout", (), ReceiptStatus.UNAVAILABLE),
])
@pytest.mark.parametrize("adapter,arguments", [
    (tools.reproducir_en_spotify, {"cancion": "Test Track"}),
    (tools.reproducir_mix_spotify, {"semilla": "Test Artist"}),
])
def test_playback_uncertainty_requires_attempt_evidence(providers, status, code, states, expected, adapter, arguments):
    providers.play.return_value = SpotifyDesktopResult(status=status, message_key=code, states=states)

    result = adapter.invoke(arguments)

    assert result.status is expected
    assert result.accepted is (expected is ReceiptStatus.ACCEPTED)
    assert result.verified is False
    assert result.diagnostic_code == code
    providers.play.assert_called_once()


@pytest.mark.parametrize("ok,capability_failure,expected", [
    (True, False, ReceiptStatus.ACCEPTED),
    (False, False, ReceiptStatus.FAILED),
    (False, True, ReceiptStatus.UNAVAILABLE),
])
@pytest.mark.parametrize("adapter,arguments", [
    (tools.reproducir_en_spotify, {"cancion": "Test Track"}),
    (tools.reproducir_mix_spotify, {"semilla": "Test Artist"}),
])
def test_api_playback_uses_acknowledgement_without_claiming_readback(
    monkeypatch, providers, adapter, arguments, ok, capability_failure, expected
):
    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "api")
    api = Mock(return_value=SpotifyAPIPlaybackResult(
        ok=ok, message="Synthetic playback acknowledgement", capability_failure=capability_failure
    ))
    monkeypatch.setattr(service, "_spotify_play_api", api)

    result = adapter.invoke(arguments)

    assert result.status is expected
    assert result.accepted is ok and result.verified is False
    assert result.result["provider"] == "api"
    assert result.diagnostic_code == f"spotify_api_playback_{'accepted' if ok else 'unavailable' if capability_failure else 'failed'}"
    api.assert_called_once()
    assert providers.mock_calls == []


def test_playback_choices_are_retained_without_becoming_a_mutation(providers):
    providers.play.return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.AMBIGUOUS, message_key="spotify_ambiguous_results", choices=CHOICES
    )

    result = tools.reproducir_en_spotify.invoke({"cancion": "Test Track"})

    assert result.status is ReceiptStatus.PARTIAL
    assert result.accepted is False and result.verified is False
    assert [choice["artist"] for choice in result.result["choices"]] == ["First Artist", "Second Artist"]
    providers.play.assert_called_once()
    providers.queue.assert_not_called()
    providers.library.assert_not_called()


@pytest.mark.parametrize("operation,adapter,arguments,method", OPERATIONS)
def test_accepted_receipt_is_replayed_without_a_second_mutation(providers, operation, adapter, arguments, method):
    getattr(providers, method).return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.UNVERIFIED, message_key=f"spotify_{operation}_sent_unverified"
    )
    request = CommandRequest.create(text="Synthetic Spotify request", profile_id="test", channel="test")
    step = ActionStep(step_id="spotify-mutation", tool_name=adapter.name, arguments=arguments)
    executor = ToolExecutionService(lambda _request, _step: adapter.invoke(arguments))

    first = executor.execute(request, step)
    second = executor.execute(request, step)

    assert first.status is ReceiptStatus.ACCEPTED
    assert second is first
    assert first.result["provider"] == "desktop"
    getattr(providers, method).assert_called_once()


@pytest.mark.parametrize("helper,method,arguments", [
    (service.add_to_queue, "queue", ("Test Track",)),
    (service.like_track, "library", ("Test Track",)),
    (service.unlike_track, "library", ("Test Track",)),
    (service.control, "control", ("pause",)),
])
def test_public_service_helpers_remain_text_and_execute_once(providers, helper, method, arguments):
    getattr(providers, method).return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.UNVERIFIED, title="Test Track", message_key="spotify_sent_unverified"
    )

    message = helper(*arguments)

    assert isinstance(message, str)
    assert "not verify" in message
    getattr(providers, method).assert_called_once()


def test_global_media_key_has_its_own_unverified_provider(monkeypatch, providers):
    providers.control.return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.UNAVAILABLE, message_key="spotify_automation_unavailable"
    )
    sent = Mock(return_value=True)
    monkeypatch.setattr(windows, "send_media_key_event", sent)

    result = tools.controlar_reproduccion.invoke({"accion": "pause"})

    assert result.status is ReceiptStatus.ACCEPTED
    assert result.accepted is True and result.verified is False
    assert result.result["provider"] == "global_media_key"
    assert result.diagnostic_code == "spotify_global_media_key_unverified"
    sent.assert_called_once_with("pause")
    providers.control.assert_called_once_with("pause")


@pytest.mark.parametrize("adapter", [tools.dar_like_spotify, tools.quitar_like_spotify])
def test_favorites_never_send_global_media_keys(monkeypatch, providers, adapter):
    providers.control.return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.UNAVAILABLE, message_key="spotify_automation_unavailable"
    )
    sent = Mock(return_value=True)
    monkeypatch.setattr(windows, "send_media_key_event", sent)

    result = adapter.invoke({"cancion": ""})

    assert result.status is ReceiptStatus.UNAVAILABLE
    assert result.accepted is False
    sent.assert_not_called()


@pytest.mark.parametrize("adapter,arguments,code", [
    (tools.agregar_a_cola_spotify, {"cancion": " "}, "spotify_queue_query_required"),
    (tools.controlar_reproduccion, {"accion": " "}, "spotify_control_action_required"),
    (tools.reproducir_en_spotify, {"cancion": " "}, "spotify_query_required"),
    (tools.reproducir_mix_spotify, {"semilla": " "}, "spotify_seed_required"),
])
def test_missing_mutation_input_is_partial_without_a_provider_call(providers, adapter, arguments, code):
    result = adapter.invoke(arguments)

    assert result.status is ReceiptStatus.PARTIAL
    assert result.result["requires_follow_up"] is True
    assert result.result["status"] == "ambiguous"
    assert result.accepted is False and result.verified is False
    assert result.diagnostic_code == code
    assert providers.mock_calls == []


@pytest.mark.parametrize("status,expected", [
    (DesktopResultStatus.SUCCESS, ReceiptStatus.SUCCEEDED),
    (DesktopResultStatus.NOT_FOUND, ReceiptStatus.FAILED),
    (DesktopResultStatus.UNAVAILABLE, ReceiptStatus.UNAVAILABLE),
    (DesktopResultStatus.UNVERIFIED, ReceiptStatus.FAILED),
])
def test_info_control_retains_query_outcome_without_mutating(providers, status, expected):
    providers.current.return_value = SpotifyDesktopResult(
        status=status, title="Test Track" if status is DesktopResultStatus.SUCCESS else "",
        playback_state="playing", message_key="spotify_current_track",
    )

    result = tools.controlar_reproduccion.invoke({"accion": "info"})

    assert result.status is expected
    assert result.accepted is False
    providers.current.assert_called_once_with()
    providers.control.assert_not_called()
    providers.play.assert_not_called()


@pytest.mark.parametrize("helper,method,arguments", [
    ("_queue_outcome", "queue", ("Test Track",)),
    ("_like_outcome", "library", ("Test Track",)),
    ("_unlike_outcome", "library", ("Test Track",)),
    ("_control_outcome", "control", ("pause",)),
])
def test_service_outcome_preserves_native_choices_and_diagnostic(providers, helper, method, arguments):
    providers_result = SpotifyDesktopResult(
        status=DesktopResultStatus.AMBIGUOUS, choices=CHOICES, message_key="spotify_ambiguous_results"
    )
    getattr(providers, method).return_value = providers_result

    outcome = getattr(service, helper)(*arguments)

    assert isinstance(outcome, service.SpotifyServiceMutationOutcome)
    assert outcome.status is DesktopResultStatus.AMBIGUOUS
    assert outcome.provider == "desktop"
    assert outcome.choices == CHOICES
    assert outcome.diagnostic_code == "spotify_ambiguous_results"
    assert outcome.requires_follow_up is True
    assert outcome.accepted is False and outcome.verified is False


def test_unverified_playback_without_a_diagnostic_does_not_narrate_failure(providers):
    providers.play.return_value = SpotifyDesktopResult(status=DesktopResultStatus.UNVERIFIED)

    result = tools.reproducir_en_spotify.invoke({"cancion": "Test Track"})

    assert result.status is ReceiptStatus.ACCEPTED
    assert "received the command" in result.user_message
    assert "unavailable" not in result.user_message
    assert result.diagnostic_code == "spotify_playback_not_verified"


@pytest.mark.parametrize("states,expected", [
    ((), ReceiptStatus.ACCEPTED),
    ((AutomationState.FOCUSING, AutomationState.FAILED), ReceiptStatus.FAILED),
])
def test_legacy_control_verification_code_does_not_override_known_no_effect(providers, states, expected):
    providers.control.return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.FAILED, message_key="spotify_control_not_verified", states=states
    )

    result = tools.controlar_reproduccion.invoke({"accion": "pause"})

    assert result.status is expected
    assert result.accepted is (expected is ReceiptStatus.ACCEPTED)
    if expected is ReceiptStatus.FAILED:
        assert "received the control" not in result.user_message
