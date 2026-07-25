from __future__ import annotations

from core.media_state import (
    clear_media_state,
    get_last_media_source,
    set_last_media_source,
)


def test_media_state_is_isolated_by_profile():
    clear_media_state()
    set_last_media_source("admin", "youtube")
    set_last_media_source("guest_1", "spotify")

    assert get_last_media_source("admin") == "youtube"
    assert get_last_media_source("guest_1") == "spotify"
    clear_media_state()


def test_media_state_rejects_unknown_sources():
    set_last_media_source("admin", "unsupported")

    assert get_last_media_source("admin") == ""
