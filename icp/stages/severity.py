"""Stage 2 — severity assessment (also re-used by the re-decision stage).

Separate LLM call from grouping: it receives the grouped incidents + service
metadata and an explicit severity rubric. Deterministic post-processing enforces
the controlled vocabularies and a floor for the high-stakes categories the task
calls out.
"""

from __future__ import annotations

from ..prompts import build_severity_prompt
from ..util import write_json
from ..vocab import INCIDENT_STATUS, SEVERITY, coerce

STAGE = "SEVERITY_ASSESSMENT_COMPLETE"


def _rollout_correlated(inc: dict) -> bool:
    blob = (inc.get("suspected_cause", "") + " " + inc.get("summary", "")).lower()
    return any(k in blob for k in ("deploy", "rollout", "roll-out", "released", "release", "rolled out"))


def _floor_category(inc: dict) -> bool:
    ps = inc.get("primary_service")
    services = set(inc.get("alert_services", []))
    if ps == "trade-execution" or "trade-execution" in services:
        return True
    if ps == "payments-ledger":
        return True
    if ps == "risk-engine" and _rollout_correlated(inc):
        return True
    return False


def run(
    client,
    incidents,
    services,
    *,
    outdir,
    input_artifacts,
    feedback_examples=None,
    operator_status=None,
    feedback_block=None,
    log_stage="severity_assessment",
    out="severity_assessments.json",
):
    # incidents carry a private "_alerts"; expose a trimmed view to the prompt
    prompt_incidents = [
        {k: v for k, v in c.items() if not k.startswith("_")} for c in incidents
    ]
    prompt = build_severity_prompt(prompt_incidents, services, feedback_block)
    result = client.generate(
        stage=log_stage,
        task="severity_assessment",
        prompt=prompt,
        context={
            "incidents": incidents,
            "services": services,
            "feedback_examples": feedback_examples or [],
            "operator_status": operator_status or {},
        },
        input_artifacts=input_artifacts,
        output_artifact=out,
        few_shot_examples=feedback_examples or None,
    )
    raw = (result.data or {}).get("assessments", [])
    by_iid = {c["incident_id"]: c for c in incidents}
    assessments = []
    for item in raw:
        iid = item.get("incident_id")
        inc = by_iid.get(iid, {})
        sev = coerce(item.get("severity"), SEVERITY, default="sev2", field="severity")
        status = coerce(item.get("status"), INCIDENT_STATUS, default="open", field="status")
        # deterministic floor for called-out categories
        if _floor_category(inc) and sev == "sev3":
            sev = "sev2"
            item["reasoning"] = (item.get("reasoning", "") + " [floor] high-stakes category may not be sev3.").strip()
            status = "open" if sev in ("sev0", "sev1") else status
        assessments.append(
            {
                "incident_id": iid,
                "severity": sev,
                "status": status,
                "business_impact": item.get("business_impact") or "Under assessment.",
                "technical_impact": item.get("technical_impact") or "Under assessment.",
                "reasoning": item.get("reasoning") or "",
            }
        )
    # cover any incident the model skipped
    covered = {a["incident_id"] for a in assessments}
    for c in incidents:
        if c["incident_id"] not in covered:
            assessments.append(
                {
                    "incident_id": c["incident_id"],
                    "severity": "sev2",
                    "status": "open",
                    "business_impact": "Under assessment (model omitted this incident).",
                    "technical_impact": "Under assessment.",
                    "reasoning": "Filled deterministically because the model response omitted this incident.",
                }
            )
    write_json(f"{outdir}/{out}", assessments)
    return assessments
