from __future__ import annotations


def _make_safe_mock_tool():
    """Creates a mock that absolutely prevents any side effects."""
    def safe_mock(tool_name, args, user_input, source="router"):
        if tool_name in ("reproducir_en_spotify", "reproducir_mix_spotify", "abrir_navegador", "abrir_youtube"):
            return f"{tool_name}:mocked"
        return f"{tool_name}:ok"
    return safe_mock


def test_compound_router_executes_music_then_weather(monkeypatch):
    from core.brain import processor, router

    calls = []

    def fake_tool(tool_name, args, user_input, source="router"):
        calls.append((tool_name, args, source))
        return f"{tool_name}:ok"

    monkeypatch.setattr(processor, "_invocar_tool_wrapper", fake_tool)

    reply = router._router_hibrido(
        "ponme reggaeton lento de cnco y dime el clima para hoy en malibu"
    )

    assert reply is not None
    assert [call[0] for call in calls] == ["reproducir_en_spotify", "obtener_clima"]
    assert calls[0][1]["cancion"] == "reggaeton lento de cnco"
    assert calls[1][1]["ciudad"] == "Malibu"
    assert "Paso 1" in reply
    assert "Paso 2" in reply


def test_compound_router_handles_verbose_nba_then_spotify(monkeypatch):
    from core.brain import processor, router

    calls = []

    def fake_tool(tool_name, args, user_input, source="router"):
        calls.append((tool_name, args, source))
        return f"{tool_name}:ok"

    monkeypatch.setattr(processor, "_invocar_tool_wrapper", fake_tool)

    reply = router._router_hibrido(
        "first, you need to search NBA matches for today and then I need you to "
        "put a song on Spotify that his name is Reggaeton Lento of CNCO. "
        "Can you do that for me?"
    )

    assert reply is not None
    assert [call[0] for call in calls] == ["obtener_deportes_espn", "reproducir_en_spotify"]
    assert calls[0][1]["consulta"] == "hoy"
    assert "reggaeton lento" in calls[1][1]["cancion"]
    assert "cnco" in calls[1][1]["cancion"]


def test_router_handles_spanish_reproduzcas_spotify_song(monkeypatch):
    from core.brain import processor, router

    calls = []

    def fake_tool(tool_name, args, user_input, source="router"):
        calls.append((tool_name, args, source))
        return f"{tool_name}:ok"

    monkeypatch.setattr(processor, "_invocar_tool_wrapper", fake_tool)

    reply = router._router_hibrido(
        "necesito que reproduzcas en spotify una cancion que se llama "
        "1000 miles de vanessa carlton"
    )

    assert reply == "reproducir_en_spotify:ok"
    assert [call[0] for call in calls] == ["reproducir_en_spotify"]
    assert calls[0][1]["cancion"] == "1000 miles de vanessa carlton"


def test_router_routes_spotify_mix_request_to_mix_tool(monkeypatch):
    from core.brain import processor, router

    calls = []

    def fake_tool(tool_name, args, user_input, source="router"):
        calls.append((tool_name, args, source))
        return f"{tool_name}:ok"

    monkeypatch.setattr(processor, "_invocar_tool_wrapper", fake_tool)

    reply = router._router_hibrido("pon un mix similar a bad bunny en spotify")

    assert reply == "reproducir_mix_spotify:ok"
    assert [call[0] for call in calls] == ["reproducir_mix_spotify"]
    assert calls[0][1]["semilla"] == "bad bunny"


def test_compound_router_handles_spanish_music_then_search(monkeypatch):
    from core.brain import processor, router

    calls = []

    def fake_tool(tool_name, args, user_input, source="router"):
        calls.append((tool_name, args, source))
        return f"{tool_name}:ok"

    monkeypatch.setattr(processor, "_invocar_tool_wrapper", fake_tool)

    reply = router._router_hibrido(
        "necesito que reproduzcas en spotify una cancion que se llama "
        "1000 miles de vanessa carlton y despues busques cuanto esta "
        "ahorita el kilo de tortillas"
    )

    assert reply is not None
    assert [call[0] for call in calls] == ["reproducir_en_spotify", "buscar_en_internet"]
    assert calls[0][1]["cancion"] == "1000 miles de vanessa carlton"
    assert "kilo de tortillas" in calls[1][1]["query"]


