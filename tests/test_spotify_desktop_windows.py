import pytest
from modules.spotify.desktop import windows as spotify_windows
from modules.spotify.desktop.models import SpotifyCandidate
from modules.spotify.desktop.windows import (
    SpotifyUIAutomationAdapter,
    SpotifyWindow,
    WindowsSpotifyWindowAdapter,
)


@pytest.fixture(autouse=True)
def _isolated_spotify_foreground(monkeypatch):
    monkeypatch.setattr(spotify_windows, "_is_spotify_foreground", lambda _handle: True, raising=False)
    monkeypatch.setattr(spotify_windows, "_window_bounds", lambda _handle: (0, 0, 1000, 1000))


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


def test_minimized_main_window_is_usable_without_launching_spotify_again(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(spotify_windows, "IS_WINDOWS", True)
    monkeypatch.setattr(spotify_windows.ctypes, "windll", SimpleNamespace(
        user32=SimpleNamespace(IsIconic=lambda _handle: True),
    ), raising=False)
    monkeypatch.setattr(spotify_windows, "_window_bounds", lambda _handle: (-32000, -32000, -31840, -31972))

    assert spotify_windows._is_usable_spotify_window(SpotifyWindow(handle=801, pid=42, title="Spotify"))


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

    def rectangle(self):
        from types import SimpleNamespace
        return SimpleNamespace(left=200, top=200, right=300, bottom=240)

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


def test_search_falls_back_to_keyboard_when_uia_edit_patterns_fail():
    class IncompatibleSearchControl(FakeControl):
        def set_edit_text(self, _value):
            raise RuntimeError("NoPatternInterfaceError")

        @property
        def iface_value(self):
            raise RuntimeError("NoPatternInterfaceError")

    search = IncompatibleSearchControl(name="Search", control_type="ComboBox")
    shortcuts = []
    text_inputs = []
    adapter = SpotifyUIAutomationAdapter(
        root_factory=lambda _handle: FakeControl(children=[search]),
        send_shortcut=lambda shortcut: shortcuts.append(shortcut),
        send_text=lambda value: text_inputs.append(value),
    )

    adapter.search(502, "Monster Meg and Dia")

    assert search.focused
    assert shortcuts == ["^a{BACKSPACE}", "{ENTER}"]
    assert text_inputs == ["Monster Meg and Dia"]


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


def test_invoke_control_tries_iface_invoke_and_legacy_before_click():
    from modules.spotify.desktop.windows import _invoke_control

    class LegacyControl:
        def __init__(self):
            self.action_done = False

        @property
        def iface_legacy_iaccessible(self):
            class Legacy:
                def __init__(self, parent):
                    self.parent = parent

                def DoDefaultAction(self):
                    self.parent.action_done = True

            return Legacy(self)

    control = LegacyControl()
    assert _invoke_control(control)
    assert control.action_done


def test_control_keyboard_shortcuts_fallback_when_buttons_are_missing():
    shortcuts = []
    adapter = SpotifyUIAutomationAdapter(
        root_factory=lambda _handle: FakeControl(children=[]),
        send_shortcut=lambda shortcut: shortcuts.append(shortcut),
    )

    assert adapter.control(509, "pause")
    assert shortcuts[-1] == " "

    assert adapter.control(509, "next")
    assert shortcuts[-1] == "^{RIGHT}"

    assert adapter.control(509, "previous")
    assert shortcuts[-1] == "^{LEFT}"

    assert adapter.control(509, "shuffle_on")
    assert shortcuts[-1] == "^s"

    assert adapter.control(509, "repeat_on")
    assert shortcuts[-1] == "^r"

    assert adapter.control(509, "volume_up")
    assert shortcuts[-1] == "^{UP}"

    assert adapter.control(509, "volume_down")
    assert shortcuts[-1] == "^{DOWN}"

    assert adapter.control(509, "mute")
    assert shortcuts[-1] == "^+{DOWN}"


def test_is_safe_click_rect_rejects_taskbar_and_out_of_bounds():
    from modules.spotify.desktop.windows import _is_safe_click_rect

    class Rect:
        def __init__(self, l, t, r, b):
            self.left, self.top, self.right, self.bottom = l, t, r, b

    win_bounds = (100, 100, 900, 700)

    # Safe coordinate inside window
    safe = Rect(200, 300, 300, 340)
    assert _is_safe_click_rect(safe, win_bounds) is True

    # Near bottom of window (potential taskbar overlap)
    near_bottom = Rect(200, 680, 300, 710)
    assert _is_safe_click_rect(near_bottom, win_bounds) is False

    # Near top titlebar
    near_top = Rect(200, 110, 300, 130)
    assert _is_safe_click_rect(near_top, win_bounds) is False

    # Outside window completely
    outside = Rect(10, 20, 50, 60)
    assert _is_safe_click_rect(outside, win_bounds) is False


def test_queue_candidate_refuses_unknown_menu_after_shift_f10():
    shortcuts = []
    adapter = SpotifyUIAutomationAdapter(
        root_factory=lambda _handle: FakeControl(children=[]),
        send_shortcut=lambda shortcut: shortcuts.append(shortcut),
    )

    class UnsafeControl(FakeControl):
        def __init__(self):
            super().__init__(name="Play Song Artist", control_type="button")
            self.focused = False

        def rectangle(self):
            class R:
                left, top, right, bottom = 0, 1040, 100, 1080
            return R()

        def set_focus(self):
            self.focused = True

    control = UnsafeControl()
    candidate = SpotifyCandidate(
        element_id="c-unsafe",
        title="Song",
        artist="Artist",
        kind="track",
        subtitle="Song",
    )
    adapter._elements["c-unsafe"] = control

    assert not adapter.queue_candidate(candidate, handle=509)
    assert control.focused
    assert "+{F10}" in shortcuts
    assert "{DOWN}{ENTER}" not in shortcuts


def test_queue_candidate_invokes_only_named_queue_item():
    shortcuts = []
    menu_item = FakeControl(name="Add to queue", control_type="MenuItem")
    adapter = SpotifyUIAutomationAdapter(
        root_factory=lambda _handle: FakeControl(children=[menu_item]),
        send_shortcut=lambda shortcut: shortcuts.append(shortcut),
    )
    candidate_control = FakeControl(name="Play Song by Artist", control_type="Button")
    candidate = SpotifyCandidate(
        element_id="candidate-semantic",
        title="Song",
        artist="Artist",
        kind="track",
        subtitle="Song",
    )
    adapter._elements[candidate.element_id] = candidate_control

    assert adapter.queue_candidate(candidate, handle=509)
    assert candidate_control.focused
    assert "+{F10}" in shortcuts
    assert menu_item.invoked
    assert "{DOWN}{ENTER}" not in shortcuts


def test_like_control_has_no_blind_keyboard_fallback():
    shortcuts = []
    adapter = SpotifyUIAutomationAdapter(
        root_factory=lambda _handle: FakeControl(children=[]),
        send_shortcut=lambda shortcut: shortcuts.append(shortcut),
    )

    assert not adapter.control(509, "like")
    assert "+%b" not in shortcuts
    assert "%+b" not in shortcuts


def test_context_menu_cannot_bypass_checked_click_fallback():
    from types import SimpleNamespace

    class UnsafeMenu(FakeControl):
        clicked = False
        def invoke(self):
            raise RuntimeError("unsupported")
        def rectangle(self):
            return SimpleNamespace(left=200, top=1100, right=300, bottom=1140)
        def click_input(self):
            self.clicked = True

    menu = UnsafeMenu(name="Add to queue", control_type="MenuItem")
    candidate = SpotifyCandidate("queue-target", "Song", "Artist")
    adapter = SpotifyUIAutomationAdapter(
        root_factory=lambda _handle: FakeControl(children=[menu]), send_shortcut=lambda _value: None,
    )
    adapter._elements[candidate.element_id] = FakeControl()

    assert not adapter.queue_candidate(candidate, handle=509)
    assert not menu.clicked


def test_observable_extended_control_states_use_accessible_names():
    controls = [
        FakeControl(name="Disable repeat", control_type="Button"),
        FakeControl(name="Remove from your Liked Songs", control_type="Button"),
        FakeControl(name="Unmute", control_type="Button"),
    ]
    adapter = SpotifyUIAutomationAdapter(
        root_factory=lambda _handle: FakeControl(children=controls),
    )

    assert adapter.repeat_state(509) is True
    assert adapter.saved_state(509) is True
    assert adapter.mute_state(509) is True


@pytest.mark.parametrize("name,control_type", [
    ("Address and search bar", "Edit"),
    ("Search Google or type a URL", "Edit"),
    ("", "Edit"),
    ("", "ComboBox"),
    ("Search in your library", "Edit"),
])
def test_search_readiness_rejects_browser_chrome_and_unrelated_inputs(name, control_type):
    control = FakeControl(name=name, control_type=control_type)
    adapter = SpotifyUIAutomationAdapter(root_factory=lambda _handle: FakeControl(children=[control]))

    assert not adapter.search_available(500)


def test_hidden_search_does_not_stop_readiness_polling():
    search = FakeControl(name="Search", control_type="ComboBox")
    search.is_visible = lambda: False
    adapter = SpotifyUIAutomationAdapter(root_factory=lambda _handle: FakeControl(children=[search]))

    assert not adapter.search_available(500)


def test_search_uses_accessibility_name_not_current_query_value():
    from types import SimpleNamespace

    search = FakeControl(name="Killer Queen Queen", control_type="ComboBox")
    search.element_info = SimpleNamespace(name="¿Qué quieres reproducir?")
    adapter = SpotifyUIAutomationAdapter(root_factory=lambda _handle: FakeControl(children=[search]))

    assert adapter.search_available(500)


def test_search_clicks_actual_editable_before_submitting_from_home():
    class Search(FakeControl):
        clicked = False
        def click_input(self):
            self.clicked = True

    search = Search(name="¿Qué quieres reproducir?", control_type="ComboBox")
    adapter = SpotifyUIAutomationAdapter(root_factory=lambda _handle: FakeControl(children=[search]), send_shortcut=lambda _value: None)

    adapter.search(500, "Dreams Fleetwood Mac")
    assert search.clicked


def test_click_fallback_refuses_rectangle_outside_target_window():
    from types import SimpleNamespace

    class OffWindow(FakeControl):
        clicked = False
        def click_input(self):
            self.clicked = True
        def rectangle(self):
            return SimpleNamespace(left=200, top=1100, right=300, bottom=1140)

    play = OffWindow(name="Play Dreams by Fleetwood Mac", control_type="Button")
    adapter = SpotifyUIAutomationAdapter(root_factory=lambda _handle: FakeControl(children=[play]))
    candidate = adapter.read_candidates(500)[0]

    assert adapter.activate(candidate)
    assert play.invoked
    assert not play.clicked


@pytest.mark.parametrize("control_type", ["Pane", "Custom", "Region"])
def test_now_playing_reads_current_spotify_region_types(control_type):
    metadata = FakeControl(name="Estás escuchando: Killer Queen de Queen", control_type=control_type)
    adapter = SpotifyUIAutomationAdapter(root_factory=lambda _handle: FakeControl(children=[metadata]))

    assert adapter.now_playing(500) == ("Killer Queen", "Queen")


def test_invoke_skips_unsupported_com_patterns_without_throwing():
    from modules.spotify.desktop.windows import _invoke_control

    class UnsupportedInvoke:
        @property
        def iface_invoke(self):
            raise RuntimeError("NoPatternInterfaceError")

        @property
        def iface_legacy_iaccessible(self):
            raise RuntimeError("NoPatternInterfaceError")

    assert not _invoke_control(UnsupportedInvoke())


def test_selection_alone_is_not_a_successful_playback_activation():
    from modules.spotify.desktop.windows import _invoke_control

    class Selectable:
        def select(self):
            pass

    assert not _invoke_control(Selectable())


def test_focus_waits_for_asynchronous_app_activate():
    clock = FakeClock()
    window = SpotifyWindow(handle=907, pid=42, title="Spotify")
    adapter = WindowsSpotifyWindowAdapter(
        process_ids=lambda: {42}, windows=lambda: [window],
        focus_window=lambda _handle: False, activate_process_window=lambda _pid: True,
        foreground_window=lambda: 907 if clock.value >= 0.5 else 100,
        monotonic=clock.monotonic, sleep=clock.sleep,
    )

    assert adapter.focus(window)
    assert clock.value == 0.5


def test_focus_reads_actual_window_even_if_activation_return_value_is_false():
    clock = FakeClock()
    window = SpotifyWindow(handle=907, pid=42, title="Spotify")
    adapter = WindowsSpotifyWindowAdapter(
        process_ids=lambda: {42}, windows=lambda: [window],
        focus_window=lambda _handle: False, activate_process_window=lambda _pid: False,
        foreground_window=lambda: 907 if clock.value >= 0.5 else 100,
        monotonic=clock.monotonic, sleep=clock.sleep,
    )

    assert adapter.focus(window)
    assert clock.value == 0.5


def test_control_rechecks_focus_after_slow_accessibility_read(monkeypatch):
    button = FakeControl(name="Pausar", control_type="Button")
    def root(_handle):
        monkeypatch.setattr(spotify_windows, "_is_spotify_foreground", lambda _handle: False)
        return FakeControl(children=[button])
    shortcuts = []
    adapter = SpotifyUIAutomationAdapter(root_factory=root, send_shortcut=shortcuts.append)

    assert not adapter.control(500, "pause")
    assert not button.invoked
    assert shortcuts == []


def test_search_stops_before_typing_if_focus_changes_after_clear(monkeypatch):
    search = FakeControl(name="Search", control_type="ComboBox")
    search.set_edit_text = None
    shortcuts, texts = [], []
    def send_shortcut(shortcut):
        shortcuts.append(shortcut)
        monkeypatch.setattr(spotify_windows, "_is_spotify_foreground", lambda _handle: False)
    adapter = SpotifyUIAutomationAdapter(
        root_factory=lambda _handle: FakeControl(children=[search]),
        send_shortcut=send_shortcut, send_text=texts.append,
    )

    with pytest.raises(RuntimeError, match="spotify_focus_lost"):
        adapter.search(500, "Killer Queen Queen")
    assert shortcuts == ["^a{BACKSPACE}"]
    assert texts == []


def test_candidate_activation_rechecks_focus_after_scrolling(monkeypatch):
    play = FakeControl(name="Play Dreams by Fleetwood Mac", control_type="Button")
    def visibility():
        monkeypatch.setattr(spotify_windows, "_is_spotify_foreground", lambda _handle: False)
        return True
    play.is_visible = visibility
    adapter = SpotifyUIAutomationAdapter(root_factory=lambda _handle: FakeControl(children=[play]))
    candidate = adapter.read_candidates(500)[0]

    assert not adapter.activate(candidate)
    assert not play.invoked


@pytest.mark.parametrize("action,opposite", [("mute", "Unmute"), ("mute", "Desilenciar")])
def test_control_does_not_invoke_opposite_action_by_substring(action, opposite):
    button = FakeControl(name=opposite, control_type="Button")
    adapter = SpotifyUIAutomationAdapter(
        root_factory=lambda _handle: FakeControl(children=[button]), send_shortcut=lambda _value: None,
    )

    adapter.control(500, action)
    assert not button.invoked


def test_search_queries_only_editable_types_instead_of_entire_spotify_tree():
    search = FakeControl(name="Search", control_type="ComboBox")
    calls = []
    class TypedRoot:
        def descendants(self, *, control_type=None):
            calls.append(control_type)
            return [search] if control_type == "ComboBox" else []
    adapter = SpotifyUIAutomationAdapter(root_factory=lambda _handle: TypedRoot())

    assert adapter.search_available(500)
    assert calls == ["ComboBox", "Edit"]


def test_player_state_queries_only_buttons_without_caching_obsolete_state():
    calls = []
    class TypedRoot:
        def descendants(self, *, control_type=None):
            calls.append(control_type)
            return [FakeControl(name="Pausar" if len(calls) == 1 else "Reproducir", control_type="Button")]
    adapter = SpotifyUIAutomationAdapter(root_factory=lambda _handle: TypedRoot())

    assert adapter.playback_state(500) == "playing"
    assert adapter.playback_state(500) == "paused"
    assert calls == ["Button", "Button"]
