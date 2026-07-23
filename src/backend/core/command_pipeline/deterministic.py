"""Side-effect-free deterministic command planning."""

from __future__ import annotations

from core.brain import router
from core.command_pipeline.models import ActionPlan, CommandRequest


class DeterministicPlanner:
    """Translate high-confidence local intents into immutable action plans."""

    def plan(self, request: CommandRequest) -> ActionPlan | None:
        return router.plan_hybrid(request)
