# Spotify Desktop Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a verified Windows Spotify Desktop playback backend that opens the client, searches and selects music through UI Automation, and automatically replaces blocked or unavailable Web API playback.

**Architecture:** Keep the existing Spotipy implementation as one backend and add a focused `tools.spotify_desktop` package containing typed results, deterministic candidate matching, Windows/UIA adapters, a bounded controller, and optional visual recovery. A thin coordinator in `tools/spotify.py` selects cached API playback or desktop automation without opening an interactive OAuth flow during a voice command.

**Tech Stack:** Python 3.11/3.12, pytest, psutil, Win32 `ctypes`, pywinauto UIA backend, optional Pillow/Groq Vision, existing LangChain tools and Quart status/configuration.

---

## File map

- Create `src/backend/tools/spotify_desktop/__init__.py`: stable exports and lazy Windows construction.
- Create `src/backend/tools/spotify_desktop/models.py`: states, requests, candidates, decisions, and typed results.
- Create `src/backend/tools/spotify_desktop/matching.py`: normalization, scoring, penalties, and ambiguity decisions.
- Create `src/backend/tools/spotify_desktop/windows.py`: Spotify process/window lifecycle and UI Automation interactions.
- Create `src/backend/tools/spotify_desktop/controller.py`: serialized state machine, playback verification, retries, and controls.
- Create `src/backend/tools/spotify_desktop/visual.py`: cropped optional visual recovery with bounded click validation and cleanup.
- Modify `src/backend/core/jarvis_config.py`: validated playback mode and desktop timeout configuration.
- Modify `src/backend/tools/spotify.py`: API/desktop coordinator while retaining the public tool names.
- Modify `src/backend/core/setup_wizard.py`: report desktop mode as usable without OAuth credentials.
- Modify `.env.example`: document mode and timeout variables.
- Modify `requirements.txt`: add one Windows-only UI Automation dependency.
- Modify `README.md` and `AGENTS.md`: install, behavior, limitations, and verification commands.
- Create `tests/test_spotify_desktop_matching.py`: deterministic ranking tests.
- Create `tests/test_spotify_desktop_windows.py`: mocked Win32/UIA adapter tests.
- Create `tests/test_spotify_desktop_controller.py`: state, retry, cancellation, verification, and visual fallback tests.
- Modify `tests/test_spotify_recs.py`: isolate real OAuth and test backend coordination.
- Modify `tests/test_installation_contract.py`: dependency and public configuration contract.
- Modify `tests/test_setup_wizard.py`: setup readiness in desktop mode.

## Task 1: Playback mode and installation contract

**Files:**
- Modify: `src/backend/core/jarvis_config.py:164-171`
- Modify: `.env.example:44-59`
- Modify: `requirements.txt:8-15`
- Modify: `tests/test_installation_contract.py:127-145`

- [ ] **Step 1: Write failing configuration and dependency tests**

Add these assertions to `tests/test_installation_contract.py`:

```python
def test_spotify_desktop_mode_and_dependency_contract():
    root = Path(__file__).resolve().parents[1]
    env_example = (root / ".env.example").read_text(encoding="utf-8")
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    config = (root / "src/backend/core/jarvis_config.py").read_text(encoding="utf-8")

    assert 'SPOTIFY_PLAYBACK_MODE="auto"' in env_example
    assert 'SPOTIFY_DESKTOP_START_TIMEOUT="20"' in env_example
    assert 'SPOTIFY_DESKTOP_ACTION_TIMEOUT="8"' in env_example
    assert 'pywinauto>=0.6.9,<0.7; sys_platform == "win32"' in requirements
    assert 'SPOTIFY_PLAYBACK_MODE = _read_choice(' in config
    assert 'SPOTIFY_DESKTOP_START_TIMEOUT = _read_float(' in config
    assert 'SPOTIFY_DESKTOP_ACTION_TIMEOUT = _read_float(' in config
```

Add a focused resolver test:

```python
def test_spotify_playback_mode_rejects_unknown_values():
    from core.jarvis_config import resolve_spotify_playback_mode

    assert resolve_spotify_playback_mode({}) == "auto"
    assert resolve_spotify_playback_mode({"SPOTIFY_PLAYBACK_MODE": "desktop"}) == "desktop"
    assert resolve_spotify_playback_mode({"SPOTIFY_PLAYBACK_MODE": "api"}) == "api"
    assert resolve_spotify_playback_mode({"SPOTIFY_PLAYBACK_MODE": "invalid"}) == "auto"
```

- [ ] **Step 2: Run the tests and confirm the contract is absent**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_installation_contract.py -q
```

Expected: failures for missing `SPOTIFY_PLAYBACK_MODE`, timeout variables, resolver, and `pywinauto` requirement.

- [ ] **Step 3: Add validated configuration**

Add this helper beside `_read_float` in `src/backend/core/jarvis_config.py`:

```python
def _read_choice(
    env: Mapping[str, str],
    name: str,
    default: str,
    choices: set[str],
) -> str:
    value = str(env.get(name, default) or default).strip().lower()
    return value if value in choices else default


