import sys
from datetime import datetime

from core import jarvis_config, jarvis_state
from core.brain import brain_utils
from core.jarvis_config import RAG_ENABLED
from core.runtime_logger import log_warning
from engines.memory_rag import rag_motor
from langchain_core.messages import SystemMessage
from services import security_manager
from utils.jarvis_auth import es_guest, get_auth_snapshot, verificar_autorizacion
from utils.jarvis_i18n import BACKEND_TRANSLATIONS, get_current_language

if jarvis_config.ROOT_DIR not in sys.path:
    sys.path.insert(0, jarvis_config.ROOT_DIR)

try:
    import jarvis_settings
except ImportError:
    class jarvis_settings:
        ASSISTANT_NAME = "J.A.R.V.I.S."
        ASSISTANT_FULLNAME = "Just A Rather Very Intelligent System"
        OWNER_TITLE = "Administrator"
        COMPANY_NAME = "TU_EMPRESA"
        LOCATION = "TU_CIUDAD, TU_PAIS"
        GUEST_PROMPT = ""
        OWNER_PROMPT = ""



def get_system_msg(user_input: str, profile_id: str | None = None) -> SystemMessage:
    """Genera el mensaje del sistema dinamico con RAG y biometria confirmada."""
    from core.core_tools import _obtener_contexto_memoria_entrelazada

    pid = jarvis_state.normalize_profile_id(
        profile_id or jarvis_state.get_active_profile_id(),
        jarvis_state.DEFAULT_PROFILE_ID,
    )
    memory_ctx = _obtener_contexto_memoria_entrelazada(pid)

    private_facts = brain_utils._limpiar_contexto_memoria(memory_ctx.get("private_facts") or "")
    shared_facts = brain_utils._limpiar_contexto_memoria(memory_ctx.get("shared_facts") or "")

    memoria_texto = private_facts if private_facts else "No data recorded yet."

    if shared_facts:
        memoria_texto += "\n\n--- Shared memory between profiles ---\n" + shared_facts

    # Enriquecimiento RAG
    if RAG_ENABLED:
        try:
            rag_context = rag_motor.buscar_contexto(user_input, top_k=5, profile_id=pid)
            if rag_context:
                rag_limpio = brain_utils._limpiar_contexto_memoria(rag_context)
                if rag_limpio:
                    memoria_texto += "\n\n" + rag_limpio
        except Exception as e:
            log_warning("rag_context_retrieval_failed", error=str(e))

    lang = get_current_language()
    bt = BACKEND_TRANSLATIONS.get(lang, BACKEND_TRANSLATIONS["en"])

    autorizado = (
        bt["auth_yes"]
        if verificar_autorizacion(pid)
        else bt["auth_no"]
    )

    _pid_activo = pid
    _pid_es_owner = _pid_activo == jarvis_state.DEFAULT_PROFILE_ID
    _nombre_activo = bt["profile_administrator"] if _pid_es_owner else bt["profile_guest"]
    perfil_activo = f"{_nombre_activo} ({bt['profile_label']}: {_pid_activo})"
    try:
        _snap = get_auth_snapshot() or {}
        _snap_pid = jarvis_state.normalize_profile_id(_snap.get("profile_id"), "")
        if _snap_pid == pid:
            _pid_activo = _snap_pid
            _pid_es_owner = _pid_activo == jarvis_state.DEFAULT_PROFILE_ID
            _nombre_activo = _snap.get("nombre") or (bt["profile_administrator"] if _pid_es_owner else bt["profile_guest"])
            perfil_activo = f"{_nombre_activo} ({bt['profile_label']}: {_pid_activo})"
    except Exception as e:
        log_warning("auth_snapshot_read_failed", error=str(e))

    ahora = datetime.now()
    getattr(jarvis_settings, "LOCALE", "en-US")
    # For fallback or simple cases where LOCALE might not affect strftime directly without locale.setlocale
    # but we can use simple ISO or standard formats.
    # For now, let's just use a standard format that fits English/Global.
    fecha_legible = ahora.strftime("%A, %B %d, %Y, %H:%M")

    es_invitado = es_guest(pid)

    if es_invitado:
        content = bt["guest_prompt"].format(
            assistant_name=jarvis_settings.ASSISTANT_NAME,
            assistant_fullname=jarvis_settings.ASSISTANT_FULLNAME,
            owner_title=jarvis_settings.OWNER_TITLE,
            location=jarvis_settings.LOCATION,
            fecha_legible=fecha_legible,
            nombre_activo=_nombre_activo,
            memoria_texto=memoria_texto
        )
        return SystemMessage(content=content)

    # Mensaje para el Administrador (owner)
    with security_manager.SECURITY_LOCK:
        strict_status = (
            bt["status_active"] if bool(security_manager.SECURITY_POLICY.get("strict_mode")) else bt["status_inactive"]
        )

    content = bt["owner_prompt"].format(
        assistant_name=jarvis_settings.ASSISTANT_NAME,
        assistant_fullname=jarvis_settings.ASSISTANT_FULLNAME,
        owner_title=jarvis_settings.OWNER_TITLE,
        company_name=jarvis_settings.COMPANY_NAME,
        location=jarvis_settings.LOCATION,
        fecha_legible=fecha_legible,
        perfil_activo=perfil_activo,
        autorizado=autorizado,
        strict_status=strict_status,
        memoria_texto=memoria_texto
    )
    return SystemMessage(content=content)

