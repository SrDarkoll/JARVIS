"""Localized YouTube response formatting."""

from utils.jarvis_i18n import get_current_language


def is_english() -> bool:
    return get_current_language().startswith("en")


def text(en: str, es: str) -> str:
    return en if is_english() else es


def video_label(title: str, channel: str = "") -> str:
    if not channel:
        return f"'{title}'"
    connector = "by" if is_english() else "de"
    return f"'{title}' {connector} {channel}"