def resolve_spotify_playback_mode(env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    return _read_choice(
        source,
        "SPOTIFY_PLAYBACK_MODE",
        "auto",
        {"auto", "api", "desktop"},
    )
```

Add these settings to the Spotify block:

```python
SPOTIFY_PLAYBACK_MODE = resolve_spotify_playback_mode()
SPOTIFY_DESKTOP_START_TIMEOUT = _read_float(
    os.environ, "SPOTIFY_DESKTOP_START_TIMEOUT", 20.0, 5.0, 60.0
)
SPOTIFY_DESKTOP_ACTION_TIMEOUT = _read_float(
    os.environ, "SPOTIFY_DESKTOP_ACTION_TIMEOUT", 8.0, 2.0, 30.0
)
```

Add to `.env.example`:

```dotenv
# auto uses a valid cached Web API token and otherwise controls Spotify Desktop.
# api forces Spotipy/OAuth. desktop never starts OAuth during playback.
SPOTIFY_PLAYBACK_MODE="auto"
SPOTIFY_DESKTOP_START_TIMEOUT="20"
SPOTIFY_DESKTOP_ACTION_TIMEOUT="8"
```

Add to `requirements.txt` after `psutil`:

```text
pywinauto>=0.6.9,<0.7; sys_platform == "win32"
```

- [ ] **Step 4: Install and verify the new Windows dependency**

Run:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m pip check
```

Expected: both commands exit `0`; `pip check` reports `No broken requirements found.`

- [ ] **Step 5: Run the contract tests**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_installation_contract.py -q
```

Expected: all installation contract tests pass.

- [ ] **Step 6: Commit the configuration contract**

```powershell
git add -- requirements.txt .env.example src/backend/core/jarvis_config.py tests/test_installation_contract.py
git commit -m "feat: configure Spotify desktop playback mode"
```

## Task 2: Typed results and deterministic candidate matching

**Files:**
- Create: `src/backend/tools/spotify_desktop/__init__.py`
- Create: `src/backend/tools/spotify_desktop/models.py`
- Create: `src/backend/tools/spotify_desktop/matching.py`
- Create: `tests/test_spotify_desktop_matching.py`

- [ ] **Step 1: Write failing matcher tests**

Create `tests/test_spotify_desktop_matching.py`:

```python
from tools.spotify_desktop.matching import choose_candidate, normalize_text
from tools.spotify_desktop.models import MatchStatus, SpotifyCandidate, SpotifyRequest


def candidate(title: str, artist: str, element_id: str) -> SpotifyCandidate:
    return SpotifyCandidate(
        element_id=element_id,
        title=title,
        artist=artist,
        kind="track",
    )


def test_normalize_text_removes_diacritics_and_noise():
    assert normalize_text("  No te APARTES de mí! ") == "no te apartes de mi"


def test_artist_match_wins_over_cover_and_live_versions():
    request = SpotifyRequest(
        raw="No te apartes de mi de Vicentico",
        query="No te apartes de mi Vicentico",
        title="No te apartes de mi",
        artist="Vicentico",
    )
    decision = choose_candidate(
        request,
        [
            candidate("No Te Apartes de Mí (En Vivo)", "Tributo a Vicentico", "cover"),
            candidate("No Te Apartes de Mí", "Vicentico", "expected"),
            candidate("No Te Apartes de Mí", "Roberto Carlos", "original"),
        ],
    )

    assert decision.status is MatchStatus.SELECTED
    assert decision.selected is not None
    assert decision.selected.element_id == "expected"


def test_title_only_request_returns_ambiguity_for_close_artists():
    request = SpotifyRequest(
        raw="No te apartes de mi",
        query="No te apartes de mi",
        title="No te apartes de mi",
    )
    decision = choose_candidate(
        request,
        [
            candidate("No Te Apartes de Mí", "Vicentico", "one"),
            candidate("No Te Apartes de Mí", "Roberto Carlos", "two"),
        ],
    )

    assert decision.status is MatchStatus.AMBIGUOUS
    assert [item.element_id for item in decision.alternatives] == ["one", "two"]


def test_requested_live_variant_is_not_penalized():
    request = SpotifyRequest(
        raw="Comfortably Numb live de Pink Floyd",
        query="Comfortably Numb live Pink Floyd",
        title="Comfortably Numb live",
        artist="Pink Floyd",
    )
    decision = choose_candidate(
        request,
        [
            candidate("Comfortably Numb", "Pink Floyd", "studio"),
            candidate("Comfortably Numb - Live", "Pink Floyd", "live"),
        ],
    )

    assert decision.status is MatchStatus.SELECTED
    assert decision.selected is not None
    assert decision.selected.element_id == "live"


def test_empty_candidates_return_not_found():
    request = SpotifyRequest(raw="missing", query="missing", title="missing")
    assert choose_candidate(request, []).status is MatchStatus.NOT_FOUND
```

- [ ] **Step 2: Run matcher tests and confirm imports fail**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_spotify_desktop_matching.py -q
```

Expected: collection fails because `tools.spotify_desktop` does not exist.

- [ ] **Step 3: Add typed models**

Create `src/backend/tools/spotify_desktop/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AutomationState(str, Enum):
    IDLE = "idle"
    DISCOVERING = "discovering"
    STARTING = "starting"
    FOCUSING = "focusing"
    SEARCHING = "searching"
    SELECTING = "selecting"
    PLAYING = "playing"
    VERIFYING = "verifying"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MatchStatus(str, Enum):
    SELECTED = "selected"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class DesktopResultStatus(str, Enum):
    SUCCESS = "success"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    RESTRICTED = "restricted"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class SpotifyRequest:
    raw: str
    query: str
    title: str = ""
    artist: str = ""
    kind: str = "track"


@dataclass(frozen=True)
class SpotifyCandidate:
    element_id: str
    title: str
    artist: str = ""
    kind: str = "track"
    subtitle: str = ""
    score: float = 0.0


@dataclass(frozen=True)
class MatchDecision:
    status: MatchStatus
    selected: SpotifyCandidate | None = None
    alternatives: tuple[SpotifyCandidate, ...] = ()
    confidence: float = 0.0


@dataclass(frozen=True)
class SpotifyDesktopResult:
    status: DesktopResultStatus
    title: str = ""
    artist: str = ""
    message_key: str = ""
    choices: tuple[SpotifyCandidate, ...] = ()
    states: tuple[AutomationState, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.status is DesktopResultStatus.SUCCESS
```

Create `src/backend/tools/spotify_desktop/__init__.py`:

```python
from tools.spotify_desktop.models import (
    AutomationState,
    DesktopResultStatus,
    MatchDecision,
    MatchStatus,
    SpotifyCandidate,
    SpotifyDesktopResult,
    SpotifyRequest,
)

__all__ = [
    "AutomationState",
    "DesktopResultStatus",
    "MatchDecision",
    "MatchStatus",
    "SpotifyCandidate",
    "SpotifyDesktopResult",
    "SpotifyRequest",
]
```

- [ ] **Step 4: Implement normalization and scoring**

Create `src/backend/tools/spotify_desktop/matching.py`:

```python
from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from difflib import SequenceMatcher

from tools.spotify_desktop.models import (
    MatchDecision,
    MatchStatus,
    SpotifyCandidate,
    SpotifyRequest,
)

_VARIANT_TERMS = {
    "acoustic",
    "cover",
    "en vivo",
    "instrumental",
    "karaoke",
    "live",
    "remix",
    "sped up",
    "tribute",
}


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", ascii_text.lower())).strip()


def _token_overlap(expected: str, actual: str) -> float:
    expected_tokens = set(normalize_text(expected).split())
    actual_tokens = set(normalize_text(actual).split())
    if not expected_tokens:
        return 0.0
    return len(expected_tokens & actual_tokens) / len(expected_tokens)


def _variant_penalty(request: SpotifyRequest, candidate: SpotifyCandidate) -> float:
    requested = normalize_text(request.raw)
    observed = normalize_text(f"{candidate.title} {candidate.subtitle} {candidate.artist}")
    penalty = 0.0
    for term in _VARIANT_TERMS:
        normalized_term = normalize_text(term)
        if normalized_term in observed and normalized_term not in requested:
            penalty += 0.12
    return min(penalty, 0.36)


def score_candidate(request: SpotifyRequest, candidate: SpotifyCandidate) -> float:
    expected_title = request.title or request.query
    title = normalize_text(candidate.title)
    title_sequence = SequenceMatcher(None, normalize_text(expected_title), title).ratio()
    title_tokens = _token_overlap(expected_title, candidate.title)
    score = (title_sequence * 0.55) + (title_tokens * 0.25)

    if request.artist:
        artist_sequence = SequenceMatcher(
            None,
            normalize_text(request.artist),
            normalize_text(candidate.artist),
        ).ratio()
        score += artist_sequence * 0.20
        if artist_sequence < 0.45:
            score -= 0.18
    elif candidate.kind != "track":
        score -= 0.20

    return max(0.0, min(1.0, score - _variant_penalty(request, candidate)))


def choose_candidate(
    request: SpotifyRequest,
    candidates: list[SpotifyCandidate],
) -> MatchDecision:
    ranked = sorted(
        (replace(item, score=score_candidate(request, item)) for item in candidates),
        key=lambda item: item.score,
        reverse=True,
    )
    if not ranked or ranked[0].score < 0.55:
        return MatchDecision(status=MatchStatus.NOT_FOUND)

    best = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None
    same_title_different_artist = bool(
        not request.artist
        and runner_up
        and normalize_text(best.title) == normalize_text(runner_up.title)
        and normalize_text(best.artist) != normalize_text(runner_up.artist)
    )
    margin = best.score - (runner_up.score if runner_up else 0.0)
    if best.score < 0.74 or margin < 0.07 or same_title_different_artist:
        return MatchDecision(
            status=MatchStatus.AMBIGUOUS,
            alternatives=tuple(ranked[:3]),
            confidence=best.score,
        )
    return MatchDecision(
        status=MatchStatus.SELECTED,
        selected=best,
        confidence=best.score,
    )
```

- [ ] **Step 5: Run matcher tests**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_spotify_desktop_matching.py -q
```

Expected: all matcher tests pass.

- [ ] **Step 6: Commit matcher foundation**

```powershell
git add -- src/backend/tools/spotify_desktop tests/test_spotify_desktop_matching.py
git commit -m "feat: rank Spotify desktop search results"
```

## Task 3: Windows process and window lifecycle

**Files:**
- Create: `src/backend/tools/spotify_desktop/windows.py`
- Create: `tests/test_spotify_desktop_windows.py`

- [ ] **Step 1: Write failing lifecycle tests with injected OS boundaries**

Create `tests/test_spotify_desktop_windows.py` with these tests and fakes:

```python
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

    try:
        adapter.ensure_window(timeout=0)
    except TimeoutError as error:
        assert str(error) == "spotify_window_not_found"
    else:
        raise AssertionError("Expected the unrelated window to be rejected")


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
```

- [ ] **Step 2: Run lifecycle tests and verify the module is missing**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_spotify_desktop_windows.py -q
```

Expected: collection fails because `tools.spotify_desktop.windows` does not exist.

- [ ] **Step 3: Implement the injected window adapter and native defaults**

Create the lifecycle portion of `src/backend/tools/spotify_desktop/windows.py`:

```python
from __future__ import annotations

import ctypes
import os
import sys
import time
import webbrowser
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass

import psutil

IS_WINDOWS = sys.platform == "win32"


@dataclass(frozen=True)
class SpotifyWindow:
    handle: int
    pid: int
    title: str


def _spotify_process_ids() -> set[int]:
    result: set[int] = set()
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if str(process.info.get("name") or "").lower() == "spotify.exe":
                result.add(int(process.info["pid"]))
        except (psutil.Error, OSError, TypeError, ValueError):
            continue
    return result


def _visible_windows() -> list[SpotifyWindow]:
    if not IS_WINDOWS:
        return []
    user32 = ctypes.windll.user32
    result: list[SpotifyWindow] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def callback(handle, _parameter):
        if not user32.IsWindowVisible(handle):
            return True
        length = user32.GetWindowTextLengthW(handle)
        if length <= 0:
            return True
        title_buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, title_buffer, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        result.append(
            SpotifyWindow(handle=int(handle), pid=int(pid.value), title=title_buffer.value)
        )
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return result


def _start_spotify_client() -> None:
    if not webbrowser.open("spotify:"):
        roaming = os.getenv("APPDATA", "")
        executable = os.path.join(roaming, "Spotify", "Spotify.exe")
        if not os.path.isfile(executable):
            raise FileNotFoundError("spotify_not_installed")
        os.startfile(executable)


def _focus_window(handle: int) -> bool:
    if not IS_WINDOWS:
        return False
    user32 = ctypes.windll.user32
    user32.ShowWindow(wintypes.HWND(handle), 9)
    return bool(user32.SetForegroundWindow(wintypes.HWND(handle)))


def _foreground_window() -> int:
    if not IS_WINDOWS:
        return 0
    return int(ctypes.windll.user32.GetForegroundWindow())


class WindowsSpotifyWindowAdapter:
    def __init__(
        self,
        *,
        process_ids: Callable[[], set[int]] = _spotify_process_ids,
        windows: Callable[[], list[SpotifyWindow]] = _visible_windows,
        start_client: Callable[[], None] = _start_spotify_client,
        focus_window: Callable[[int], bool] = _focus_window,
        foreground_window: Callable[[], int] = _foreground_window,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval: float = 0.25,
    ) -> None:
        self._process_ids = process_ids
        self._windows = windows
        self._start_client = start_client
        self._focus_window = focus_window
        self._foreground_window = foreground_window
        self._monotonic = monotonic
        self._sleep = sleep
        self._poll_interval = max(0.0, poll_interval)

    def discover_window(self) -> SpotifyWindow | None:
        process_ids = self._process_ids()
        candidates = [window for window in self._windows() if window.pid in process_ids]
        return max(candidates, key=lambda item: len(item.title), default=None)

    def ensure_window(self, timeout: float) -> SpotifyWindow:
        window = self.discover_window()
        if window is not None:
            return window
        self._start_client()
        deadline = self._monotonic() + max(0.0, timeout)
        while True:
            window = self.discover_window()
            if window is not None:
                return window
            if self._monotonic() >= deadline:
                raise TimeoutError("spotify_window_not_found")
            self._sleep(self._poll_interval)

    def focus(self, window: SpotifyWindow) -> bool:
        return self._focus_window(window.handle) and self.is_foreground(window)

    def is_foreground(self, window: SpotifyWindow) -> bool:
        return self._foreground_window() == window.handle

    def current_title(self, window: SpotifyWindow) -> str:
        for candidate in self._windows():
            if candidate.handle == window.handle and candidate.pid == window.pid:
                return candidate.title
        return ""
```

- [ ] **Step 4: Run lifecycle tests**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_spotify_desktop_windows.py -q
```

Expected: all lifecycle tests pass without opening real Spotify.

- [ ] **Step 5: Commit the Windows lifecycle adapter**

```powershell
git add -- src/backend/tools/spotify_desktop/windows.py tests/test_spotify_desktop_windows.py
git commit -m "feat: manage Spotify desktop windows"
```

## Task 4: UI Automation search, candidates, and controls

**Files:**
- Modify: `src/backend/tools/spotify_desktop/windows.py`
- Modify: `tests/test_spotify_desktop_windows.py`

- [ ] **Step 1: Add failing UIA interaction tests**

Append fake controls and tests to `tests/test_spotify_desktop_windows.py`:

```python
from tools.spotify_desktop.models import SpotifyCandidate
from tools.spotify_desktop.windows import SpotifyUIAutomationAdapter


class FakeControl:
    def __init__(self, *, name="", control_type="", children=None):
        self.name = name
        self.control_type = control_type
        self.children = children or []
        self.text = ""
        self.invoked = False

    def window_text(self):
        return self.name

    def descendants(self):
        return list(self.children)

    def set_edit_text(self, value):
        self.text = value

    def invoke(self):
        self.invoked = True


def test_search_uses_accessible_edit_control_before_shortcut():
    search = FakeControl(name="Buscar", control_type="Edit")
    root = FakeControl(children=[search])
    shortcuts = []
    adapter = SpotifyUIAutomationAdapter(
        root_factory=lambda _handle: root,
        send_shortcut=lambda shortcut: shortcuts.append(shortcut),
    )

    adapter.search(500, "No te apartes de mi Vicentico")

    assert search.text == "No te apartes de mi Vicentico"
    assert shortcuts == []


def test_search_uses_documented_ctrl_k_when_edit_is_initially_missing():
    search = FakeControl(name="Search", control_type="Edit")
    roots = iter([FakeControl(), FakeControl(children=[search])])
    shortcuts = []
    adapter = SpotifyUIAutomationAdapter(
        root_factory=lambda _handle: next(roots),
        send_shortcut=lambda shortcut: shortcuts.append(shortcut),
    )

    adapter.search(501, "Killer Queen Queen")

    assert shortcuts == ["^k"]
    assert search.text == "Killer Queen Queen"


def test_result_elements_are_mapped_to_stable_candidate_ids():
    row = FakeControl(name="Killer Queen | Queen | Song", control_type="ListItem")
    root = FakeControl(children=[row])
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
    assert row.invoked


def test_control_uses_accessible_button_name():
    next_button = FakeControl(name="Next", control_type="Button")
    root = FakeControl(children=[next_button])
    adapter = SpotifyUIAutomationAdapter(root_factory=lambda _handle: root)

    assert adapter.control(503, "siguiente")
    assert next_button.invoked
```

- [ ] **Step 2: Run UIA tests and confirm adapter import fails**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_spotify_desktop_windows.py -q
```

Expected: failures because `SpotifyUIAutomationAdapter` is not defined.

- [ ] **Step 3: Implement lazy pywinauto boundaries and semantic lookup**

Append to `src/backend/tools/spotify_desktop/windows.py`:

```python
from typing import Any

from tools.spotify_desktop.models import SpotifyCandidate

_SEARCH_NAMES = {"buscar", "search"}
_CONTROL_NAMES = {
    "pausar": {"pause", "pausar"},
    "reanudar": {"play", "reproducir", "resume", "reanudar"},
    "siguiente": {"next", "siguiente"},
    "anterior": {"previous", "anterior"},
    "shuffle_on": {"enable shuffle", "activar aleatorio", "shuffle"},
    "shuffle_off": {"disable shuffle", "desactivar aleatorio", "shuffle"},
    "repeat_on": {"enable repeat", "activar repeticion", "repeat"},
    "repeat_off": {"disable repeat", "desactivar repeticion", "repeat"},
}


def _default_uia_root(handle: int):
    from pywinauto import Desktop

    return Desktop(backend="uia").window(handle=handle)


def _default_shortcut(shortcut: str) -> None:
    from pywinauto.keyboard import send_keys

    send_keys(shortcut, pause=0.05)


def _control_name(control: Any) -> str:
    try:
        return str(control.window_text() or "").strip()
    except Exception:
        return ""


def _control_type(control: Any) -> str:
    explicit = getattr(control, "control_type", "")
    if explicit:
        return str(explicit)
    info = getattr(control, "element_info", None)
    return str(getattr(info, "control_type", "") or "")


class SpotifyUIAutomationAdapter:
    def __init__(
        self,
        *,
        root_factory: Callable[[int], Any] = _default_uia_root,
        send_shortcut: Callable[[str], None] = _default_shortcut,
    ) -> None:
        self._root_factory = root_factory
        self._send_shortcut = send_shortcut
        self._elements: dict[str, Any] = {}

    def _controls(self, handle: int) -> list[Any]:
        return list(self._root_factory(handle).descendants())

    def _search_edit(self, handle: int):
        for control in self._controls(handle):
            if _control_type(control).lower() != "edit":
                continue
            name = _control_name(control).lower()
            if not name or any(label in name for label in _SEARCH_NAMES):
                return control
        return None

    def search(self, handle: int, query: str) -> None:
        edit = self._search_edit(handle)
        if edit is None:
            self._send_shortcut("^k")
            edit = self._search_edit(handle)
        if edit is None:
            raise RuntimeError("spotify_search_unavailable")
        edit.set_edit_text(str(query or "").strip())

    def read_candidates(self, handle: int) -> list[SpotifyCandidate]:
        self._elements.clear()
        candidates: list[SpotifyCandidate] = []
        for control in self._controls(handle):
            if _control_type(control).lower() not in {"dataitem", "hyperlink", "listitem"}:
                continue
            parts = [part.strip() for part in _control_name(control).split("|") if part.strip()]
            if len(parts) < 2:
                continue
            element_id = f"candidate-{len(candidates)}"
            candidate = SpotifyCandidate(
                element_id=element_id,
                title=parts[0],
                artist=parts[1],
                kind="track" if len(parts) < 3 or parts[2].lower() in {"song", "track", "cancion"} else parts[2].lower(),
                subtitle=parts[2] if len(parts) >= 3 else "",
            )
            self._elements[element_id] = control
            candidates.append(candidate)
        return candidates

    def activate(self, candidate: SpotifyCandidate) -> bool:
        control = self._elements.get(candidate.element_id)
        if control is None:
            return False
        try:
            control.invoke()
        except Exception:
            control.double_click_input()
        return True

    def control(self, handle: int, action: str) -> bool:
        names = _CONTROL_NAMES.get(str(action or "").strip().lower(), set())
        for control in self._controls(handle):
            if _control_type(control).lower() != "button":
                continue
            observed = _control_name(control).lower()
            if any(name in observed for name in names):
                control.invoke()
                return True
        return False

    def now_playing(self, handle: int) -> tuple[str, str] | None:
        for control in self._controls(handle):
            name = _control_name(control)
            control_type = _control_type(control).lower()
            if control_type not in {"group", "text"} or "|" not in name:
                continue
            parts = [part.strip() for part in name.split("|") if part.strip()]
            if len(parts) >= 2:
                return parts[0], parts[1]
        return None
```

- [ ] **Step 4: Run UIA and lifecycle tests**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_spotify_desktop_windows.py -q
```

Expected: all tests pass and no real desktop input occurs.

- [ ] **Step 5: Commit UI Automation support**

```powershell
git add -- src/backend/tools/spotify_desktop/windows.py tests/test_spotify_desktop_windows.py
git commit -m "feat: automate Spotify desktop search"
```

## Task 5: State machine, ambiguity, retry, and verification

**Files:**
- Create: `src/backend/tools/spotify_desktop/controller.py`
- Create: `tests/test_spotify_desktop_controller.py`
- Modify: `src/backend/tools/spotify_desktop/__init__.py`

- [ ] **Step 1: Write failing controller tests**

Create `tests/test_spotify_desktop_controller.py` with injected adapters:

```python
import threading

from tools.spotify_desktop.controller import SpotifyDesktopController
from tools.spotify_desktop.models import (
    AutomationState,
    DesktopResultStatus,
    SpotifyCandidate,
    SpotifyRequest,
)
from tools.spotify_desktop.windows import SpotifyWindow


class FakeWindowAdapter:
    def __init__(self, titles):
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


class FakeUIA:
    def __init__(self, candidates, now_playing=None):
        self.candidates = candidates
        self.playing = now_playing
        self.activated = []

    def search(self, handle, query):
        assert handle == 700
        assert query

    def read_candidates(self, handle):
        assert handle == 700
        return list(self.candidates)

    def activate(self, candidate):
        self.activated.append(candidate.element_id)
        return True

    def now_playing(self, _handle):
        return self.playing

    def control(self, _handle, _action):
        return True


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
        title="No Te Apartes de Mí",
        artist="Vicentico",
    )


