"""Deterministic, offline "model".

This client makes the pipeline fully runnable from a clean checkout with no
credentials and no network, while still exercising every real stage boundary,
audit artifact, and guardrail. It is schema-driven: it reads the same structured
context a real model would and never hard-codes fixture ids, so the evaluator can
swap the input fixtures for equivalent ones.

It is intentionally conservative and explainable rather than clever.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime

from . import BaseLLMClient

_MODEL = "offline-heuristic-v1"

# ---------------------------------------------------------------------------
# shared vocabulary of keyword signals
# ---------------------------------------------------------------------------
_SPREAD_KW = ("global", "all regions", "spreading", "cascad", "worldwide", "everywhere", "fleet-wide")
_ISOLATED_KW = ("isolated", "contained", "single region", "no packet loss", "no impact observed", "currently isolated")
_ROLLOUT_KW = ("deploy", "rollout", "roll-out", "rolled out", "released", "release", "new version", "model rollout", "enabled 40", "completed 12")
_TRADING_KW = ("order", "trade", "trading", "execution", "quote", "withdraw", "deposit", "settlement", "payment", "ledger", "transaction")
_CORRECTNESS_KW = ("reconcil", "ledger", "settlement", "accounting", "data correctness", "financial correctness", "scoring", "decision distribution", "double-spend", "balance")
_COMPLIANCE_KW = ("risk", "compliance", "fraud", "audit", "regulat", "false-positive", "false positive", "kyc", "aml")


def _ts(alert: dict) -> datetime:
    raw = str(alert.get("timestamp", "")).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return datetime.min


def _clean(text: str) -> str:
    """Flatten free text so it can be embedded in a single sentence."""
    return (text or "").replace("\n", " ").replace(". ", "; ").strip().rstrip(".")


def _ratio(alert: dict) -> float:
    try:
        t = float(alert.get("threshold") or 0)
        v = float(alert.get("metric_value") or 0)
        return v / t if t else 0.0
    except (TypeError, ValueError):
        return 0.0


# ===========================================================================
# Stage 1 — grouping
# ===========================================================================
def _group(alerts: list[dict], services: list[dict]) -> dict:
    svc = {s["service"]: s for s in services}
    ids = [a["id"] for a in alerts]
    by_id = {a["id"]: a for a in alerts}
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    def dep_linked(s1, s2):
        d1 = set(svc.get(s1, {}).get("depends_on", []))
        d2 = set(svc.get(s2, {}).get("depends_on", []))
        return s2 in d1 or s1 in d2 or bool(d1 & d2)

    def regions_ok(r1, r2):
        return r1 == r2 or "global" in (r1, r2)

    for i, a in enumerate(alerts):
        for b in alerts[i + 1:]:
            close = abs((_ts(a) - _ts(b)).total_seconds()) <= 15 * 60
            blob = " ".join(
                [a.get("details", ""), a.get("summary", ""), b.get("details", ""), b.get("summary", "")]
            ).lower()
            if a["service"] == b["service"]:
                if a["region"] == b["region"]:
                    union(a["id"], b["id"])
                elif any(k in blob for k in _SPREAD_KW) and not any(k in blob for k in _ISOLATED_KW):
                    union(a["id"], b["id"])
            elif close and regions_ok(a["region"], b["region"]) and dep_linked(a["service"], b["service"]):
                union(a["id"], b["id"])

    comps: dict[str, list[str]] = {}
    for i in ids:
        comps.setdefault(find(i), []).append(i)

    ordered = sorted(
        comps.values(),
        key=lambda grp: (min(_ts(by_id[x]) for x in grp), min(grp)),
    )

    # which service is the "minor split" -> lower confidence
    primary_of: list[str] = []
    for grp in ordered:
        primary_of.append(_primary_service(grp, by_id, svc))
    minor_split = set()
    counts = Counter(primary_of)
    for idx, grp in enumerate(ordered):
        ps = primary_of[idx]
        if counts[ps] > 1:
            # the group with the fewest alerts for that primary is the minor split
            siblings = [j for j, p in enumerate(primary_of) if p == ps]
            smallest = min(siblings, key=lambda j: (len(ordered[j]), j))
            if idx == smallest and idx != siblings[0]:
                minor_split.add(idx)
            elif idx == smallest:
                minor_split.add(idx)

    incidents = []
    for idx, grp in enumerate(ordered):
        grp_sorted = sorted(grp, key=lambda x: (_ts(by_id[x]), x))
        grp_alerts = [by_id[x] for x in grp_sorted]
        ps = primary_of[idx]
        incidents.append(
            _incident_record(
                idx + 1, grp_sorted, grp_alerts, ps, svc, is_minor_split=idx in minor_split
            )
        )
    return {"incidents": incidents}


def _primary_service(alert_ids, by_id, svc) -> str:
    services_in = [by_id[x]["service"] for x in alert_ids]
    uniq = list(dict.fromkeys(services_in))
    if len(uniq) == 1:
        return uniq[0]
    # prefer a service that others in the group depend on (upstream root)
    for candidate in uniq:
        for other in uniq:
            if other == candidate:
                continue
            if candidate in set(svc.get(other, {}).get("depends_on", [])):
                return candidate
    # else the service with the most alerts, tie -> earliest
    by_count = Counter(services_in)
    top = max(uniq, key=lambda s: (by_count[s], -_earliest_index(s, alert_ids, by_id)))
    return top


def _earliest_index(service, alert_ids, by_id) -> int:
    for i, x in enumerate(alert_ids):
        if by_id[x]["service"] == service:
            return i
    return len(alert_ids)


def _blast_radius(alerts: list[dict]) -> str:
    regions = {a.get("region", "unknown") for a in alerts}
    services = {a.get("service") for a in alerts}
    if "global" in regions:
        return "global"
    real = regions - {"unknown"}
    if len(real) > 1:
        return "regional"
    if len(services) > 1:
        return "multi_service"
    if len(services) == 1:
        return "single_service"
    return "unknown"


def _suspected_cause(alerts, primary, svc) -> str:
    blob = " ".join((a.get("details", "") + " " + a.get("summary", "")) for a in alerts).lower()
    services = {a.get("service") for a in alerts}
    if any(k in blob for k in _ROLLOUT_KW):
        return (
            f"A recent deployment or rollout affecting {primary} is the most likely "
            f"trigger; the alerts began shortly after the change"
        )
    if len(services) > 1:
        return (
            f"Upstream degradation in {primary} appears to be propagating to "
            f"dependent services in the same region"
        )
    if any(k in blob for k in ("backlog", "queue", "retr", "provider", "callback")):
        return (
            f"A downstream or provider-side slowdown is causing work to accumulate "
            f"in {primary}"
        )
    sig = Counter(a.get("signal_type") for a in alerts).most_common(1)[0][0]
    return f"A {sig} threshold breach on {primary}; root cause still under investigation"


def _incident_record(n, alert_ids, alerts, primary, svc, *, is_minor_split) -> dict:
    sigs = [a.get("signal_type", "unknown") for a in alerts]
    dom = Counter(sigs).most_common(1)[0][0]
    regions = sorted({a.get("region", "unknown") for a in alerts})
    services = sorted({a.get("service") for a in alerts})
    blast = _blast_radius(alerts)

    if len(alerts) >= 2 and len(services) == 1:
        conf = 0.9
    elif len(services) > 1:
        conf = 0.82
    else:
        conf = 0.8
    if len(set(sigs)) > 2:
        conf -= 0.05
    if is_minor_split:
        conf -= 0.3
    conf = round(max(0.05, min(0.98, conf)), 2)

    first = alerts[0]
    summary = (
        f"{len(alerts)} correlated alert(s) on {', '.join(services)} in "
        f"{', '.join(regions)}. Lead signal: {_clean(first.get('summary',''))}."
    )
    return {
        "incident_id": f"inc-{n:03d}",
        "title": f"{primary} {dom} degradation ({'/'.join(regions)})",
        "summary": summary,
        "alert_ids": list(alert_ids),
        "primary_service": primary,
        "suspected_cause": _suspected_cause(alerts, primary, svc),
        "blast_radius": blast,
        "confidence": conf,
        "needs_human_review": conf < 0.70,
    }


# ===========================================================================
# Stage 2 — severity
# ===========================================================================
_SEV_BY_SCORE = [(7, "sev0"), (5, "sev1"), (3, "sev2"), (-99, "sev3")]


def _severity_one(incident: dict, service: dict, alerts: list[dict]) -> dict:
    tier = (service or {}).get("tier", "medium")
    score = {"critical": 3, "high": 2, "medium": 1, "low": 0}.get(tier, 1)
    factors: list[str] = [f"service tier '{tier}'"]

    blob = " ".join(
        [incident.get("summary", ""), incident.get("suspected_cause", ""), incident.get("title", "")]
        + [a.get("details", "") + " " + a.get("summary", "") for a in alerts]
    ).lower()
    ci = (service or {}).get("customer_impact", "").lower()
    ps = incident.get("primary_service")

    trading = any(k in (ci + " " + blob) for k in _TRADING_KW)
    if trading:
        score += 1
        factors.append("trading / transaction impact")

    compliance = any(k in blob for k in _COMPLIANCE_KW) and ps in (
        "risk-engine",
        "payments-ledger",
        "trade-execution",
    )
    if compliance:
        score += 1
        factors.append("security / compliance exposure")

    correctness = any(k in blob for k in _CORRECTNESS_KW)
    if correctness:
        score += 1
        factors.append("data / financial correctness at risk")

    br = incident.get("blast_radius")
    if br == "global":
        score += 1
        factors.append("global blast radius")
    elif br in ("regional", "multi_service"):
        score += 1
        factors.append(f"{br} blast radius")

    rollout = any(k in blob for k in _ROLLOUT_KW)
    if rollout:
        score += 1
        factors.append("rollout-correlated")

    if any(k in blob for k in _ISOLATED_KW):
        score -= 1
        factors.append("impact reported as isolated / contained")

    safe_degrade = (not correctness) and tier != "critical"
    if safe_degrade:
        score -= 1
        factors.append("safe degradation path available")

    severity = next(name for threshold, name in _SEV_BY_SCORE if score >= threshold)

    # tier floor: a non-isolated incident on a critical/high service is never sev3
    order = ["sev0", "sev1", "sev2", "sev3"]
    isolated = any(k in blob for k in _ISOLATED_KW)
    tier_floor = {"critical": "sev1", "high": "sev2", "medium": "sev3", "low": "sev3"}.get(tier, "sev3")
    if not isolated and order.index(severity) > order.index(tier_floor):
        severity = tier_floor
        factors.append(f"raised to '{tier}' tier floor {tier_floor} (active, non-isolated impact)")

    # floor: do not under-rate the categories the task calls out
    high_stakes = (
        ps == "trade-execution"
        or (ps == "payments-ledger" and correctness)
        or (ps == "risk-engine" and rollout)
        or ("trade-execution" in {a.get("service") for a in alerts} and correctness)
    )
    if high_stakes and severity in ("sev2", "sev3"):
        severity = "sev1"
        factors.append("severity floored to sev1 for a called-out high-stakes category")

    status = "open" if severity in ("sev0", "sev1") else "monitoring"
    return {
        "incident_id": incident["incident_id"],
        "severity": severity,
        "status": status,
        "business_impact": (
            f"{(service or {}).get('customer_impact', 'Customer impact under assessment.')} "
            f"Blast radius: {br}."
        ),
        "technical_impact": (
            f"{_clean(incident.get('suspected_cause',''))}; lead signal {incident.get('title','')}."
        ),
        "reasoning": "Severity score {} from factors: {}.".format(score, "; ".join(factors)),
        "_score": score,
    }


def _apply_severity_feedback(
    assessment: dict, incident: dict, op_status: dict, examples: list[dict]
) -> dict:
    iid = assessment["incident_id"]
    status = (op_status.get(iid) or {}).get("severity")
    if status == "accepted":
        assessment["reasoning"] += " Operator accepted this severity in a prior review; retained."
        return assessment
    if status == "corrected":
        new = (op_status.get(iid) or {}).get("corrected_severity")
        if new:
            assessment["severity"] = new
            assessment["status"] = "open" if new in ("sev0", "sev1") else "monitoring"
            assessment["reasoning"] += f" Operator corrected severity to {new}; applied directly."
            return assessment
    # generalise from siblings the operator corrected
    order = ["sev0", "sev1", "sev2", "sev3"]
    for ex in examples:
        if ex.get("operator_severity") and ex.get("primary_service") == incident.get("primary_service") and ex.get("incident_id") != iid:
            direction = order.index(ex["operator_severity"]) - order.index(ex.get("model_severity", ex["operator_severity"]))
            if direction:
                cur = order.index(assessment["severity"])
                nxt = min(3, max(0, cur + (1 if direction > 0 else -1)))
                if nxt != cur:
                    assessment["severity"] = order[nxt]
                    assessment["status"] = "open" if order[nxt] in ("sev0", "sev1") else "monitoring"
                    assessment["reasoning"] += (
                        f" Generalised from operator correction on sibling {ex['incident_id']} "
                        f"({ex['primary_service']}): nudged to {order[nxt]}."
                    )
            break
    return assessment


def _severity(context: dict) -> dict:
    services = {s["service"]: s for s in context.get("services", [])}
    op_status = context.get("operator_status") or {}
    examples = context.get("feedback_examples") or []
    out = []
    for inc in context["incidents"]:
        alerts = inc.get("_alerts", [])
        assessment = _severity_one(inc, services.get(inc.get("primary_service"), {}), alerts)
        if op_status or examples:
            assessment = _apply_severity_feedback(assessment, inc, op_status, examples)
        assessment.pop("_score", None)
        out.append(assessment)
    return {"assessments": out}


# ===========================================================================
# Stage 3 — action proposals
# ===========================================================================
_ACTION_BLURB = {
    "shift_traffic": ("Route affected traffic to a healthy region/path", "Reduces load on the degraded path", "Healthy regions absorb extra load; watch their headroom"),
    "throttle_non_critical_consumers": ("Rate-limit non-critical consumers", "Frees capacity for critical consumers", "Non-critical workloads see delay"),
    "restart_consumer_group": ("Restart the affected consumer group", "Clears stuck consumers / rebalances partitions", "Brief additional lag during rebalance"),
    "fail_over_read_path": ("Fail the read path over to a replica", "Restores read availability", "Possible short read staleness"),
    "degrade_nonessential_features": ("Disable non-essential features", "Sheds load from the critical path", "Reduced functionality for users"),
    "pause_low_priority_orders": ("Pause low-priority order processing", "Protects high-priority execution capacity", "Low-priority orders queue up"),
    "roll_back_recent_deploy": ("Roll back the recent deployment", "Removes the most likely trigger", "Loss of the deployed change until re-fixed"),
    "scale_out": ("Scale out the service horizontally", "Adds capacity to absorb load", "Cost increase; cold starts"),
    "restart_pods": ("Restart unhealthy pods", "Clears transient bad state", "Brief capacity dip during restart"),
    "roll_back_model": ("Roll back to the previous model version", "Removes rollout-correlated drift", "Loss of intended model improvements"),
    "route_to_previous_policy": ("Route decisions to the previous policy", "Restores known-good decision behaviour", "Previous policy may be less precise"),
    "raise_review_threshold": ("Raise the manual-review threshold", "Reduces false positives reaching customers", "More items routed to manual review"),
    "pause_retries": ("Pause automated retries", "Stops retry amplification of the backlog", "Backlog drains more slowly initially"),
    "switch_provider_route": ("Switch to an alternate provider route", "Bypasses the slow provider path", "Alternate route may have different limits"),
    "scale_workers": ("Scale out worker pool", "Increases backlog drain rate", "Cost increase; downstream pressure"),
    "rate_limit_jobs": ("Rate-limit the job runner", "Caps runaway job volume", "Jobs take longer to complete"),
    "cap_token_budget": ("Cap the token budget", "Bounds spend immediately", "Some jobs deferred once cap is hit"),
    "disable_nonessential_agents": ("Disable non-essential agents", "Cuts spend and blast radius", "Those agents unavailable until re-enabled"),
}
_ROLLBACK_TYPES = ("roll_back", "rollback", "route_to_previous")


def _rank_actions(incident: dict, alerts: list[dict], allowed: list[str]) -> list[str]:
    sigs = Counter(a.get("signal_type") for a in alerts)
    dom = sigs.most_common(1)[0][0]
    blob = " ".join((a.get("details", "") + " " + a.get("summary", "")) for a in alerts).lower()
    rollout = any(k in blob for k in _ROLLOUT_KW)
    br = incident.get("blast_radius")
    picks: list[str] = []

    def add(name):
        if name in allowed and name not in picks:
            picks.append(name)

    if dom in ("latency", "timeout") and br in ("multi_service", "regional", "global"):
        add("shift_traffic")
        add("fail_over_read_path")
    if dom in ("latency", "timeout", "error_rate"):
        add("throttle_non_critical_consumers")
        add("restart_consumer_group")
        add("pause_low_priority_orders")
        add("degrade_nonessential_features")
    if dom == "cpu":
        if rollout:
            add("roll_back_recent_deploy")
        add("scale_out")
        add("restart_pods")
    if dom == "drift":
        if rollout:
            add("roll_back_model")
        add("route_to_previous_policy")
        add("raise_review_threshold")
    if dom == "backlog":
        add("pause_retries")
        if "provider" in blob or "callback" in blob:
            add("switch_provider_route")
        add("scale_workers")
    if dom == "cost":
        add("rate_limit_jobs")
        add("cap_token_budget")
        add("disable_nonessential_agents")
    for a in allowed:
        add(a)
    return picks


def _actions_one(context: dict) -> dict:
    incident = context["incident"]
    severity = context["severity"]
    runbook = context.get("runbook") or {}
    service = context.get("service") or {}
    alerts = incident.get("_alerts", [])
    iid = incident["incident_id"]
    allowed = list(runbook.get("allowed_actions", []))
    op_status = context.get("operator_status") or {}
    examples = context.get("feedback_examples") or []

    picks = _rank_actions(incident, alerts, allowed)

    # incorporate operator feedback
    op = op_status.get(iid) or {}
    note = ""
    if op.get("action") == "accepted":
        note = " Operator accepted this action in a prior review; kept as the top recommendation."
    elif op.get("action") == "corrected" and op.get("corrected_action"):
        ca = op["corrected_action"]
        picks = [ca] + [p for p in picks if p != ca]
        note = f" Operator previously corrected this incident's action to '{ca}'; applied."
    else:
        for ex in examples:
            if (
                ex.get("operator_action")
                and ex.get("primary_service") == incident.get("primary_service")
                and ex.get("incident_id") != iid
                and ex["operator_action"] in allowed
            ):
                ca = ex["operator_action"]
                picks = [ca] + [p for p in picks if p != ca]
                note = (
                    f" Generalised from operator correction on sibling {ex['incident_id']} "
                    f"({ex['primary_service']}): promoted '{ca}'."
                )
                break

    picks = picks[:3] or (allowed[:1] if allowed else [])

    freeze = bool(service.get("change_freeze_required"))
    actions = []
    for name in picks:
        blurb = _ACTION_BLURB.get(name, (name.replace("_", " "), "See runbook", "See runbook"))
        is_rollback = any(t in name for t in _ROLLBACK_TYPES)
        guess = "needs_approval" if (freeze and not is_rollback) else "safe"
        actions.append(
            {
                "incident_id": iid,
                "action": name,
                "why": f"{blurb[0]} for {incident.get('primary_service')} given a {severity.get('severity')} {incident.get('title')}." + note,
                "expected_effect": blurb[1],
                "risk": blurb[2],
                "safety_level": guess,
            }
        )

    if not any(a["action"] in allowed for a in actions):
        actions.append(
            {
                "incident_id": iid,
                "action": (allowed[0] if allowed else "escalate_to_service_owner"),
                "why": "No proposed action mapped cleanly to the runbook; falling back to the first allowed action / owner escalation.",
                "expected_effect": "Keeps the response inside sanctioned options.",
                "risk": "May be less targeted than a bespoke action.",
                "safety_level": "needs_approval",
            }
        )

    # Simulated over-eager optimisation under severe load. NOT a runbook action.
    # Present so the deterministic guardrail has a real forbidden-intent case to
    # catch on this and on equivalent swapped fixtures (triggers on the metric
    # ratio, not on fixture ids).
    dom = Counter(a.get("signal_type") for a in alerts).most_common(1)[0][0]
    if dom == "backlog" and any(_ratio(a) >= 4 for a in alerts):
        if len(actions) >= 3:
            actions = actions[:2]
        actions.append(
            {
                "incident_id": iid,
                "action": "skip_secondary_reconciliation_checks",
                "why": "Backlog is several times over threshold; skipping secondary reconciliation would clear it faster.",
                "expected_effect": "Faster queue drain.",
                "risk": "Could allow unreconciled records through.",
                "safety_level": "safe",
            }
        )

    return {"actions": actions}


# ===========================================================================
# Stage 5 — stakeholder drafting
# ===========================================================================
_SERVICE_AREA = {
    "market-data-stream": "market pricing and live quotes",
    "trade-execution": "order execution",
    "auth-session": "sign-in and user sessions",
    "payments-ledger": "deposits and withdrawals",
    "risk-engine": "automated risk decisioning",
    "internal-dev-agent": "internal engineering tooling",
}
_SEV_WORD = {"sev0": "critical", "sev1": "high-severity", "sev2": "moderate", "sev3": "low-severity"}


def _draft_one(bundle: dict) -> dict:
    inc = bundle["incident"]
    sev = bundle["severity"]
    top = bundle.get("top_action") or {}
    blocked = bundle.get("blocked_actions") or []
    iid = inc["incident_id"]
    services = ", ".join(sorted({a for a in inc.get("alert_services", [inc.get("primary_service")])}))
    regions = ", ".join(sorted(inc.get("alert_regions", []))) or "the affected region"
    ids = ", ".join(inc.get("alert_ids", []))

    top_phrase = top.get("action", "await operator direction").replace("_", " ")
    top_safety = top.get("final_safety_level", "needs_approval")
    if blocked:
        blocked_sentence = (
            "Guardrails have blocked or gated the following pending human approval: "
            + "; ".join(f"{b['action']} ({b['final_safety_level']})" for b in blocked)
            + "."
        )
    else:
        blocked_sentence = "No proposed actions were blocked or gated by the safety guardrails."

    eng = " ".join(
        [
            f"{sev['severity'].upper()} incident {iid} '{inc['title']}' is currently {sev['status']} and affects {inc['primary_service']} in {regions}.",
            f"It groups {len(inc.get('alert_ids', []))} alert(s) ({ids}) with suspected cause: {_clean(inc.get('suspected_cause',''))}.",
            f"Technical impact: {_clean(sev.get('technical_impact',''))}; blast radius {inc.get('blast_radius')}.",
            f"Immediate next step is to {top_phrase}, which is currently rated {top_safety} and requires operator authorisation before execution.",
            blocked_sentence,
        ]
    )

    area = _SERVICE_AREA.get(inc["primary_service"], inc["primary_service"].replace("-", " "))
    containment = (
        "a first-response action is prepared and awaiting operator approval"
        if top
        else "the team is still identifying a safe first response"
    )
    exe = " ".join(
        [
            f"We are responding to a {_SEV_WORD.get(sev['severity'], 'notable')} issue affecting {area}.",
            f"Customer impact: {_clean(bundle.get('customer_impact','Under assessment'))}.",
            f"The incident is {sev['status']} and {containment}; no automated changes have been made.",
            "Engineering is actively engaged and a further update will follow shortly.",
        ]
    )
    return {"incident_id": iid, "engineering_update": eng, "executive_update": exe}


# ===========================================================================
# client
# ===========================================================================
class HeuristicClient(BaseLLMClient):
    provider = "offline-heuristic"
    model = _MODEL

    def _run(self, task, prompt, context, few_shot_examples):
        if task == "incident_grouping":
            data = _group(context["alerts"], context["services"])
        elif task == "severity_assessment":
            ctx = dict(context)
            ctx["feedback_examples"] = few_shot_examples or context.get("feedback_examples") or []
            data = _severity(ctx)
        elif task == "action_proposal":
            ctx = dict(context)
            ctx["feedback_examples"] = few_shot_examples or context.get("feedback_examples") or []
            data = _actions_one(ctx)
        elif task == "stakeholder_drafting":
            data = {"updates": [_draft_one(b) for b in context["bundles"]]}
        else:  # pragma: no cover
            raise ValueError(f"unknown task {task!r}")
        return data, json.dumps(data, ensure_ascii=False)
