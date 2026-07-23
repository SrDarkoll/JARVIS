# Unified Readable Log

## Goal

Persist a single chronological, human-readable account of JARVIS activity in
`src/backend/logs/log.txt`, including conversations, tool execution, frontend
HUD events, backend runtime logs, warnings, errors, and uncategorized console
output.

## Format

Every physical line starts with a local timestamp including milliseconds and a
category:

```text
[2026-07-22 18:42:13.245] [CONVERSATION] USUARIO(admin): Jarvis, pon Monster de Meg and Dia.
[2026-07-22 18:42:14.817] [CONVERSATION] JARVIS(admin): Reproduciendo Monster de Meg & Dia.
[2026-07-22 18:42:14.820] [TOOL] END reproducir_en_spotify | status=ok | elapsed_ms=1179.20
```

Embedded line breaks are escaped so one event cannot create timestamp-less
continuation lines. Context is rendered as stable `key=value` pairs.

## Capture Boundaries

- `core/unified_log.py` owns UTF-8 writes, timestamps, redaction, rotation, and
  optional stdout/stderr capture.
- `core/runtime_logger.py` mirrors runtime INFO/WARNING/ERROR records to the
  unified writer while preserving terminal output.
- `core/brain/processor.py` records user and JARVIS conversation turns for
  classic chat, streaming chat, voice, Telegram, and internal callers.
- `core/brain/tool_manager.py` records every tool START and END, including
  blocked, unavailable, successful, and failed outcomes.
- `/api/frontend/log` records each HUD event with the `FRONTEND` category.
- `jarvis_backend.py` installs console capture early enough to retain legacy
  `print` diagnostics and framework output.
- Existing JSONL observability remains unchanged and is additionally mirrored
  as readable `EVENT` records.

## Privacy And Safety

Conversation text is intentionally stored in plaintext because that is the
requested diagnostic behavior. Known environment secret values and common
credential assignments, bearer tokens, authorization headers, and sensitive
URL query parameters are redacted before writing. The log directory remains
ignored by Git.

## Retention

Logging is enabled by default, rotates at 5 MiB, and retains three backups.
`JARVIS_UNIFIED_LOG_ENABLED`, `JARVIS_UNIFIED_LOG_MAX_BYTES`, and
`JARVIS_UNIFIED_LOG_BACKUP_COUNT` make those defaults configurable.

## Failure Behavior

Logging must never break chat, tools, voice, or startup. Writer failures are
swallowed after a best-effort terminal warning to the original stream. Console
capture can be installed once and always forwards text to the original stream.

## Verification

- Unit tests cover formatting, redaction, rotation, multiline handling, and
  stdout tee behavior.
- Integration tests cover runtime categories, frontend forwarding,
  conversation turns, and tool lifecycle entries.
- Full Python, frontend syntax, dependency, and whitespace checks remain release
  gates.
