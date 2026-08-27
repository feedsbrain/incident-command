"""Stage 7b — feedback-driven re-decision.

Re-runs severity assessment (one call) and action proposal (one call per
incident) with the feedback block injected, then re-applies the deterministic
guardrails to the new proposals. Grouping is intentionally NOT re-run here (it
would only be re-run if the operator disputed the clustering); that choice is
recorded in the combined output.
"""

from __future__ import annotations

from . import actions as actions_stage
from . import guardrails as guardrails_stage
from . import severity as severity_stage

STAGE = "REDECISION_COMPLETE"


def run(
    client,
    incidents,
    runbooks,
    services,
    feedback,
    *,
    outdir,
    input_artifacts,
):
    new_severities = severity_stage.run(
        client,
        incidents,
        services,
        outdir=outdir,
        input_artifacts=input_artifacts + ["feedback_examples.json"],
        feedback_examples=feedback["examples"],
        operator_status=feedback["operator_status"],
        feedback_block=feedback["feedback_block"],
        log_stage="severity_redecision",
        out="severity_redecision.json",
    )
    new_proposals = actions_stage.run(
        client,
        incidents,
        new_severities,
        runbooks,
        services,
        outdir=outdir,
        input_artifacts=["severity_redecision.json"] + input_artifacts + ["feedback_examples.json"],
        feedback_examples=feedback["examples"],
        operator_status=feedback["operator_status"],
        feedback_block=feedback["feedback_block"],
        log_stage="action_redecision",
        out="actions_redecision.json",
    )
    new_safety = guardrails_stage.run(
        client, new_proposals, incidents, runbooks, services, outdir=outdir,
        out="safety_review_redecision.json",
    )
    return new_severities, new_proposals, new_safety
