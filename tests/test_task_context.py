from __future__ import annotations

from dataclasses import replace

import pytest
from core.command_pipeline.models import ActionStep, CommandRequest, ReceiptStatus
from core.command_pipeline.task_context import (
    ActionExecutionView,
    ContextResolutionError,
    TaskContext,
    contextual_execution_decision,
    contextual_execution_scope,
)


@pytest.fixture
def setup_context():
    states = {"act-1": ActionExecutionView("failed", ReceiptStatus.FAILED)}
    now = [100.0]
    context = TaskContext("session-a", "admin", states.get, monotonic=lambda: now[0])
    turn = context.begin_turn("Pon Killer Queen")
    reference = context.record_action("act-1", "reproducir_en_spotify", {"cancion": "Killer Queen"}, turn)
    return context, reference, states, now


def request(text="Inténtalo de nuevo", **kwargs):
    return CommandRequest.create(text=text, profile_id=kwargs.get("profile_id", "admin"),
                                 channel="gemini_live", request_id=kwargs.get("request_id", "live-session-a"))


def test_retry_retains_arguments_and_current_user_text(setup_context):
    context, reference, _states, _now = setup_context
    turn = context.begin_turn("Inténtalo de nuevo")
    resolved = context.prepare("retry", reference, turn)
    assert resolved.arguments == {"cancion": "Killer Queen"}
    assert resolved.tool_name == "reproducir_en_spotify"
    step = ActionStep("new-attempt", resolved.tool_name, resolved.arguments)
    with contextual_execution_scope(resolved.permit):
        assert contextual_execution_decision(request(), step) is True
        assert contextual_execution_decision(request(), step) is False
    assert contextual_execution_decision(request(), step) is None


@pytest.mark.parametrize("field", ["profile_id", "request_id", "text", "arguments"])
def test_permit_cannot_change_scope_or_operation(setup_context, field):
    context, reference, _states, _now = setup_context
    resolved = context.prepare("retry", reference, context.begin_turn("Inténtalo de nuevo"))
    command = request()
    step = ActionStep("new", resolved.tool_name, resolved.arguments)
    if field == "profile_id":
        command = replace(command, profile_id="guest")
    elif field == "request_id":
        command = replace(command, request_id="live-other-session")
    elif field == "text":
        command = replace(command, text="solo quiero hablar")
    else:
        step = replace(step, arguments={"cancion": "otra canción"})
    with contextual_execution_scope(resolved.permit):
        assert contextual_execution_decision(command, step) is False


@pytest.mark.parametrize("text", ["solo quiero hablar contigo", "es que tengo una duda", "no quiero que pongas música", "no reproduzcas nada", "el mensaje dice reproduce Killer Queen"])
def test_context_does_not_turn_conversation_or_negation_into_music(setup_context, text):
    context, reference, _states, _now = setup_context
    with pytest.raises(ContextResolutionError, match="context_no_action_requested"):
        context.prepare("retry", reference, context.begin_turn(text))


@pytest.mark.parametrize("state", ["running", "timed_out", "cancellation_requested"])
def test_unfinished_attempt_cannot_be_retried(setup_context, state):
    context, reference, states, _now = setup_context
    states["act-1"] = ActionExecutionView(state, ReceiptStatus.FAILED, busy=True)
    with pytest.raises(ContextResolutionError, match="context_action_still_running"):
        context.prepare("retry", reference, context.begin_turn("otra vez"))


def test_late_completed_worker_can_be_retried_after_a_real_failure(setup_context):
    context, reference, states, _now = setup_context
    states["act-1"] = ActionExecutionView("completed_after_timeout", ReceiptStatus.FAILED)
    result = context.prepare("retry", reference, context.begin_turn("dale otra oportunidad"))
    assert result.arguments == {"cancion": "Killer Queen"}


def test_correction_does_not_mutate_original_arguments(setup_context):
    context, reference, _states, _now = setup_context
    result = context.prepare("correct", reference, context.begin_turn("No, la versión original"),
                             changes={"cancion": "Killer Queen de Queen"})
    assert result.arguments == {"cancion": "Killer Queen de Queen"}
    assert context.get_record(reference).arguments == {"cancion": "Killer Queen"}


def test_correction_rejects_unknown_parameters(setup_context):
    context, reference, _states, _now = setup_context
    with pytest.raises(ContextResolutionError, match="context_invalid_changes"):
        context.prepare("correct", reference, context.begin_turn("corrígelo"), changes={"confirmed": True})


