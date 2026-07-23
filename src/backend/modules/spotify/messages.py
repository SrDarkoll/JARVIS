"""Localized Spotify response formatting."""

from utils.jarvis_i18n import get_current_language


def is_english() -> bool:
    return get_current_language().startswith("en")


def text(en: str, es: str) -> str:
    return en if is_english() else es


def track_label(track_name: str, artist: str) -> str:
    if not artist:
        return f"'{track_name}'"
    connector = "by" if is_english() else "de"
    return f"'{track_name}' {connector} {artist}"


def track_plain_label(track_name: str, artist: str) -> str:
    if not artist:
        return track_name
    connector = "by" if is_english() else "de"
    return f"{track_name} {connector} {artist}"


def playback_success_message(
    track_name: str,
    artist: str,
    similar_count: int = 0,
    automix_ok: bool = False,
) -> str:
    label = track_label(track_name, artist)
    if similar_count:
        if automix_ok:
            return text(
                f"Playing {label}. AutoMix updated with {similar_count} similar tracks.",
                f"Reproduciendo {label}. AutoMix actualizado con {similar_count} similares.",
            )
        return text(
            f"Playing {label}. Queue loaded with {similar_count} similar tracks.",
            f"Reproduciendo {label}. Cola con {similar_count} similares.",
        )
    return text(f"Playing {label}.", f"Reproduciendo {label}.")


# Private-name aliases keep compatibility during the package migration.
_spotify_is_english = is_english
_spotify_text = text
_spotify_track_label = track_label
_spotify_track_plain_label = track_plain_label
_spotify_playback_success_message = playback_success_message
