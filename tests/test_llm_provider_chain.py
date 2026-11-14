from core.llm_providers import ProviderSlot, provider_headers, resolve_llm_provider_config

ALL = {"DEEPSEEK_API_KEY": "ds", "GEMINI_API_KEY": "g", "GROQ_API_KEY": "q",
       "JARVIS_OLLAMA_BASE_URL": "http://127.0.0.1:11434/v1"}

def _names(cfg): return [s.provider for s in cfg.chain]

def test_chain_default_order_and_defaults():
    cfg = resolve_llm_provider_config(ALL)
    assert _names(cfg) == ["deepseek", "gemini", "groq", "ollama"]
    ds, ollama = cfg.chain[0], cfg.chain[3]
    assert (ds.model, ds.base_url, ds.timeout) == (
        "deepseek-v4.1-flash", "https://opencode.ai/inference/go/openai/v1", 20.0)
    assert (ollama.model, ollama.api_key) == ("qwen2.5:7b", "ollama")
    assert (cfg.primary_provider, cfg.fallback_provider) == ("deepseek", "gemini")
    assert cfg.chain[1].timeout == 10.0

def test_ollama_absent_without_base_url():
    assert "ollama" not in _names(resolve_llm_provider_config({"GEMINI_API_KEY": "g"}))

def test_requested_provider_moves_to_front():
    cfg = resolve_llm_provider_config({**ALL, "JARVIS_LLM_PROVIDER": "groq"})
    assert _names(cfg) == ["groq", "deepseek", "gemini", "ollama"]

def test_requested_provider_without_credentials_keeps_default_order():
    cfg = resolve_llm_provider_config({"GEMINI_API_KEY": "g", "GROQ_API_KEY": "q",
                                       "JARVIS_LLM_PROVIDER": "deepseek"})
    assert _names(cfg) == ["gemini", "groq"]

def test_ollama_only_chain_is_configured():
    cfg = resolve_llm_provider_config({"JARVIS_OLLAMA_BASE_URL": "http://localhost:11434/v1"})
    assert cfg.configured and cfg.primary_provider == "ollama"

def test_plain_http_only_for_private_ollama():
    assert "ollama" not in _names(resolve_llm_provider_config(
        {"JARVIS_OLLAMA_BASE_URL": "http://example.com:11434/v1"}))
    assert "ollama" in _names(resolve_llm_provider_config(
        {"JARVIS_OLLAMA_BASE_URL": "http://192.168.1.50:11434/v1"}))
    assert "deepseek" not in _names(resolve_llm_provider_config(
        {"DEEPSEEK_API_KEY": "ds", "JARVIS_DEEPSEEK_BASE_URL": "http://api.deepseek.com/v1"}))

def test_deepseek_timeout_is_clamped():
    cfg = resolve_llm_provider_config({"DEEPSEEK_API_KEY": "ds", "JARVIS_DEEPSEEK_TIMEOUT": "999"})
    assert cfg.chain[0].timeout == 120.0

def test_provider_headers_send_session_only_to_opencode():
    go = ProviderSlot("deepseek", "m", "k", "https://opencode.ai/inference/go/openai/v1", 20.0)
    direct = ProviderSlot("deepseek", "m", "k", "https://api.deepseek.com/v1", 20.0)
    gemini = ProviderSlot("gemini", "m", "k", "https://generativelanguage.googleapis.com/v1beta/openai/", 10.0)
    assert provider_headers(go, session_id="abc") == {"User-Agent": "JARVIS/0.1.0", "x-opencode-session": "abc"}
    assert provider_headers(direct, session_id="abc") == {"User-Agent": "JARVIS/0.1.0"}
    assert provider_headers(gemini, session_id="abc") == {}
