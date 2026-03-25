# This module is retained for backwards compatibility only.
# Planning and task review are now handled by supervisor.agent.SupervisorAgent.
#
# The old CodexClient mixed implementation-specific knowledge (Unity file paths,
# game-specific summaries, hardcoded fallback plans) into the planner, which
# violated the rule that the supervisor agent must never make coding decisions.
# That logic has been removed. The supervisor now receives a live repo tree
# and produces plans from first principles.

from supervisor.agent import SupervisorAgent as CodexClient, SupervisorError as PlannerError

__all__ = ["CodexClient", "PlannerError"]
