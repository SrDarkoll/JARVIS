"""Normalizacion de texto y tratamiento 'Administrador' para respuestas."""

from __future__ import annotations

import re

_MOJIBAKE_REPLACEMENTS = {
    "\u00c3\u00a1": "\u00e1",
    "\u00c3\u00a9": "\u00e9",
    "\u00c3\u00ad": "\u00ed",
    "\u00c3\u00b3": "\u00f3",
    "\u00c3\u00ba": "\u00fa",
    "\u00c3\u0081": "\u00c1",
    "\u00c3\u2030": "\u00c9",
    "\u00c3\u008d": "\u00cd",
    "\u00c3\u201c": "\u00d3",
    "\u00c3\u0161": "\u00da",
    "\u00c3\u00b1": "\u00f1",
    "\u00c3\u2018": "\u00d1",
    "\u00c3\u00bc": "\u00fc",
    "\u00c3\u0153": "\u00dc",
    "\u00c2\u00bf": "\u00bf",
    "\u00c2\u00a1": "\u00a1",
    "\u00c2\u00b0": "\u00b0",
    "\u00e2\u20ac\u201d": "-",
    "\u00e2\u20ac\u201c": "-",
    "\u00e2\u20ac\u02dc": "'",
    "\u00e2\u20ac\u2122": "'",
    "\u00e2\u20ac\u0153": '"',
    "\u00e2\u20ac\u009d": '"',
    "\u00e2\u20ac\u00a6": "...",
    "\u00e2\u2020\u2019": "->",
    "\u00c3\u009f": "\u00df",
}


def reparar_unicode(texto: str) -> str:
    if not isinstance(texto, str):
        return texto
    limpio = texto
    for malo, bueno in _MOJIBAKE_REPLACEMENTS.items():
        limpio = limpio.replace(malo, bueno)
    return limpio


def normalizar_tratamiento_admin(texto: str) -> str:
    if not isinstance(texto, str):
        return texto

    limpio = reparar_unicode(texto)
    limpio = re.sub(r"(?i)\bsr\.?\b", "Administrador", limpio)
    limpio = re.sub(r"Señor|Senor", "Administrador", limpio, flags=re.IGNORECASE)
    limpio = re.sub(r"\bSr\.\s*\.", "Administrador", limpio, flags=re.IGNORECASE)
    limpio = re.sub(r"(?i)\bseñor\s*[,.:]?\s*señor\b", "Administrador", limpio)
    limpio = re.sub(r"(?i)\bseñor\b", "Administrador", limpio)
    limpio = re.sub(r"[ \t]{2,}", " ", limpio)
    return reparar_unicode(limpio.strip())
