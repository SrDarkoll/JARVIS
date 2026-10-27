from dataclasses import replace

import pytest
from core.command_pipeline.deterministic import DeterministicPlanner
from core.command_pipeline.models import (
    ActionPlan, CommandOutcome, CommandRequest, ExecutionReceipt, PlanSource, ReceiptStatus,
)
from core.command_pipeline.responses import ResponseComposer


def _request():
    return CommandRequest.create(text="Gracias.", profile_id="admin", channel="chat", language="es")


@pytest.mark.parametrize("claim", [
    "Estoy abriendo el bloc de notas.",
    "Ejecutando comando para minimizar todas las ventanas activas. Abriendo el bloc de notas.",
    "De acuerdo. He guardado el archivo en el escritorio.",
    "Ya añadí la canción a la cola.",
    "I'm opening Spotify now.",
    "I've saved the document.",
])
def test_direct_reply_cannot_report_an_unexecuted_effect(claim):
    request = _request()
    plan = ActionPlan(request_id=request.request_id, source=PlanSource.GROQ, direct_response=claim)

    response = ResponseComposer().compose(request, plan, ())

    assert response.text != claim
    assert response.outcome is CommandOutcome.RESPONDED
    assert "No he ejecutado" in response.text
    assert response.receipts == ()


@pytest.mark.parametrize("reply", [
    "De nada. ¿En qué más te ayudo?",
    "Para abrir el bloc de notas, pulsa Inicio y escribe su nombre.",
    'La frase «Estoy abriendo el bloc» describe una acción en curso.',
    'Por ejemplo: "He abierto el archivo" es una afirmación, no una prueba.',
    "No he abierto ninguna aplicación.",
])
def test_explanations_quotes_and_negations_are_not_execution_claims(reply):
    request = _request()
    plan = ActionPlan(request_id=request.request_id, source=PlanSource.GROQ, direct_response=reply)

    assert ResponseComposer().compose(request, plan, ()).text == reply


@pytest.mark.parametrize("outcome", [CommandOutcome.VERIFIED, CommandOutcome.EXECUTED, CommandOutcome.ACCEPTED])
def test_direct_reply_cannot_manufacture_an_execution_outcome(outcome):
    request = _request()
    plan = ActionPlan(request_id=request.request_id, source=PlanSource.GROQ,
                      direct_response="De nada.", direct_outcome=outcome)

    assert ResponseComposer().compose(request, plan, ()).outcome is CommandOutcome.RESPONDED


def test_social_candidate_is_explicitly_conversation_only():
    plan = DeterministicPlanner().plan(_request())
    assert plan is not None
    assert plan.conversation_only is True
    assert replace(plan, direct_response="De nada.").steps == ()


def test_verified_search_cannot_be_rewritten_as_an_unrelated_effect():
    class Synthesizer:
        def synthesize(self, *_args):
            return "He abierto el bloc de notas."
    request = _request()
    plan = ActionPlan(request_id=request.request_id, source=PlanSource.GROQ)
    receipt = ExecutionReceipt(request.request_id, "search", "buscar_en_internet",
                               ReceiptStatus.SUCCEEDED, "dato", "Encontré información.", True, "")
    response = ResponseComposer(synthesizer=Synthesizer()).compose(request, plan, (receipt,))
    assert response.text == "Encontré información."
