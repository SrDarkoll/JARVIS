import logging
import threading

import pytest
from modules.spotify.desktop.controller import SpotifyDesktopController
from modules.spotify.desktop.models import (
    AutomationState,
    DesktopResultStatus,
    SpotifyCandidate,
    SpotifyDesktopResult,
    SpotifyRequest,
)
from modules.spotify.desktop.visual import (
    SpotifyVisualRecovery,
    VisualTarget,
    _visual_target_from_response,
)
from modules.spotify.desktop.windows import SpotifyWindow


def test_unverified_desktop_result_is_not_ok():
    result = SpotifyDesktopResult(status=DesktopResultStatus.UNVERIFIED)

    assert result.ok is False


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
        self.control_calls = []
        self.player_state = "playing"
        self.shuffle_enabled = False
        self.repeat_enabled = False
        self.saved = False
        self.muted = False

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
        self.control_calls.append(_action)
        if _action == "pause":
            self.player_state = "paused"
        elif _action == "resume":
            self.player_state = "playing"
        elif _action == "shuffle_on":
            self.shuffle_enabled = True
        elif _action == "shuffle_off":
            self.shuffle_enabled = False
        elif _action == "repeat_on":
            self.repeat_enabled = True
        elif _action == "repeat_off":
            self.repeat_enabled = False
        elif _action == "like":
            self.saved = True
        elif _action == "unlike":
            self.saved = False
        elif _action == "mute":
            self.muted = True
        elif _action == "unmute":
            self.muted = False
        return True

    def playback_state(self, _handle):
        return self.player_state

    def shuffle_state(self, _handle):
        return self.shuffle_enabled

    def repeat_state(self, _handle):
        return self.repeat_enabled

    def saved_state(self, _handle):
        return self.saved

    def mute_state(self, _handle):
        return self.muted


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


def test_identified_track_without_playback_state_is_not_confirmed():
    uia = FakeUIA([expected_candidate()], now_playing=("No Te Apartes de Mí", "Vicentico"))
    uia.player_state = None
    clock = FakeClock()

    result = make_controller(FakeWindowAdapter(), uia, clock=clock).play(request())

    assert not result.ok
    assert result.message_key == "spotify_playback_not_verified"
    assert uia.control_calls == []


def test_warm_window_waits_for_lazy_accessibility_search():
    class LazySearch(FakeUIA):
        attempts = 0

        def search(self, handle, query):
            self.attempts += 1
            if self.attempts < 3:
                raise RuntimeError("spotify_search_unavailable")
            super().search(handle, query)

    uia = LazySearch([expected_candidate()], now_playing=("No Te Apartes de Mí", "Vicentico"))
    clock = FakeClock()
    result = make_controller(FakeWindowAdapter(), uia, clock=clock).play(request())
    assert result.ok
    assert uia.attempts == 3


def test_existing_spotify_window_can_still_be_loading_after_restore():
    clock = FakeClock()

    class RestoringUIA(FakeUIA):
        def search(self, handle, query):
            if clock.monotonic() < 3.0:
                raise RuntimeError("spotify_search_unavailable")
            super().search(handle, query)

    uia = RestoringUIA([expected_candidate()], now_playing=("No Te Apartes de Mí", "Vicentico"))
    controller = SpotifyDesktopController(FakeWindowAdapter(), uia, start_timeout=5, action_timeout=1,
                                          playback_stability=0, monotonic=clock.monotonic, sleep=clock.sleep)
    result = controller.play(request())
    assert result.status is DesktopResultStatus.SUCCESS
    assert uia.searches == [request().query]
    assert 3 <= clock.value < 5


def test_warm_search_resubmits_once_when_only_suggestions_are_visible():
    class SuggestionsOnly(FakeUIA):
        def read_candidates(self, _handle):
            return [expected_candidate()] if len(self.searches) >= 2 else []

    uia = SuggestionsOnly([], now_playing=("No Te Apartes de Mí", "Vicentico"))
    clock = FakeClock()

    result = make_controller(FakeWindowAdapter(), uia, action_timeout=2, clock=clock).play(request())

    assert result.ok
    assert uia.searches == [request().query, request().query]


def test_no_results_do_not_cause_unbounded_search_resubmission():
    uia = FakeUIA([])
    clock = FakeClock()

    result = make_controller(FakeWindowAdapter(), uia, action_timeout=3, clock=clock).play(request())

    assert result.status is DesktopResultStatus.NOT_FOUND
    assert len(uia.searches) <= 2


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


class SettlingPlaybackUIA(FakeUIA):
    def __init__(self, states):
        super().__init__(
            [expected_candidate()],
            now_playing=("No Te Apartes de M\u00ed", "Vicentico"),
        )
        self.states = iter(states)
        self.last_state = states[-1]
        self.samples = []

    def playback_state(self, _handle):
        self.last_state = next(self.states, self.last_state)
        self.samples.append(self.last_state)
        return self.last_state

    def control(self, _handle, action):
        self.control_calls.append(action)
        return True


