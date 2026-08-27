#!/usr/bin/env python3
"""Standalone validator for the incident-command pipeline artifacts.

    python validate.py            # validate artifacts in the current directory
    python validate.py --dir out  # validate elsewhere

Exit code 0 iff every check passes. This does NOT run the pipeline; run
`python run.py ...` first (a clean checkout has no artifacts yet).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from icp.vocab import ACTION_SAFETY, BLAST_RADIUS, INCIDENT_STATUS, OPERATOR_REVIEW, SEVERITY

REQUIRED = [
    "alerts.json", "services.json", "runbooks.json",
    "incident_groups.json", "severity_assessments.json", "action_proposals.json",
    "safety_review.json", "stakeholder_updates.json", "operator_feedback.jsonl",
    "severity_redecision.json", "actions_redecision.json", "incident_command_output.json",
    "analytics_summary.json", "feedback_store_status.json", "escalation_bundle.json",
    "llm_calls.jsonl",
]

RISKY_SUBSTRINGS = ("bypass", "skip_reconcil", "skip_secondary_reconcil", "disable_authentication",
                    "disable_risk", "auto_approve", "force_accept", "without_confirmation",
                    "mark_withdrawals_complete", "extend_session_validity", "drop_live_ticks",
                    "disable_price_validation")


class Checker:
    def __init__(self):
        self.failed = 0
        self.passed = 0

    def check(self, name, ok, detail=""):
        mark = "PASS" if ok else "FAIL"
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        print(f"  [{mark}] {name}" + (f" -- {detail}" if detail and not ok else ""))
        return ok


def _load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _load_jsonl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".")
    args = ap.parse_args(argv)
    d = Path(args.dir)
    c = Checker()

    print("\n=== 1. required artifacts exist ===")
    for name in REQUIRED:
        c.check(f"exists: {name}", (d / name).exists())
    if c.failed:
        print("\nMissing artifacts; run `python run.py --operator-mode auto` first.")
        return 1

    print("\n=== 2. JSON / JSONL parse ===")
    parsed = {}
    for name in REQUIRED:
        try:
            parsed[name] = _load_jsonl(d / name) if name.endswith(".jsonl") else _load_json(d / name)
            c.check(f"parses: {name}", True)
        except Exception as e:  # noqa: BLE001
            c.check(f"parses: {name}", False, str(e))
    if c.failed:
        return 1

    alerts = parsed["alerts.json"]
    services = parsed["services.json"]
    runbooks = {r["service"]: r for r in parsed["runbooks.json"]}
    groups = parsed["incident_groups.json"]
    sev = parsed["severity_assessments.json"]
    props = parsed["action_proposals.json"]
    safety = parsed["safety_review.json"]
    updates = parsed["stakeholder_updates.json"]
    feedback = parsed["operator_feedback.jsonl"]
    sev_re = parsed["severity_redecision.json"]
    act_re = parsed["actions_redecision.json"]
    combined = parsed["incident_command_output.json"]
    llm = parsed["llm_calls.jsonl"]

    incident_ids = [g["incident_id"] for g in groups]

    print("\n=== 3. every alert assigned to exactly one incident ===")
    assigned = [x for g in groups for x in g["alert_ids"]]
    alert_ids = [a["id"] for a in alerts]
    c.check("all alerts assigned", sorted(assigned) == sorted(alert_ids),
            f"assigned={sorted(assigned)} alerts={sorted(alert_ids)}")
    c.check("no alert in two incidents", len(assigned) == len(set(assigned)))

    print("\n=== 4. controlled vocabularies enforced ===")
    c.check("blast_radius vocab", all(g["blast_radius"] in BLAST_RADIUS for g in groups))
    c.check("severity vocab (assessment)", all(s["severity"] in SEVERITY for s in sev))
    c.check("status vocab (assessment)", all(s["status"] in INCIDENT_STATUS for s in sev))
    c.check("severity vocab (redecision)", all(s["severity"] in SEVERITY for s in sev_re))
    c.check("safety vocab (model+final)",
            all(r["model_safety_level"] in ACTION_SAFETY and r["final_safety_level"] in ACTION_SAFETY
                for r in safety))
    c.check("operator review-action vocab",
            all(r["severity_status"] in OPERATOR_REVIEW and r["action_status"] in OPERATOR_REVIEW
                for r in feedback))
    c.check("corrected severities in vocab",
            all((r["corrected_severity"] in SEVERITY) for r in feedback if r["corrected_severity"]))

    print("\n=== 5. confidence in [0,1] ===")
    c.check("confidence range", all(0.0 <= g["confidence"] <= 1.0 for g in groups))

    print("\n=== 6. low-confidence incidents flagged ===")
    c.check("confidence < 0.70 => needs_human_review",
            all(g["needs_human_review"] for g in groups if g["confidence"] < 0.70))

    print("\n=== 7. severity is a separate stage from grouping ===")
    stages = [r["stage"] for r in llm]
    grp_calls = [r for r in llm if r["stage"] == "incident_grouping"]
    sev_calls = [r for r in llm if r["stage"] == "severity_assessment"]
    c.check("distinct grouping + severity LLM calls", len(grp_calls) == 1 and len(sev_calls) == 1)
    c.check("grouping and severity have different prompts",
            grp_calls and sev_calls and grp_calls[0]["prompt_hash"] != sev_calls[0]["prompt_hash"])
    c.check("separate artifacts on disk", (d / "incident_groups.json").exists() and (d / "severity_assessments.json").exists())

    print("\n=== 8. action proposals are separate per incident ===")
    ap_calls = [r for r in llm if r["stage"] == "action_proposal"]
    c.check("one action_proposal call per incident",
            sorted(r["incident_id"] for r in ap_calls) == sorted(incident_ids),
            f"calls={sorted(r['incident_id'] for r in ap_calls)}")
    c.check("every incident has >=1 proposed action",
            all(any(p["incident_id"] == i for p in props) for i in incident_ids))
    c.check("1..3 actions per incident",
            all(1 <= sum(p["incident_id"] == i for p in props) <= 3 for i in incident_ids))

    print("\n=== 9. deterministic safety checks override forbidden / risky actions ===")
    ok_forbidden = True
    for r in safety:
        g = next((x for x in groups if x["incident_id"] == r["incident_id"]), {})
        involved = [g.get("primary_service")] + g.get("alert_services", [])
        forb = {a for s in involved for a in runbooks.get(s, {}).get("forbidden_actions", [])}
        norm = r["action"].strip().lower().replace("-", "_").replace(" ", "_")
        if norm in forb and r["final_safety_level"] != "forbidden":
            ok_forbidden = False
        if any(k in norm for k in RISKY_SUBSTRINGS) and r["final_safety_level"] != "forbidden":
            ok_forbidden = False
    c.check("forbidden/risky actions marked forbidden", ok_forbidden)
    # change-freeze escalation
    freeze_ok = True
    for r in safety:
        g = next((x for x in groups if x["incident_id"] == r["incident_id"]), {})
        involved = [g.get("primary_service")] + g.get("alert_services", [])
        frozen = any(next((s for s in services if s["service"] == x), {}).get("change_freeze_required") for x in involved)
        norm = r["action"].strip().lower().replace("-", "_")
        is_rollback = any(t in norm for t in ("roll_back", "rollback", "route_to_previous", "revert"))
        if frozen and not is_rollback and r["final_safety_level"] == "safe":
            freeze_ok = False
    c.check("change-freeze non-rollback actions not left 'safe'", freeze_ok)
    c.check("safety review preserves model proposal + verdict",
            all("model_proposal" in r and "model_safety_level" in r and "final_safety_level" in r for r in safety))
    c.check("guardrail demonstrably overrides somewhere",
            any(r["final_safety_level"] != r["model_safety_level"] for r in safety))

    print("\n=== 10. stakeholder updates for every incident ===")
    upd_by = {u["incident_id"]: u for u in updates}
    c.check("update per incident", set(upd_by) >= set(incident_ids))

    def sc(t):
        import re
        return len([p for p in re.split(r"(?<=[.!?])\s+", (t or "").strip()) if p.strip()])

    c.check("engineering update 4-6 sentences",
            all(4 <= sc(upd_by[i]["engineering_update"]) <= 6 for i in incident_ids),
            {i: sc(upd_by[i]["engineering_update"]) for i in incident_ids})
    c.check("executive update 3-5 sentences",
            all(3 <= sc(upd_by[i]["executive_update"]) <= 5 for i in incident_ids),
            {i: sc(upd_by[i]["executive_update"]) for i in incident_ids})

    print("\n=== 11. operator feedback persisted ===")
    c.check("operator_feedback.jsonl non-empty", len(feedback) >= 1)
    c.check("feedback record shape",
            all({"incident_id", "original_severity", "original_action", "action_status",
                 "severity_status", "timestamp"} <= set(r) for r in feedback))
    fss = parsed["feedback_store_status.json"]
    c.check("feedback_store_status records load", "prior_records_loaded" in fss and "loaded_before_first_redecision" in fss)

    print("\n=== 12. re-decision includes feedback-derived examples ===")
    c.check("feedback_examples.json exists", (d / "feedback_examples.json").exists())
    re_calls = [r for r in llm if r["stage"] in ("severity_redecision", "action_redecision")]
    c.check("re-decision calls exist", len(re_calls) >= 1 + len(incident_ids) - 1)
    examples = _load_json(d / "feedback_examples.json") if (d / "feedback_examples.json").exists() else []
    if examples:
        c.check("re-decision calls set few_shot_examples_included=true",
                all(r["few_shot_examples_included"] is True for r in re_calls))
    else:
        c.check("re-decision calls exist (no examples built this run)", bool(re_calls))
    c.check("severity_redecision + actions_redecision non-empty", len(sev_re) >= 1 and len(act_re) >= 1)

    print("\n=== 13. before/after comparison + agreement delta computed ===")
    comp = combined.get("before_after_comparison", [])
    agr = combined.get("agreement_delta", {})
    need_keys = {"incident_id", "original_severity", "operator_severity", "redecided_severity",
                 "original_top_action", "operator_action", "redecided_top_action", "moved_toward_feedback"}
    c.check("comparison row per incident", {r["incident_id"] for r in comp} == set(incident_ids))
    c.check("comparison rows well-formed", all(need_keys <= set(r) for r in comp))
    c.check(
        "agreement delta present + numeric",
        isinstance(agr.get("combined_delta"), (int, float))
        and isinstance(agr.get("severity", {}).get("delta"), (int, float, type(None)))
        and isinstance(agr.get("action", {}).get("delta"), (int, float, type(None)))
        and agr.get("scope"),
    )
    # independently recompute the delta from comparison rows
    recomputed = _recompute_delta(comp, feedback)
    c.check("agreement delta reproducible from comparison rows",
            _close(recomputed["severity"], agr.get("severity", {}).get("delta")) and
            _close(recomputed["action"], agr.get("action", {}).get("delta")),
            f"recomputed={recomputed} reported_sev={agr.get('severity',{}).get('delta')} reported_act={agr.get('action',{}).get('delta')}")

    print("\n=== 14. LLM call log has separate records for required stages ===")
    for stg, expected in [("incident_grouping", 1), ("severity_assessment", 1),
                          ("action_proposal", len(incident_ids)), ("stakeholder_drafting", None),
                          ("severity_redecision", 1), ("action_redecision", len(incident_ids))]:
        n = sum(1 for r in llm if r["stage"] == stg)
        c.check(f"llm log: {stg}", (n >= 1) if expected is None else (n == expected), f"n={n}")
    c.check("llm records carry required fields",
            all({"stage", "timestamp", "provider", "model", "prompt_hash", "input_artifacts",
                 "output_artifact", "few_shot_examples_included"} <= set(r) for r in llm))

    print("\n=== 15. pipeline stage ordering ===")
    st = parsed.get("incident_command_output.json", {}).get("pipeline", {})
    if (d / "pipeline_state.json").exists():
        st = _load_json(d / "pipeline_state.json")
    order = [t["stage"] for t in st.get("transitions", [])]
    c.check("reached RESULTS_FINALISED", st.get("current_stage") == "RESULTS_FINALISED")
    c.check("grouping precedes severity precedes actions precedes finalise",
            _before(order, "INCIDENT_GROUPING_COMPLETE", "SEVERITY_ASSESSMENT_COMPLETE")
            and _before(order, "SEVERITY_ASSESSMENT_COMPLETE", "ACTION_PROPOSALS_COMPLETE")
            and _before(order, "OPERATOR_REVIEW_COLLECTED", "REDECISION_COMPLETE")
            and _before(order, "REDECISION_COMPLETE", "RESULTS_FINALISED"))

    print(f"\n=========== {c.passed} passed, {c.failed} failed ===========")
    return 1 if c.failed else 0


def _before(order, a, b):
    return a in order and b in order and order.index(a) < order.index(b)


def _close(a, b, eps=1e-6):
    if a is None or b is None:
        return a == b
    return abs(a - b) <= eps


def _recompute_delta(comp, feedback):
    fb = {r["incident_id"]: r for r in feedback}
    out = {}
    for dim, orig_k, re_k in [("severity", "original_severity", "redecided_severity"),
                              ("action", "original_top_action", "redecided_top_action")]:
        status_k = "severity_status" if dim == "severity" else "action_status"
        op_k = "operator_severity" if dim == "severity" else "operator_action"
        rows = [r for r in comp if fb.get(r["incident_id"], {}).get(status_k) in ("accepted", "corrected")]
        if not rows:
            out[dim] = None
            continue
        before = sum(1 for r in rows if r[orig_k] == r[op_k]) / len(rows)
        after = sum(1 for r in rows if r[re_k] == r[op_k]) / len(rows)
        out[dim] = round(after - before, 4)
    return out


if __name__ == "__main__":
    sys.exit(main())
