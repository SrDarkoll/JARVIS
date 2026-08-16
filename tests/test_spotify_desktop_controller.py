import logging
import threading

from modules.spotify.desktop.controller import SpotifyDesktopController
from modules.spotify.desktop.models import (
    AutomationState,
    DesktopResultStatus,
    SpotifyCandidate,
    SpotifyRequest,
)
from modules.spotify.desktop.visual import (
    SpotifyVisualRecovery,
    VisualTarget,
    _visual_target_from_response,
)
from modules.spotify.desktop.windows import SpotifyWindow


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class FakeWindowAdapter:
    def __init__(self, titles=()):
        self.window = SpotifyWindow(handle=700, pid=70, title="Spotify")
        self.titles = iter(titles)
        self.focused = True

    def ensure_window(self, timeout):
        assert timeout > 0
        return self.window

    def focus(self, _window):
        return self.focused

    def is_foreground(self, _window):
        return self.focused

    def current_title(self, _window):
        return next(self.titles, "")

    def bounds(self, _window):
        return (0, 0, 100, 100)


class FakeUIA:
    def __init__(self, candidates, now_playing=None):
        self.candidates = candidates
        self.playing = now_playing
        self.activated = []
        self.searches = []
        self.player_state = "playing"
        self.shuffle_enabled = False

    def search(self, handle, query):
        assert handle == 700
        assert query
        self.searches.append(query)

    def read_candidates(self, handle):
        assert handle == 700
        return list(self.candidates)

    def activate(self, candidate):
        self.activated.append(candidate.element_id)
        return True

    def now_playing(self, _handle):
        return self.playing

    def control(self, _handle, _action):
        if _action == "pause":
            self.player_state = "paused"
        elif _action == "resume":
            self.player_state = "playing"
        elif _action == "shuffle_on":
            self.shuffle_enabled = True
        elif _action == "shuffle_off":
            self.shuffle_enabled = False
        return True

    def playback_state(self, _handle):
        return self.player_state

    def shuffle_state(self, _handle):
        return self.shuffle_enabled


def request():
    return SpotifyRequest(
        raw="No te apartes de mi de Vicentico",
        query="No te apartes de mi Vicentico",
        title="No te apartes de mi",
        artist="Vicentico",
    )


def expected_candidate(element_id="expected"):
    return SpotifyCandidate(
        element_id=element_id,
        title="No Te Apartes de M\u00ed",
        artist="Vicentico",
    )


def make_controller(
    windows,
    uia,
    *,
    action_timeout=1,
    clock=None,
    visual=None,
    playback_stability=0,
):
    options = {}
    if clock is not None:
        options = {"monotonic": clock.monotonic, "sleep": clock.sleep}
    return SpotifyDesktopController(
        windows,
        uia,
        visual_recovery=visual,
        start_timeout=2,
        action_timeout=action_timeout,
        playback_stability=playback_stability,
        **options,
    )


def test_verified_candidate_reports_success():
    windows = FakeWindowAdapter(["Vicentico - No Te Apartes de M\u00ed"])
    uia = FakeUIA(
        [expected_candidate()],
        now_playing=("No Te Apartes de M\u00ed", "Vicentico"),
    )

    result = make_controller(windows, uia).play(request())

    assert result.status is DesktopResultStatus.SUCCESS
    assert result.title == "No Te Apartes de M\u00ed"
    assert result.artist == "Vicentico"
    assert result.states[-1] is AutomationState.COMPLETE
    assert uia.activated == ["expected"]


def test_ambiguous_results_are_returned_without_clicking():
    windows = FakeWindowAdapter(["Spotify"])
    uia = FakeUIA(
        [
            expected_candidate("vicentico"),
            SpotifyCandidate("roberto", "No Te Apartes de M\u00ed", "Roberto Carlos"),
        ]
    )
    title_only = SpotifyRequest(
        raw="No te apartes de mi",
        query="No te apartes de mi",
        title="No te apartes de mi",
    )

    result = make_controller(windows, uia).play(title_only)

    assert result.status is DesktopResultStatus.AMBIGUOUS
    assert len(result.choices) == 2
    assert uia.activated == []