def test_verified_candidate_reports_success():
    windows = FakeWindowAdapter(["Vicentico - No Te Apartes de Mí"])
    uia = FakeUIA([expected_candidate()], now_playing=("No Te Apartes de Mí", "Vicentico"))
    controller = SpotifyDesktopController(windows, uia, start_timeout=2, action_timeout=1)

    result = controller.play(request())

    assert result.status is DesktopResultStatus.SUCCESS
    assert result.title == "No Te Apartes de Mí"
    assert result.artist == "Vicentico"
    assert result.states[-1] is AutomationState.COMPLETE
    assert uia.activated == ["expected"]


def test_ambiguous_results_are_returned_without_clicking():
    windows = FakeWindowAdapter(["Spotify"])
    uia = FakeUIA(
        [
            expected_candidate("vicentico"),
            SpotifyCandidate("roberto", "No Te Apartes de Mí", "Roberto Carlos"),
        ]
    )
    controller = SpotifyDesktopController(windows, uia, start_timeout=2, action_timeout=1)
    title_only = SpotifyRequest(
        raw="No te apartes de mi",
        query="No te apartes de mi",
        title="No te apartes de mi",
    )

    result = controller.play(title_only)

    assert result.status is DesktopResultStatus.AMBIGUOUS
    assert len(result.choices) == 2
    assert uia.activated == []


