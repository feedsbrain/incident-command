"""Stage 3 — first-response action proposals, one LLM call PER incident.

Each call sees only its incident, that incident's severity assessment, and the
matching runbook excerpt. Post-processing guarantees vocabulary validity and
runbook traceability; it does NOT decide safety -- that is Stage 4.
"""

from __future__ import annotations

from ..prompts import build_action_prompt
from ..util import write_json
from ..vocab import ACTION_SAFETY, coerce

STAGE = "ACTION_PROPOSALS_COMPLETE"

_NO_FIT_MARKERS = ("no listed action", "none of the listed", "not in runbook", "no allowed action", "no runbook action")


def run(
    client,
    incidents,
    severities,
    runbooks,
    services,
    *,
    outdir,
    input_artifacts,
    feedback_examples=None,
    operator_status=None,
    feedback_block=None,
    log_stage="action_proposal",
    out="action_proposals.json",
):
    sev_by = {s["incident_id"]: s for s in severities}
    rb_by = {r["service"]: r for r in runbooks}
    svc_by = {s["service"]: s for s in services}

    proposals: dict[str, list[dict]] = {}
    flat: list[dict] = []
    for inc in incidents:
        iid = inc["incident_id"]
        ps = inc["primary_service"]
        runbook = rb_by.get(ps, {"service": ps, "allowed_actions": [], "forbidden_actions": [], "notes": ""})
        service = svc_by.get(ps, {"service": ps})
        sev = sev_by.get(iid, {"incident_id": iid, "severity": "sev2", "status": "open"})
        prompt = build_action_prompt(
            {k: v for k, v in inc.items() if not k.startswith("_")}, sev, runbook, service, feedback_block
        )
        result = client.generate(
            stage=log_stage,
            task="action_proposal",
            prompt=prompt,
            context={
                "incident": inc,
                "severity": sev,
                "runbook": runbook,
                "service": service,
                "feedback_examples": feedback_examples or [],
                "operator_status": operator_status or {},
            },
            incident_id=iid,
            input_artifacts=input_artifacts,
            output_artifact=out,
            few_shot_examples=feedback_examples or None,
        )
        raw = (result.data or {}).get("actions", [])
        allowed = set(runbook.get("allowed_actions", []))
        cleaned = []
        for a in raw[:4]:
            name = str(a.get("action", "")).strip().lower().replace(" ", "_").replace("-", "_")
            if not name:
                continue
            cleaned.append(
                {
                    "incident_id": iid,
                    "action": name,
                    "why": a.get("why") or "",
                    "expected_effect": a.get("expected_effect") or "",
                    "risk": a.get("risk") or "",
                    "safety_level": coerce(a.get("safety_level"), ACTION_SAFETY, default="needs_approval", field="safety_level"),
                }
            )
        cleaned = cleaned[:3] if len(cleaned) > 3 else cleaned
        # runbook traceability guarantee
        traceable = any(a["action"] in allowed for a in cleaned)
        explained = any(any(m in (a.get("why", "").lower()) for m in _NO_FIT_MARKERS) for a in cleaned)
        if not traceable and not explained and allowed:
            cleaned.append(
                {
                    "incident_id": iid,
                    "action": sorted(allowed)[0],
                    "why": "Added deterministically: no model action was traceable to the runbook allowed_actions and none explained why.",
                    "expected_effect": "Keeps at least one recommendation inside sanctioned options.",
                    "risk": "May be less targeted.",
                    "safety_level": "needs_approval",
                }
            )
        if not cleaned:
            cleaned.append(
                {
                    "incident_id": iid,
                    "action": (sorted(allowed)[0] if allowed else "escalate_to_service_owner"),
                    "why": "No actions returned by the model.",
                    "expected_effect": "Owner triages manually.",
                    "risk": "Slower first response.",
                    "safety_level": "needs_approval",
                }
            )
        proposals[iid] = cleaned
        flat.extend(cleaned)

    write_json(f"{outdir}/{out}", flat)
    return proposals
