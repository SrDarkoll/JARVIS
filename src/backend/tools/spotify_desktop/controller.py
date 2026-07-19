from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace

from tools.spotify_desktop.matching import (
    choose_candidate,
    normalize_text,
    score_candidate,
)
from tools.spotify_desktop.models import (
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
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._windows = window_adapter
        self._uia = uia_adapter
        self._visual = visual_recovery
        self._start_timeout = max(0.1, start_timeout)
        self._action_timeout = max(0.05, action_timeout)
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
            normalized_expected in normalized_observed
            or normalized_observed in normalized_expected
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
            artist_matches = not candidate.artist or cls._text_matches(
                candidate.artist, observed[1]
            )
            return title_matches and artist_matches

        return cls._text_matches(candidate.title, window_title) and (
            not candidate.artist
            or normalize_text(candidate.artist) in normalize_text(window_title)
        )

    def _wait_for_decision(
        self,
        window,
        request: SpotifyRequest,
        generation: int,
    ) -> tuple[MatchDecision | None, list[SpotifyCandidate]]:
        deadline = self._monotonic() + self._action_timeout
        ranked: list[SpotifyCandidate] = []
        while True:
            if self._cancelled(generation):
                return None, ranked

            candidates = self._uia.read_candidates(window.handle)
            ranked = sorted(
                (
                    replace(item, score=score_candidate(request, item))
                    for item in candidates
                ),
                key=lambda item: item.score,
                reverse=True,
            )
            decision = choose_candidate(request, ranked)
            if decision.status is not MatchStatus.NOT_FOUND:
                return decision, ranked

            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return decision, ranked
            self._sleep(min(0.2, remaining))

    def _verify(
        self,
        window,
        candidate: SpotifyCandidate,
        generation: int,
    ) -> bool | None:
        deadline = self._monotonic() + self._action_timeout
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

    @staticmethod
    def _cancelled_result(states: list[AutomationState]) -> SpotifyDesktopResult:
        states.append(AutomationState.CANCELLED)
        return SpotifyDesktopResult(
            status=DesktopResultStatus.CANCELLED,
            message_key="spotify_cancelled",
            states=tuple(states),
        )

    def play(self, request: SpotifyRequest) -> SpotifyDesktopResult:
        states = [AutomationState.IDLE]
        generation = self._next_generation()
        started = self._monotonic()
        acquired = self._lock.acquire(
            timeout=self._start_timeout + self._action_timeout
        )
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
            window = self._windows.ensure_window(self._start_timeout)
            states.append(AutomationState.FOCUSING)
            if not self._windows.focus(window) or not self._windows.is_foreground(window):
                states.append(AutomationState.FAILED)
                return SpotifyDesktopResult(
                    status=DesktopResultStatus.FAILED,
                    message_key="spotify_focus_lost",
                    states=tuple(states),
                )

            states.append(AutomationState.SEARCHING)
            self._uia.search(window.handle, request.query)
            if self._cancelled(generation):
                return self._cancelled_result(states)

            decision, ranked = self._wait_for_decision(window, request, generation)
            if decision is None:
                return self._cancelled_result(states)
            if decision.status is MatchStatus.NOT_FOUND:
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
            verified = self._verify(window, selected, generation)
            if verified is None:
                return self._cancelled_result(states)

            if not verified:
                retry = next(
                    (
                        item
                        for item in ranked
                        if item.element_id != selected.element_id and item.score >= 0.55
                    ),
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
            self._lock.release()

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
            if not self._uia.control(window.handle, action):
                return SpotifyDesktopResult(
                    status=DesktopResultStatus.RESTRICTED,
                    message_key="spotify_action_restricted",
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