def test_focus_loss_aborts_before_typing():
    windows = FakeWindowAdapter(["Spotify"])
    windows.focused = False
    uia = FakeUIA([expected_candidate()])
    controller = SpotifyDesktopController(windows, uia, start_timeout=2, action_timeout=1)

    result = controller.play(request())

    assert result.status is DesktopResultStatus.FAILED
    assert result.message_key == "spotify_focus_lost"
    assert uia.activated == []


def test_unverified_playback_retries_next_ranked_candidate_once():
    class RetryUIA(FakeUIA):
        def now_playing(self, _handle):
            if self.activated and self.activated[-1] == "second":
                return "No Te Apartes", "Vicentico"
            return None

    windows = FakeWindowAdapter(["Spotify"])
    uia = RetryUIA(
        [
            expected_candidate("first"),
            SpotifyCandidate("second", "No Te Apartes", "Vicentico"),
        ]
    )
    controller = SpotifyDesktopController(windows, uia, start_timeout=2, action_timeout=0.05)

    result = controller.play(request())

    assert result.status is DesktopResultStatus.SUCCESS
    assert uia.activated == ["first", "second"]


def test_lock_is_released_after_exception():
    class FailingUIA(FakeUIA):
        def search(self, _handle, _query):
            raise RuntimeError("layout changed")

    windows = FakeWindowAdapter(["Spotify"])
    controller = SpotifyDesktopController(
        windows,
        FailingUIA([]),
        start_timeout=2,
        action_timeout=1,
    )

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
                now_playing=("No Te Apartes de Mí", "Vicentico"),
            )
            self.search_calls = 0

        def search(self, handle, query):
            super().search(handle, query)
            self.search_calls += 1
            if self.search_calls == 1:
                entered.set()
                assert release.wait(timeout=2)

    windows = FakeWindowAdapter(["Spotify"])
    controller = SpotifyDesktopController(
        windows,
        BlockingUIA(),
        start_timeout=2,
        action_timeout=1,
    )
    results = []
    first = threading.Thread(target=lambda: results.append(controller.play(request())))
    second = threading.Thread(target=lambda: results.append(controller.play(request())))

    first.start()
    assert entered.wait(timeout=2)
    second.start()
    release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert sorted((result.status for result in results), key=lambda item: item.value) == [
        DesktopResultStatus.CANCELLED,
        DesktopResultStatus.SUCCESS,
    ]