def test_focus_loss_aborts_before_typing():
    windows = FakeWindowAdapter(["Spotify"])
    windows.focused = False
    uia = FakeUIA([expected_candidate()])

    result = make_controller(windows, uia).play(request())

    assert result.status is DesktopResultStatus.FAILED
    assert result.message_key == "spotify_focus_lost"
    assert uia.searches == []
    assert uia.activated == []


def test_stale_results_are_polled_until_the_requested_song_appears():
    clock = FakeClock()

    class UpdatingUIA(FakeUIA):
        def __init__(self):
            super().__init__([], now_playing=("No Te Apartes de M\u00ed", "Vicentico"))
            self.snapshots = iter(
                [
                    [SpotifyCandidate("stale", "Killer Queen", "Queen")],
                    [expected_candidate()],
                ]
            )

        def read_candidates(self, handle):
            assert handle == 700
            return next(self.snapshots)

    uia = UpdatingUIA()
    result = make_controller(FakeWindowAdapter(), uia, action_timeout=1, clock=clock).play(request())

    assert result.status is DesktopResultStatus.SUCCESS
    assert uia.activated == ["expected"]
    assert clock.value == 0.2


def test_cold_start_waits_for_spotify_search_to_become_accessible():
    clock = FakeClock()

    class ColdStartWindowAdapter(FakeWindowAdapter):
        def discover_window(self):
            return None

    class StartingUIA(FakeUIA):
        def __init__(self):
            super().__init__(
                [expected_candidate()],
                now_playing=("No Te Apartes de M\u00ed", "Vicentico"),
            )
            self.attempts = 0

        def search(self, handle, query):
            self.attempts += 1
            if self.attempts < 3:
                raise RuntimeError("spotify_search_unavailable")
            super().search(handle, query)

    uia = StartingUIA()
    result = make_controller(
        ColdStartWindowAdapter(),
        uia,
        action_timeout=1,
        clock=clock,
    ).play(request())

    assert result.status is DesktopResultStatus.SUCCESS
    assert uia.attempts == 3
    assert uia.searches == [request().query]
    assert clock.value == 0.5


def test_cold_start_waits_for_search_surface_before_focusing():
    clock = FakeClock()

    class ColdStartWindowAdapter(FakeWindowAdapter):
        def __init__(self):
            super().__init__()
            self.discovery_calls = 0
            self.focused_at = None

        def discover_window(self):
            self.discovery_calls += 1
            return None if self.discovery_calls == 1 else self.window

        def focus(self, window):
            self.focused_at = clock.value
            return super().focus(window)

    class LoadingUIA(FakeUIA):
        def __init__(self):
            super().__init__(
                [expected_candidate()],
                now_playing=("No Te Apartes de M\u00ed", "Vicentico"),
            )
            self.readiness_checks = 0

        def search_available(self, _handle):
            self.readiness_checks += 1
            return self.readiness_checks >= 3

    windows = ColdStartWindowAdapter()
    uia = LoadingUIA()
    result = make_controller(windows, uia, clock=clock).play(request())

    assert result.status is DesktopResultStatus.SUCCESS
    assert uia.readiness_checks == 3
    assert windows.focused_at == 0.5


def test_cold_start_retries_search_when_spotify_returns_stale_results():
    clock = FakeClock()

    class ColdStartWindowAdapter(FakeWindowAdapter):
        def discover_window(self):
            return None

    class HydratingUIA(FakeUIA):
        def __init__(self):
            super().__init__([], now_playing=("No Te Apartes de M\u00ed", "Vicentico"))
            self.search_attempts = 0

        def search(self, handle, query):
            self.search_attempts += 1
            super().search(handle, query)

        def read_candidates(self, handle):
            assert handle == 700
            if self.search_attempts < 2:
                return [SpotifyCandidate("stale", "Killer Queen", "Queen")]
            return [expected_candidate()]

    uia = HydratingUIA()
    result = make_controller(
        ColdStartWindowAdapter(),
        uia,
        action_timeout=2,
        clock=clock,
    ).play(request())

    assert result.status is DesktopResultStatus.SUCCESS
    assert uia.search_attempts == 2
    assert uia.activated == ["expected"]
    assert clock.value == 1.0


