from __future__ import annotations

import pytest

from core.brain import processor
from core.command_pipeline.deterministic import DeterministicPlanner
from core.command_pipeline.execution import ToolExecutionService
from core.command_pipeline.orchestrator import CommandOrchestrator
from core.command_pipeline.responses import ResponseComposer


class InMemoryHistory:
    def __init__(self) -> None:
        self.interactions = []

    def get_history(self, _profile_id: str) -> list:
        return []

    def append_interaction(self, request, response) -> None:
        self.interactions.append((request, response))


class GroqMustNotRun:
    def plan(self, *_args, **_kwargs):
        raise AssertionError("deterministic command reached Groq")


@pytest.fixture
def deterministic_pipeline(monkeypatch):
    calls = []

    def invoke_once(request, step):
        calls.append(
            (
                request.request_id,
                step.step_id,
                step.tool_name,
                dict(step.arguments),
            )
        )
        return f"ok:{step.tool_name}"

    orchestrator = CommandOrchestrator(
        deterministic=DeterministicPlanner(),
        groq=GroqMustNotRun(),
        executor=ToolExecutionService(invoke_once),
        responses=ResponseComposer(),
        history=InMemoryHistory(),
    )
    monkeypatch.delenv("JARVIS_LEGACY_COMMAND_PIPELINE", raising=False)
    monkeypatch.setattr(
        processor,
        "_get_command_orchestrator",
        lambda: orchestrator,
    )
    monkeypatch.setattr(
        processor,
        "_record_pipeline_response",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        processor,
        "get_default_location",
        lambda: "Matamoros",
    )
    monkeypatch.setattr(
        processor.pending_spotify_selections,
        "snapshot",
        lambda _profile_id: (),
    )
    return calls


@pytest.mark.parametrize(
    ("command", "expected_tool"),
    [
        ("quien eres", None),
        ("como estas", None),
        ("que hora es", None),
        ("que fecha es hoy", None),
        ("cuanto es 2 + 2", None),
        ("clima en Matamoros", "obtener_clima"),
        (
            "recuerdame pagar la luz en 10 minutos",
            "poner_recordatorio",
        ),
        ("sube el volumen 10", "ajustar_volumen"),
        ("pon Monster de Meg and Dia", "reproducir_en_spotify"),
        (
            "pon un mix similar a Coldplay",
            "reproducir_mix_spotify",
        ),
        ("pausa la musica", "controlar_reproduccion"),
        ("siguiente cancion", "controlar_reproduccion"),
        ("abre spotify", "abrir_aplicacion"),
        ("abre youtube", "abrir_navegador"),
        (
            "busca noticias de tecnologia hoy",
            "buscar_en_internet",
        ),
        (
            "que partidos de la NBA hay hoy",
            "obtener_deportes_espn",
        ),
        ("abre la calculadora", "abrir_aplicacion"),
        ("modo trabajo", "ejecutar_rutina"),
        ("recarga plugins", "recargar_plugins"),
        ("apaga la computadora", "controlar_pc"),
    ],
)
def test_top_twenty_commands_use_one_pipeline_and_execute_once(
    deterministic_pipeline,
    command,
    expected_tool,
) -> None:
    reply, should_listen = processor.procesar_mensaje(
        command,
        profile_id="admin",
    )

    assert reply
    assert should_listen is (command == "como estas")
    if expected_tool is None:
        assert deterministic_pipeline == []
    else:
        assert len(deterministic_pipeline) == 1
        assert deterministic_pipeline[0][2] == expected_tool


@pytest.mark.parametrize(
    ("command", "expected_arguments"),
    [
        ("sube el volumen 10", {"nivel": "+10"}),
        ("baja el volumen 20", {"nivel": "-20"}),
        ("pon el volumen al 35", {"nivel": 35}),
        ("silencia el volumen", {"nivel": 0}),
    ],
)
def test_volume_commands_preserve_requested_operation(
    deterministic_pipeline,
    command,
    expected_arguments,
) -> None:
    processor.procesar_mensaje(command, profile_id="admin")

    assert deterministic_pipeline[0][2] == "ajustar_volumen"
    assert deterministic_pipeline[0][3] == expected_arguments