def test_router_weather_wins_over_social_status(monkeypatch):
    from core.brain import processor, router

    calls = []

    def fake_tool(tool_name, args, user_input, source="router"):
        calls.append((tool_name, args, source))
        return f"{tool_name}:ok"

    monkeypatch.setattr(processor, "_invocar_tool_wrapper", fake_tool)

    reply = router._router_hibrido(
        "how are you today how is the weather doing like in malibu"
    )

    assert reply == "obtener_clima:ok"
    assert [call[0] for call in calls] == ["obtener_clima"]
    assert calls[0][1]["ciudad"] == "Malibu"


def test_preflight_compound_handles_reminder_then_music(monkeypatch):
    from core import core_tools
    from core.brain import processor

    reminders = []
    calls = []

    def fake_reminder(text, minutes):
        reminders.append((text, minutes))

    def fake_tool(tool_name, args, user_input, source="router"):
        calls.append((tool_name, args, source))
        return f"{tool_name}:ok"

    monkeypatch.setattr(core_tools, "agregar_recordatorio", fake_reminder)
    monkeypatch.setattr(processor, "_invocar_tool_wrapper", fake_tool)

    reply, should_listen = processor._preflight(
        "recuerdame revisar el horno en 5 minutos y pon despacito",
        "admin",
    )

    assert should_listen is False
    assert reminders == [("revisar el horno", 5)]
    assert [call[0] for call in calls] == ["reproducir_en_spotify"]
    assert calls[0][1]["cancion"] == "despacito"
    assert "Paso 1" in reply
    assert "Paso 2" in reply


def test_preflight_compound_handles_music_then_reminder(monkeypatch):
    from core import core_tools
    from core.brain import processor

    reminders = []
    calls = []

    def fake_reminder(text, minutes):
        reminders.append((text, minutes))

    def fake_tool(tool_name, args, user_input, source="router"):
        calls.append((tool_name, args, source))
        return f"{tool_name}:ok"

    monkeypatch.setattr(core_tools, "agregar_recordatorio", fake_reminder)
    monkeypatch.setattr(processor, "_invocar_tool_wrapper", fake_tool)

    reply, should_listen = processor._preflight(
        "pon despacito y recuerdame revisar el horno en 5 minutos",
        "admin",
    )

    assert should_listen is False
    assert [call[0] for call in calls] == ["reproducir_en_spotify"]
    assert calls[0][1]["cancion"] == "despacito"
    assert reminders == [("revisar el horno", 5)]
    assert "Paso 1" in reply
    assert "Paso 2" in reply


def test_router_stop_the_music_is_not_media_control(monkeypatch):
    from core.brain import processor, router

    calls = []

    def fake_tool(tool_name, args, user_input, source="router"):
        calls.append((tool_name, args, source))
        return f"{tool_name}:ok"

    monkeypatch.setattr(processor, "_invocar_tool_wrapper", fake_tool)

    reply = router._router_hibrido("jarvis can you stop the music please")

    assert reply != "controlar_reproduccion:ok"
    assert calls == []


def test_router_bare_stop_is_not_media_control(monkeypatch):
    from core.brain import processor, router

    calls = []

    def fake_tool(tool_name, args, user_input, source="router"):
        calls.append((tool_name, args, source))
        return f"{tool_name}:ok"

    monkeypatch.setattr(processor, "_invocar_tool_wrapper", fake_tool)

    reply = router._router_hibrido("stop")

    assert reply != "controlar_reproduccion:ok"
    assert calls == []


def test_router_spanish_para_music_is_not_media_control(monkeypatch):
    from core.brain import processor, router

    calls = []

    def fake_tool(tool_name, args, user_input, source="router"):
        calls.append((tool_name, args, source))
        return f"{tool_name}:ok"

    monkeypatch.setattr(processor, "_invocar_tool_wrapper", fake_tool)

    reply = router._router_hibrido("jarvis para la musica por favor")

    assert reply != "controlar_reproduccion:ok"
    assert calls == []