def test_unverified_playback_retries_next_ranked_candidate_once():
    clock = FakeClock()

    class RetryUIA(FakeUIA):
        def now_playing(self, _handle):
            if self.activated and self.activated[-1] == "second":
                return "No Te Apartes", "Vicentico"
            return None

    uia = RetryUIA(
        [
            expected_candidate("first"),
            SpotifyCandidate("second", "No Te Apartes", "Vicentico"),
        ]
    )
    result = make_controller(FakeWindowAdapter(), uia, action_timeout=0.4, clock=clock).play(request())

    assert result.status is DesktopResultStatus.SUCCESS
    assert uia.activated == ["first", "second"]


def test_loaded_but_paused_track_is_resumed_before_success():
    class PausedAfterSelectionUIA(FakeUIA):
        def __init__(self):
            super().__init__(
                [expected_candidate()],
                now_playing=("No Te Apartes de M\u00ed", "Vicentico"),
            )
            self.player_state = "paused"
            self.control_actions = []

        def control(self, handle, action):
            self.control_actions.append(action)
            return super().control(handle, action)

    uia = PausedAfterSelectionUIA()
    result = make_controller(FakeWindowAdapter(), uia).play(request())

    assert result.status is DesktopResultStatus.SUCCESS
    assert uia.control_actions == ["resume"]
    assert uia.player_state == "playing"


def test_paused_track_is_not_reported_as_playing_when_resume_fails():
    class ResumeFailureUIA(FakeUIA):
        def __init__(self):
            super().__init__(
                [expected_candidate()],
                now_playing=("No Te Apartes de M\u00ed", "Vicentico"),
            )
            self.player_state = "paused"

        def control(self, _handle, _action):
            return False

    result = make_controller(FakeWindowAdapter(), ResumeFailureUIA()).play(request())

    assert result.status is DesktopResultStatus.FAILED
    assert result.message_key == "spotify_playback_not_verified"


def test_transient_playback_is_not_reported_as_stable_success():
    clock = FakeClock()

    class TransientPlaybackUIA(FakeUIA):
        def __init__(self):
            super().__init__(
                [expected_candidate()],
                now_playing=("No Te Apartes de M\u00ed", "Vicentico"),
            )
            self.states = iter(["playing", "playing", "paused", "paused"])

        def playback_state(self, _handle):
            return next(self.states, "paused")

        def control(self, _handle, _action):
            return True

    result = make_controller(
        FakeWindowAdapter(),
        TransientPlaybackUIA(),
        action_timeout=1,
        clock=clock,
        playback_stability=0.5,
    ).play(request())

    assert result.status is DesktopResultStatus.FAILED
    assert result.message_key == "spotify_playback_not_verified"


def test_lock_is_released_after_exception():
    class FailingUIA(FakeUIA):
        def search(self, _handle, _query):
            raise RuntimeError("layout changed")

    controller = make_controller(FakeWindowAdapter(), FailingUIA([]))

    first = controller.play(request())
    second = controller.play(request())

    assert first.status is DesktopResultStatus.FAILED
    assert second.status is DesktopResultStatus.FAILED