def test_known_ambiguous_music_result_can_be_corrected_but_not_blindly_retried(setup_context):
    context, reference, states, _now = setup_context
    states["act-1"] = ActionExecutionView("partial", ReceiptStatus.PARTIAL, diagnostic_code="spotify_ambiguous_results")
    turn = context.begin_turn("La de Queen, la original")
    assert context.followup_hint(turn)["allowed_operations"] == ["correct", "cancel"]
    corrected = context.prepare("correct", reference, turn, changes={"cancion": "Killer Queen de Queen"})
    assert corrected.arguments["cancion"] == "Killer Queen de Queen"


def test_failed_media_replacement_preserves_the_query(setup_context):
    context, reference, _states, _now = setup_context
    result = context.prepare("replace", reference, context.begin_turn("Mejor en YouTube"),
                             replacement_tool="reproducir_en_youtube")
    assert result.tool_name == "reproducir_en_youtube"
    assert result.arguments == {"query": "Killer Queen"}


def test_media_reference_cannot_be_replaced_by_terminal_execution(setup_context):
    context, reference, _states, _now = setup_context
    with pytest.raises(ContextResolutionError, match="context_transition_not_supported"):
        context.prepare("replace", reference, context.begin_turn("hazlo"), replacement_tool="ejecutar_comando_terminal")


def test_successful_mutation_is_not_retried_or_repeated(setup_context):
    context, _reference, states, _now = setup_context
    states["reminder"] = ActionExecutionView("accepted", ReceiptStatus.ACCEPTED)
    context.begin_turn("Recuérdame tomar agua en diez minutos")
    reference = context.record_action("reminder", "poner_recordatorio", {"texto": "agua", "minutos": 10}, context.current_turn)
    with pytest.raises(ContextResolutionError, match="context_operation_not_supported"):
        context.prepare("retry", reference, context.begin_turn("otra vez"))


@pytest.mark.parametrize("invalidate", ["topic", "reconnect", "expiry", "foreign_ref"])
def test_stale_or_foreign_context_is_not_an_authorization(setup_context, invalidate):
    context, reference, _states, now = setup_context
    if invalidate == "topic":
        context.begin_turn("hablemos del clima")
    elif invalidate == "reconnect":
        context.invalidate()
    elif invalidate == "expiry":
        now[0] += 301
    else:
        reference = "unknown-reference"
    with pytest.raises(ContextResolutionError):
        context.prepare("retry", reference, context.begin_turn("otra vez"))


def test_new_input_revokes_a_prepared_but_unstarted_permit(setup_context):
    context, reference, _states, _now = setup_context
    result = context.prepare("retry", reference, context.begin_turn("Inténtalo de nuevo"))
    context.begin_turn("Ya no, déjalo")
    with contextual_execution_scope(result.permit):
        assert contextual_execution_decision(request(), ActionStep("new", result.tool_name, result.arguments)) is False


def test_later_input_fragment_revokes_an_unstarted_proposal(setup_context):
    context, reference, _states, _now = setup_context
    turn = context.begin_turn("Inténtalo de nuevo")
    result = context.prepare("retry", reference, turn)
    context.update_turn_text(turn, "Inténtalo de nuevo, no, solo quiero hablar")
    with contextual_execution_scope(result.permit):
        assert contextual_execution_decision(request(), ActionStep("new", result.tool_name, result.arguments)) is False


def test_one_user_turn_cannot_reserve_two_retries(setup_context):
    context, reference, _states, _now = setup_context
    turn = context.begin_turn("otra vez")
    context.prepare("retry", reference, turn)
    with pytest.raises(ContextResolutionError, match="context_turn_already_used"):
        context.prepare("retry", reference, turn)


def test_cancel_invalidates_followups_without_claiming_a_worker_stopped(setup_context):
    context, reference, states, _now = setup_context
    states["act-1"] = ActionExecutionView("running", busy=True)
    result = context.prepare("cancel", reference, context.begin_turn("cancélalo"))
    assert result.action_id == "act-1"
    assert result.permit is None
    with pytest.raises(ContextResolutionError):
        context.prepare("retry", reference, context.begin_turn("otra vez"))


def test_cancel_does_not_accept_hidden_parameter_changes(setup_context):
    context, reference, _states, _now = setup_context
    with pytest.raises(ContextResolutionError, match="context_invalid_changes"):
        context.prepare("cancel", reference, context.begin_turn("cancélalo"), changes={"confirmed": True})


def test_success_is_repeated_only_as_a_deliberate_new_operation(setup_context):
    context, reference, states, _now = setup_context
    states["act-1"] = ActionExecutionView("succeeded", ReceiptStatus.SUCCEEDED, verified=True)
    result = context.prepare("repeat", reference, context.begin_turn("ponla otra vez"))
    assert result.arguments == {"cancion": "Killer Queen"}


