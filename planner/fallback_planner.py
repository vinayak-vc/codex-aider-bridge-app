# FallbackPlanner has been intentionally removed.
#
# The original fallback planner generated hardcoded implementation plans
# (full Unity game task lists, generic README stubs) when the supervisor
# failed to respond. This made the bridge itself act as a developer —
# a direct violation of the architecture rule that the bridge never makes
# coding or implementation decisions.
#
# The correct behaviour when the supervisor fails to plan is to raise an
# error and let the operator supply a --plan-file manually. This keeps
# all implementation decisions with the supervisor agent.


class FallbackPlanner:
    def build_plan(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise NotImplementedError(
            "FallbackPlanner has been removed. "
            "If the supervisor cannot produce a valid plan after all retries, "
            "use --plan-file to supply a plan manually."
        )