def test_controller_logs_state_and_duration_without_query(caplog):
    caplog.set_level("INFO", logger="JARVIS")
    windows = FakeWindowAdapter(["Vicentico - No Te Apartes de Mí"])
    uia = FakeUIA(
        [expected_candidate()],
        now_playing=("No Te Apartes de Mí", "Vicentico"),
    )
    controller = SpotifyDesktopController(windows, uia, start_timeout=2, action_timeout=1)

    controller.play(request())

    output = caplog.text
    assert "final_state=complete" in output
    assert "duration_ms=" in output
    assert "No te apartes" not in output
```

- [ ] **Step 2: Run controller tests and confirm the module is missing**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_spotify_desktop_controller.py -q
```

Expected: collection fails because `tools.spotify_desktop.controller` does not exist.

- [ ] **Step 3: Implement the serialized controller**

Create `src/backend/tools/spotify_desktop/controller.py`:

```python
from __future__ import annotations

import threading
import time
from collections.abc import Callable

from tools.spotify_desktop.matching import choose_candidate, normalize_text
from tools.spotify_desktop.models import (
    AutomationState,
    DesktopResultStatus,
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
        self._start_timeout = start_timeout
        self._action_timeout = action_timeout
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = threading.Lock()
        self._generation_lock = threading.Lock()
        self._generation = 0

    def _next_generation(self) -> int:
        with self._generation_lock:
            self._generation += 1
            return self._generation

    def _cancelled(self, generation: int) -> bool:
        with self._generation_lock:
            return generation != self._generation

    @staticmethod
    def _matches(candidate: SpotifyCandidate, observed: tuple[str, str] | None, title: str) -> bool:
        expected_title = normalize_text(candidate.title)
        expected_artist = normalize_text(candidate.artist)
        if observed:
            observed_title = normalize_text(observed[0])
            observed_artist = normalize_text(observed[1])
            return expected_title in observed_title and (
                not expected_artist or expected_artist in observed_artist
            )
        normalized_window = normalize_text(title)
        return expected_title in normalized_window and (
            not expected_artist or expected_artist in normalized_window
        )

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
            if self._matches(candidate, observed, self._windows.current_title(window)):
                return True
            if self._monotonic() >= deadline:
                return False
            self._sleep(0.2)

    def play(self, request: SpotifyRequest) -> SpotifyDesktopResult:
        states = [AutomationState.IDLE]
        generation = self._next_generation()
        if not self._lock.acquire(timeout=self._start_timeout + self._action_timeout):
            return SpotifyDesktopResult(
                status=DesktopResultStatus.FAILED,
                message_key="spotify_automation_busy",
                states=tuple(states),
            )
        try:
            if self._cancelled(generation):
                return SpotifyDesktopResult(
                    status=DesktopResultStatus.CANCELLED,
                    message_key="spotify_cancelled",
                    states=(AutomationState.IDLE, AutomationState.CANCELLED),
                )
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
                states.append(AutomationState.CANCELLED)
                return SpotifyDesktopResult(
                    status=DesktopResultStatus.CANCELLED,
                    message_key="spotify_cancelled",
                    states=tuple(states),
                )
            candidates = self._uia.read_candidates(window.handle)
            decision = choose_candidate(request, candidates)
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
            states.append(AutomationState.SELECTING)
            if not self._uia.activate(selected):
                raise RuntimeError("spotify_candidate_activation_failed")
            states.extend([AutomationState.PLAYING, AutomationState.VERIFYING])
            verified = self._verify(window, selected, generation)
            if verified is None:
                states.append(AutomationState.CANCELLED)
                return SpotifyDesktopResult(
                    status=DesktopResultStatus.CANCELLED,
                    message_key="spotify_cancelled",
                    states=tuple(states),
                )
            if not verified:
                ranked = sorted(candidates, key=lambda item: item.score, reverse=True)
                retry = next((item for item in ranked if item.element_id != selected.element_id), None)
                retry_verified = (
                    self._verify(window, retry, generation)
                    if retry is not None and self._uia.activate(retry)
                    else False
                )
                if retry_verified is None:
                    states.append(AutomationState.CANCELLED)
                    return SpotifyDesktopResult(
                        status=DesktopResultStatus.CANCELLED,
                        message_key="spotify_cancelled",
                        states=tuple(states),
                    )
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
        except TimeoutError:
            states.append(AutomationState.FAILED)
            return SpotifyDesktopResult(
                status=DesktopResultStatus.UNAVAILABLE,
                message_key="spotify_start_timeout",
                states=tuple(states),
            )
        except Exception:
            states.append(AutomationState.FAILED)
            return SpotifyDesktopResult(
                status=DesktopResultStatus.FAILED,
                message_key="spotify_automation_failed",
                states=tuple(states),
            )
        finally:
            self._lock.release()

    def control(self, action: str) -> SpotifyDesktopResult:
        if not self._lock.acquire(blocking=False):
            return SpotifyDesktopResult(
                status=DesktopResultStatus.FAILED,
                message_key="spotify_automation_busy",
            )
        try:
            window = self._windows.ensure_window(self._start_timeout)
            if not self._windows.focus(window):
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
        finally:
            self._lock.release()
```

Update `src/backend/tools/spotify_desktop/__init__.py` to export `SpotifyDesktopController`.

- [ ] **Step 4: Add sanitized operation diagnostics**

Add `import logging`, store `self._logger = logging.getLogger("JARVIS")`, and
record the operation start immediately before lock acquisition:

```python
started = self._monotonic()
```

In the `finally` block, before releasing the lock, record only state and timing:

```python
final_state = states[-1].value if states else AutomationState.FAILED.value
duration_ms = max(0, int((self._monotonic() - started) * 1000))
self._logger.info(
    "spotify_desktop_operation final_state=%s duration_ms=%d",
    final_state,
    duration_ms,
)
```

Change the generic handler to `except Exception as error:` and add:

```python
self._logger.warning(
    "spotify_desktop_failed error_type=%s",
    type(error).__name__,
)
```

Do not include the request, query, candidate text, window title, path, or raw
exception message in these records. `control` continues to use the existing
sanitized Spotify provider logger until its observable state checks are added.

- [ ] **Step 5: Correct retry ranking to preserve matcher scores**

Before activation, create a ranked list with `score_candidate`, pass it to
`choose_candidate`, and use the same ranked list for retry:

```python
from dataclasses import replace

from tools.spotify_desktop.matching import score_candidate

ranked = sorted(
    (replace(item, score=score_candidate(request, item)) for item in candidates),
    key=lambda item: item.score,
    reverse=True,
)
decision = choose_candidate(request, ranked)
```

Replace the later retry assignment with:

```python
retry = next(
    (item for item in ranked if item.element_id != selected.element_id),
    None,
)
```

- [ ] **Step 6: Run controller, matcher, and window tests**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_spotify_desktop_controller.py tests\test_spotify_desktop_matching.py tests\test_spotify_desktop_windows.py -q
```

Expected: all desktop automation tests pass.

- [ ] **Step 7: Commit the controller**

```powershell
git add -- src/backend/tools/spotify_desktop tests/test_spotify_desktop_controller.py
git commit -m "feat: verify Spotify desktop playback"
```

## Task 6: Optional cropped visual recovery

**Files:**
- Create: `src/backend/tools/spotify_desktop/visual.py`
- Modify: `src/backend/tools/spotify_desktop/controller.py`
- Modify: `tests/test_spotify_desktop_controller.py`

- [ ] **Step 1: Add failing visual recovery safety tests**

Append to `tests/test_spotify_desktop_controller.py`:

```python
from pathlib import Path

from tools.spotify_desktop.visual import SpotifyVisualRecovery, VisualTarget


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
```

- [ ] **Step 2: Run the visual tests and confirm the module is missing**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_spotify_desktop_controller.py -q
```

Expected: collection fails because `tools.spotify_desktop.visual` does not exist.

- [ ] **Step 3: Implement bounded capture lifecycle**

Create `src/backend/tools/spotify_desktop/visual.py`:

