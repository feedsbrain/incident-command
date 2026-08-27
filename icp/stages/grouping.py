"""Stage 1 — incident grouping.

One LLM call over all alerts + service metadata. The model output is then
*repaired deterministically* so the hard invariant the evaluator checks --
"every alert belongs to exactly one incident" -- cannot be violated by a bad
model response.
"""

from __future__ import annotations

from ..prompts import build_grouping_prompt
from ..util import write_json
from ..vocab import BLAST_RADIUS, coerce

STAGE = "INCIDENT_GROUPING_COMPLETE"
_LOG_STAGE = "incident_grouping"
_OUT = "incident_groups.json"


def _strip_private(inc: dict) -> dict:
    return {k: v for k, v in inc.items() if not k.startswith("_")}


def run(client, alerts, services, *, outdir, input_artifacts):
    prompt = build_grouping_prompt(alerts, services)
    result = client.generate(
        stage=_LOG_STAGE,
        task="incident_grouping",
        prompt=prompt,
        context={"alerts": alerts, "services": services},
        input_artifacts=input_artifacts,
        output_artifact=_OUT,
    )
    raw_incidents = (result.data or {}).get("incidents", [])
    by_id = {a["id"]: a for a in alerts}
    all_ids = [a["id"] for a in alerts]

    incidents: list[dict] = []
    seen: set[str] = set()
    for i, inc in enumerate(raw_incidents, 1):
        iid = inc.get("incident_id") or f"inc-{i:03d}"
        # dedupe alert ids across incidents (keep first claim)
        aids = [x for x in inc.get("alert_ids", []) if x in by_id and x not in seen]
        seen.update(aids)
        try:
            conf = float(inc.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        conf = round(max(0.0, min(1.0, conf)), 4)
        blast = coerce(inc.get("blast_radius"), BLAST_RADIUS, default="unknown", field="blast_radius")
        incidents.append(
            {
                "incident_id": iid,
                "title": inc.get("title") or f"Incident {iid}",
                "summary": inc.get("summary") or "",
                "alert_ids": aids,
                "primary_service": inc.get("primary_service")
                or (by_id[aids[0]]["service"] if aids else "unknown"),
                "suspected_cause": inc.get("suspected_cause") or "Under investigation.",
                "blast_radius": blast,
                "confidence": conf,
                "needs_human_review": conf < 0.70,
                "_alerts": [by_id[x] for x in aids],
            }
        )

    # repair: assign any alert the model dropped
    missing = [x for x in all_ids if x not in seen]
    if missing:
        if incidents:
            # attach each orphan to the incident sharing its service, else a new bucket
            leftovers = []
            for x in missing:
                host = next(
                    (c for c in incidents if by_id[x]["service"] == c["primary_service"]),
                    None,
                )
                if host:
                    host["alert_ids"].append(x)
                    host["_alerts"].append(by_id[x])
                else:
                    leftovers.append(x)
            if leftovers:
                incidents.append(_orphan_incident(leftovers, by_id, len(incidents) + 1))
        else:
            incidents.append(_orphan_incident(missing, by_id, 1))

    # drop any incident that ended up empty
    incidents = [c for c in incidents if c["alert_ids"]]

    # recompute review flag and enrich for downstream stages
    for c in incidents:
        c["needs_human_review"] = bool(c["needs_human_review"] or c["confidence"] < 0.70)
        c["alert_services"] = sorted({a["service"] for a in c["_alerts"]})
        c["alert_regions"] = sorted({a["region"] for a in c["_alerts"]})

    write_json(f"{outdir}/{_OUT}", [_strip_private(c) for c in incidents])
    return incidents


def _orphan_incident(ids, by_id, n):
    alerts = [by_id[x] for x in ids]
    return {
        "incident_id": f"inc-{n:03d}",
        "title": f"Unclustered alerts ({', '.join(sorted({a['service'] for a in alerts}))})",
        "summary": "Alerts not clustered by the model; grouped deterministically for completeness.",
        "alert_ids": list(ids),
        "primary_service": alerts[0]["service"],
        "suspected_cause": "Under investigation.",
        "blast_radius": "unknown",
        "confidence": 0.4,
        "needs_human_review": True,
        "_alerts": alerts,
    }
