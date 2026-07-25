from modules.spotify.desktop.controller import SpotifyDesktopController
from modules.spotify.desktop.models import (
    AutomationState,
    DesktopResultStatus,
    MatchDecision,
    MatchStatus,
    SpotifyCandidate,
    SpotifyDesktopResult,
    SpotifyRequest,
)


def build_windows_controller(*, start_timeout: float, action_timeout: float):
    import os

    from core import jarvis_config
    from core.service_container import services

    from modules.spotify.desktop.visual import build_default_visual_recovery
    from modules.spotify.desktop.windows import (
        SpotifyUIAutomationAdapter,
        WindowsSpotifyWindowAdapter,
    )

    vision_model = services.llm_vision if jarvis_config.VISION_ENABLED else None
    visual_recovery = build_default_visual_recovery(
        model=vision_model,
        scratch_dir=os.path.join(
            jarvis_config.ROOT_DIR,
            "scratch",
            "spotify_visual",
        ),
    )
    return SpotifyDesktopController(
        WindowsSpotifyWindowAdapter(),
        SpotifyUIAutomationAdapter(),
        visual_recovery=visual_recovery,
        start_timeout=start_timeout,
        action_timeout=action_timeout,
    )


__all__ = [
    "AutomationState",
    "DesktopResultStatus",
    "MatchDecision",
    "MatchStatus",
    "SpotifyCandidate",
    "SpotifyDesktopController",
    "SpotifyDesktopResult",
    "SpotifyRequest",
    "build_windows_controller",
]