@pytest.mark.parametrize(
    ("states", "timeout", "stability", "minimum_elapsed"),
    [
        (["paused", "paused", "playing"], 8, 5.5, 5.9),
        (["paused", "paused", "playing", "playing", None, "playing"], 2, 0.4, 1.4),
        (["paused", "paused", "playing", "playing", "paused", "playing"], 2, 0.4, 1.4),
    ],
    ids=["delayed-resume", "unknown-resets-stability", "pause-resets-stability"],
)
def test_playback_settle_observes_single_resume_until_continuously_playing(
    states, timeout, stability, minimum_elapsed
):
    clock = FakeClock()
    uia = SettlingPlaybackUIA(states)

    result = make_controller(
        FakeWindowAdapter(), uia, clock=clock,
        action_timeout=timeout, playback_stability=stability,
    ).play(request())

    assert result.status is DesktopResultStatus.SUCCESS
    assert uia.samples[:3] == ["paused", "paused", "playing"]
    assert uia.control_calls == ["resume"]
    assert clock.value >= minimum_elapsed - 1e-9
    assert clock.value <= timeout


def test_playback_settle_persistent_pause_expires_without_repeated_toggles():
    clock = FakeClock()
    uia = SettlingPlaybackUIA(["paused"])

    result = make_controller(FakeWindowAdapter(), uia, clock=clock).play(request())

    assert result.status is DesktopResultStatus.FAILED
    assert result.message_key == "spotify_playback_not_verified"
    assert clock.value == pytest.approx(1)
    assert uia.control_calls == ["resume"]


def test_playback_settle_does_not_extend_deadline_for_late_playback():
    clock = FakeClock()
    uia = SettlingPlaybackUIA(["paused"] * 4 + ["playing"])

    result = make_controller(
        FakeWindowAdapter(), uia, clock=clock, playback_stability=0.5,
    ).play(request())

    assert result.status is DesktopResultStatus.FAILED
    assert clock.value == pytest.approx(1)
    assert uia.control_calls == ["resume"]


def test_playback_settle_rejects_a_track_change_after_identity_verification(monkeypatch):
    clock = FakeClock()
    uia = SettlingPlaybackUIA(["paused", "playing"])

    def advance(seconds):
        clock.value += seconds
        if clock.value >= 0.4:
            uia.playing = ("Another Track", "Another Artist")

    monkeypatch.setattr(clock, "sleep", advance)
    result = make_controller(
        FakeWindowAdapter(), uia, clock=clock, playback_stability=0.4,
    ).play(request())

    assert result.status is DesktopResultStatus.FAILED
    assert result.message_key == "spotify_playback_not_verified"
    assert uia.control_calls == ["resume"]


def test_playback_settle_observes_cancellation_after_accepted_resume(monkeypatch):
    clock = FakeClock()
    uia = SettlingPlaybackUIA(["paused", "paused", "playing"])

    def advance(seconds):
        clock.value += seconds
        if clock.value >= 0.4:
            controller._next_generation()

    monkeypatch.setattr(clock, "sleep", advance)
    controller = make_controller(FakeWindowAdapter(), uia, clock=clock)

    result = controller.play(request())

    assert result.status is DesktopResultStatus.CANCELLED
    assert uia.control_calls == ["resume"]
    assert clock.value == pytest.approx(0.4)


def test_readonly_playback_verification_can_finish_after_focus_changes(monkeypatch):
    clock = FakeClock()
    windows = FakeWindowAdapter()
    uia = SettlingPlaybackUIA(["paused", "playing"])

    def advance(seconds):
        clock.value += seconds
        if clock.value >= 0.4:
            windows.focused = False

    monkeypatch.setattr(clock, "sleep", advance)
    result = make_controller(
        windows, uia, clock=clock, playback_stability=0.4,
    ).play(request())

    assert result.status is DesktopResultStatus.SUCCESS
    assert uia.control_calls == ["resume"]
    assert clock.value >= 0.6 - 1e-9


def test_playback_settle_never_sends_resume_after_focus_is_lost():
    windows = FakeWindowAdapter()

    class FocusChangingUIA(SettlingPlaybackUIA):
        def playback_state(self, handle):
            windows.focused = False
            return super().playback_state(handle)

    uia = FocusChangingUIA(["paused"])
    result = make_controller(windows, uia).play(request())
    assert result.status is DesktopResultStatus.FAILED
    assert uia.control_calls == []


def test_background_playback_verification_still_rejects_a_changed_track(monkeypatch):
    clock = FakeClock()
    windows = FakeWindowAdapter()
    uia = SettlingPlaybackUIA(["playing"])

    def advance(seconds):
        clock.value += seconds
        windows.focused = False
        if clock.value >= 0.4:
            uia.playing = ("Another Track", "Another Artist")

    monkeypatch.setattr(clock, "sleep", advance)
    result = make_controller(windows, uia, clock=clock, playback_stability=0.6).play(request())
    assert result.status is DesktopResultStatus.FAILED
    assert uia.control_calls == []


