"""Pipeline orchestrator.

Drives the stage machine in :mod:`icp.statemachine`. Each decision stage is a
separate module with its own artifact; this file only wires inputs to outputs and
enforces ordering. The final combined artifact is written *last*, after
VALIDATION_COMPLETE, so it can never predate a stage.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .llm import CallLogger, build_client
from .statemachine import StateMachine
from .util import now_iso, read_json, read_jsonl, write_json
from .stages import (
    actions as actions_stage,
    analytics as analytics_stage,
    comparison as comparison_stage,
    drafting as drafting_stage,
    escalation as escalation_stage,
    feedback as feedback_stage,
    grouping as grouping_stage,
    guardrails as guardrails_stage,
    operator as operator_stage,
    severity as severity_stage,
)
from .stages.redecision import run as redecision_run
from .stages.guardrails import top_action

FEEDBACK_FILE = "operator_feedback.jsonl"
LLM_LOG = "llm_calls.jsonl"

REQUIRED_ARTIFACTS = [
    "alerts.json", "services.json", "runbooks.json",
    "incident_groups.json", "severity_assessments.json", "action_proposals.json",
    "safety_review.json", "stakeholder_updates.json", "operator_feedback.jsonl",
    "severity_redecision.json", "actions_redecision.json", "incident_command_output.json",
    "analytics_summary.json", "feedback_store_status.json", "escalation_bundle.json",
    "llm_calls.jsonl",
]


def run_pipeline(
    *,
    indir=".",
    outdir=".",
    provider="auto",
    model=None,
    operator_mode="interactive",
    operator_script=None,
    fresh=False,
):
    indir, outdir = Path(indir), Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    state = StateMachine()

    # --- inputs ---------------------------------------------------------------
    alerts = read_json(indir / "alerts.json")
    services = read_json(indir / "services.json")
    runbooks = read_json(indir / "runbooks.json")
    if indir.resolve() != outdir.resolve():
        for f in ("alerts.json", "services.json", "runbooks.json"):
            shutil.copy(indir / f, outdir / f)
    state.enter("INPUTS_LOADED", require_prev="INIT")

    # --- fresh generated artifacts (never wipe the feedback store unless asked)
    feedback_path = outdir / FEEDBACK_FILE
    if fresh and feedback_path.exists():
        feedback_path.unlink()
    for name in [LLM_LOG, "incident_command_output.json", "pipeline_state.json"]:
        p = outdir / name
        if p.exists():
            p.unlink()
    audit_dir = outdir / "audit" / "prompts"
    if audit_dir.exists():
        shutil.rmtree(audit_dir)

    logger = CallLogger(log_path=outdir / LLM_LOG, audit_dir=audit_dir)
    client = build_client(provider, logger, model=model)
    print(f"[pipeline] provider={client.provider} model={client.model}")

    # --- prior feedback (optional stage) -----------------------------------
    prior_existed = feedback_path.exists()
    prior_records = read_jsonl(feedback_path) if prior_existed else []
    if prior_records:
        state.enter("PRIOR_FEEDBACK_LOADED", require_prev="INPUTS_LOADED")
        print(f"[pipeline] loaded {len(prior_records)} prior operator feedback record(s)")

    # --- stage 1: grouping -------------------------------------------------
    incidents = grouping_stage.run(
        client, alerts, services, outdir=str(outdir),
        input_artifacts=["alerts.json", "services.json"],
    )
    state.enter("INCIDENT_GROUPING_COMPLETE")

    # --- stage 2: severity (baseline) -----------------------------------
    orig_sev = severity_stage.run(
        client, incidents, services, outdir=str(outdir),
        input_artifacts=["incident_groups.json", "services.json"],
    )
    state.enter("SEVERITY_ASSESSMENT_COMPLETE")

    # --- stage 3: action proposals (baseline) --------------------------
    orig_proposals = actions_stage.run(
        client, incidents, orig_sev, runbooks, services, outdir=str(outdir),
        input_artifacts=["incident_groups.json", "severity_assessments.json", "runbooks.json"],
    )
    state.enter("ACTION_PROPOSALS_COMPLETE")

    # --- stage 4: guardrails (deterministic) ---------------------------
    orig_safety = guardrails_stage.run(
        client, orig_proposals, incidents, runbooks, services, outdir=str(outdir),
    )
    state.enter("SAFETY_GUARDRAILS_COMPLETE")

    # --- stage 5: stakeholder drafting -------------------------------
    updates = drafting_stage.run(
        client, incidents, orig_sev, orig_safety, services, outdir=str(outdir),
        input_artifacts=["incident_groups.json", "severity_assessments.json", "safety_review.json"],
    )
    state.enter("STAKEHOLDER_DRAFTING_COMPLETE")

    # --- stage 6: operator review ----------------------------------
    current_records = operator_stage.run(
        incidents, orig_sev, orig_safety, outdir=str(outdir),
        feedback_path=str(feedback_path), mode=operator_mode, script_path=operator_script,
    )
    state.enter("OPERATOR_REVIEW_COLLECTED")

    # --- stage 7a: feedback examples -----------------------------
    feedback = feedback_stage.build(
        prior_records, current_records, incidents,
        outdir=str(outdir), feedback_path=str(feedback_path), prior_existed=prior_existed,
    )
    state.enter("FEEDBACK_EXAMPLES_BUILT")

    # --- stage 7b: re-decision -----------------------------------
    new_sev, new_proposals, new_safety = redecision_run(
        client, incidents, runbooks, services, feedback,
        outdir=str(outdir),
        input_artifacts=["incident_groups.json", "services.json", "runbooks.json"],
    )
    state.enter("REDECISION_COMPLETE")

    # --- before/after + agreement delta -------------------------
    comparison_rows, agreement = comparison_stage.build(
        incidents, orig_sev, orig_safety, new_sev, new_safety, current_records, outdir=str(outdir),
    )
    state.enter("BEFORE_AFTER_COMPARISON_COMPLETE")

    # --- stage 8: analytics ------------------------------------
    analytics = analytics_stage.build(
        incidents, len(alerts), orig_sev, new_sev, orig_safety, new_safety,
        current_records, agreement, outdir=str(outdir),
    )
    state.enter("ANALYTICS_GENERATED")

    # --- stage 10: escalation bundle --------------------------
    escalation = escalation_stage.build(incidents, new_sev, services, outdir=str(outdir))

    # --- internal invariant check ----------------------------
    _internal_validate(outdir, alerts, incidents, orig_sev, new_sev, orig_safety, logger, feedback)
    state.enter("VALIDATION_COMPLETE")
    state.enter("RESULTS_FINALISED")

    # --- combined output (written last, reflecting the finalised state) ------
    combined = _combine(
        client, state, alerts, services, runbooks, incidents,
        orig_sev, new_sev, orig_proposals, new_proposals, orig_safety, new_safety,
        updates, current_records, comparison_rows, agreement, analytics, escalation, feedback,
    )
    write_json(outdir / "incident_command_output.json", combined)
    write_json(outdir / "pipeline_state.json", state.as_dict())

    print(f"\n[pipeline] RESULTS_FINALISED — {len(logger.records)} LLM calls logged to {LLM_LOG}")
    return combined


# ---------------------------------------------------------------------------
def _flatten_safety(safety_by_incident):
    return [entry for results in safety_by_incident.values() for entry in results]


def _combine(
    client, state, alerts, services, runbooks, incidents,
    orig_sev, new_sev, orig_proposals, new_proposals, orig_safety, new_safety,
    updates, current_records, comparison_rows, agreement, analytics, escalation, feedback,
):
    osev = {s["incident_id"]: s for s in orig_sev}
    nsev = {s["incident_id"]: s for s in new_sev}
    upd = {u["incident_id"]: u for u in updates}
    rows = {r["incident_id"]: r for r in comparison_rows}

    joined = []
    for inc in incidents:
        iid = inc["incident_id"]
        ot = top_action(orig_safety.get(iid, []))
        nt = top_action(new_safety.get(iid, []))
        joined.append(
            {
                "incident_id": iid,
                "grouping": {k: v for k, v in inc.items() if not k.startswith("_")},
                "severity_original": osev.get(iid),
                "severity_redecided": nsev.get(iid),
                "top_action_original": ot,
                "top_action_redecided": nt,
                "safety_review_original": orig_safety.get(iid, []),
                "safety_review_redecided": new_safety.get(iid, []),
                "stakeholder_update": upd.get(iid),
                "before_after": rows.get(iid),
            }
        )

    return {
        "generated_at": now_iso(),
        "provider": client.provider,
        "model": client.model,
        "pipeline": state.as_dict(),
        "inputs": {"alerts": len(alerts), "services": len(services), "runbooks": len(runbooks)},
        "regrouping_rerun": False,
        "regrouping_note": "Grouping was not re-run; operator feedback did not dispute clustering. "
                           "Re-decision covered severity and action proposals with feedback injected.",
        "feedback_store": feedback["status"],
        "feedback_examples": feedback["examples"],
        "incidents": joined,
        "severity_original": orig_sev,
        "severity_redecided": new_sev,
        "action_proposals_original": orig_proposals,
        "action_proposals_redecided": new_proposals,
        "safety_review_original": _flatten_safety(orig_safety),
        "safety_review_redecided": _flatten_safety(new_safety),
        "stakeholder_updates": updates,
        "operator_feedback": current_records,
        "before_after_comparison": comparison_rows,
        "agreement_delta": agreement,
        "analytics": analytics,
        "escalation_bundle": escalation,
        "artifacts": {name: name for name in REQUIRED_ARTIFACTS},
    }


def _internal_validate(outdir, alerts, incidents, orig_sev, new_sev, orig_safety, logger, feedback):
    """Hard invariants. Raises AssertionError to abort before finalisation."""
    alert_ids = [a["id"] for a in alerts]
    assigned = [x for c in incidents for x in c["alert_ids"]]
    assert sorted(assigned) == sorted(alert_ids), "alerts must map to exactly one incident"
    assert len(assigned) == len(set(assigned)), "an alert was assigned to two incidents"
    for c in incidents:
        assert 0.0 <= c["confidence"] <= 1.0
        assert (c["confidence"] < 0.70) == c["needs_human_review"] or c["needs_human_review"]
    stages = {r["stage"] for r in logger.records}
    for required in ("incident_grouping", "severity_assessment", "action_proposal",
                     "stakeholder_drafting", "severity_redecision", "action_redecision"):
        assert required in stages, f"missing LLM call log for {required}"
    n_inc = len(incidents)
    for st in ("action_proposal", "action_redecision"):
        cnt = sum(1 for r in logger.records if r["stage"] == st)
        assert cnt == n_inc, f"{st}: expected {n_inc} calls, logged {cnt}"
    if feedback["examples"]:
        for r in logger.records:
            if r["stage"] in ("severity_redecision", "action_redecision"):
                assert r["few_shot_examples_included"] is True, "re-decision must inject feedback examples"
    # a guardrail must actually be capable of overriding
    flat = _flatten_safety(orig_safety)
    assert any(e["final_safety_level"] != e["model_safety_level"] for e in flat) or \
        all(e["final_safety_level"] != "forbidden" for e in flat), "guardrail never engaged"
