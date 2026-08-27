"""Stage 4 — deterministic safety guardrails.

No LLM. Every proposed action is re-checked in code against the runbook and
service metadata. The model's own safety guess is preserved; the deterministic
verdict can only make an action *more* restrictive, never less.
"""

from __future__ import annotations

from ..util import write_json
from ..vocab import stricter_safety

STAGE = "SAFETY_GUARDRAILS_COMPLETE"

_ROLLBACK_TYPES = ("roll_back", "rollback", "route_to_previous", "revert")
_TRAFFIC_TYPES = ("shift_traffic", "fail_over", "failover", "switch_provider_route", "reroute", "route_traffic")

_INTENT_VERBS = (
    "bypass", "skip", "disable", "without", "ignore", "suppress", "turn_off",
    "omit", "force", "auto_approve", "auto_merge", "circumvent", "override", "unsafe",
)
_INTENT_CONTROLS = {
    "reconcil": "reconciliation",
    "risk": "risk",
    "scoring": "risk-scoring",
    "audit": "audit",
    "authentication": "authentication",
    "auth": "authentication",
    "validation": "validation",
    "price_valid": "price-validation",
    "review": "manual-review",
    "confirmation": "confirmation",
    "compliance": "compliance",
    "kyc": "compliance",
}
_PARAPHRASE = {
    "mark_withdrawals_complete": "marks withdrawals complete without settlement confirmation (accounting inconsistency)",
    "mark_backlog_cleared": "clears the backlog without reconciliation",
    "force_accept_order": "forces order acceptance, bypassing execution controls",
    "force_accept_orders": "forces order acceptance, bypassing execution controls",
    "auto_approve_all": "auto-approves all transactions, bypassing risk controls",
    "grant_broader": "expands permissions and blast radius",
    "extend_session_validity": "extends session validity globally, weakening authentication controls",
    "disable_price_validation": "disables price validation",
    "drop_live_ticks": "drops live ticks, producing missing/stale market data",
}


_SAFETY_ORDER = {"safe": 0, "needs_approval": 1, "forbidden": 2}


def top_action(safety_results: list[dict]) -> dict | None:
    """Pick the recommended action: safest first, ties broken by proposal order."""
    if not safety_results:
        return None
    return min(
        enumerate(safety_results),
        key=lambda pair: (_SAFETY_ORDER.get(pair[1]["final_safety_level"], 3), pair[0]),
    )[1]


def _norm(action: str) -> str:
    return str(action).strip().lower().replace("-", "_").replace(" ", "_")


def _forbidden_intent(norm: str) -> str | None:
    if any(v in norm for v in _INTENT_VERBS):
        for key, label in _INTENT_CONTROLS.items():
            if key in norm:
                return label
    return None


def _paraphrase(norm: str) -> str | None:
    for frag, reason in _PARAPHRASE.items():
        if frag in norm:
            return reason
    return None


def run(client, proposals, incidents, runbooks, services, *, outdir, out="safety_review.json"):
    rb_by = {r["service"]: r for r in runbooks}
    svc_by = {s["service"]: s for s in services}
    inc_by = {c["incident_id"]: c for c in incidents}

    review: list[dict] = []
    by_incident: dict[str, list[dict]] = {}

    for iid, actions in proposals.items():
        inc = inc_by.get(iid, {})
        ps = inc.get("primary_service")
        # blast-radius aware: consider every service the incident touches, not
        # just the primary. A forbidden action or a change freeze on ANY involved
        # service constrains the response.
        involved = [ps] + [s for s in inc.get("alert_services", []) if s != ps]
        forbidden = {
            _norm(x)
            for s in involved
            for x in rb_by.get(s, {}).get("forbidden_actions", [])
        }
        allowed = {
            _norm(x)
            for s in involved
            for x in rb_by.get(s, {}).get("allowed_actions", [])
        }
        frozen_services = [s for s in involved if svc_by.get(s, {}).get("change_freeze_required")]
        freeze = bool(frozen_services)
        results = []
        for a in actions:
            norm = _norm(a["action"])
            model_level = a.get("safety_level", "needs_approval")
            final = model_level
            reasons: list[str] = []
            sanctioned = norm in allowed and norm not in forbidden

            if norm in forbidden:
                final = "forbidden"
                reasons.append(f"'{norm}' is listed in runbook.forbidden_actions for {ps}")

            # fuzzy intent / paraphrase checks are skipped for an action that is
            # explicitly sanctioned by a runbook (trust the runbook over keywords)
            if not sanctioned:
                intent = _forbidden_intent(norm)
                if intent:
                    final = "forbidden"
                    reasons.append(
                        f"action implies bypassing {intent} controls; rejected regardless of phrasing"
                    )

                para = _paraphrase(norm)
                if para:
                    final = "forbidden"
                    reasons.append(f"recognised unsafe pattern: {para}")

            if final != "forbidden" and freeze:
                is_rollback = any(t in norm for t in _ROLLBACK_TYPES)
                if not is_rollback:
                    final = stricter_safety(final, "needs_approval")
                    is_traffic = any(t in norm for t in _TRAFFIC_TYPES)
                    reasons.append(
                        f"change freeze active on {', '.join(frozen_services)} "
                        f"(in this incident's blast radius): "
                        + ("traffic-altering" if is_traffic else "non-rollback")
                        + " action escalated to needs_approval"
                    )

            final = stricter_safety(final, model_level)
            if final == model_level and not reasons:
                reasons.append("no guardrail triggered; model verdict retained")

            entry = {
                "incident_id": iid,
                "action": a["action"],
                "model_safety_level": model_level,
                "final_safety_level": final,
                "guardrail_reasons": reasons,
                "model_proposal": dict(a),
            }
            results.append(entry)
            review.append(entry)
        by_incident[iid] = results

    write_json(f"{outdir}/{out}", review)
    return by_incident
