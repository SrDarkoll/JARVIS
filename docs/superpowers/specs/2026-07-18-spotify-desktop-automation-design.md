# Spotify Desktop Automation for Windows

## Context

Spotify blocks Web API access for Development Mode applications whose owner
does not have an active Premium subscription. JARVIS must therefore be able to
play music through the installed Windows desktop client without bypassing
Spotify account restrictions or depending on undocumented web endpoints.

JARVIS already opens `spotify:` URIs, enumerates and focuses Windows desktop
windows, parses music requests, and optionally analyzes screenshots. It does
not currently inspect Spotify's accessibility tree, select search results, or
verify that playback actually changed.

## Goals

- Open Spotify Desktop when it is not running.
- Detect, restore, and focus its main window using process identity and window
  handles rather than title text alone.
- Search for a requested title, artist, album, playlist, or free-form seed.
- Select the best visible result using deterministic matching.
- Start playback and verify the observed title and artist.
- Control play, pause, next, previous, shuffle, and repeat without Web API
  access when Spotify Desktop permits the action.
- Preserve the existing Web API integration for users who have a working token.
- Return controlled, localized errors and never leave keyboard modifiers held.
- Keep screenshots local, cropped to Spotify, short-lived, and optional.

## Non-goals

- Bypassing Spotify Premium, ads, regional restrictions, or account controls.
- Circumventing DRM or downloading Spotify audio.
- Guaranteeing unrestricted on-demand playback when Spotify Free itself
  disallows the requested action.
- Automating macOS, Linux, mobile devices, TVs, or remote Spotify sessions in
  this iteration.
- Depending on fixed screen coordinates, window size, display scaling, theme,
  or UI language as the primary strategy.

## User-visible behavior

For a request such as `reproduce No te apartes de mi de Vicentico en Spotify`,
JARVIS will:

1. Parse the title and optional artist using the existing music parser.
2. Select a playback backend according to `SPOTIFY_PLAYBACK_MODE`.
3. Open Spotify Desktop if needed and wait for its main window.
4. Bring Spotify to the foreground for the duration of the operation.
5. Open Search, enter the normalized query, and inspect candidate results.
6. Play a high-confidence candidate automatically.
7. Verify the resulting track from accessible now-playing metadata and the
   Spotify window title.
8. Report success only after verification.

When two candidates remain plausible and no artist was supplied, JARVIS returns
a short disambiguation question with up to three title/artist choices. It does
not silently claim that the first result is correct.

## Playback mode selection

Add `SPOTIFY_PLAYBACK_MODE` with these values:

- `auto` (default): use the Web API only when a cached token is already valid;
  otherwise use desktop automation. A voice command must never block waiting
  for an interactive OAuth callback.
- `api`: retain the existing Spotipy behavior, including explicit OAuth setup.
- `desktop`: never initialize interactive Spotify OAuth during playback.

In `auto`, a Web API authorization, Premium, or developer-access failure marks
the API backend unavailable for the process and immediately delegates to the
desktop backend. Transient rate limits and network errors remain distinguishable
from permanent capability failures.

## Architecture

### `SpotifyPlaybackCoordinator`

Owns backend selection and exposes the current tool-level contract. It receives
typed results from the API and desktop backends, chooses fallbacks, localizes the
final message, and prevents two Spotify commands from running concurrently.

### `SpotifyDesktopController`

Implements a bounded state machine:

`IDLE -> DISCOVERING -> STARTING -> FOCUSING -> SEARCHING -> SELECTING -> PLAYING -> VERIFYING -> COMPLETE`

Any state may transition to `FAILED`; a newer command may transition the active
operation to `CANCELLED`. Each state has a condition-based timeout. The
controller accepts injected process, window, UI Automation, clock, keyboard,
and capture adapters so unit tests never control the real desktop.

### `WindowsSpotifyWindowAdapter`

- Finds `Spotify.exe` processes with `psutil`.
- Enumerates visible top-level windows with Win32 APIs.
- Associates windows with Spotify process IDs.
- Restores and foregrounds the selected window.
- Starts the installed client with `spotify:` and falls back to the detected
  `Spotify.exe` path when protocol activation fails.
- Polls for readiness instead of sleeping for a fixed number of seconds.

### `SpotifyUIAutomationAdapter`

Uses the Windows UI Automation backend provided by `pywinauto`.

Primary interaction order:

1. Find an accessible search/edit control by control type, automation metadata,
   and localized names such as `Search` or `Buscar`.
2. Set its text through UI Automation.
3. Inspect accessible result containers and descendants.
4. Invoke or select the chosen result through its supported UIA pattern.

Fallback interaction order:

1. Focus the verified Spotify window.
2. Send Spotify's documented `Ctrl+K` shortcut.
3. Replace the search text and submit it.
4. Navigate only among elements observed in the current UI state.

The adapter must not click a guessed coordinate. Mouse input is allowed only
when a target rectangle came from the current UI Automation tree or from an
approved visual recovery result.

### `SpotifyCandidateMatcher`

Candidate selection is deterministic and independent of the LLM:

- Normalize case, whitespace, punctuation, and diacritics.
- Score title token overlap and sequence similarity.
- Give artist agreement a strong positive weight when the user supplied one.
- Penalize karaoke, tribute, cover, remix, live, sped-up, and instrumental
  variants unless those terms appear in the request.
- Prefer a track result over albums, artists, podcasts, and playlists for an
  ordinary song request.
- Require a high confidence threshold and a sufficient margin over the second
  candidate before automatic playback.

Medium-confidence results produce a disambiguation response. Low-confidence or
empty results produce a controlled not-found response.