def test_new_request_cancels_a_pending_search():
    entered = threading.Event()
    release = threading.Event()

    class BlockingUIA(FakeUIA):
        def __init__(self):
            super().__init__(
                [expected_candidate()],
                now_playing=("No Te Apartes de M\u00ed", "Vicentico"),
            )
            self.search_calls = 0

        def search(self, handle, query):
            super().search(handle, query)
            self.search_calls += 1
            if self.search_calls == 1:
                entered.set()
                assert release.wait(timeout=2)

    controller = make_controller(FakeWindowAdapter(), BlockingUIA())
    results = []
    first = threading.Thread(target=lambda: results.append(controller.play(request())))
    second = threading.Thread(target=lambda: results.append(controller.play(request())))

    first.start()
    assert entered.wait(timeout=2)
    second.start()
    release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert {result.status for result in results} == {
        DesktopResultStatus.CANCELLED,
        DesktopResultStatus.SUCCESS,
    }


def test_controller_logs_state_and_duration_without_query(caplog, monkeypatch):
    monkeypatch.setattr(logging.getLogger("JARVIS"), "propagate", True)
    caplog.set_level("INFO", logger="JARVIS")
    windows = FakeWindowAdapter(["Vicentico - No Te Apartes de M\u00ed"])
    uia = FakeUIA(
        [expected_candidate()],
        now_playing=("No Te Apartes de M\u00ed", "Vicentico"),
    )
    controller = make_controller(windows, uia)

    controller.play(request())

    output = caplog.text
    assert "final_state=complete" in output
    assert "duration_ms=" in output
    assert "No te apartes" not in output


def test_controller_writes_sanitized_matching_diagnostics(monkeypatch):
    from modules.spotify.desktop import controller as controller_module

    records = []
    monkeypatch.setattr(
        controller_module,
        "write_log",
        lambda category, message, **context: records.append(
            (category, message, context)
        ),
    )
    uia = FakeUIA(
        [expected_candidate()],
        now_playing=("No Te Apartes de M\u00ed", "Vicentico"),
    )

    class FakeClock:
        def monotonic(self):
            return 100.0
        def sleep(self, seconds):
            pass

    result = make_controller(FakeWindowAdapter(), uia, clock=FakeClock()).play(request())

    assert result.status is DesktopResultStatus.SUCCESS
    assert records == [
        (
            "SPOTIFY",
            "Desktop playback operation",
            {
                "final_state": "complete",
                "decision": "selected",
                "duration_ms": 0,
                "candidate_count": 1,
                "top_score": 1.0,
            },
        )
    ]
    assert "No Te Apartes" not in str(records)


def test_visual_target_must_stay_inside_spotify_window(tmp_path):
    recovery = SpotifyVisualRecovery(
        scratch_dir=tmp_path,
        capture=lambda _handle, path: path.write_bytes(b"png"),
        analyze=lambda _path, _query: VisualTarget(10, 10, 20, 20, "expected"),
        click=lambda _handle, _x, _y: True,
    )

    assert recovery.activate(700, (0, 0, 100, 100), "expected")
    assert list(tmp_path.iterdir()) == []


def test_visual_target_outside_window_is_rejected_and_deleted(tmp_path):
    recovery = SpotifyVisualRecovery(
        scratch_dir=tmp_path,
        capture=lambda _handle, path: path.write_bytes(b"png"),
        analyze=lambda _path, _query: VisualTarget(90, 90, 30, 30, "outside"),
        click=lambda _handle, _x, _y: True,
    )

    assert not recovery.activate(700, (0, 0, 100, 100), "expected")
    assert list(tmp_path.iterdir()) == []


def test_visual_recovery_is_unavailable_without_analyzer(tmp_path):
    recovery = SpotifyVisualRecovery(scratch_dir=tmp_path)
    assert not recovery.available


class VisionResponse:
    def __init__(self, content):
        self.content = content


def test_visual_response_parser_accepts_structured_json():
    target = _visual_target_from_response(
        VisionResponse('{"x": 10, "y": 20, "width": 30, "height": 40, "label": "track"}')
    )
    assert target == VisualTarget(10, 20, 30, 40, "track")


def test_visual_response_parser_extracts_surrounded_json():
    target = _visual_target_from_response(VisionResponse('result: {"x": 1, "y": 2, "width": 3, "height": 4}'))
    assert target == VisualTarget(1, 2, 3, 4)