@pytest.mark.parametrize("stage", ["identity", "playback_state"])
def test_playback_settle_logs_the_failed_stage_without_track_metadata(stage, monkeypatch):
    from modules.spotify.desktop import controller as controller_module

    records = []
    monkeypatch.setattr(
        controller_module, "write_log",
        lambda category, message, **context: records.append((category, message, context)),
    )
    clock = FakeClock()
    uia = SettlingPlaybackUIA([None])
    if stage == "identity":
        uia.playing = ("Another Track", "Another Artist")

    result = make_controller(FakeWindowAdapter(), uia, clock=clock).play(request())

    assert result.status is DesktopResultStatus.FAILED
    assert (
        "SPOTIFY", "Desktop playback verification", {"stage": stage, "reason": "timeout"}
    ) in records
    assert uia.control_calls == []
    for metadata in (request().query, expected_candidate().title, "Vicentico", "Another Track"):
        assert metadata not in str(records)


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
        ),
        (
            "SPOTIFY", "Desktop playback result",
            {"status": "success", "code": "spotify_playback_started", "stage": "complete"},
        ),
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


def test_pause_already_satisfied_does_not_toggle_playback():
    uia = FakeUIA([])
    uia.player_state = "paused"
    controller = make_controller(FakeWindowAdapter(), uia)

    result = controller.control("pause")

    assert result.status is DesktopResultStatus.SUCCESS
    assert uia.control_calls == []


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

    assert result.status is DesktopResultStatus.UNVERIFIED
    assert result.message_key == "spotify_control_sent_unverified"


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

    def fake_queue(candidate, _action, handle=None):
        assert handle == 700
        uia.queued.append(candidate.element_id)
        return True

    uia.context_action_candidate = fake_queue

    controller = make_controller(FakeWindowAdapter(), uia)
    result = controller.queue(request())

    assert result.status is DesktopResultStatus.UNVERIFIED
    assert result.message_key == "spotify_queue_sent_unverified"
    assert result.title == "No te apartes de mi"
    assert result.artist == "Vicentico"
    assert "candidate-0" in uia.queued


def test_observable_extended_controls_are_verified():
    controller = make_controller(FakeWindowAdapter(), FakeUIA([]))

    for action in ["like", "unlike", "repeat_on", "repeat_off", "mute", "unmute"]:
        res = controller.control(action)
        assert res.status is DesktopResultStatus.SUCCESS


def test_volume_command_is_reported_unverified_without_observer():
    controller = make_controller(FakeWindowAdapter(), FakeUIA([]))

    result = controller.control("volume_up")

    assert result.status is DesktopResultStatus.UNVERIFIED
    assert result.message_key == "spotify_control_sent_unverified"


def test_unobserved_like_is_not_reported_as_success():
    clock = FakeClock()

    class UnchangedSavedStateUIA(FakeUIA):
        def control(self, _handle, action):
            self.control_calls.append(action)
            return True

    uia = UnchangedSavedStateUIA([])
    controller = make_controller(
        FakeWindowAdapter(),
        uia,
        action_timeout=0.2,
        clock=clock,
    )

    result = controller.control("like")

    assert result.status is DesktopResultStatus.UNVERIFIED
    assert result.message_key == "spotify_control_sent_unverified"


def test_queue_failure_never_activates_candidate_as_playback():
    candidate = expected_candidate("queue-target")
    uia = FakeUIA([candidate])
    uia.context_action_candidate = lambda *_args, **_kwargs: False

    result = make_controller(FakeWindowAdapter(), uia).queue(request())

    assert result.status is DesktopResultStatus.RESTRICTED
    assert result.message_key == "spotify_queue_failed"
    assert uia.activated == []


def test_named_library_action_targets_candidate_without_playing_it():
    candidate = expected_candidate("library-target")
    uia = FakeUIA([candidate])
    actions = []

    def context_action(selected, action, handle=None):
        actions.append((selected.element_id, action, handle))
        return True

    uia.context_action_candidate = context_action

    result = make_controller(FakeWindowAdapter(), uia).library(request(), "like")

    assert result.status is DesktopResultStatus.UNVERIFIED
    assert result.message_key == "spotify_library_sent_unverified"
    assert actions == [("library-target", "like", 700)]
    assert uia.activated == []


def test_current_track_with_unknown_state_is_not_verified():
    uia = FakeUIA([], now_playing=("On Melancholy Hill", "Gorillaz"))
    uia.player_state = None

    result = make_controller(FakeWindowAdapter(), uia).current()

    assert result.status is DesktopResultStatus.UNVERIFIED
    assert result.title == "On Melancholy Hill"
    assert result.artist == "Gorillaz"
    assert result.playback_state == ""
