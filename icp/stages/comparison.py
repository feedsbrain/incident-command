"""Before/after comparison + agreement delta (computed in code).

For every incident it lines up the original decision, the operator's stated
preference (if any), and the re-decided value, then classifies the movement.
The agreement delta is computed only over incidents where the operator gave an
``accepted`` or ``corrected`` label, per the task.
"""

from __future__ import annotations

from ..util import write_json
from ..vocab import severity_distance
from .guardrails import top_action

STAGE = "BEFORE_AFTER_COMPARISON_COMPLETE"


def _outcome(before_match, after_match, dist_before=None, dist_after=None):
    if after_match and not before_match:
        return "moved_toward"
    if after_match and before_match:
        return "maintained_agreement"
    if before_match and not after_match:
        return "moved_away"
    if dist_before is not None and dist_after is not None:
        if dist_after < dist_before:
            return "moved_toward"
        if dist_after > dist_before:
            return "moved_away"
    return "no_change"


def build(
    incidents,
    orig_sev,
    orig_safety,
    new_sev,
    new_safety,
    operator_records,
    *,
    outdir,
):
    osev = {s["incident_id"]: s["severity"] for s in orig_sev}
    nsev = {s["incident_id"]: s["severity"] for s in new_sev}
    rec_by = {r["incident_id"]: r for r in operator_records}

    def top(safety_map, iid):
        t = top_action(safety_map.get(iid, []))
        return t["action"] if t else None

    rows = []
    sev_before = sev_after = sev_n = 0
    act_before = act_after = act_n = 0

    for inc in incidents:
        iid = inc["incident_id"]
        rec = rec_by.get(iid, {})
        o_s, n_s = osev.get(iid), nsev.get(iid)
        o_a, n_a = top(orig_safety, iid), top(new_safety, iid)

        s_status = rec.get("severity_status")
        a_status = rec.get("action_status")
        op_sev = (
            rec.get("corrected_severity") if s_status == "corrected"
            else o_s if s_status == "accepted"
            else None
        )
        op_act = (
            rec.get("corrected_action") if a_status == "corrected"
            else o_a if a_status == "accepted"
            else None
        )

        sev_outcome = None
        if op_sev is not None:
            b, a = o_s == op_sev, n_s == op_sev
            sev_outcome = _outcome(b, a, severity_distance(o_s, op_sev), severity_distance(n_s, op_sev))
            sev_n += 1
            sev_before += int(b)
            sev_after += int(a)

        act_outcome = None
        if op_act is not None:
            b, a = o_a == op_act, n_a == op_act
            act_outcome = _outcome(b, a)
            act_n += 1
            act_before += int(b)
            act_after += int(a)

        provided = [x for x in (sev_outcome, act_outcome) if x is not None]
        moved = (
            None if not provided
            else all(x in ("moved_toward", "maintained_agreement") for x in provided)
        )
        influence = "none"
        if a_status == "skipped" and o_a != n_a:
            influence = "generalised_from_sibling_feedback"
        elif o_s != n_s or o_a != n_a:
            influence = "direct_operator_feedback"

        rows.append(
            {
                "incident_id": iid,
                "original_severity": o_s,
                "operator_severity": op_sev,
                "redecided_severity": n_s,
                "severity_outcome": sev_outcome,
                "original_top_action": o_a,
                "operator_action": op_act,
                "redecided_top_action": n_a,
                "action_outcome": act_outcome,
                "moved_toward_feedback": moved,
                "feedback_influence": influence,
            }
        )

    def ratio(x, n):
        return round(x / n, 4) if n else None

    sev_delta = None if not sev_n else round(sev_after / sev_n - sev_before / sev_n, 4)
    act_delta = None if not act_n else round(act_after / act_n - act_before / act_n, 4)
    deltas = [d for d in (sev_delta, act_delta) if d is not None]

    agreement = {
        "scope": "incidents with operator accepted/corrected labels only",
        "severity": {
            "n": sev_n,
            "agreement_before": ratio(sev_before, sev_n),
            "agreement_after": ratio(sev_after, sev_n),
            "delta": sev_delta,
        },
        "action": {
            "n": act_n,
            "agreement_before": ratio(act_before, act_n),
            "agreement_after": ratio(act_after, act_n),
            "delta": act_delta,
        },
        "combined_delta": round(sum(deltas) / len(deltas), 4) if deltas else None,
        "moved_toward_feedback_count": sum(1 for r in rows if r["moved_toward_feedback"]),
    }

    write_json(f"{outdir}/before_after_comparison.json", {"comparison": rows, "agreement_delta": agreement})
    return rows, agreement
