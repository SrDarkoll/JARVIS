from __future__ import annotations

import os

import pytest
from core.setup_configuration import (
    ProviderSetup,
    SetupAttemptLimiter,
    SetupConfigurationError,
    apply_provider_configuration,
    provider_environment,
    save_provider_configuration,
    validate_provider_setup,
)


def test_provider_payload_accepts_only_bounded_gemini_or_groq_keys():
    setup = validate_provider_setup("gemini", "g" * 20, True)
    assert setup.provider == "gemini"
    assert "g" * 20 not in repr(setup)

    for provider in ("", "openai", "../gemini"):
        with pytest.raises(SetupConfigurationError, match="invalid_provider"):
            validate_provider_setup(provider, "x" * 20, True)

    for key in ("short", "x\nsecret", "x\0secret", "é" * 20, "x" * 513):
        with pytest.raises(SetupConfigurationError, match="invalid_key_format"):
            validate_provider_setup("groq", key, True)

    with pytest.raises(SetupConfigurationError, match="invalid_request"):
        validate_provider_setup("groq", "q" * 24, False)


def test_provider_environment_changes_only_selected_provider_values():
    setup = ProviderSetup("groq", "q" * 24, True)

    result = provider_environment(
        {"KEEP": "value", "GEMINI_API_KEY": "existing"},
        setup,
    )

    assert result == {
        "KEEP": "value",
        "GEMINI_API_KEY": "existing",
        "GROQ_API_KEY": "q" * 24,
        "JARVIS_LLM_PROVIDER": "groq",
    }


def test_atomic_provider_write_preserves_comments_and_unrelated_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# user config\nWEATHER_LOCATION=Matamoros\nGROQ_API_KEY=old\n",
        encoding="utf-8",
    )

    result = save_provider_configuration(
        env_file,
        ProviderSetup("gemini", "g" * 24, True),
    )

    text = env_file.read_text(encoding="utf-8")
    assert "# user config" in text
    assert "WEATHER_LOCATION=Matamoros" in text
    assert f'GEMINI_API_KEY="{"g" * 24}"' in text
    assert "JARVIS_LLM_PROVIDER=gemini" in text
    assert "GROQ_API_KEY=old" in text
    assert result == {"provider": "gemini", "configured": True}


def test_atomic_replace_failure_preserves_previous_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("KEEP=previous\n", encoding="utf-8")

    def fail_replace(*_args):
        raise OSError("locked")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(SetupConfigurationError, match="configuration_save_failed"):
        save_provider_configuration(
            env_file,
            ProviderSetup("groq", "q" * 24, True),
        )

    assert env_file.read_text(encoding="utf-8") == "KEEP=previous\n"
    assert list(tmp_path.glob(".env.*.tmp")) == []


def test_setup_attempt_limiter_uses_a_bounded_sliding_window():
    now = [100.0]
    limiter = SetupAttemptLimiter(
        limit=2,
        window_seconds=10,
        max_clients=2,
        clock=lambda: now[0],
    )

    assert limiter.check("client-a") == 0
    assert limiter.check("client-a") == 0
    assert limiter.check("client-a") == 10

    now[0] = 111.0
    assert limiter.check("client-a") == 0
    limiter.reset()
    assert limiter.check("client-a") == 0


def test_provider_apply_validates_before_write_and_commits_after_activation(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("KEEP=value\n", encoding="utf-8")
    environ = {"KEEP": "value"}
    observed = {}

    def prepare(proposed):
        observed["proposed"] = dict(proposed)
        assert env_file.read_text(encoding="utf-8") == "KEEP=value\n"
        return object()

    def activate(_candidate):
        assert "GROQ_API_KEY" not in environ
        assert "GROQ_API_KEY" in env_file.read_text(encoding="utf-8")
        return {"provider": "groq", "runtime_ready": True}

    result = apply_provider_configuration(
        provider="groq",
        api_key="q" * 24,
        save_local=True,
        env_path=env_file,
        environ=environ,
        prepare_runtime=prepare,
        activate_runtime=activate,
    )

    assert observed["proposed"]["GROQ_API_KEY"] == "q" * 24
    assert environ["JARVIS_LLM_PROVIDER"] == "groq"
    assert environ["GROQ_API_KEY"] == "q" * 24
    assert result == {
        "ok": True,
        "configured": True,
        "provider": "groq",
        "runtime_ready": True,
        "restart_required": False,
    }


def test_provider_apply_restores_env_file_when_activation_fails(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("KEEP=previous\n", encoding="utf-8")
    environ = {"KEEP": "previous"}

    def fail_activation(_candidate):
        raise RuntimeError("activation failed")

    with pytest.raises(RuntimeError, match="activation failed"):
        apply_provider_configuration(
            provider="gemini",
            api_key="g" * 24,
            save_local=True,
            env_path=env_file,
            environ=environ,
            prepare_runtime=lambda _env: object(),
            activate_runtime=fail_activation,
        )

    assert env_file.read_text(encoding="utf-8") == "KEEP=previous\n"
    assert "GEMINI_API_KEY" not in environ