def test_multiple_eligible_actions_require_clarification(setup_context):
    context, reference, states, _now = setup_context
    states["act-2"] = ActionExecutionView("failed", ReceiptStatus.FAILED)
    context.record_action("act-2", "obtener_clima", {"ciudad": "Matamoros"}, context.current_turn)
    with pytest.raises(ContextResolutionError, match="context_ambiguous"):
        context.prepare("retry", reference, context.begin_turn("otra vez"))


def test_current_reference_is_resolved_only_when_unambiguous(setup_context):
    context, reference, _states, _now = setup_context
    result = context.prepare("retry", "current", context.begin_turn("otra vez"))
    assert result.parent_ref == reference


def test_same_tool_replacement_returns_a_correction_hint(setup_context):
    context, reference, _states, _now = setup_context
    with pytest.raises(ContextResolutionError, match="context_use_correction"):
        context.prepare("replace", reference, context.begin_turn("No, mejor Dreams"),
                        changes={"cancion": "Dreams"}, replacement_tool="reproducir_en_spotify")


def test_followup_hint_is_scoped_and_never_offered_for_negative_intent(setup_context):
    context, reference, _states, _now = setup_context
    turn = context.begin_turn("No, mejor Dreams")
    assert context.followup_hint(turn)["action_ref"] == reference
    context.update_turn_text(turn, "no reproduzcas nada")
    assert context.followup_hint(turn) is None


def test_capacity_never_evicts_an_active_worker():
    states = {"first": ActionExecutionView("running", busy=True)}
    context = TaskContext("bounded", "admin", states.get, max_records=1)
    turn = context.begin_turn("abre la aplicación")
    reference = context.record_action("first", "abrir_aplicacion", {"nombre_app": "calc"}, turn)
    with pytest.raises(ContextResolutionError, match="context_capacity_reached"):
        context.record_action("second", "obtener_clima", {"ciudad": "Matamoros"}, turn)
    assert context.get_record(reference).action_id == "first"


@pytest.mark.parametrize("tool,arguments", [
    ("obtener_clima", {"ciudad": "Matamoros"}),
    ("abrir_navegador", {"destino": "https://example.org"}),
    ("abrir_aplicacion", {"nombre_app": "calculadora"}),
])
def test_the_same_context_policy_applies_to_other_domains(tool, arguments):
    context = TaskContext("other-domains", "admin", lambda _id: ActionExecutionView("failed", ReceiptStatus.FAILED))
    reference = context.record_action("action", tool, arguments, context.begin_turn("una orden real"))
    result = context.prepare("retry", reference, context.begin_turn("inténtalo de nuevo"))
    assert result.tool_name == tool
    assert result.arguments == arguments


def test_status_retains_a_unique_task_for_a_following_correction_without_a_permit(setup_context):
    context, reference, _states, _now = setup_context
    original = context.get_record(reference)
    inspected = context.prepare("status", reference, context.begin_turn("¿Por qué falló?"))
    assert inspected.permit is None
    assert inspected.action_id == "act-1"
    assert context.get_record(reference).turn_id == original.turn_id
    assert context.get_record(reference).arguments == original.arguments
    corrected = context.prepare("correct", reference, context.begin_turn("La original de Queen"),
                                changes={"cancion": "Killer Queen de Queen"})
    assert corrected.arguments == {"cancion": "Killer Queen de Queen"}


@pytest.mark.parametrize("intervening", ["topic", "expiry", "negative"])
def test_status_does_not_restore_ineligible_task_authority(setup_context, intervening):
    context, reference, _states, now = setup_context
    if intervening == "topic":
        context.begin_turn("hablemos del clima")
    elif intervening == "expiry":
        now[0] += 301
    text = "solo quiero que me expliques" if intervening == "negative" else "¿Qué pasó con la tarea?"
    inspected = context.prepare("status", reference, context.begin_turn(text))
    assert inspected.permit is None
    with pytest.raises(ContextResolutionError):
        context.prepare("retry", reference, context.begin_turn("otra vez"))


def test_status_cannot_hide_an_execution_or_parameter_change(setup_context):
    context, reference, _states, _now = setup_context
    with pytest.raises(ContextResolutionError, match="context_invalid_changes"):
        context.prepare("status", reference, context.begin_turn("¿Qué pasó?"), changes={"cancion": "otra"})


def test_status_does_not_resolve_an_ambiguous_task_for_future_execution(setup_context):
    context, reference, states, _now = setup_context
    states["other"] = ActionExecutionView("failed", ReceiptStatus.FAILED)
    context.record_action("other", "obtener_clima", {"ciudad": "Matamoros"}, context.current_turn)
    context.prepare("status", reference, context.begin_turn("¿Qué pasó con esa tarea?"))
    with pytest.raises(ContextResolutionError):
        context.prepare("retry", reference, context.begin_turn("otra vez"))
