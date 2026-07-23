from __future__ import annotations

from types import SimpleNamespace

from core.command_pipeline.models import (
    ActionPlan,
    ActionStep,
    CommandRequest,
    ExecutionReceipt,
    PlanSource,
    ReceiptStatus,
)
from core.command_pipeline.responses import ResponseComposer
from core.command_pipeline.synthesis import GroqResponseSynthesizer


def _request() -> CommandRequest:
    return CommandRequest.create(
        text="Busca las noticias importantes de Python.",
        profile_id="admin",
        channel="voice",
        language="es",
        request_id="synthesis-1",
    )


def _plan() -> ActionPlan:
    return ActionPlan(
        request_id="synthesis-1",
        source=PlanSource.GROQ,
        steps=(
            ActionStep(
                "search-1",
                "buscar_en_internet",
                {"query": "noticias Python"},
            ),
        ),
    )


def _receipt(
    status: ReceiptStatus = ReceiptStatus.SUCCEEDED,
    *,
    message: str = "Resultado tecnico extenso.",
    result=None,
) -> ExecutionReceipt:
    return ExecutionReceipt(
        request_id="synthesis-1",
        step_id="search-1",
        tool_name="buscar_en_internet",
        status=status,
        result=message if result is None else result,
        user_message=message,
        verified=status is ReceiptStatus.SUCCEEDED,
        diagnostic_code="" if status is ReceiptStatus.SUCCEEDED else "blocked",
    )


class FakeModel:
    def __init__(self, content: str = "Resumen claro y breve.") -> None:
        self.content = content
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return SimpleNamespace(content=self.content)


class FailingSynthesizer:
    def synthesize(self, *_args, **_kwargs):
        raise RuntimeError("provider details must remain internal")


class RecordingSynthesizer:
    def __init__(self) -> None:
        self.calls = []

    def synthesize(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "No debe ejecutarse."


def test_successful_receipt_is_synthesized_once_without_tools() -> None:
    model = FakeModel()
    composer = ResponseComposer(
        synthesizer=GroqResponseSynthesizer(model)
    )

    response = composer.compose(_request(), _plan(), (_receipt(),))

    assert response.text == "Resumen claro y breve."
    assert len(model.calls) == 1
    assert len(model.calls[0]) == 2
    assert "buscar_en_internet" in model.calls[0][1].content


def test_synthesis_input_is_bounded() -> None:
    model = FakeModel()
    synthesizer = GroqResponseSynthesizer(
        model,
        max_input_chars=600,
    )
    long_result = "dato " * 2000

    synthesizer.synthesize(
        _request(),
        _plan(),
        (_receipt(message=long_result, result=long_result),),
        long_result,
    )

    assert len(model.calls[0][1].content) <= 600


def test_blocked_receipt_bypasses_synthesis() -> None:
    synthesizer = RecordingSynthesizer()
    composer = ResponseComposer(synthesizer=synthesizer)
    receipt = _receipt(
        ReceiptStatus.BLOCKED,
        message="Necesito confirmacion explicita.",
    )

    response = composer.compose(_request(), _plan(), (receipt,))

    assert response.text == "Necesito confirmacion explicita."
    assert response.outcome == "failed"
    assert synthesizer.calls == []


def test_duplicate_receipt_bypasses_synthesis() -> None:
    synthesizer = RecordingSynthesizer()
    composer = ResponseComposer(synthesizer=synthesizer)

    response = composer.compose(
        _request(),
        _plan(),
        (_receipt(ReceiptStatus.DUPLICATE),),
    )

    assert response.text == "Resultado tecnico extenso."
    assert synthesizer.calls == []


def test_synthesis_failure_returns_deterministic_fallback() -> None:
    composer = ResponseComposer(synthesizer=FailingSynthesizer())

    response = composer.compose(_request(), _plan(), (_receipt(),))

    assert response.text == "Resultado tecnico extenso."


def test_empty_or_oversized_model_output_returns_fallback() -> None:
    for content in ("", "x" * 701):
        composer = ResponseComposer(
            synthesizer=GroqResponseSynthesizer(
                FakeModel(content),
                max_output_chars=700,
            )
        )

        response = composer.compose(_request(), _plan(), (_receipt(),))

        assert response.text == "Resultado tecnico extenso."


def test_synthesized_output_removes_markup_and_urls_for_tts() -> None:
    model = FakeModel(
        "<think>hidden</think>[Python](https://python.org) "
        "publico novedades en https://example.com."
    )
    composer = ResponseComposer(
        synthesizer=GroqResponseSynthesizer(model)
    )

    response = composer.compose(_request(), _plan(), (_receipt(),))

    assert response.text == "Python publico novedades en"
    assert "http" not in response.text
    assert "hidden" not in response.text
