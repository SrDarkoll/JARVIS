from __future__ import annotations

from dataclasses import dataclass

from voice.live_context import LiveContextPolicy, build_live_context


@dataclass(frozen=True)
class Message:
    role: str
    content: str


def test_live_context_is_bounded_and_uses_recent_turns():
    history = [
        Message("user", f"turn-{index}-" + "x" * 300)
        for index in range(20)
    ]

    context = build_live_context(
        base_instruction="JARVIS system",
        language="es",
        profile_name="Ramiro",
        profile_facts="prefiere respuestas directas",
        history=history,
        allowed_tool_names=("obtener_clima",),
        policy=LiveContextPolicy(max_chars=1800, recent_turns=6),
    )

    assert len(context) <= 1800
    assert "turn-19" in context
    assert "turn-0" not in context
    assert "Ramiro" in context
    assert "obtener_clima" in context


def test_live_context_redacts_secrets_paths_and_hidden_reasoning():
    context = build_live_context(
        base_instruction=(
            "key=AIza-secret-value C:\\private\\file.txt "
            "https://provider.test/socket?token=gsk_private_value"
        ),
        language="en",
        profile_name="User",
        profile_facts="thought: private chain",
        history=(
            Message("assistant", "reasoning: hidden trace"),
            Message("user", "visible request"),
        ),
        allowed_tool_names=(),
        policy=LiveContextPolicy(max_chars=1000, recent_turns=6),
    )

    assert "AIza-secret-value" not in context
    assert "gsk_private_value" not in context
    assert "C:\\private" not in context
    assert "private chain" not in context
    assert "hidden trace" not in context
    assert "visible request" in context


def test_live_context_language_instruction_is_unmistakable():
    spanish = build_live_context(
        base_instruction="JARVIS",
        language="es",
        profile_name="Administrador",
        profile_facts="",
        history=(),
        allowed_tool_names=(),
        policy=LiveContextPolicy(),
    )
    english = build_live_context(
        base_instruction="JARVIS",
        language="en",
        profile_name="Administrator",
        profile_facts="",
        history=(),
        allowed_tool_names=(),
        policy=LiveContextPolicy(),
    )

    assert "RESPONDE INEQUÍVOCAMENTE EN ESPAÑOL" in spanish
    assert "RESPOND UNMISTAKABLY IN ENGLISH" in english
