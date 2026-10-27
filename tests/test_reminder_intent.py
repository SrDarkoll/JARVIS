from __future__ import annotations

import pytest
from core.brain.brain_utils import parse_reminder
from core.command_pipeline.deterministic import DeterministicPlanner
from core.command_pipeline.models import CommandRequest

NON_COMMANDS = [
    "Explícame qué significa 'recuérdame tomar agua en 5 minutos'",
    "How do I say 'remind me to drink water in 5 minutes' in Spanish?",
    "No pongas un recordatorio: recuérdame pagar la luz en 10 minutos",
    "La frase recuérdame llamar en 10 minutos es un ejemplo, no una orden.",
    "Recuérdame pagar la luz en 10 minutos, es sólo un ejemplo.",
    "Remind me to call in 10 minutes, but don't create anything.",
]


@pytest.mark.parametrize("text", NON_COMMANDS)
def test_reminder_parser_does_not_turn_mentions_or_examples_into_commands(text):
    assert parse_reminder(text) == (None, None)


@pytest.mark.parametrize("text", NON_COMMANDS)
def test_deterministic_planner_does_not_schedule_mentioned_reminders(text):
    request = CommandRequest.create(text=text, profile_id="admin", channel="chat", language="es")
    plan = DeterministicPlanner().plan(request)
    assert plan is None or all(step.tool_name != "poner_recordatorio" for step in plan.steps)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Recuérdame tomar agua en 5 minutos", ("tomar agua", 5)),
        ("Jarvis, por favor recuérdame llamar a mamá en 2 horas.", ("llamar a mamá", 120)),
        ("Please remind me to drink water in 5 minutes.", ("to drink water", 5)),
        ("Oye Jarvis, recuérdame revisar el horno en 10min", ("revisar el horno", 10)),
        ("Jarvis remind me to leave in 2h, please.", ("to leave", 120)),
        ("Recuérdame no olvidar las llaves en 5 minutos", ("no olvidar las llaves", 5)),
        ("Pon un recordatorio para pagar la luz en 10 minutos", ("pagar la luz", 10)),
        ("Recordatorio para estudiar en 30min", ("estudiar", 30)),
        ("Recuérdame dar las gracias a Ana en 10 minutos", ("dar las gracias a Ana", 10)),
        ("Remind me to say please and thanks in 5 minutes", ("to say please and thanks", 5)),
    ],
)
def test_reminder_parser_preserves_real_commands_and_their_full_content(text, expected):
    assert parse_reminder(text) == expected


@pytest.mark.parametrize("text", [
    "Recuérdame llamar en 1 hora y 30 minutos",
    "Remind me to call in 1 hour and 30 minutes",
    "Recuérdame llamar en 1 hora y media",
])
def test_complex_duration_is_not_silently_reduced_to_one_component(text):
    # Unsupported durations should reach the planner rather than become a wrong timer.
    assert parse_reminder(text) == (None, None)
