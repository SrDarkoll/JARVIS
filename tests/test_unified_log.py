import io
import re
import sys
from pathlib import Path

import pytest

from core import unified_log
from core.runtime_logger import log_warning


@pytest.fixture
def unified_log_path(tmp_path: Path):
    log_path = tmp_path / "log.txt"
    unified_log.configure_unified_log(
        log_path,
        enabled=True,
        max_bytes=1024 * 1024,
        backup_count=2,
    )
    yield log_path
    unified_log.reset_unified_log_for_tests()


def test_write_log_adds_timestamp_category_context_and_escapes_newlines(
    unified_log_path: Path,
):
    unified_log.write_log(
        "conversation",
        "USUARIO(admin): Hola\nJarvis",
        channel="voice",
    )

    lines = unified_log_path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 1
    assert re.match(
        r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\] "
        r"\[CONVERSATION\] ",
        lines[0],
    )
    assert r"USUARIO(admin): Hola\nJarvis" in lines[0]
    assert "channel=voice" in lines[0]


def test_write_conversation_uses_readable_roles(unified_log_path: Path):
    unified_log.write_conversation(
        "USUARIO",
        "Pon Monster de Meg and Dia",
        profile_id="admin",
        channel="voice",
    )
    unified_log.write_conversation(
        "JARVIS",
        "Reproduciendo Monster.",
        profile_id="admin",
        channel="voice",
    )

    content = unified_log_path.read_text(encoding="utf-8")

    assert "[CONVERSATION] USUARIO(admin): Pon Monster de Meg and Dia" in content
    assert "[CONVERSATION] JARVIS(admin): Reproduciendo Monster." in content


def test_write_log_redacts_environment_and_explicit_credentials(
    unified_log_path: Path,
    monkeypatch,
):
    secret = "secret-value-that-must-not-leak"
    monkeypatch.setenv("TEST_PROVIDER_API_KEY", secret)

    unified_log.write_log(
        "ERROR",
        f"Provider failed with Authorization: Bearer abcdefghijklmnopqrstuvwxyz and {secret}",
        api_key="another-sensitive-value",
        url="https://example.test/callback?token=query-secret&safe=yes",
    )

    content = unified_log_path.read_text(encoding="utf-8")

    assert secret not in content
    assert "abcdefghijklmnopqrstuvwxyz" not in content
    assert "another-sensitive-value" not in content
    assert "query-secret" not in content
    assert content.count("[REDACTED]") >= 4
    assert "safe=yes" in content


def test_unified_log_rotates_at_the_configured_size(tmp_path: Path):
    log_path = tmp_path / "log.txt"
    unified_log.configure_unified_log(
        log_path,
        enabled=True,
        max_bytes=240,
        backup_count=2,
    )
    try:
        for index in range(20):
            unified_log.write_log("TEST", f"event-{index}-" + ("x" * 60))

        assert log_path.is_file()
        assert (tmp_path / "log.txt.1").is_file()
        assert len(list(tmp_path.glob("log.txt*"))) <= 3
    finally:
        unified_log.reset_unified_log_for_tests()


def test_tee_preserves_console_output_and_captures_complete_lines(
    unified_log_path: Path,
):
    original = io.StringIO()
    tee = unified_log.UnifiedLogTee(original, "STDOUT")

    tee.write("Spotify ")
    tee.write("ready\nsecond line\n")
    tee.flush()

    assert original.getvalue() == "Spotify ready\nsecond line\n"
    content = unified_log_path.read_text(encoding="utf-8")
    assert "[STDOUT] Spotify ready" in content
    assert "[STDOUT] second line" in content


def test_disabled_unified_log_does_not_create_a_file(tmp_path: Path):
    log_path = tmp_path / "log.txt"
    unified_log.configure_unified_log(
        log_path,
        enabled=False,
        max_bytes=1024,
        backup_count=1,
    )
    try:
        unified_log.write_log("TEST", "not persisted")
        assert not log_path.exists()
    finally:
        unified_log.reset_unified_log_for_tests()


def test_runtime_logger_writes_one_categorized_record(unified_log_path: Path):
    log_warning("browser_recognition_unavailable", reason="network")

    content = unified_log_path.read_text(encoding="utf-8")

    assert content.count("browser_recognition_unavailable") == 1
    assert "[WARNING] browser_recognition_unavailable" in content
    assert "reason=network" in content


def test_console_capture_is_idempotent_and_suppresses_runtime_duplicates(
    tmp_path: Path,
    monkeypatch,
):
    log_path = tmp_path / "log.txt"
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    try:
        assert unified_log.install_console_capture(
            log_file=log_path,
            enabled=True,
            max_bytes=1024 * 1024,
            backup_count=1,
        )
        installed_stdout = sys.stdout
        assert unified_log.install_console_capture(
            log_file=log_path,
            enabled=True,
            max_bytes=1024 * 1024,
            backup_count=1,
        )
        assert sys.stdout is installed_stdout

        print("legacy diagnostic")
        sys.stderr.write("[INFO] JARVIS: runtime line\n")
        log_warning("runtime line")
    finally:
        unified_log.reset_unified_log_for_tests()

    assert "legacy diagnostic\n" in stdout.getvalue()
    assert "[INFO] JARVIS: runtime line\n" in stderr.getvalue()
    content = log_path.read_text(encoding="utf-8")
    assert "[STDOUT] legacy diagnostic" in content
    assert content.count("runtime line") == 1
    assert "[WARNING] runtime line" in content
