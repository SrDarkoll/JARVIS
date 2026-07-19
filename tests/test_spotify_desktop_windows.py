import pytest

from tools.spotify_desktop.windows import SpotifyWindow, WindowsSpotifyWindowAdapter


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def test_existing_spotify_window_is_reused_without_starting():
    starts = []
    adapter = WindowsSpotifyWindowAdapter(
        process_ids=lambda: {40, 41},
        windows=lambda: [SpotifyWindow(handle=900, pid=41, title="Artist - Track")],
        start_client=lambda: starts.append(True),
        focus_window=lambda handle: handle == 900,
    )

    window = adapter.ensure_window(timeout=1)

    assert window.handle == 900
    assert starts == []


def test_closed_spotify_is_started_and_polled_until_ready():
    clock = FakeClock()
    starts = []
    snapshots = iter(
        [
            [],
            [],
            [SpotifyWindow(handle=901, pid=42, title="Spotify Premium")],
        ]
    )
    adapter = WindowsSpotifyWindowAdapter(
        process_ids=lambda: {42},
        windows=lambda: next(snapshots),
        start_client=lambda: starts.append(True),
        focus_window=lambda handle: handle == 901,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        poll_interval=0.25,
    )

    assert adapter.ensure_window(timeout=2).handle == 901
    assert starts == [True]
    assert clock.value == 0.5


def test_window_from_unrelated_process_is_rejected():
    adapter = WindowsSpotifyWindowAdapter(
        process_ids=lambda: {42},
        windows=lambda: [SpotifyWindow(handle=902, pid=99, title="Spotify login")],
        start_client=lambda: None,
        focus_window=lambda _handle: True,
        poll_interval=0,
    )

    with pytest.raises(TimeoutError, match="^spotify_window_not_found$"):
        adapter.ensure_window(timeout=0)


def test_focus_is_revalidated_against_foreground_handle():
    foreground = {"handle": 0}

    def focus(handle: int) -> bool:
        foreground["handle"] = handle
        return True

    adapter = WindowsSpotifyWindowAdapter(
        process_ids=lambda: {42},
        windows=lambda: [SpotifyWindow(handle=903, pid=42, title="Spotify")],
        start_client=lambda: None,
        focus_window=focus,
        foreground_window=lambda: foreground["handle"],
    )

    window = adapter.ensure_window(timeout=1)
    assert adapter.focus(window)
    assert adapter.is_foreground(window)


def test_current_title_requires_same_process_and_handle():
    adapter = WindowsSpotifyWindowAdapter(
        process_ids=lambda: {42},
        windows=lambda: [
            SpotifyWindow(handle=904, pid=99, title="Spoofed title"),
            SpotifyWindow(handle=905, pid=42, title="Expected Artist - Expected Track"),
        ],
        start_client=lambda: None,
        focus_window=lambda _handle: True,
    )

    assert (
        adapter.current_title(SpotifyWindow(handle=905, pid=42, title="Spotify"))
        == "Expected Artist - Expected Track"
    )
    assert adapter.current_title(SpotifyWindow(handle=904, pid=42, title="Spotify")) == ""
