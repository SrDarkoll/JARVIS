"""Autorización biométrica por voz.

La autorización depende exclusivamente de quién habla.
- Si la voz identificada es el admin (DEFAULT_PROFILE_ID) → autorizado
- Si es un perfil desconocido o invitado → no autorizado
"""

from __future__ import annotations

import threading
from datetime import datetime

# Estado de autorización — ahora ligado al profile_id activo
_auth_lock = threading.Lock()
_auth_state: dict = {
    "autorizado": False,
    "profile_id": None,  # qué perfil está autorizado
    "nombre": None,
    "timestamp": 0.0,
    "metodo": None,  # "biometria"
}

DEFAULT_AUTHORIZED_PROFILE = "admin"
AUTH_TTL_SECONDS = 3600  # 1 hora — mientras sea la misma voz, sigue autorizado


def autorizar_por_biometria(profile_id: str, nombre: str) -> None:
    """Llamado por el backend cuando Speechbrain identifica al usuario."""
    with _auth_lock:
        _auth_state["autorizado"] = True
        _auth_state["profile_id"] = profile_id
        _auth_state["nombre"] = nombre
        _auth_state["timestamp"] = datetime.now().timestamp()
        _auth_state["metodo"] = "biometria"


def revocar_autorizacion(profile_id: str | None = None) -> None:
    """Revoca auth de un perfil específico o de todos."""
    with _auth_lock:
        if profile_id is None or _auth_state.get("profile_id") == profile_id:
            _auth_state["autorizado"] = False
            _auth_state["profile_id"] = None
            _auth_state["nombre"] = None
            _auth_state["timestamp"] = 0.0
            _auth_state["metodo"] = None


def verificar_autorizacion(profile_id: str | None = None) -> bool:
    """
    Retorna True si el perfil activo está autorizado.
    Si se pasa profile_id, verifica que sea ese perfil específicamente.
    """
    with _auth_lock:
        if not _auth_state["autorizado"]:
            return False
        # Verificar TTL
        age = datetime.now().timestamp() - float(_auth_state.get("timestamp", 0))
        if age > AUTH_TTL_SECONDS:
            _auth_state["autorizado"] = False
            _auth_state["profile_id"] = None
            _auth_state["nombre"] = None
            _auth_state["timestamp"] = 0.0
            _auth_state["metodo"] = None
            return False
        # Si NO se pide verificar un perfil específico, la sesión activa basta
        if profile_id is None:
            return True
        if _auth_state.get("profile_id") != profile_id:
            return False
        return True


def es_perfil_autorizado(profile_id: str) -> bool:
    """El Administrador (perfil maestro) siempre está autorizado si se identifica por voz."""
    return profile_id == DEFAULT_AUTHORIZED_PROFILE





def get_auth_snapshot() -> dict:
    with _auth_lock:
        return dict(_auth_state)


def es_guest(profile_id: str | None) -> bool:
    """Determina si el perfil es un invitado (no owner).

    Guests: guest_*, tg_*, web_test*, etc.
    Owner: admin (único perfil con acceso completo)
    """
    if not profile_id:
        return True
    pid = str(profile_id).strip().lower()
    if pid == DEFAULT_AUTHORIZED_PROFILE:
        return False
    if pid.startswith("guest_") or pid.startswith("tg_"):
        return True
    if pid.startswith("web_") or pid.startswith("test"):
        return True
    return True


def es_owner(profile_id: str | None) -> bool:
    """Determina si el perfil es el owner (Señor)."""
    if not profile_id:
        return False
    return str(profile_id).strip().lower() == DEFAULT_AUTHORIZED_PROFILE


def activar_perfil_invitado(profile_id: str, nombre: str) -> None:
    """Actualiza el estado activo con el perfil del invitado identificado.

    A diferencia de autorizar_por_biometria, NO marca autorizado=True.
    Solo expone el nombre/profile_id para que get_system_msg y
    _respuesta_rapida_social usen el nombre correcto del invitado.
    """
    with _auth_lock:
        _auth_state["autorizado"] = False  # invitados nunca están autorizados
        _auth_state["profile_id"] = str(profile_id or "").strip()
        _auth_state["nombre"] = str(nombre or "Invitado").strip()
        _auth_state["timestamp"] = datetime.now().timestamp()
        _auth_state["metodo"] = "guest_voice"



