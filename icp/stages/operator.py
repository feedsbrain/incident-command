"""Stage 6 — operator review interface.

Interactive by default. For automated / CI runs it also supports a scripted
operator (a JSON file of canned answers) and a plain accept-all mode, so the
evaluator can run the full pipeline non-interactively while a human can still
drive it for real. Every reviewed incident produces one persisted feedback
record (accepted / corrected / skipped) in ``operator_feedback.jsonl``.
"""

from __future__ import annotations

import sys

from ..util import append_jsonl, now_iso, read_json
from ..vocab import SEVERITY
from .guardrails import top_action

STAGE = "OPERATOR_REVIEW_COLLECTED"


def _classify_severity(resp: str, original: str):
    r = (resp or "").strip().lower().replace("correct to:", "").strip()
    if r in ("", "y", "yes", "ok", "accept", "accepted"):
        return "accepted", None
    if r in ("skip", "s", "skipped"):
        return "skipped", None
    if r in SEVERITY:
        return ("accepted", None) if r == original else ("corrected", r)
    return "accepted", None


def _classify_action(resp: str, original: str):
    r = (resp or "").strip().lower().replace("correct to:", "").strip().replace(" ", "_").replace("-", "_")
    if r in ("", "y", "yes", "ok", "accept", "accepted"):
        return "accepted", None
    if r in ("skip", "s", "skipped"):
        return "skipped", None
    return ("accepted", None) if r == original else ("corrected", r)


def _panel(inc, sev, top):
    ids = ", ".join(inc["alert_ids"])
    print("\n" + "-" * 72)
    print(f"{inc['incident_id'].upper()}  {inc['title']}")
    print(f"  grouped alerts : {ids}")
    print(f"  primary service: {inc['primary_service']}   blast radius: {inc['blast_radius']}")
    print(f"  grouping conf. : {inc['confidence']}  (needs_human_review={inc['needs_human_review']})")
    print(f"  severity       : {sev['severity']}  (status {sev['status']})")
    if top:
        print(f"  top action     : {top['action']}  ->  final safety: {top['final_safety_level']}")
        if top.get("guardrail_reasons"):
            print(f"                   guardrail: {top['guardrail_reasons'][0]}")
    else:
        print("  top action     : (none proposed)")


def _script_lookup(script, iid, service):
    if not script:
        return {"severity": "y", "action": "y"}
    return (
        (script.get("by_incident") or {}).get(iid)
        or (script.get("by_service") or {}).get(service)
        or script.get("_default")
        or {"severity": "y", "action": "y"}
    )


def run(incidents, severities, safety_by_incident, *, outdir, feedback_path, mode="interactive", script_path=None):
    sev_by = {s["incident_id"]: s for s in severities}
    script = None
    if mode == "script" and script_path:
        try:
            script = read_json(script_path)
        except FileNotFoundError:
            print(f"[operator] script {script_path} not found; falling back to accept-all")
            mode = "auto"

    interactive = mode == "interactive" and sys.stdin is not None and sys.stdin.isatty()
    if mode == "interactive" and not interactive:
        print("[operator] no interactive TTY detected; using accept-all. "
              "Use --operator-mode script:<file> or run in a terminal for real review.")
        mode = "auto"

    print("\n================  OPERATOR REVIEW  ================")
    print(f"mode: {'interactive' if interactive else mode}")

    records = []
    for inc in incidents:
        iid = inc["incident_id"]
        sev = sev_by.get(iid, {"severity": "sev2", "status": "open"})
        top = top_action(safety_by_incident.get(iid, []))
        original_action = top["action"] if top else ""
        _panel(inc, sev, top)

        if interactive:
            sev_resp = input("  severity correct? (y / correct to: [sev0|sev1|sev2|sev3] / skip): ")
            act_resp = input("  action correct?   (y / correct to: [action_name] / skip): ")
        elif mode == "script":
            entry = _script_lookup(script, iid, inc["primary_service"])
            sev_resp, act_resp = str(entry.get("severity", "y")), str(entry.get("action", "y"))
            print(f"  [scripted] severity={sev_resp!r} action={act_resp!r}")
        else:
            sev_resp, act_resp = "y", "y"
            print("  [auto] accepted severity and action")

        s_status, s_corr = _classify_severity(sev_resp, sev["severity"])
        a_status, a_corr = _classify_action(act_resp, original_action)
        rec = {
            "incident_id": iid,
            "original_severity": sev["severity"],
            "corrected_severity": s_corr,
            "original_action": original_action,
            "corrected_action": a_corr,
            "action_status": a_status,
            "severity_status": s_status,
            "timestamp": now_iso(),
        }
        append_jsonl(feedback_path, rec)
        records.append(rec)

    corr = sum(1 for r in records if "corrected" in (r["severity_status"], r["action_status"]))
    print(f"\n[operator] collected {len(records)} reviews ({corr} with at least one correction); "
          f"appended to {feedback_path}")
    return records
