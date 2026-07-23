import pytest
from modules.spotify.desktop.models import SpotifyCandidate
from modules.spotify.desktop.windows import (
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


def test_window_discovery_ignores_auxiliary_spotify_windows():
    adapter = WindowsSpotifyWindowAdapter(
        process_ids=lambda: {42},
        windows=lambda: [
            SpotifyWindow(handle=800, pid=42, title="Artist - Track"),
            SpotifyWindow(handle=801, pid=42, title="Spotify Free"),
        ],
        start_client=lambda: None,
        focus_window=lambda _handle: True,
        window_validator=lambda window: window.handle == 801,
    )

    assert adapter.discover_window() == SpotifyWindow(
        handle=801,
        pid=42,
        title="Spotify Free",
    )


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
        adapter.current_title(SpotifyWindow(handle=905, pid=42, title="Spotify")) == "Expected Artist - Expected Track"
    )
    assert adapter.current_title(SpotifyWindow(handle=904, pid=42, title="Spotify")) == ""


def test_bounds_are_read_for_the_verified_spotify_handle():
    observed = []
    adapter = WindowsSpotifyWindowAdapter(
        process_ids=lambda: {42},
        windows=lambda: [SpotifyWindow(handle=906, pid=42, title="Spotify")],
        start_client=lambda: None,
        focus_window=lambda _handle: True,
        window_bounds=lambda handle: observed.append(handle) or (10, 20, 810, 620),
    )
    window = adapter.ensure_window(timeout=1)

    assert adapter.bounds(window) == (10, 20, 810, 620)
    assert observed == [906]


def test_focus_uses_app_activate_when_set_foreground_is_denied():
    foreground = {"handle": 100}
    activated = []

    def app_activate(pid):
        activated.append(pid)
        foreground["handle"] = 907
        return True

    adapter = WindowsSpotifyWindowAdapter(
        process_ids=lambda: {42},
        windows=lambda: [SpotifyWindow(handle=907, pid=42, title="Spotify")],
        start_client=lambda: None,
        focus_window=lambda _handle: False,
        activate_process_window=app_activate,
        foreground_window=lambda: foreground["handle"],
    )
    window = adapter.ensure_window(timeout=1)

    assert adapter.focus(window)
    assert activated == [42]


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


def test_value_pattern_search_pulses_input_event_before_submit():
    class ValuePattern:
        def __init__(self):
            self.value = ""

        def SetValue(self, value):
            self.value = value

    search = FakeControl(name="Search", control_type="ComboBox")
    search.set_edit_text = None
    search.iface_value = ValuePattern()
    shortcuts = []
    adapter = SpotifyUIAutomationAdapter(
        root_factory=lambda _handle: FakeControl(children=[search]),
        send_shortcut=lambda shortcut: shortcuts.append(shortcut),
    )

    adapter.search(502, "Dreams")

    assert search.iface_value.value == "Dreams"
    assert shortcuts == ["{END}x{BACKSPACE}", "{ENTER}"]


def test_result_play_buttons_are_mapped_to_candidates_and_deduplicated():
    first = FakeControl(name="Reproducir Killer Queen, de Queen", control_type="Button")
    duplicate = FakeControl(name="Reproducir Killer Queen, de Queen", control_type="Button")
    root = FakeControl(children=[first, duplicate])
    adapter = SpotifyUIAutomationAdapter(root_factory=lambda _handle: root)

    candidates = adapter.read_candidates(503)

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


def test_real_input_click_is_preferred_when_the_control_supports_it():
    class ClickableControl(FakeControl):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.clicked = False

        def click_input(self):
            self.clicked = True

    play = ClickableControl(
        name="Reproducir Killer Queen, de Queen",
        control_type="Button",
    )
    adapter = SpotifyUIAutomationAdapter(root_factory=lambda _handle: FakeControl(children=[play]))
    candidate = adapter.read_candidates(503)[0]

    assert adapter.activate(candidate)
    assert play.clicked
    assert not play.invoked


def test_offscreen_result_is_scrolled_into_view_and_invoked():
    class ScrollItem:
        def __init__(self, control):
            self.control = control

        def ScrollIntoView(self):
            self.control.scrolled = True
            self.control.visible = True

    class OffscreenControl(FakeControl):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.clicked = False
            self.scrolled = False
            self.visible = False
            self.iface_scroll_item = ScrollItem(self)

        def is_visible(self):
            return self.visible

        def click_input(self):
            self.clicked = True

    play = OffscreenControl(
        name="Reproducir Killer Queen, de Queen",
        control_type="Button",
    )
    adapter = SpotifyUIAutomationAdapter(root_factory=lambda _handle: FakeControl(children=[play]))
    candidate = adapter.read_candidates(503)[0]

    assert adapter.activate(candidate)
    assert play.scrolled
    assert play.invoked
    assert not play.clicked


def test_english_result_accessible_name_is_supported():
    play = FakeControl(name="Play Dreams by Fleetwood Mac", control_type="Button")
    adapter = SpotifyUIAutomationAdapter(root_factory=lambda _handle: FakeControl(children=[play]))

    assert adapter.read_candidates(504)[0].artist == "Fleetwood Mac"


def test_control_uses_accessible_button_name():
    next_button = FakeControl(name="Siguiente", control_type="Button")
    root = FakeControl(children=[next_button])
    adapter = SpotifyUIAutomationAdapter(root_factory=lambda _handle: root)

    assert adapter.control(505, "next")
    assert next_button.invoked


def test_now_playing_reads_localized_accessible_metadata():
    metadata = FakeControl(
        name="Est\u00e1s escuchando: Killer Queen de Queen",
        control_type="Group",
    )
    adapter = SpotifyUIAutomationAdapter(root_factory=lambda _handle: FakeControl(children=[metadata]))

    assert adapter.now_playing(505) == ("Killer Queen", "Queen")


def test_now_playing_uses_last_artist_separator_when_title_contains_de():
    metadata = FakeControl(
        name=(
            "Est\u00e1s escuchando: No Te Apartes de M\u00ed "
            "(feat. Valeria Bertuccelli) de Vicentico, Valeria Bertuccelli"
        ),
        control_type="Group",
    )
    adapter = SpotifyUIAutomationAdapter(root_factory=lambda _handle: FakeControl(children=[metadata]))

    assert adapter.now_playing(506) == (
        "No Te Apartes de M\u00ed (feat. Valeria Bertuccelli)",
        "Vicentico, Valeria Bertuccelli",
    )


def test_playback_state_ignores_search_result_play_buttons():
    result_button = FakeControl(
        name="Reproducir Killer Queen, de Queen",
        control_type="Button",
    )
    pause_button = FakeControl(name="Pausar", control_type="Button")
    adapter = SpotifyUIAutomationAdapter(
        root_factory=lambda _handle: FakeControl(children=[result_button, pause_button])
    )

    assert adapter.playback_state(507) == "playing"


def test_playback_and_shuffle_states_use_exact_accessible_controls():
    play_button = FakeControl(name="Reproducir", control_type="Button")
    shuffle_button = FakeControl(
        name="Deshabilitar el Modo aleatorio para Killer Queen",
        control_type="Button",
    )
    adapter = SpotifyUIAutomationAdapter(
        root_factory=lambda _handle: FakeControl(children=[play_button, shuffle_button])
    )

    assert adapter.playback_state(508) == "paused"
    assert adapter.shuffle_state(508) is True
