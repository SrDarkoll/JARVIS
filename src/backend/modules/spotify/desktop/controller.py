from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace

from core.unified_log import write_log

from modules.spotify.desktop.matching import (
    choose_candidate,
    normalize_text,
    score_candidate,
)
from modules.spotify.desktop.models import (
    AutomationState,
    DesktopResultStatus,
    MatchDecision,
    MatchStatus,
    SpotifyCandidate,
    SpotifyDesktopResult,
    SpotifyRequest,
)


class SpotifyDesktopController:
    def __init__(
        self,
        window_adapter,
        uia_adapter,
        *,
        visual_recovery=None,
        start_timeout: float = 20.0,
        action_timeout: float = 8.0,
        playback_stability: float = 5.5,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._windows = window_adapter
        self._uia = uia_adapter
        self._visual = visual_recovery
        self._start_timeout = max(0.1, start_timeout)
        self._action_timeout = max(0.05, action_timeout)
        self._playback_stability = max(0.0, playback_stability)
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = threading.Lock()
        self._generation_lock = threading.Lock()
        self._generation = 0
        self._logger = logging.getLogger("JARVIS")

    def _next_generation(self) -> int:
        with self._generation_lock:
            self._generation += 1
            return self._generation

    def _cancelled(self, generation: int) -> bool:
        with self._generation_lock:
            return generation != self._generation

    @staticmethod
    def _text_matches(expected: str, observed: str) -> bool:
        normalized_expected = normalize_text(expected)
        normalized_observed = normalize_text(observed)
        if not normalized_expected or not normalized_observed:
            return False
        if normalized_expected == normalized_observed:
            return True
        shorter = min(
            (normalized_expected, normalized_observed),
            key=len,
        )
        return len(shorter) >= 4 and (
            normalized_expected in normalized_observed or normalized_observed in normalized_expected
        )

    @classmethod
    def _matches(
        cls,
        candidate: SpotifyCandidate,
        observed: tuple[str, str] | None,
        window_title: str,
    ) -> bool:
        if observed:
            title_matches = cls._text_matches(candidate.title, observed[0])
            artist_matches = not candidate.artist or cls._text_matches(candidate.artist, observed[1])
            return title_matches and artist_matches

        return cls._text_matches(candidate.title, window_title) and (
            not candidate.artist or normalize_text(candidate.artist) in normalize_text(window_title)
        )

    def _wait_for_decision(
        self,
        window,
        request: SpotifyRequest,
        generation: int,
        *,
        retry_search: bool = False,
    ) -> tuple[MatchDecision | None, list[SpotifyCandidate], str | None]:
        deadline = self._monotonic() + self._action_timeout
        next_search_retry = self._monotonic() + 0.75
        ranked: list[SpotifyCandidate] = []
        while True:
            if self._cancelled(generation):
                return None, ranked, None

            candidates = self._uia.read_candidates(window.handle)
            ranked = sorted(
                (replace(item, score=score_candidate(request, item)) for item in candidates),
                key=lambda item: item.score,
                reverse=True,
            )
            decision = choose_candidate(request, ranked)
            if decision.status is not MatchStatus.NOT_FOUND:
                return decision, ranked, None

            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return decision, ranked, None
            if retry_search and self._monotonic() >= next_search_retry:
                if not self._windows.is_foreground(window):
                    return None, ranked, "spotify_focus_lost"
                try:
                    self._uia.search(window.handle, request.query)
                except RuntimeError as error:
                    if str(error) != "spotify_search_unavailable":
                        raise
                next_search_retry = self._monotonic() + 1.0
            self._sleep(min(0.2, remaining))

    def _search_when_ready(
        self,
        window,
        query: str,
        generation: int,
        *,
        readiness_timeout: float,
    ) -> str:
        deadline = self._monotonic() + max(0.0, readiness_timeout)
        while True:
            if self._cancelled(generation):
                return "cancelled"
            if not self._windows.is_foreground(window):
                return "focus_lost"
            try:
                self._uia.search(window.handle, query)
                return "ready"
            except RuntimeError as error:
                if str(error) != "spotify_search_unavailable":
                    raise

            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return "unavailable"
            self._sleep(min(0.25, remaining))

    def _wait_for_search_surface(self, window, generation: int):
        search_available = getattr(self._uia, "search_available", None)
        discover_window = getattr(self._windows, "discover_window", None)
        if not callable(search_available):
            return window

        deadline = self._monotonic() + self._start_timeout
        while True:
            if self._cancelled(generation):
                return None
            if callable(discover_window):
                window = discover_window() or window
            try:
                if search_available(window.handle):
                    return window
            except Exception as error:
                self._logger.debug(
                    "spotify_search_surface_pending error_type=%s",
                    type(error).__name__,
                )

            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return window
            self._sleep(min(0.25, remaining))

    def _verify(
        self,
        window,
        candidate: SpotifyCandidate,
        generation: int,
        *,
        timeout: float | None = None,
    ) -> bool | None:
        deadline = self._monotonic() + (self._action_timeout if timeout is None else max(0.0, timeout))
        while True:
            if self._cancelled(generation):
                return None
            observed = self._uia.now_playing(window.handle)
            window_title = "" if observed else self._windows.current_title(window)
            if self._matches(candidate, observed, window_title):
                return True

            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return False
            self._sleep(min(0.2, remaining))

    def _ensure_playing(self, window, generation: int) -> bool | None:
        playback_state = getattr(self._uia, "playback_state", None)
        control = getattr(self._uia, "control", None)
        if not callable(playback_state):
            return True

        deadline = self._monotonic() + self._action_timeout
        stable_since: float | None = None
        resumed = False
        while True:
            if self._cancelled(generation):
                return None
            observed = playback_state(window.handle)
            now = self._monotonic()
            if observed is None:
                return True
            if observed == "playing":
                stable_since = stable_since if stable_since is not None else now
                if now - stable_since >= self._playback_stability:
                    return True
            elif observed == "paused":
                stable_since = None
                if resumed or not callable(control):
                    return False
                if not self._windows.is_foreground(window):
                    return False
                if not control(window.handle, "resume"):
                    return False
                resumed = True
            else:
                return False
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return False
            self._sleep(min(0.2, remaining))

    @staticmethod
    def _cancelled_result(states: list[AutomationState]) -> SpotifyDesktopResult:
        states.append(AutomationState.CANCELLED)
        return SpotifyDesktopResult(
            status=DesktopResultStatus.CANCELLED,
            message_key="spotify_cancelled",
            states=tuple(states),
        )

    def _try_visual_recovery(
        self,
        window,
        request: SpotifyRequest,
        generation: int,
        states: list[AutomationState],
    ) -> SpotifyDesktopResult | None:
        if self._visual is None or not self._windows.is_foreground(window):
            return None
        try:
            activated = self._visual.activate(
                window.handle,
                self._windows.bounds(window),
                request.query,
            )
        except Exception as error:
            self._logger.warning(
                "spotify_visual_recovery_failed error_type=%s",
                type(error).__name__,
            )
            return None
        if not activated:
            return None

        states.extend(
            [
                AutomationState.SELECTING,
                AutomationState.PLAYING,
                AutomationState.VERIFYING,
            ]
        )
        expected = SpotifyCandidate(
            element_id="visual-recovery",
            title=request.title or request.query,
            artist=request.artist,
        )
        verified = self._verify(window, expected, generation)
        if verified is None:
            return self._cancelled_result(states)
        if not verified:
            states.append(AutomationState.FAILED)
            return SpotifyDesktopResult(
                status=DesktopResultStatus.FAILED,
                message_key="spotify_playback_not_verified",
                states=tuple(states),
            )

        playing = self._ensure_playing(window, generation)
        if playing is None:
            return self._cancelled_result(states)
        if not playing:
            states.append(AutomationState.FAILED)
            return SpotifyDesktopResult(
                status=DesktopResultStatus.FAILED,
                message_key="spotify_playback_not_verified",
                states=tuple(states),
            )

        states.append(AutomationState.COMPLETE)
        return SpotifyDesktopResult(
            status=DesktopResultStatus.SUCCESS,
            title=request.title,
            artist=request.artist,
            message_key="spotify_playback_started",
            states=tuple(states),
        )

    def play(self, request: SpotifyRequest) -> SpotifyDesktopResult:
        states = [AutomationState.IDLE]
        ranked: list[SpotifyCandidate] = []
        decision_status = "not_started"
        generation = self._next_generation()
        started = self._monotonic()
        acquired = self._lock.acquire(timeout=self._start_timeout + self._action_timeout)
        if not acquired:
            return SpotifyDesktopResult(
                status=DesktopResultStatus.FAILED,
                message_key="spotify_automation_busy",
                states=tuple(states),
            )

        try:
            if self._cancelled(generation):
                return self._cancelled_result(states)

            states.append(AutomationState.DISCOVERING)
            discover_window = getattr(self._windows, "discover_window", None)
            cold_start = callable(discover_window) and discover_window() is None
            window = self._windows.ensure_window(self._start_timeout)
            if cold_start:
                window = self._wait_for_search_surface(window, generation)
                if window is None:
                    return self._cancelled_result(states)
            states.append(AutomationState.FOCUSING)
            if not self._windows.focus(window) or not self._windows.is_foreground(window):
                states.append(AutomationState.FAILED)
                return SpotifyDesktopResult(
                    status=DesktopResultStatus.FAILED,
                    message_key="spotify_focus_lost",
                    states=tuple(states),
                )

            states.append(AutomationState.SEARCHING)
            search_status = self._search_when_ready(
                window,
                request.query,
                generation,
                readiness_timeout=self._start_timeout if cold_start else 0.0,
            )
            decision_status = search_status
            if search_status == "cancelled":
                return self._cancelled_result(states)
            if search_status == "focus_lost":
                states.append(AutomationState.FAILED)
                return SpotifyDesktopResult(
                    status=DesktopResultStatus.FAILED,
                    message_key="spotify_focus_lost",
                    states=tuple(states),
                )
            if search_status == "unavailable":
                visual_result = self._try_visual_recovery(
                    window,
                    request,
                    generation,
                    states,
                )
                if visual_result is not None:
                    return visual_result
                states.append(AutomationState.FAILED)
                return SpotifyDesktopResult(
                    status=DesktopResultStatus.FAILED,
                    message_key="spotify_search_unavailable",
                    states=tuple(states),
                )
            if self._cancelled(generation):
                return self._cancelled_result(states)

            decision, ranked, decision_error = self._wait_for_decision(
                window,
                request,
                generation,
                retry_search=cold_start,
            )
            decision_status = (
                decision.status.value if decision is not None else "cancelled"
            )
            if decision_error:
                states.append(AutomationState.FAILED)
                return SpotifyDesktopResult(
                    status=DesktopResultStatus.FAILED,
                    message_key=decision_error,
                    states=tuple(states),
                )
            if decision is None:
                return self._cancelled_result(states)
            if decision.status is MatchStatus.NOT_FOUND:
                visual_result = self._try_visual_recovery(
                    window,
                    request,
                    generation,
                    states,
                )
                if visual_result is not None:
                    return visual_result
                states.append(AutomationState.FAILED)
                return SpotifyDesktopResult(
                    status=DesktopResultStatus.NOT_FOUND,
                    message_key="spotify_no_results",
                    states=tuple(states),
                )
            if decision.status is MatchStatus.AMBIGUOUS:
                states.append(AutomationState.FAILED)
                return SpotifyDesktopResult(
                    status=DesktopResultStatus.AMBIGUOUS,
                    message_key="spotify_ambiguous_results",
                    choices=decision.alternatives,
                    states=tuple(states),
                )

            selected = decision.selected
            if selected is None:
                raise RuntimeError("spotify_candidate_missing")
            if not self._windows.is_foreground(window):
                states.append(AutomationState.FAILED)
                return SpotifyDesktopResult(
                    status=DesktopResultStatus.FAILED,
                    message_key="spotify_focus_lost",
                    states=tuple(states),
                )

            states.append(AutomationState.SELECTING)
            if not self._uia.activate(selected):
                raise RuntimeError("spotify_candidate_activation_failed")
            states.extend([AutomationState.PLAYING, AutomationState.VERIFYING])
            verified = self._verify(
                window,
                selected,
                generation,
                timeout=min(2.0, self._action_timeout),
            )
            if verified is None:
                return self._cancelled_result(states)

            activate_fallback = getattr(self._uia, "activate_fallback", None)
            if (
                not verified
                and callable(activate_fallback)
                and self._windows.is_foreground(window)
                and activate_fallback(selected)
            ):
                verified = self._verify(window, selected, generation)
                if verified is None:
                    return self._cancelled_result(states)

            if not verified:
                retry = next(
                    (item for item in ranked if item.element_id != selected.element_id and item.score >= 0.55),
                    None,
                )
                if retry is None or not self._windows.is_foreground(window):
                    retry_verified = False
                elif self._uia.activate(retry):
                    retry_verified = self._verify(window, retry, generation)
                else:
                    retry_verified = False

                if retry_verified is None:
                    return self._cancelled_result(states)
                if not retry_verified:
                    states.append(AutomationState.FAILED)
                    return SpotifyDesktopResult(
                        status=DesktopResultStatus.FAILED,
                        message_key="spotify_playback_not_verified",
                        states=tuple(states),
                    )
                selected = retry

            playing = self._ensure_playing(window, generation)
            if playing is None:
                return self._cancelled_result(states)
            if not playing:
                states.append(AutomationState.FAILED)
                return SpotifyDesktopResult(
                    status=DesktopResultStatus.FAILED,
                    message_key="spotify_playback_not_verified",
                    states=tuple(states),
                )

            states.append(AutomationState.COMPLETE)
            return SpotifyDesktopResult(
                status=DesktopResultStatus.SUCCESS,
                title=selected.title,
                artist=selected.artist,
                message_key="spotify_playback_started",
                states=tuple(states),
            )
        except (FileNotFoundError, ImportError):
            states.append(AutomationState.FAILED)
            return SpotifyDesktopResult(
                status=DesktopResultStatus.UNAVAILABLE,
                message_key="spotify_desktop_unavailable",
                states=tuple(states),
            )
        except TimeoutError:
            states.append(AutomationState.FAILED)
            return SpotifyDesktopResult(
                status=DesktopResultStatus.UNAVAILABLE,
                message_key="spotify_start_timeout",
                states=tuple(states),
            )
        except Exception as error:
            states.append(AutomationState.FAILED)
            self._logger.warning(
                "spotify_desktop_failed error_type=%s",
                type(error).__name__,
            )
            return SpotifyDesktopResult(
                status=DesktopResultStatus.FAILED,
                message_key="spotify_automation_failed",
                states=tuple(states),
            )
        finally:
            final_state = states[-1].value
            duration_ms = max(0, int((self._monotonic() - started) * 1000))
            self._logger.info(
                "spotify_desktop_operation final_state=%s duration_ms=%d",
                final_state,
                duration_ms,
            )
            write_log(
                "SPOTIFY",
                "Desktop playback operation",
                final_state=final_state,
                decision=decision_status,
                duration_ms=duration_ms,
                candidate_count=len(ranked),
                top_score=round(ranked[0].score, 3) if ranked else 0.0,
            )
            self._lock.release()

    @staticmethod
    def _track_key(observed: tuple[str, str] | None, fallback: str = "") -> str:
        if observed:
            return normalize_text(f"{observed[0]} {observed[1]}")
        return normalize_text(fallback)

    def _verify_control(self, window, action: str, before_track: str) -> bool:
        deadline = self._monotonic() + self._action_timeout
        while True:
            if action in {"pause", "resume"}:
                expected = "paused" if action == "pause" else "playing"
                observed = self._uia.playback_state(window.handle)
                if observed == expected:
                    return True
            elif action in {"shuffle_on", "shuffle_off"}:
                expected = action == "shuffle_on"
                observed = self._uia.shuffle_state(window.handle)
                if observed is expected:
                    return True
            elif action in {"next", "previous"}:
                current_track = self._track_key(
                    self._uia.now_playing(window.handle),
                    self._windows.current_title(window),
                )
                if before_track and current_track and current_track != before_track:
                    return True
            else:
                return False

            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return False
            self._sleep(min(0.2, remaining))

    def control(self, action: str) -> SpotifyDesktopResult:
        if not self._lock.acquire(blocking=False):
            return SpotifyDesktopResult(
                status=DesktopResultStatus.FAILED,
                message_key="spotify_automation_busy",
            )
        try:
            window = self._windows.ensure_window(self._start_timeout)
            if not self._windows.focus(window) or not self._windows.is_foreground(window):
                return SpotifyDesktopResult(
                    status=DesktopResultStatus.FAILED,
                    message_key="spotify_focus_lost",
                )
            before_track = self._track_key(
                self._uia.now_playing(window.handle),
                self._windows.current_title(window),
            )
            if not self._uia.control(window.handle, action):
                return SpotifyDesktopResult(
                    status=DesktopResultStatus.RESTRICTED,
                    message_key="spotify_action_restricted",
                )
            if not self._verify_control(window, action, before_track):
                return SpotifyDesktopResult(
                    status=DesktopResultStatus.FAILED,
                    message_key="spotify_control_not_verified",
                )
            return SpotifyDesktopResult(
                status=DesktopResultStatus.SUCCESS,
                message_key="spotify_control_complete",
            )
        except (FileNotFoundError, ImportError, TimeoutError):
            return SpotifyDesktopResult(
                status=DesktopResultStatus.UNAVAILABLE,
                message_key="spotify_desktop_unavailable",
            )
        except Exception as error:
            self._logger.warning(
                "spotify_desktop_control_failed error_type=%s",
                type(error).__name__,
            )
            return SpotifyDesktopResult(
                status=DesktopResultStatus.FAILED,
                message_key="spotify_automation_failed",
            )
        finally:
            self._lock.release()