```python
from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VisualTarget:
    x: int
    y: int
    width: int
    height: int
    label: str = ""


class SpotifyVisualRecovery:
    def __init__(
        self,
        *,
        scratch_dir: str | Path,
        capture: Callable[[int, Path], None] | None = None,
        analyze: Callable[[Path, str], VisualTarget | None] | None = None,
        click: Callable[[int, int, int], bool] | None = None,
    ) -> None:
        self._scratch_dir = Path(scratch_dir)
        self._capture = capture
        self._analyze = analyze
        self._click = click

    @property
    def available(self) -> bool:
        return bool(self._capture and self._analyze and self._click)

    @staticmethod
    def _inside(target: VisualTarget, bounds: tuple[int, int, int, int]) -> bool:
        left, top, right, bottom = bounds
        window_width = right - left
        window_height = bottom - top
        return (
            target.width > 0
            and target.height > 0
            and target.x >= 0
            and target.y >= 0
            and target.x + target.width <= window_width
            and target.y + target.height <= window_height
        )

    def activate(
        self,
        handle: int,
        bounds: tuple[int, int, int, int],
        query: str,
    ) -> bool:
        if not self.available:
            return False
        self._scratch_dir.mkdir(parents=True, exist_ok=True)
        path = self._scratch_dir / f"spotify-{uuid.uuid4().hex}.png"
        try:
            self._capture(handle, path)
            target = self._analyze(path, query)
            if target is None or not self._inside(target, bounds):
                return False
            left, top, _right, _bottom = bounds
            center_x = left + target.x + target.width // 2
            center_y = top + target.y + target.height // 2
            return bool(self._click(handle, center_x, center_y))
        finally:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
```

- [ ] **Step 4: Add production capture, structured vision parsing, and guarded click adapters**

Add these imports and helpers to `visual.py`:

```python
import base64
import ctypes
import json
import re


def _capture_spotify_window(handle: int, path: Path) -> None:
    from pywinauto import Desktop

    image = Desktop(backend="uia").window(handle=handle).capture_as_image()
    image.save(path)


def _visual_target_from_response(response) -> VisualTarget | None:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        text = " ".join(
            str(item.get("text") or "") if isinstance(item, dict) else str(item)
            for item in content
        )
    else:
        text = str(content or "")
    match = re.search(r"\{[^{}]+\}", text, flags=re.DOTALL)
    if match is None:
        return None
    try:
        payload = json.loads(match.group(0))
        return VisualTarget(
            x=int(payload["x"]),
            y=int(payload["y"]),
            width=int(payload["width"]),
            height=int(payload["height"]),
            label=str(payload.get("label") or ""),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _vision_analyzer(model):
    def analyze(path: Path, query: str) -> VisualTarget | None:
        from langchain_core.messages import HumanMessage

        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        prompt = (
            "This image contains only a Spotify Desktop window. Locate the single "
            f"search result that best matches {query!r}. Return JSON only with integer "
            "fields x, y, width, height and a short label. Coordinates must be relative "
            "to the top-left corner of this image. Return {} when no reliable match exists."
        )
        response = model.invoke(
            [
                HumanMessage(
                    content=[
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{encoded}"},
                        },
                    ]
                )
            ]
        )
        return _visual_target_from_response(response)

    return analyze


def _click_if_foreground(handle: int, x: int, y: int) -> bool:
    if int(ctypes.windll.user32.GetForegroundWindow()) != int(handle):
        return False
    from pywinauto import mouse

    mouse.click(coords=(x, y))
    return True


def build_default_visual_recovery(*, model, scratch_dir: str | Path):
    if model is None:
        return SpotifyVisualRecovery(scratch_dir=scratch_dir)
    return SpotifyVisualRecovery(
        scratch_dir=scratch_dir,
        capture=_capture_spotify_window,
        analyze=_vision_analyzer(model),
        click=_click_if_foreground,
    )
```

Add these parser tests to `tests/test_spotify_desktop_controller.py`:

```python
from tools.spotify_desktop.visual import _visual_target_from_response


class VisionResponse:
    def __init__(self, content):
        self.content = content


def test_visual_response_parser_accepts_structured_json():
    target = _visual_target_from_response(
        VisionResponse('{"x": 10, "y": 20, "width": 30, "height": 40, "label": "track"}')
    )
    assert target == VisualTarget(10, 20, 30, 40, "track")


def test_visual_response_parser_extracts_surrounded_json():
    target = _visual_target_from_response(
        VisionResponse('result: {"x": 1, "y": 2, "width": 3, "height": 4}')
    )
    assert target == VisualTarget(1, 2, 3, 4)


def test_visual_response_parser_rejects_missing_fields_and_non_json():
    assert _visual_target_from_response(VisionResponse('{"x": 1}')) is None
    assert _visual_target_from_response(VisionResponse("no reliable match")) is None


def test_negative_visual_geometry_never_clicks(tmp_path):
    clicks = []
    recovery = SpotifyVisualRecovery(
        scratch_dir=tmp_path,
        capture=lambda _handle, path: path.write_bytes(b"png"),
        analyze=lambda _path, _query: VisualTarget(-1, 10, 20, 20),
        click=lambda _handle, x, y: clicks.append((x, y)) or True,
    )

    assert not recovery.activate(700, (0, 0, 100, 100), "expected")
    assert clicks == []
```

- [ ] **Step 5: Wire visual recovery only after deterministic search failure**

In `SpotifyDesktopController.play`, catch only
`RuntimeError("spotify_search_unavailable")` around search/candidate discovery.
Before returning failure, verify the window is still foreground and call:

```python
if (
    self._visual is not None
    and self._windows.is_foreground(window)
    and self._visual.activate(
        window.handle,
        self._windows.bounds(window),
        request.query,
    )
):
    states.extend([AutomationState.PLAYING, AutomationState.VERIFYING])
    if self._verify_query(window, request):
        states.append(AutomationState.COMPLETE)
        return SpotifyDesktopResult(
            status=DesktopResultStatus.SUCCESS,
            title=request.title,
            artist=request.artist,
            message_key="spotify_playback_started",
            states=tuple(states),
        )
```

Add `bounds(window)` to `WindowsSpotifyWindowAdapter` using `GetWindowRect`, and
add `_verify_query` to compare the request against UIA/window metadata. Keep the
generic exception handler outside this recovery block so vision never handles
programming errors or unrelated Win32 failures.

- [ ] **Step 6: Run desktop automation tests**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_spotify_desktop_controller.py tests\test_spotify_desktop_windows.py -q
```

Expected: all tests pass; scratch directories are empty after success and failure.

- [ ] **Step 7: Commit visual recovery**

```powershell
git add -- src/backend/tools/spotify_desktop tests/test_spotify_desktop_controller.py tests/test_spotify_desktop_windows.py
git commit -m "feat: add bounded Spotify visual recovery"
```

## Task 7: Coordinate API and desktop playback without blocking OAuth

**Files:**
- Modify: `src/backend/tools/spotify.py:56-88,290-328,1219-1598`
- Modify: `tests/test_spotify_recs.py`
- Modify: `src/backend/tools/spotify_desktop/__init__.py`

- [ ] **Step 1: Preserve and verify the existing OAuth isolation fix**

Keep the existing module fixture in `tests/test_spotify_recs.py`:

```python
@pytest.fixture(autouse=True)
def _disable_real_spotify_client(monkeypatch):
    """Keep unit tests independent from local credentials and OAuth state."""
    monkeypatch.setattr(spotify, "sp", None)
```

Run before integration:

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_spotify_recs.py -q
```

Expected: `19 passed` with no port 8888 listener and no browser window.

- [ ] **Step 2: Add failing coordinator tests**

Append to `tests/test_spotify_recs.py`:

