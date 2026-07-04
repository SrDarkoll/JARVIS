def test_briefing_accepts_plain_string_llm_response(monkeypatch, tmp_path):
    from core.service_container import services  # pyright: ignore[reportMissingImports]
    from tools import utilities  # pyright: ignore[reportMissingImports]

    class PlainStringLLM:
        def invoke(self, _prompt):
            return "<think>internal</think> Daily briefing ready."

    original_llm = services.llm
    cache = services.noticias_cache
    cache.clear()
    services.llm = PlainStringLLM()
    monkeypatch.setattr(utilities, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(utilities, "obtener_noticias_newsapi", lambda _lang: "- One headline")

    try:
        utilities.generar_resumen_noticias(forzar=True)
        assert cache["listo"] is True
        assert cache["resumen"] == "Daily briefing ready."
    finally:
        services.llm = original_llm
        cache.clear()


def test_extract_llm_text_accepts_openai_dict_shape():
    from tools.utilities import _extract_llm_text  # pyright: ignore[reportMissingImports]

    response = {"choices": [{"message": {"content": "Briefing from dict."}}]}

    assert _extract_llm_text(response) == "Briefing from dict."
