# Spotify Module Architecture

## Goal

Make Spotify a self-contained first-party capability under `modules/spotify`
without changing its user-facing behavior or requiring callers to understand
whether playback uses the Web API or Windows Desktop automation.

## Boundaries

- `modules/spotify/tools.py` exposes the three LangChain tools.
- `modules/spotify/service.py` selects API or Desktop playback and owns fallback
  state.
- `modules/spotify/api/` owns OAuth, Spotipy calls, recommendations, queues,
  devices, and API playback controls.
- `modules/spotify/desktop/` owns Windows discovery, UI Automation, matching,
  visual recovery, and playback verification.
- `modules/spotify/followup.py` owns per-profile ambiguous-result selections.
- `modules/spotify/messages.py` owns localized response formatting.
- `tools/spotify.py` remains a small public compatibility facade. It contains no
  implementation.

`core/`, HTTP routes, and other modules may import Spotify's public tools or
follow-up service, but must not depend on Spotipy or pywinauto implementation
details.

## Data Flow

1. A tool receives a normalized music or playback-control request.
2. `service.py` chooses cached Web API playback or Windows Desktop automation.
3. The selected provider returns a structured result.
4. The service formats the final localized response.
5. Ambiguous Desktop results are retained by profile and resolved before the
   dynamic web router on the next turn.

## Compatibility

- Existing public imports from `tools.spotify` continue to work.
- Internal imports move to `modules.spotify`.
- Environment variable names, OAuth cache location, LangChain tool names, and
  user-facing behavior remain unchanged.
- The migration must pass the existing Spotify tests before old implementation
  files are removed.

## Verification

- Spotify unit and controller tests.
- Full pytest suite.
- Python compilation and Ruff undefined-name checks.
- JavaScript syntax checks remain part of the release gate even though this
  refactor is backend-only.
- A Windows Desktop smoke test must search, disambiguate, activate, and verify a
  real track without fixed coordinates.
