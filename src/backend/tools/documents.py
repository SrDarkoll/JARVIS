"""Explicit local-document opening; no reading, writing or shell interpretation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from core import jarvis_state
from core.command_pipeline.models import ReceiptStatus, ToolExecutionResult
from langchain_core.tools import tool
from utils.jarvis_auth import verificar_autorizacion
from utils.jarvis_i18n import get_current_language

from tools.desktop_document_resolver import DocumentResolutionError, resolve_local_document


def _launch_document(target: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(target))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])


@tool
def abrir_documento(nombre: str) -> ToolExecutionResult:
    """Solicita abrir un PDF, PPTX, DOCX, XLSX o TXT local por nombre o prefijo único.

    Busca sólo en el escritorio o JARVIS_DOCUMENT_ROOTS, con un nivel de carpetas.
    No lee su contenido, no ejecuta scripts y nunca elige entre varios resultados.
    """
    spanish = not get_current_language().startswith("en")

    def result(status, code, es, en):
        message = es if spanish else en
        return ToolExecutionResult(result={"requested_name": nombre}, user_message=message, status=status,
                                   accepted=status is ReceiptStatus.ACCEPTED, diagnostic_code=code)

    if not verificar_autorizacion(jarvis_state.get_active_profile_id()):
        return result(ReceiptStatus.BLOCKED, "document_authorization_required",
                      "Se requiere autorización para abrir documentos.", "Authorization is required to open documents.")
    try:
        target = resolve_local_document(nombre.strip())
    except DocumentResolutionError as exc:
        if exc.diagnostic_code == "document_ambiguous":
            return result(ReceiptStatus.PARTIAL, exc.diagnostic_code,
                          "Hay varios documentos con ese nombre o prefijo. Indica un nombre más preciso.",
                          "Several documents match that name or prefix. Please give a more precise name.")
        return result(ReceiptStatus.FAILED, exc.diagnostic_code,
                      "No pude resolver ese documento dentro de las ubicaciones y tipos permitidos.",
                      "That document could not be resolved within the allowed locations and file types.")
    if target is None:
        return result(ReceiptStatus.FAILED, "document_not_found",
                      "No encontré ese documento en las ubicaciones permitidas.",
                      "No matching document was found in the allowed locations.")
    try:
        _launch_document(target)
    except (OSError, subprocess.SubprocessError):
        return result(ReceiptStatus.FAILED, "document_launch_failed",
                      "No se pudo enviar la solicitud de apertura del documento.", "The document open request failed.")
    return result(ReceiptStatus.ACCEPTED, "document_launch_accepted",
                  f"Solicité abrir {target.name}. No he confirmado que la aplicación lo haya mostrado.",
                  f"Requested opening {target.name}. I have not confirmed that its application displayed it.")
