"""Side-effect-free deterministic command planning."""

from __future__ import annotations

from utils.jarvis_i18n import language_scope

from core.brain import router, social_engine
from core.command_pipeline.models import ActionPlan, CommandRequest, PlanSource


class DeterministicPlanner:
    """Translate high-confidence local intents into immutable action plans."""

    def plan(self, request: CommandRequest) -> ActionPlan | None:
        planned = router.plan_hybrid(request)
        if planned is not None:
            return planned
        with language_scope(request.language):
            reply = social_engine._respuesta_rapida_social(request.text, request.profile_id, strict=True)
        if reply is not None:
            return ActionPlan(request_id=request.request_id, source=PlanSource.DETERMINISTIC,
                              direct_response=reply, conversation_only=True)
        return None
