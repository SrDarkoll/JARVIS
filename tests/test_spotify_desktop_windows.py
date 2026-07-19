import pytest

from tools.spotify_desktop.models import SpotifyCandidate
from tools.spotify_desktop.windows import (
    SpotifyUIAutomationAdapter,
    SpotifyWindow,
    WindowsSpotifyWindowAdapter,
)


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


class FakeControl:
    def __init__(self, *, name="", control_type="", children=None):
        self.name = name
        self.control_type = control_type
        self.children = children or []
        self.text = ""
        self.invoked = False
        self.focused = False
        self.typed = []

    def window_text(self):
        return self.name

    def descendants(self):
        return list(self.children)

    def set_edit_text(self, value):
        self.text = value

    def set_focus(self):
        self.focused = True

    def type_keys(self, value, **_kwargs):
        self.typed.append(value)

    def invoke(self):
        self.invoked = True


def test_search_uses_accessible_combo_box_before_shortcut():
    search = FakeControl(name="What do you want to play?", control_type="ComboBox")
    root = FakeControl(children=[search])
    shortcuts = []
    text_inputs = []
    adapter = SpotifyUIAutomationAdapter(
        root_factory=lambda _handle: root,
        send_shortcut=lambda shortcut: shortcuts.append(shortcut),
        send_text=lambda value: text_inputs.append(value),
    )

    adapter.search(500, "No te apartes de mi Vicentico")

    assert search.text == "No te apartes de mi Vicentico"
    assert search.focused
    assert shortcuts == ["{ENTER}"]
    assert text_inputs == []


def test_search_uses_documented_ctrl_k_when_control_is_initially_missing():
    search = FakeControl(name="Search", control_type="ComboBox")
    roots = iter([FakeControl(), FakeControl(children=[search])])
    shortcuts = []
    text_inputs = []
    adapter = SpotifyUIAutomationAdapter(
        root_factory=lambda _handle: next(roots),
        send_shortcut=lambda shortcut: shortcuts.append(shortcut),
        send_text=lambda value: text_inputs.append(value),
    )

    adapter.search(501, "Killer Queen Queen")

    assert search.text == "Killer Queen Queen"
    assert shortcuts == ["^k", "{ENTER}"]
    assert text_inputs == []


def test_result_play_buttons_are_mapped_to_candidates_and_deduplicated():
    first = FakeControl(
        name="Reproducir Killer Queen, de Queen", control_type="Button"
    )
    duplicate = FakeControl(
        name="Reproducir Killer Queen, de Queen", control_type="Button"
    )
    root = FakeControl(children=[first, duplicate])
    adapter = SpotifyUIAutomationAdapter(root_factory=lambda _handle: root)

    candidates = adapter.read_candidates(502)

    assert candidates == [
        SpotifyCandidate(
            element_id="candidate-0",
            title="Killer Queen",
            artist="Queen",
            kind="track",
            subtitle="Song",
        )
    ]
    assert adapter.activate(candidates[0])
    assert first.invoked
    assert not duplicate.invoked


def test_english_result_accessible_name_is_supported():
    play = FakeControl(name="Play Dreams by Fleetwood Mac", control_type="Button")
    adapter = SpotifyUIAutomationAdapter(
        root_factory=lambda _handle: FakeControl(children=[play])
    )

    assert adapter.read_candidates(503)[0].artist == "Fleetwood Mac"


def test_control_uses_accessible_button_name():
    next_button = FakeControl(name="Siguiente", control_type="Button")
    root = FakeControl(children=[next_button])
    adapter = SpotifyUIAutomationAdapter(root_factory=lambda _handle: root)

    assert adapter.control(504, "next")
    assert next_button.invoked


def test_now_playing_reads_localized_accessible_metadata():
    metadata = FakeControl(
        name="Est\u00e1s escuchando: Killer Queen de Queen",
        control_type="Group",
    )
    adapter = SpotifyUIAutomationAdapter(
        root_factory=lambda _handle: FakeControl(children=[metadata])
    )

    assert adapter.now_playing(505) == ("Killer Queen", "Queen")
