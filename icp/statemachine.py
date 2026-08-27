"""Explicit pipeline stage machine.

The task requires that the final output is never produced before every decision
stage has run in order. Rather than trusting call order, the orchestrator drives
this machine and every stage asserts its precondition through :meth:`enter`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .util import now_iso

STAGES: tuple[str, ...] = (
    "INIT",
    "INPUTS_LOADED",
    "PRIOR_FEEDBACK_LOADED",
    "INCIDENT_GROUPING_COMPLETE",
    "SEVERITY_ASSESSMENT_COMPLETE",
    "ACTION_PROPOSALS_COMPLETE",
    "SAFETY_GUARDRAILS_COMPLETE",
    "STAKEHOLDER_DRAFTING_COMPLETE",
    "OPERATOR_REVIEW_COLLECTED",
    "FEEDBACK_EXAMPLES_BUILT",
    "REDECISION_COMPLETE",
    "BEFORE_AFTER_COMPARISON_COMPLETE",
    "ANALYTICS_GENERATED",
    "VALIDATION_COMPLETE",
    "RESULTS_FINALISED",
)

_INDEX = {name: i for i, name in enumerate(STAGES)}


class StageError(RuntimeError):
    pass


@dataclass
class StateMachine:
    current: str = "INIT"
    history: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.history:
            self.history.append({"stage": "INIT", "at": now_iso()})

    def enter(self, stage: str, *, require_prev: str | None = None) -> None:
        """Advance to ``stage``.

        ``stage`` must come strictly after the current stage in :data:`STAGES`
        (the single optional ``PRIOR_FEEDBACK_LOADED`` step may be skipped).
        """
        if stage not in _INDEX:
            raise StageError(f"unknown stage {stage!r}")
        if require_prev is not None and self.current != require_prev:
            raise StageError(
                f"cannot enter {stage}: expected to be at {require_prev}, at {self.current}"
            )
        if _INDEX[stage] <= _INDEX[self.current]:
            raise StageError(
                f"cannot enter {stage}: already at {self.current} (would move backwards)"
            )
        self.current = stage
        self.history.append({"stage": stage, "at": now_iso()})

    def reached(self, stage: str) -> bool:
        return _INDEX[self.current] >= _INDEX[stage]

    def assert_reached(self, stage: str) -> None:
        if not self.reached(stage):
            raise StageError(f"required stage {stage} not reached (at {self.current})")

    def as_dict(self) -> dict:
        return {"current_stage": self.current, "transitions": list(self.history)}