```python
from tools.spotify_desktop.models import (
    DesktopResultStatus,
    SpotifyCandidate,
    SpotifyDesktopResult,
)


def test_auto_mode_uses_desktop_without_cached_api_token(monkeypatch):
    calls = []
    monkeypatch.setattr(spotify, "SPOTIFY_PLAYBACK_MODE", "auto")
    monkeypatch.setattr(spotify, "_spotify_has_valid_cached_token", lambda: False)
    monkeypatch.setattr(
        spotify,
        "_spotify_play_desktop",
        lambda song: calls.append(song) or "desktop-ok",
    )

    assert spotify.reproducir_en_spotify.invoke({"cancion": "Killer Queen"}) == "desktop-ok"
    assert calls == ["Killer Queen"]


def test_api_mode_keeps_explicit_spotipy_path(monkeypatch):
    monkeypatch.setattr(spotify, "SPOTIFY_PLAYBACK_MODE", "api")
    monkeypatch.setattr(
        spotify,
        "_spotify_play_api",
        lambda song: spotify.SpotifyAPIPlaybackResult(
            ok=True,
            message=f"api:{song}",
        ),
    )
    monkeypatch.setattr(
        spotify,
        "_spotify_play_desktop",
        lambda _song: (_ for _ in ()).throw(AssertionError("desktop must not run")),
    )

    assert spotify.reproducir_en_spotify.invoke({"cancion": "Killer Queen"}) == "api:Killer Queen"


def test_auto_mode_falls_back_after_permanent_api_capability_failure(monkeypatch):
    monkeypatch.setattr(spotify, "SPOTIFY_PLAYBACK_MODE", "auto")
    monkeypatch.setattr(spotify, "_spotify_has_valid_cached_token", lambda: True)
    monkeypatch.setattr(
        spotify,
        "_spotify_play_api",
        lambda _song: spotify.SpotifyAPIPlaybackResult(
            ok=False,
            message="blocked",
            capability_failure=True,
        ),
    )
    monkeypatch.setattr(spotify, "_spotify_play_desktop", lambda _song: "desktop-ok")

    assert spotify.reproducir_en_spotify.invoke({"cancion": "Killer Queen"}) == "desktop-ok"


def test_desktop_ambiguity_is_localized(monkeypatch):
    result = SpotifyDesktopResult(
        status=DesktopResultStatus.AMBIGUOUS,
        message_key="spotify_ambiguous_results",
        choices=(
            SpotifyCandidate("one", "No Te Apartes de Mí", "Vicentico"),
            SpotifyCandidate("two", "No Te Apartes de Mí", "Roberto Carlos"),
        ),
    )
    monkeypatch.setattr(spotify, "_spotify_desktop_result", lambda _song: result)

    message = spotify._spotify_play_desktop("No te apartes de mi")

    assert "Vicentico" in message
    assert "Roberto Carlos" in message
```

- [ ] **Step 3: Run coordinator tests and verify failures**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_spotify_recs.py -q
```

Expected: failures for missing coordinator helpers and desktop result formatting.

- [ ] **Step 4: Add lazy desktop controller construction**

In `src/backend/tools/spotify_desktop/__init__.py`, add:

```python
def build_windows_controller(*, start_timeout: float, action_timeout: float):
    import os

    from core import jarvis_config
    from core.service_container import services
    from tools.spotify_desktop.controller import SpotifyDesktopController
    from tools.spotify_desktop.visual import build_default_visual_recovery
    from tools.spotify_desktop.windows import (
        SpotifyUIAutomationAdapter,
        WindowsSpotifyWindowAdapter,
    )

    visual_recovery = build_default_visual_recovery(
        model=services.llm_vision,
        scratch_dir=os.path.join(jarvis_config.ROOT_DIR, "scratch", "spotify_visual"),
    )
    return SpotifyDesktopController(
        WindowsSpotifyWindowAdapter(),
        SpotifyUIAutomationAdapter(),
        visual_recovery=visual_recovery,
        start_timeout=start_timeout,
        action_timeout=action_timeout,
    )
```

Do not construct UIA objects during module import.

- [ ] **Step 5: Extract the current API implementation behind a plain function**

In `src/backend/tools/spotify.py`:

1. Rename the current decorated `reproducir_en_spotify` implementation to
   `_spotify_play_api` and remove its `@tool` decorator.
2. Return a typed `SpotifyAPIPlaybackResult` internally for success and permanent
   capability failures; keep provider text conversion in a wrapper.
3. Classify `premium`, `developer access`, invalid grant, and forbidden app
   states as `capability_failure=True`.

Add the type near the configuration block:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class SpotifyAPIPlaybackResult:
    ok: bool
    message: str
    capability_failure: bool = False
```

Wrap all existing return messages from `_spotify_play_api` in this type without
changing their text. Do not call `get_access_token` from the cache probe.

- [ ] **Step 6: Implement a non-interactive cached-token probe**

Add:

```python
def _spotify_has_valid_cached_token() -> bool:
    if sp is None:
        return False
    manager = getattr(sp, "auth_manager", None)
    handler = getattr(manager, "cache_handler", None)
    if manager is None or handler is None:
        return False
    try:
        token = handler.get_cached_token()
        return bool(token and manager.validate_token(token))
    except Exception as error:
        _spotify_log_error("cached_token_probe", error)
        return False
```

This function must not call `manager.get_access_token`.

- [ ] **Step 7: Add desktop request formatting and public coordinator**

Add lazy state and request conversion:

```python
from tools.spotify_desktop import (
    DesktopResultStatus,
    SpotifyDesktopResult,
    SpotifyRequest,
    build_windows_controller,
)

_desktop_controller = None
_spotify_api_capability_failed = False
SPOTIFY_PLAYBACK_MODE = jarvis_config.SPOTIFY_PLAYBACK_MODE


def _get_desktop_controller():
    global _desktop_controller
    if _desktop_controller is None:
        _desktop_controller = build_windows_controller(
            start_timeout=jarvis_config.SPOTIFY_DESKTOP_START_TIMEOUT,
            action_timeout=jarvis_config.SPOTIFY_DESKTOP_ACTION_TIMEOUT,
        )
    return _desktop_controller


def _spotify_desktop_request(song: str) -> SpotifyRequest:
    query, title, artist = _parsear_query_spotify(song)
    return SpotifyRequest(
        raw=song,
        query=query,
        title=title or query,
        artist=artist,
    )


def _spotify_desktop_result(song: str) -> SpotifyDesktopResult:
    return _get_desktop_controller().play(_spotify_desktop_request(song))


def _spotify_play_desktop(song: str) -> str:
    result = _spotify_desktop_result(song)
    if result.status is DesktopResultStatus.SUCCESS:
        return _spotify_text(
            f"Playing {_spotify_track_label(result.title, result.artist)} through Spotify Desktop.",
            f"Reproduciendo {_spotify_track_label(result.title, result.artist)} mediante Spotify Desktop.",
        )
    if result.status is DesktopResultStatus.AMBIGUOUS:
        choices = "; ".join(
            _spotify_track_plain_label(item.title, item.artist)
            for item in result.choices
        )
        return _spotify_text(
            f"I found several close matches: {choices}. Which one should I play?",
            f"Encontré varias coincidencias: {choices}. ¿Cuál debo reproducir?",
        )
    messages = {
        "spotify_no_results": _spotify_text(
            "I could not find that item in Spotify Desktop.",
            "No encontré ese contenido en Spotify Desktop.",
        ),
        "spotify_focus_lost": _spotify_text(
            "Spotify lost focus before I could type the search.",
            "Spotify perdió el foco antes de que pudiera escribir la búsqueda.",
        ),
        "spotify_start_timeout": _spotify_text(
            "Spotify Desktop did not become ready in time.",
            "Spotify Desktop no estuvo listo a tiempo.",
        ),
        "spotify_playback_not_verified": _spotify_text(
            "Spotify received the command, but I could not verify the requested track.",
            "Spotify recibió el comando, pero no pude verificar la canción solicitada.",
        ),
    }
    return messages.get(
        result.message_key,
        _spotify_text(
            "Spotify Desktop automation is unavailable.",
            "La automatización de Spotify Desktop no está disponible.",
        ),
    )


@tool
def reproducir_en_spotify(cancion: str) -> str:
    """Play music using a cached Spotify API session or Spotify Desktop on Windows."""
    global _spotify_api_capability_failed
    song = str(cancion or "").strip()
    if not song:
        return _spotify_text("Tell me what to play.", "Dime qué deseas reproducir.")
    if SPOTIFY_PLAYBACK_MODE == "desktop":
        return _spotify_play_desktop(song)
    if SPOTIFY_PLAYBACK_MODE == "auto" and (
        _spotify_api_capability_failed or not _spotify_has_valid_cached_token()
    ):
        return _spotify_play_desktop(song)

    api_result = _spotify_play_api(song)
    if api_result.ok or SPOTIFY_PLAYBACK_MODE == "api":
        return api_result.message
    if api_result.capability_failure:
        _spotify_api_capability_failed = True
        return _spotify_play_desktop(song)
    return api_result.message
```