def test_visual_response_parser_rejects_missing_fields_and_non_json():
    assert _visual_target_from_response(VisionResponse('{"x": 1}')) is None
    assert _visual_target_from_response(VisionResponse("no reliable match")) is None


def test_negative_visual_geometry_never_clicks(tmp_path):
    clicks = []
    recovery = SpotifyVisualRecovery(
        scratch_dir=tmp_path,
        capture=lambda _handle, path: path.write_bytes(b"png"),
        analyze=lambda _path, _query: VisualTarget(-1, 10, 20, 20, "expected"),
        click=lambda _handle, x, y: clicks.append((x, y)) or True,
    )

    assert not recovery.activate(700, (0, 0, 100, 100), "expected")
    assert clicks == []
    assert list(tmp_path.iterdir()) == []


def test_visual_recovery_runs_only_after_search_layout_is_unavailable():
    class SearchUnavailableUIA(FakeUIA):
        def search(self, _handle, _query):
            raise RuntimeError("spotify_search_unavailable")

    class VisualRecovery:
        def __init__(self):
            self.calls = []

        def activate(self, handle, bounds, query):
            self.calls.append((handle, bounds, query))
            return True

    visual = VisualRecovery()
    uia = SearchUnavailableUIA(
        [],
        now_playing=("No Te Apartes de M\u00ed", "Vicentico"),
    )
    result = make_controller(
        FakeWindowAdapter(),
        uia,
        visual=visual,
    ).play(request())

    assert result.status is DesktopResultStatus.SUCCESS
    assert visual.calls == [(700, (0, 0, 100, 100), request().query)]


def test_visual_recovery_does_not_hide_unrelated_programming_errors():
    class BrokenUIA(FakeUIA):
        def search(self, _handle, _query):
            raise RuntimeError("unexpected layout bug")

    class VisualRecovery:
        def __init__(self):
            self.called = False

        def activate(self, _handle, _bounds, _query):
            self.called = True
            return True

    visual = VisualRecovery()
    result = make_controller(
        FakeWindowAdapter(),
        BrokenUIA([]),
        visual=visual,
    ).play(request())

    assert result.status is DesktopResultStatus.FAILED
    assert not visual.called


def test_pause_control_is_verified_before_success():
    controller = make_controller(FakeWindowAdapter(), FakeUIA([]))

    result = controller.control("pause")

    assert result.status is DesktopResultStatus.SUCCESS


def test_unobserved_control_change_is_not_reported_as_success():
    clock = FakeClock()

    class UnchangedUIA(FakeUIA):
        def control(self, _handle, _action):
            return True

    controller = make_controller(
        FakeWindowAdapter(),
        UnchangedUIA([]),
        action_timeout=0.4,
        clock=clock,
    )

    result = controller.control("pause")

    assert result.status is DesktopResultStatus.FAILED
    assert result.message_key == "spotify_control_not_verified"


def test_queue_candidate_success():
    candidates = [
        SpotifyCandidate(
            element_id="candidate-0",
            title="No te apartes de mi",
            artist="Vicentico",
            kind="track",
            subtitle="Song",
        )
    ]
    uia = FakeUIA(candidates)
    uia.queued = []

    def fake_queue(candidate):
        uia.queued.append(candidate.element_id)
        return True

    uia.queue_candidate = fake_queue

    controller = make_controller(FakeWindowAdapter(), uia)
    result = controller.queue(request())

    assert result.status is DesktopResultStatus.SUCCESS
    assert result.title == "No te apartes de mi"
    assert result.artist == "Vicentico"
    assert "candidate-0" in uia.queued


def test_like_and_extended_controls_verified():
    controller = make_controller(FakeWindowAdapter(), FakeUIA([]))

    for action in ["like", "unlike", "repeat_on", "repeat_off", "volume_up", "volume_down", "mute"]:
        res = controller.control(action)
        assert res.status is DesktopResultStatus.SUCCESS