### `SpotifyPlaybackVerifier`

Verification uses independent evidence in this order:

1. Accessible now-playing title and artist.
2. Spotify's top-level window title, which currently exposes `artist - track`.
3. Accessible play/pause state as supporting evidence.

Success requires title agreement and, when requested, artist agreement. A
changed play/pause icon alone is insufficient. The verifier polls for a bounded
period and permits one retry with the next candidate.

### `SpotifyVisualRecovery`

Visual recovery is optional and runs only after UI Automation and the keyboard
fallback fail.

- Capture only the Spotify window rectangle, never the full desktop.
- Store the image in repo-local ignored scratch storage.
- Redact or omit logs containing visible UI text not required for diagnosis.
- Ask the configured vision model for structured candidate rectangles and
  labels, not a free-form action plan.
- Validate every rectangle is inside the current Spotify window before clicking.
- Delete the capture after the operation unless explicit debug retention is on.
- Disable this path cleanly when vision or image dependencies are unavailable.

The vision model may propose a target but cannot execute arbitrary desktop
actions or override the state machine.

## Subsequent playback controls

For pause, resume, next, previous, shuffle, and repeat:

1. Prefer an accessible Spotify control with verified name and state.
2. Use documented Spotify shortcuts while its verified window is focused.
3. Use Windows media keys only for transport actions when Spotify is the active
   media session.
4. Verify the expected state change before reporting success.

Volume remains an operating-system mixer action unless a Spotify-specific
volume request is explicit.

## Concurrency and user interruption

- A process-local lock serializes Spotify desktop operations.
- A new explicit Spotify request cancels the previous pending search.
- Timeouts release the lock and all pressed modifiers in `finally` blocks.
- JARVIS does not type until the foreground window handle is revalidated.
- If focus changes away from Spotify before text entry, the operation aborts
  instead of typing into another application.
- The visible control period should normally last less than five seconds after
  Spotify is ready.

## Security and privacy

- Desktop automation remains subject to the existing admin authorization
  policy for local application control.
- Search text is treated as untrusted text, not as a command or key sequence.
- Keyboard injection sends text through a literal-text path and never evaluates
  user-provided shortcut syntax.
- Logs include state names, durations, confidence scores, and exception classes;
  they exclude OAuth tokens, client secrets, raw screenshots, and clipboard
  contents.
- The implementation must not inspect unrelated windows or retain full desktop
  captures.

## Dependencies and installation

- Add `pywinauto` as the single Windows desktop-automation dependency.
- Reuse existing `psutil` and Win32 `ctypes` support.
- Keep Pillow and vision support optional; the deterministic UIA/keyboard path
  must work without them.
- On non-Windows systems, imports remain lazy and the desktop backend returns a
  controlled unsupported-platform result.

Setup and README documentation will explain the foreground-control behavior,
Spotify Free limitations, and the `auto`, `api`, and `desktop` modes.

## Error handling

The desktop backend returns typed internal outcomes such as:

- `spotify_not_installed`
- `spotify_start_timeout`
- `spotify_window_not_found`
- `spotify_focus_lost`
- `spotify_search_unavailable`
- `spotify_no_results`
- `spotify_ambiguous_results`
- `spotify_playback_not_verified`
- `spotify_action_restricted`
- `spotify_automation_unavailable`

User-facing messages are localized and contain a concrete next action. Raw UIA,
Win32, provider, path, and token details remain in sanitized diagnostic logs.

## Testing strategy

### Unit tests

- State transitions, timeout behavior, cancellation, and lock release.
- Process/window matching with multiple Spotify helper processes.
- Query normalization and candidate scoring in English and Spanish.
- Variant penalties and artist-aware disambiguation.
- API mode selection with valid, absent, expired, and blocked tokens.
- Literal keyboard input and foreground-window revalidation.
- Playback verification from UIA metadata and window titles.
- Screenshot bounds validation and guaranteed cleanup.
- Controlled behavior when optional dependencies are missing.

### Integration tests

- Fake UIA tree for English and Spanish Spotify layouts.
- Spotify initially closed, minimized, maximized, and already focused.
- Empty results, delayed results, duplicate versions, ads, and unavailable
  playback controls.
- Concurrent commands and a user focus change during automation.
- API failure followed by successful desktop fallback.

### Manual Windows acceptance matrix

- Display scaling at 100%, 125%, and 150%.
- Light and dark Spotify themes.
- Spotify in English and Spanish.
- Exact title and artist, title only, artist only, accents, and misspellings.
- Free account behavior with ads or restricted actions.
- Spotify closed, backgrounded, minimized, and on a secondary monitor.
- JARVIS core mode with vision disabled.

## Acceptance criteria

- With Spotify closed, JARVIS opens it and starts a high-confidence requested
  track without Web API access when Spotify Free permits playback.
- With Spotify already running, the verified track starts without relaunching
  the client.
- JARVIS never reports playback success unless observed metadata matches the
  selected candidate.
- Ambiguous requests produce useful choices instead of an arbitrary selection.
- No path relies on static screen coordinates.
- The deterministic desktop path works without Groq Vision.
- Failures finish within bounded time and leave keyboard, focus, locks, and
  temporary files in a recoverable state.
- Existing Spotify API tests and all project regressions continue to pass.

## References

- Spotify keyboard shortcuts: https://support.spotify.com/article/keyboard-shortcuts/
- Spotify February 2026 Development Mode changes:
  https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide
- Spotify redirect URI requirements:
  https://developer.spotify.com/documentation/web-api/concepts/redirect_uri