Apply the same backend decision to `controlar_reproduccion`, mapping its action
to `controller.control(action)`. In desktop mode, `reproducir_mix_spotify`
starts the requested seed and explicitly reports that subsequent radio/mix
selection is controlled by the Spotify client.

- [ ] **Step 8: Run Spotify routing and coordinator tests**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_spotify_recs.py tests\test_compound_router.py tests\test_router.py -q
```

Expected: all Spotify and router tests pass; no OAuth browser opens.

- [ ] **Step 9: Commit backend coordination**

```powershell
git add -- src/backend/tools/spotify.py src/backend/tools/spotify_desktop tests/test_spotify_recs.py
git commit -m "feat: fall back to Spotify desktop playback"
```

## Task 8: Setup status, documentation, and user-facing diagnostics

**Files:**
- Modify: `src/backend/core/setup_wizard.py:28-39`
- Modify: `tests/test_setup_wizard.py`
- Modify: `README.md:188-205,330-378`
- Modify: `AGENTS.md:196-215`

- [ ] **Step 1: Add failing setup status tests**

Add to `tests/test_setup_wizard.py`:

```python
def test_spotify_desktop_mode_does_not_require_api_credentials():
    status = build_setup_status(
        env={"SPOTIFY_PLAYBACK_MODE": "desktop"},
        language="es",
        admin_voice_profiles=1,
        weather_location="Matamoros",
    )

    assert status["items"]["spotify"]["configured"] is True
    assert status["items"]["spotify"]["mode"] == "desktop"


def test_spotify_api_mode_requires_client_credentials():
    status = build_setup_status(
        env={"SPOTIFY_PLAYBACK_MODE": "api"},
        language="es",
        admin_voice_profiles=1,
        weather_location="Matamoros",
    )

    assert status["items"]["spotify"]["configured"] is False
    assert status["items"]["spotify"]["mode"] == "api"
```

- [ ] **Step 2: Run setup tests and confirm desktop mode is not represented**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_setup_wizard.py -q
```

Expected: failures for missing mode and desktop readiness.

- [ ] **Step 3: Update setup readiness**

In `build_setup_status`, calculate:

```python
spotify_mode = str(source.get("SPOTIFY_PLAYBACK_MODE") or "auto").strip().lower()
if spotify_mode not in {"auto", "api", "desktop"}:
    spotify_mode = "auto"
spotify_api_configured = _env_has(source, "SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET") or _env_has(
    source, "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"
)
```

After constructing `items`, assign the Spotify item with:

```python
items["spotify"] = {
    "configured": spotify_mode == "desktop" or spotify_api_configured,
    "optional": True,
    "label": "Spotify",
    "mode": spotify_mode,
}
```

- [ ] **Step 4: Document installation and limitations**

Update `README.md` with:

```markdown
### Spotify playback modes

- `SPOTIFY_PLAYBACK_MODE=auto` uses an already-authorized Web API token and
  otherwise controls the Windows Spotify Desktop client.
- `SPOTIFY_PLAYBACK_MODE=desktop` does not require Spotify developer keys. It
  briefly focuses Spotify, searches with UI Automation, selects a verified
  match, and confirms the now-playing title before reporting success.
- `SPOTIFY_PLAYBACK_MODE=api` keeps the Spotipy/OAuth integration and requires
  an eligible Spotify developer application.

Desktop automation does not bypass Spotify Free restrictions, advertisements,
regional availability, or DRM. Keep Spotify signed in. During a search, JARVIS
may hold foreground focus for up to five seconds. Visual recovery is optional,
captures only the Spotify window, and deletes the image after the attempt.
```

Update `AGENTS.md` with targeted tests:

```powershell
pytest tests\test_spotify_desktop_matching.py tests\test_spotify_desktop_windows.py tests\test_spotify_desktop_controller.py tests\test_spotify_recs.py -q
```

- [ ] **Step 5: Run setup and installation contract tests**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_setup_wizard.py tests\test_installation_contract.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit setup and documentation**

```powershell
git add -- README.md AGENTS.md src/backend/core/setup_wizard.py tests/test_setup_wizard.py
git commit -m "docs: explain Spotify desktop fallback"
```

## Task 9: Full verification and live Windows acceptance

**Files:**
- Modify only files proven necessary by failures from this task.

- [ ] **Step 1: Run focused Spotify regressions**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_spotify_desktop_matching.py tests\test_spotify_desktop_windows.py tests\test_spotify_desktop_controller.py tests\test_spotify_recs.py tests\test_compound_router.py -q
```

Expected: all tests pass without opening OAuth or controlling the desktop.

- [ ] **Step 2: Run static verification**

Run each command separately:

```powershell
.\venv\Scripts\python.exe -m compileall -q start_app.py src\backend
.\venv\Scripts\python.exe -m ruff check src\backend\tools\spotify.py src\backend\tools\spotify_desktop tests\test_spotify_desktop_matching.py tests\test_spotify_desktop_windows.py tests\test_spotify_desktop_controller.py tests\test_spotify_recs.py --select F
.\venv\Scripts\python.exe -m pip check
git diff --check
```

Expected: all commands exit `0`; line-ending warnings alone are informational.

- [ ] **Step 3: Run the complete regression suite**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest -q
```

Expected: the existing skipped biometric test remains skipped and every other test passes.

- [ ] **Step 4: Start JARVIS in desktop playback mode**

Set `SPOTIFY_PLAYBACK_MODE=desktop` in the ignored local `.env`, then start:

```powershell
.\venv\Scripts\python.exe start_app.py
```

Expected: `http://127.0.0.1:5002` responds and startup does not open Spotify OAuth.

- [ ] **Step 5: Inspect the live Spotify accessibility tree before acting**

Run a read-only diagnostic that records control types and sanitized accessible
names for the Spotify window. Do not log unrelated windows. Confirm the current
Spotify version exposes either a search `Edit` control or the documented
`Ctrl+K` search overlay, result rows, and now-playing metadata.

Expected: the diagnostic identifies stable semantic controls. If names differ
from the test fakes, update only the semantic parser and add the observed shape
as a regression fixture before continuing.

- [ ] **Step 6: Perform approved live playback checks**

From JARVIS, issue these commands one at a time:

```text
Reproduce No te apartes de mí de Vicentico en Spotify.
Pausa Spotify.
Reanuda Spotify.
Siguiente canción.
```

Expected:

- Spotify opens or focuses without OAuth.
- The requested track is selected when Spotify Free permits it.
- JARVIS reports success only when title/artist metadata confirms the result.
- Pause/resume/next each produce an observed state change.
- No screenshot remains under ignored scratch storage.

- [ ] **Step 7: Test failure recovery**

Repeat with Spotify minimized, closed, and with a deliberately ambiguous title.
Move focus away from Spotify during one search.

Expected:

- Closed/minimized Spotify is recovered conditionally.
- Ambiguous results produce title/artist choices.
- Focus loss aborts without typing into the other application.
- A second command works after every failure, proving lock cleanup.

- [ ] **Step 8: Review repository hygiene**

Run:

```powershell
git status --short
git lfs ls-files
git check-ignore -v .env scratch
```

Expected: only intentional source/docs changes remain, ONNX files remain in Git
LFS, and `.env` plus scratch output remain ignored.

- [ ] **Step 9: Commit any verification-driven corrections**

When Step 5-7 required a tested correction:

```powershell
git add -- src/backend/tools/spotify.py src/backend/tools/spotify_desktop tests README.md AGENTS.md .env.example requirements.txt
git commit -m "fix: harden Spotify desktop automation"
```

If no corrections were necessary, do not create an empty commit.
