from core.security.tool_policy import tool_requires_authorization


def _es_bloqueo_autorizacion(texto: str) -> bool:
    t = (texto or "").lower()
    return "acceso_denegado" in t or ("requiere" in t and "autoriz" in t)


def _mensaje_solicitar_autorizacion() -> str:
    return (
        "ACCESO_DENEGADO: Esta accion requiere autorizacion previa. "
        "Identifiquese por voz para elevar su nivel de autorizacion."
    )


def _tool_requiere_autorizacion(tool_name: str) -> bool:
    return tool_requires_authorization(tool_name)
