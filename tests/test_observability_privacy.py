from __future__ import annotations

from pathlib import Path


def test_observability_file_redacts_nested_secrets(monkeypatch, tmp_path) -> None:
    from core import jarvis_observability

    log_path = tmp_path / "events.jsonl"
    secret = "FAKE_OBSERVABILITY_SECRET_123456"
    monkeypatch.setattr(jarvis_observability, "OBS_LOG_FILE", str(log_path))

    jarvis_observability.obs_event(
        "privacy_probe",
        token=secret,
        nested={"api_key": secret, "safe": "visible"},
        url=f"https://provider.invalid/ws?key={secret}&safe=yes",
    )
    jarvis_observability._log_worker.queue.join()

    content = log_path.read_text(encoding="utf-8")
    assert secret not in content
    assert content.count("[REDACTED]") >= 3
    assert "visible" in content
    assert "safe=yes" in content


def test_log_worker_rotates_bounded_jsonl_files(tmp_path: Path) -> None:
    from core.jarvis_observability import LogWorker

    log_path = tmp_path / "events.jsonl"
    worker = LogWorker(max_bytes=180, backup_count=2)
    worker.start()
    try:
        for index in range(20):
            worker.log(str(log_path), f"event-{index}-" + ("x" * 60))
        worker.queue.join()
    finally:
        worker.stop()
        worker.join(timeout=2)

    files = sorted(tmp_path.glob("events.jsonl*"))
    assert log_path in files
    assert (tmp_path / "events.jsonl.1") in files
    assert len(files) <= 3
