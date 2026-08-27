"""Prompt construction for every LLM stage.

Each builder returns the *exact* string that is (a) sent to a real provider and
(b) hashed + archived for replay. The offline model ignores the prose and reads
the structured context, but the prompts are written to be genuine so a swap to a
real provider is a config change, not a rewrite.

The task specifies required prompt contents per stage; the assertions at import
time document that contract.
"""

from __future__ import annotations

import json

from .vocab import ACTION_SAFETY, BLAST_RADIUS, INCIDENT_STATUS, SEVERITY


def _j(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


# --- Stage 1: incident grouping ----------------------------------------------------

GROUPING_SCHEMA = {
    "incidents": [
        {
            "incident_id": "inc-001",
            "title": "string",
            "summary": "string",
            "alert_ids": ["a-101", "a-102"],
            "primary_service": "string",
            "suspected_cause": "string",
            "blast_radius": list(BLAST_RADIUS),
            "confidence": "number 0.0-1.0",
            "needs_human_review": "boolean",
        }
    ]
}


def build_grouping_prompt(alerts: list[dict], services: list[dict]) -> str:
    return f"""STAGE 1 — INCIDENT GROUPING

You are clustering noisy production alerts into incidents. Work only from the
data below. Do not propose actions or severities here.

## ALERTS (all {len(alerts)})
{_j(alerts)}

## SERVICE METADATA (dependencies, tiers, ownership)
{_j(services)}

## INSTRUCTIONS
1. Cluster alerts that are potentially related (same service, a dependency edge
   between services, shared region + time window, or a plausible shared root
   cause) into incidents.
2. Every alert MUST be assigned to exactly one primary incident. Do not leave an
   alert unassigned and do not place an alert in two incidents.
3. Give each incident a short title, a one-paragraph summary, the primary
   affected service, a suspected cause, and a blast_radius from
   {list(BLAST_RADIUS)}.
4. Provide confidence as a number from 0.0 to 1.0 for the grouping of that
   incident.
5. Set needs_human_review to true for any incident whose confidence is below
   0.70.

## OUTPUT (JSON only, matching this schema)
{_j(GROUPING_SCHEMA)}
"""


# --- Stage 2: severity assessment ------------------------------------------------

SEVERITY_SCHEMA = {
    "assessments": [
        {
            "incident_id": "inc-001",
            "severity": list(SEVERITY),
            "status": list(INCIDENT_STATUS),
            "business_impact": "string",
            "technical_impact": "string",
            "reasoning": "string",
        }
    ]
}

SEVERITY_CRITERIA = """Assess each incident against ALL of these criteria and
state how each applied in the reasoning field:
  - customer-facing impact (who is affected and how badly)
  - trading or transaction impact (orders, quotes, settlement, withdrawals)
  - security or compliance exposure (risk controls, audit, fraud, regulatory)
  - breadth of affected services or regions (blast radius)
  - whether the issue is rollout-correlated (a recent deploy / model rollout)
  - whether a safe degradation path exists (throttle, pause, fail over, roll back)
  - whether data correctness or financial correctness may be at risk

Severity scale (sev0 = most severe):
  sev0 - critical, broad customer/financial impact, no safe degradation
  sev1 - major impact to a critical capability or correctness/compliance risk
  sev2 - contained or degraded, safe fallback exists, limited blast radius
  sev3 - minor, internal, or already isolated

Incidents involving trade-execution dependency failure, payments reconciliation
backlog, or rollout-correlated risk-engine drift must NOT be rated sev3 without
strong, explicit justification."""


def build_severity_prompt(
    incidents: list[dict], services: list[dict], feedback_block: str | None
) -> str:
    fb = f"\n## OPERATOR FEEDBACK FROM PRIOR RUNS (learn from these)\n{feedback_block}\n" if feedback_block else ""
    return f"""STAGE 2 — SEVERITY ASSESSMENT

Separate stage from grouping. You receive the already-grouped incidents and the
service metadata. Assign severity and an initial status only.

## GROUPED INCIDENTS
{_j(incidents)}

## SERVICE METADATA
{_j(services)}

## SEVERITY CRITERIA
{SEVERITY_CRITERIA}
{fb}
## OUTPUT (JSON only)
{_j(SEVERITY_SCHEMA)}
"""


# --- Stage 3: action proposals --------------------------------------------------

ACTION_SCHEMA = {
    "actions": [
        {
            "incident_id": "inc-001",
            "action": "string (prefer a runbook allowed_action id)",
            "why": "string",
            "expected_effect": "string",
            "risk": "string",
            "safety_level": list(ACTION_SAFETY),
        }
    ]
}


def build_action_prompt(
    incident: dict,
    severity: dict,
    runbook: dict,
    service: dict,
    feedback_block: str | None,
) -> str:
    fb = f"\n## OPERATOR FEEDBACK ON SIMILAR PAST INCIDENTS\n{feedback_block}\n" if feedback_block else ""
    return f"""STAGE 3 — FIRST-RESPONSE ACTION PROPOSAL (single incident)

Propose 1 to 3 first-response actions for THIS incident only. You recommend
only; nothing is executed. Prefer actions drawn from the runbook allowed_actions.
If no listed action fits, you may propose another but must explain why no listed
action is appropriate.

## INCIDENT
{_j(incident)}

## SEVERITY ASSESSMENT
{_j(severity)}

## RUNBOOK EXCERPT FOR {runbook.get('service')}
{_j(runbook)}

## SERVICE METADATA
{_j(service)}
{fb}
## RULES
- At least one proposed action must be traceable to allowed_actions unless you
  explicitly justify why none fit.
- Never propose anything that bypasses risk, reconciliation, authentication, or
  audit controls, even if phrased differently.
- Set safety_level to your best guess from {list(ACTION_SAFETY)}; a deterministic
  guardrail pass will re-check it.

## OUTPUT (JSON only)
{_j(ACTION_SCHEMA)}
"""


# --- Stage 5: stakeholder drafting --------------------------------------------

DRAFTING_SCHEMA = {
    "updates": [
        {
            "incident_id": "inc-001",
            "engineering_update": "string (4-6 sentences)",
            "executive_update": "string (3-5 sentences)",
        }
    ]
}


def build_drafting_prompt(bundles: list[dict]) -> str:
    return f"""STAGE 5 — STAKEHOLDER DRAFTING

For each incident produce two distinct updates.

Engineering commander update: 4 to 6 sentences. Include affected systems, likely
impact, immediate next steps, and any actions blocked or gated by safety checks.

Executive update: 3 to 5 sentences. Concise, low-jargon, no speculation stated as
fact. Mention customer or business impact and the current containment posture.

## PER-INCIDENT INPUT (incident, severity, top action, guardrail outcomes)
{_j(bundles)}

## OUTPUT (JSON only)
{_j(DRAFTING_SCHEMA)}
"""


# Contract assertions -----------------------------------------------------------
assert "cluster" in build_grouping_prompt([], []).lower()
assert "0.70" in build_grouping_prompt([], [])
for _kw in ("customer", "trading", "compliance", "rollout", "degrad", "correctness"):
    assert _kw in build_severity_prompt([], [], None).lower(), _kw
