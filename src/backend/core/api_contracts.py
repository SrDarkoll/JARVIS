"""Explicit API contracts for key endpoints.

Contracts are lightweight runtime validators to catch silent regressions in
request/response payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContractResult:
    ok: bool
    error: str = ""


def _ensure_dict(payload: Any) -> ContractResult:
    if isinstance(payload, dict):
        return ContractResult(True)
    return ContractResult(False, "Payload must be a JSON object.")


def _ensure_type(payload: dict, key: str, allowed_types: tuple[type, ...]) -> ContractResult:
    if key not in payload:
        return ContractResult(False, f"Missing required field: {key}")
    if not isinstance(payload.get(key), allowed_types):
        expected = ", ".join(t.__name__ for t in allowed_types)
        return ContractResult(False, f"Field '{key}' must be of type: {expected}")
    return ContractResult(True)


def validate_language_request(payload: Any) -> ContractResult:
    res = _ensure_dict(payload)
    if not res.ok:
        return res
    return _ensure_type(payload, "language", (str,))


def validate_language_response(payload: Any) -> ContractResult:
    res = _ensure_dict(payload)
    if not res.ok:
        return res
    for key in ("ok", "language", "locale", "name", "tts_swapped"):
        if key not in payload:
            return ContractResult(False, f"Missing required field: {key}")
    if not isinstance(payload.get("ok"), bool):
        return ContractResult(False, "Field 'ok' must be bool")
    if not isinstance(payload.get("language"), str):
        return ContractResult(False, "Field 'language' must be str")
    if not isinstance(payload.get("locale"), str):
        return ContractResult(False, "Field 'locale' must be str")
    if not isinstance(payload.get("name"), str):
        return ContractResult(False, "Field 'name' must be str")
    if not isinstance(payload.get("tts_swapped"), bool):
        return ContractResult(False, "Field 'tts_swapped' must be bool")
    return ContractResult(True)


def validate_voice_response(payload: Any) -> ContractResult:
    res = _ensure_dict(payload)
    if not res.ok:
        return res

    required = ("identity_source",)
    for key in required:
        if key not in payload:
            return ContractResult(False, f"Missing required field: {key}")

    if not isinstance(payload.get("identity_source"), str):
        return ContractResult(False, "Field 'identity_source' must be str")

    if "response" in payload and not isinstance(payload.get("response"), str):
        return ContractResult(False, "Field 'response' must be str when present")
    if "should_listen" in payload and not isinstance(payload.get("should_listen"), bool):
        return ContractResult(False, "Field 'should_listen' must be bool when present")
    if (
        "profile_id" in payload
        and payload.get("profile_id") is not None
        and not isinstance(payload.get("profile_id"), str)
    ):
        return ContractResult(False, "Field 'profile_id' must be str|null when present")
    if "nombre" in payload and payload.get("nombre") is not None and not isinstance(payload.get("nombre"), str):
        return ContractResult(False, "Field 'nombre' must be str|null when present")

    return ContractResult(True)


def validate_status_full_response(payload: Any) -> ContractResult:
    res = _ensure_dict(payload)
    if not res.ok:
        return res

    for key in ("status", "llm_ok", "llm_latency_ms", "cpu", "ram", "temp", "weather", "security", "proactive"):
        if key not in payload:
            return ContractResult(False, f"Missing required field: {key}")

    if not isinstance(payload.get("status"), str):
        return ContractResult(False, "Field 'status' must be str")
    if not isinstance(payload.get("llm_ok"), bool):
        return ContractResult(False, "Field 'llm_ok' must be bool")
    if not isinstance(payload.get("llm_latency_ms"), (int, float)):
        return ContractResult(False, "Field 'llm_latency_ms' must be number")
    if not isinstance(payload.get("cpu"), (int, float)):
        return ContractResult(False, "Field 'cpu' must be number")
    if not isinstance(payload.get("ram"), (int, float)):
        return ContractResult(False, "Field 'ram' must be number")
    if not isinstance(payload.get("temp"), (int, float)):
        return ContractResult(False, "Field 'temp' must be number")

    weather = payload.get("weather")
    security = payload.get("security")
    proactive = payload.get("proactive")
    if not isinstance(weather, dict):
        return ContractResult(False, "Field 'weather' must be object")
    if not isinstance(security, dict):
        return ContractResult(False, "Field 'security' must be object")
    if not isinstance(proactive, dict):
        return ContractResult(False, "Field 'proactive' must be object")

    for key in ("temp", "desc"):
        if key not in weather:
            return ContractResult(False, f"Missing required weather field: {key}")

    if "strict_mode" not in security or "blocked_total" not in security:
        return ContractResult(False, "Missing required security fields")
    if "enabled" not in proactive:
        return ContractResult(False, "Missing required proactive field: enabled")

    return ContractResult(True)
