"""Stage 7a — build the feedback block used to re-decide.

Turns persisted operator feedback (prior runs + the review just collected) into:
  * ``examples``        - structured few-shot records with incident context
  * ``operator_status`` - per-incident {severity/action: accepted|corrected|skipped}
  * ``feedback_block``  - a text block injected into the re-decision prompts
  * ``status``          - what was loaded (also written to feedback_store_status.json)
"""

from __future__ import annotations

from collections import Counter

from ..util import write_json
from .severity import _rollout_correlated

STAGE = "FEEDBACK_EXAMPLES_BUILT"


def _incident_ctx(incidents_by_id, iid):
    inc = incidents_by_id.get(iid)
    if not inc:
        return {"primary_service": None, "blast_radius": None, "signal_types": [], "rollout_correlated": None}
    return {
        "primary_service": inc["primary_service"],
        "blast_radius": inc["blast_radius"],
        "signal_types": sorted({a["signal_type"] for a in inc.get("_alerts", [])}),
        "rollout_correlated": _rollout_correlated(inc),
    }


def _example(rec, ctx, source):
    sev_known = rec["severity_status"] in ("accepted", "corrected")
    act_known = rec["action_status"] in ("accepted", "corrected")
    return {
        "incident_id": rec["incident_id"],
        "source": source,
        "primary_service": ctx["primary_service"],
        "blast_radius": ctx["blast_radius"],
        "signal_types": ctx["signal_types"],
        "rollout_correlated": ctx["rollout_correlated"],
        "model_severity": rec["original_severity"],
        "operator_severity": (rec["corrected_severity"] or rec["original_severity"]) if sev_known else None,
        "severity_status": rec["severity_status"],
        "model_action": rec["original_action"],
        "operator_action": (rec["corrected_action"] or rec["original_action"]) if act_known else None,
        "action_status": rec["action_status"],
    }


def _render_block(examples):
    lines = []
    for i, ex in enumerate(examples, 1):
        lines.append(f"Example {i} (source: {ex['source']} run)")
        lines.append(
            f"  incident {ex['incident_id']}  service={ex['primary_service']}  "
            f"blast_radius={ex['blast_radius']}  rollout_correlated={ex['rollout_correlated']}  "
            f"signals={ex['signal_types']}"
        )
        if ex["severity_status"] == "corrected":
            lines.append(f"  severity: model said {ex['model_severity']} -> operator corrected to {ex['operator_severity']}")
        elif ex["severity_status"] == "accepted":
            lines.append(f"  severity: operator confirmed {ex['operator_severity']}")
        if ex["action_status"] == "corrected":
            lines.append(f"  action: model said {ex['model_action']} -> operator corrected to {ex['operator_action']}")
        elif ex["action_status"] == "accepted" and ex["model_action"]:
            lines.append(f"  action: operator confirmed {ex['model_action']}")
        lines.append("")
    return "\n".join(lines).strip()


def build(prior_records, current_records, incidents, *, outdir, feedback_path, prior_existed):
    incidents_by_id = {c["incident_id"]: c for c in incidents}

    examples = []
    for rec in prior_records:
        ex = _example(rec, _incident_ctx(incidents_by_id, rec["incident_id"]), "prior")
        if ex["severity_status"] != "skipped" or ex["action_status"] != "skipped":
            examples.append(ex)
    for rec in current_records:
        ex = _example(rec, _incident_ctx(incidents_by_id, rec["incident_id"]), "current")
        if ex["severity_status"] != "skipped" or ex["action_status"] != "skipped":
            examples.append(ex)

    # prefer corrections, then accepted; cap for prompt size
    examples.sort(key=lambda e: (0 if "corrected" in (e["severity_status"], e["action_status"]) else 1))
    examples = examples[:10]

    operator_status: dict[str, dict] = {}
    for rec in list(prior_records) + list(current_records):  # current overrides prior
        operator_status[rec["incident_id"]] = {
            "severity": rec["severity_status"],
            "corrected_severity": rec["corrected_severity"],
            "action": rec["action_status"],
            "corrected_action": rec["corrected_action"],
        }

    feedback_block = _render_block(examples) if examples else ""

    status = {
        "prior_feedback_file": str(feedback_path),
        "existed_before_run": bool(prior_existed),
        "prior_records_loaded": len(prior_records),
        "current_run_records_appended": len(current_records),
        "total_records": len(prior_records) + len(current_records),
        "loaded_before_first_redecision": True,
        "examples_built": len(examples),
        "corrections_in_scope": sum(
            1 for r in list(prior_records) + list(current_records)
            if "corrected" in (r["severity_status"], r["action_status"])
        ),
        "source_breakdown": dict(Counter(e["source"] for e in examples)),
    }
    write_json(f"{outdir}/feedback_examples.json", examples)
    write_json(f"{outdir}/feedback_store_status.json", status)

    return {
        "examples": examples,
        "operator_status": operator_status,
        "feedback_block": feedback_block,
        "status": status,
    }
