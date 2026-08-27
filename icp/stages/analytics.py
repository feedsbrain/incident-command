"""Stage 8 — analytics summary (JSON + terminal)."""

from __future__ import annotations

from collections import Counter

from ..util import write_json

STAGE = "ANALYTICS_GENERATED"


def build(
    incidents,
    raw_alert_count,
    orig_severities,
    final_severities,
    orig_safety_by_incident,
    final_safety_by_incident,
    operator_records,
    agreement,
    *,
    outdir,
):
    confs = [c["confidence"] for c in incidents]
    final_sev = Counter(s["severity"] for s in final_severities)
    orig_sev = Counter(s["severity"] for s in orig_severities)
    blast = Counter(c["blast_radius"] for c in incidents)

    def safety_counts(safety_map):
        c = Counter()
        for results in safety_map.values():
            for r in results:
                c[r["final_safety_level"]] += 1
        return dict(c)

    svc_involvement = Counter()
    for c in incidents:
        for svc in c.get("alert_services", []):
            svc_involvement[svc] += 1

    sev_corr = sum(1 for r in operator_records if r["severity_status"] == "corrected")
    act_corr = sum(1 for r in operator_records if r["action_status"] == "corrected")

    summary = {
        "incidents_formed": len(incidents),
        "raw_alerts": raw_alert_count,
        "alerts_per_incident_avg": round(raw_alert_count / len(incidents), 2) if incidents else 0,
        "average_grouping_confidence": round(sum(confs) / len(confs), 4) if confs else None,
        "severity_distribution_final": dict(final_sev),
        "severity_distribution_original": dict(orig_sev),
        "blast_radius_distribution": dict(blast),
        "needs_human_review_count": sum(1 for c in incidents if c["needs_human_review"]),
        "proposed_actions_by_safety_level_original": safety_counts(orig_safety_by_incident),
        "proposed_actions_by_safety_level_final": safety_counts(final_safety_by_incident),
        "operator_corrections_total": sum(
            1 for r in operator_records if "corrected" in (r["severity_status"], r["action_status"])
        ),
        "operator_severity_corrections": sev_corr,
        "operator_action_corrections": act_corr,
        "before_after_agreement_delta": {
            "severity": agreement["severity"]["delta"],
            "action": agreement["action"]["delta"],
            "combined": agreement["combined_delta"],
        },
        "services_most_frequently_involved": [
            {"service": s, "incident_count": n} for s, n in svc_involvement.most_common()
        ],
    }
    write_json(f"{outdir}/analytics_summary.json", summary)
    _print(summary)
    return summary


def _print(s):
    print("\n================  ANALYTICS  ================")
    print(f"  incidents formed from {s['raw_alerts']} raw alerts : {s['incidents_formed']}")
    print(f"  average grouping confidence                : {s['average_grouping_confidence']}")
    print(f"  severity distribution (final)              : {s['severity_distribution_final']}")
    print(f"  blast radius distribution                  : {s['blast_radius_distribution']}")
    print(f"  incidents flagged for human review         : {s['needs_human_review_count']}")
    print(f"  actions by safety level (final)            : {s['proposed_actions_by_safety_level_final']}")
    print(f"  operator corrections (sev / action)        : {s['operator_severity_corrections']} / {s['operator_action_corrections']}")
    print(f"  agreement delta (sev / action / combined)  : "
          f"{s['before_after_agreement_delta']['severity']} / "
          f"{s['before_after_agreement_delta']['action']} / "
          f"{s['before_after_agreement_delta']['combined']}")
    print(f"  services most involved                     : "
          + ", ".join(f"{d['service']}({d['incident_count']})" for d in s['services_most_frequently_involved'][:5]))
