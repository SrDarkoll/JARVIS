"""Tests for Workflow and Routine Predictor Engine."""

from __future__ import annotations

import tempfile
from datetime import datetime
import pytest

from modules.proactive.models import DesktopContext
from modules.proactive.predictor import WorkflowPredictorEngine
from modules.proactive.reasoner import ProactiveReasoner
from modules.proactive.tools import (
    aprender_patron_rutina,
    consultar_rutinas_predictivas,
    predecir_siguiente_accion,
)


@pytest.fixture
def temp_predictor():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        predictor = WorkflowPredictorEngine(storage_path=tf.name)
        yield predictor


def test_learn_and_list_routines(temp_predictor):
    routine = temp_predictor.learn_routine(
        name="Inicio de Trabajo",
        trigger_type="time",
        trigger_condition="09:00",
        actions=[{"tool": "reproducir_musica_spotify", "args": {"busqueda": "Focus Lofi"}}],
    )
    assert routine.name == "Inicio de Trabajo"
    assert routine.trigger_condition == "09:00"

    routines = temp_predictor.list_routines()
    assert len(routines) == 1
    assert routines[0].routine_id == routine.routine_id

    # Test deletion
    assert temp_predictor.delete_routine(routine.routine_id) is True
    assert len(temp_predictor.list_routines()) == 0


def test_predict_time_routine(temp_predictor):
    temp_predictor.learn_routine(
        name="Standup Meeting",
        trigger_type="time",
        trigger_condition="10:00",
        actions=[{"tool": "consultar_memoria_unificada", "args": {"consulta": "agenda"}}],
        days_of_week=(0, 1, 2, 3, 4, 5, 6),
    )

    # Simulate at 10:05 (Monday = weekday 0)
    fake_time = datetime(2026, 8, 24, 10, 5)
    preds = temp_predictor.predict(now=fake_time)

    assert len(preds) >= 1
    matched = [p for p in preds if "Standup Meeting" in p.title]
    assert len(matched) == 1
    assert matched[0].confidence >= 0.70


def test_predict_dev_context(temp_predictor):
    ctx = DesktopContext(
        active_app="Code.exe",
        window_title="main.py - JARVIS - Visual Studio Code",
        is_ide=True,
    )
    preds = temp_predictor.predict(context=ctx)

    assert any("Desarrollo" in p.title or "dev" in p.prediction_id for p in preds)


def test_proactive_reasoner_integrates_predictions():
    reasoner = ProactiveReasoner()
    ctx = DesktopContext(
        active_app="Code.exe",
        window_title="main.py - Visual Studio Code",
        is_ide=True,
    )
    alerts = reasoner.evaluate_context(ctx)
    assert any("Sugerencia Anticipada" in a.message or "Desarrollo" in a.message for a in alerts)


def test_prediction_langchain_tools():
    # Register routine tool
    out_learn = aprender_patron_rutina.invoke({
        "nombre_rutina": "Pausa Activa",
        "disparador": "16:00",
        "tipo_disparador": "time",
        "herramienta_accion": "reproducir_musica_spotify",
        "argumentos_accion": '{"busqueda": "Chill vibes"}',
    })
    assert "registrada exitosamente" in out_learn

    # List routines tool
    out_list = consultar_rutinas_predictivas.invoke({})
    assert "Pausa Activa" in out_list

    # Predict tool
    out_pred = predecir_siguiente_accion.invoke({"contexto_adicional": ""})
    assert "PREDICCIÓN PROACTIVA" in out_pred or "Probabilidad" in out_pred


def test_prediction_tools_in_base_catalog():
    from core import core_tools

    tools = core_tools.get_base_tools()
    tool_names = {t.name for t in tools}

    assert "predecir_siguiente_accion" in tool_names
    assert "aprender_patron_rutina" in tool_names
    assert "consultar_rutinas_predictivas" in tool_names
