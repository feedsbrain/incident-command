"""Stage 5 — stakeholder drafting.

A distinct stage that turns decided state into two audiences' updates. One
batched LLM call. Deterministic guard: if a returned update falls outside the
required sentence range it is replaced with a template built from the same
structured bundle.
"""

from __future__ import annotations

from ..llm.heuristic_client import _draft_one as _template_update
from ..prompts import build_drafting_prompt
from ..util import count_sentences, write_json
from .guardrails import top_action

STAGE = "STAKEHOLDER_DRAFTING_COMPLETE"


def _bundles(incidents, severities, safety_by_incident, services):
    sev_by = {s["incident_id"]: s for s in severities}
    svc_by = {s["service"]: s for s in services}
    out = []
    for inc in incidents:
        iid = inc["incident_id"]
        sev = sev_by.get(iid, {"incident_id": iid, "severity": "sev2", "status": "open", "technical_impact": ""})
        results = safety_by_incident.get(iid, [])
        top = top_action(results)
        blocked = [r for r in results if r["final_safety_level"] != "safe"]
        out.append(
            {
                "incident": {
                    "incident_id": iid,
                    "title": inc["title"],
                    "primary_service": inc["primary_service"],
                    "alert_ids": inc["alert_ids"],
                    "alert_services": inc.get("alert_services", []),
                    "alert_regions": inc.get("alert_regions", []),
                    "suspected_cause": inc["suspected_cause"],
                    "blast_radius": inc["blast_radius"],
                },
                "severity": sev,
                "top_action": {
                    "action": top["action"] if top else None,
                    "final_safety_level": top["final_safety_level"] if top else None,
                }
                if top
                else {},
                "blocked_actions": [
                    {"action": b["action"], "final_safety_level": b["final_safety_level"]} for b in blocked
                ],
                "customer_impact": svc_by.get(inc["primary_service"], {}).get(
                    "customer_impact", "Under assessment."
                ),
            }
        )
    return out


def run(client, incidents, severities, safety_by_incident, services, *, outdir, input_artifacts):
    bundles = _bundles(incidents, severities, safety_by_incident, services)
    prompt = build_drafting_prompt(bundles)
    result = client.generate(
        stage="stakeholder_drafting",
        task="stakeholder_drafting",
        prompt=prompt,
        context={"bundles": bundles},
        input_artifacts=input_artifacts,
        output_artifact="stakeholder_updates.json",
    )
    raw = {u.get("incident_id"): u for u in (result.data or {}).get("updates", [])}
    updates = []
    for b in bundles:
        iid = b["incident"]["incident_id"]
        u = raw.get(iid, {})
        eng = (u.get("engineering_update") or "").strip()
        exe = (u.get("executive_update") or "").strip()
        if not (4 <= count_sentences(eng) <= 6 and 3 <= count_sentences(exe) <= 5):
            fallback = _template_update(b)
            eng, exe = fallback["engineering_update"], fallback["executive_update"]
        updates.append({"incident_id": iid, "engineering_update": eng, "executive_update": exe})
    write_json(f"{outdir}/stakeholder_updates.json", updates)
    return updates
