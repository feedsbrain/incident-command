"""Stage 10 — machine-readable escalation bundle for sev0 / sev1 incidents."""

from __future__ import annotations

from ..util import slugify, write_json


def build(incidents, final_severities, services, *, outdir):
    sev_by = {s["incident_id"]: s for s in final_severities}
    svc_by = {s["service"]: s for s in services}
    bundle = []
    for inc in incidents:
        sev = sev_by.get(inc["incident_id"], {})
        if sev.get("severity") not in ("sev0", "sev1"):
            continue
        team = svc_by.get(inc["primary_service"], {}).get("owner_team", "Unassigned")
        bundle.append(
            {
                "incident_id": inc["incident_id"],
                "severity": sev["severity"],
                "owner_team": team,
                "page": True,
                "suggested_channel": f"#incident-{slugify(team)}",
                "summary": f"{sev['severity'].upper()} {inc['title']} - {inc['summary']}"[:280],
            }
        )
    write_json(f"{outdir}/escalation_bundle.json", bundle)
    return bundle
