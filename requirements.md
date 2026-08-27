## BUILD

Build a replayable incident-command pipeline for a high-availability platform that ingests production alerts, service metadata, and runbook excerpts; performs incident clustering and severity assessment; proposes containment actions; generates an executive update and an engineering action plan; accepts operator overrides; and re-runs decisioning using the accumulated feedback.

This is not a one-shot summarisation task. The evaluator will run your solution from a clean checkout, may replace the alert fixtures with equivalent inputs using the same schemas, and will verify that your system separates decision stages, preserves audit artifacts, applies guardrails before recommending actions, and demonstrates how human feedback changes downstream decisions.

The task is intentionally scoped to test implementation and architecture judgment together. A strong solution will show production-quality thinking around reliability, rollback safety, explainability, validation, and operational tradeoffs.

---

## SCENARIO

You are building an internal AI-assisted incident workflow for a platform engineering organisation. Incoming signals are noisy and partially redundant. The system should help engineers rapidly:

- group related alerts into incidents
- estimate business and technical severity
- identify likely blast radius
- recommend safe first-response actions
- draft stakeholder communications
- incorporate operator corrections so future runs improve

The system must be designed so that AI recommendations never directly execute changes. It should produce structured recommendations, confidence, rationale, and safety flags.

---

## INPUT FILES

Your pipeline must read the following files from disk:

- `alerts.json`
- `services.json`
- `runbooks.json`

The evaluator may replace these fixtures with equivalent data using the same schemas.

---

## SAMPLE `alerts.json`

```json
[
  {
    "id": "a-101",
    "timestamp": "2026-05-12T09:00:00Z",
    "service": "market-data-stream",
    "region": "eu-west-1",
    "signal_type": "latency",
    "summary": "p99 latency increased from 35ms to 480ms for live ticks",
    "details": "Consumer lag rising across 4 partitions; packet retransmits elevated.",
    "metric_value": 480,
    "threshold": 120
  },
  {
    "id": "a-102",
    "timestamp": "2026-05-12T09:01:00Z",
    "service": "market-data-stream",
    "region": "eu-west-1",
    "signal_type": "error_rate",
    "summary": "tick publish failures at 7.8%",
    "details": "Retries exhausted for downstream websocket fanout.",
    "metric_value": 7.8,
    "threshold": 1.0
  },
  {
    "id": "a-103",
    "timestamp": "2026-05-12T09:02:00Z",
    "service": "trade-execution",
    "region": "eu-west-1",
    "signal_type": "timeout",
    "summary": "order submission timeout spike",
    "details": "Timeouts increased for requests dependent on latest quote fetch.",
    "metric_value": 11.2,
    "threshold": 2.0
  },
  {
    "id": "a-104",
    "timestamp": "2026-05-12T09:03:00Z",
    "service": "auth-session",
    "region": "ap-south-1",
    "signal_type": "cpu",
    "summary": "CPU sustained above 92%",
    "details": "New deployment completed 12 minutes earlier; login traffic normal.",
    "metric_value": 92,
    "threshold": 80
  },
  {
    "id": "a-105",
    "timestamp": "2026-05-12T09:04:00Z",
    "service": "payments-ledger",
    "region": "global",
    "signal_type": "backlog",
    "summary": "withdrawal reconciliation queue depth exceeded 18,000",
    "details": "Processor retries rising; settlement callback delay from provider observed.",
    "metric_value": 18000,
    "threshold": 4000
  },
  {
    "id": "a-106",
    "timestamp": "2026-05-12T09:05:00Z",
    "service": "risk-engine",
    "region": "eu-west-1",
    "signal_type": "drift",
    "summary": "decision distribution shifted 23% from baseline",
    "details": "Recent model rollout enabled 40 minutes ago; false-positive complaints beginning.",
    "metric_value": 23,
    "threshold": 10
  },
  {
    "id": "a-107",
    "timestamp": "2026-05-12T09:06:00Z",
    "service": "internal-dev-agent",
    "region": "global",
    "signal_type": "cost",
    "summary": "LLM token spend 4.6x daily baseline",
    "details": "Burst correlated with autonomous code review jobs.",
    "metric_value": 4.6,
    "threshold": 2.0
  },
  {
    "id": "a-108",
    "timestamp": "2026-05-12T09:07:00Z",
    "service": "market-data-stream",
    "region": "us-east-1",
    "signal_type": "latency",
    "summary": "p99 latency increased from 28ms to 170ms",
    "details": "No packet loss observed; impact currently isolated.",
    "metric_value": 170,
    "threshold": 120
  }
]
```

---

## SAMPLE `services.json`

```json
[
  {
    "service": "market-data-stream",
    "tier": "critical",
    "owner_team": "Core Trading",
    "depends_on": ["network-edge", "websocket-fanout"],
    "customer_impact": "Delayed or stale pricing may affect trading experience.",
    "change_freeze_required": false
  },
  {
    "service": "trade-execution",
    "tier": "critical",
    "owner_team": "Core Trading",
    "depends_on": ["market-data-stream", "order-router"],
    "customer_impact": "Orders may fail or be delayed.",
    "change_freeze_required": true
  },
  {
    "service": "auth-session",
    "tier": "high",
    "owner_team": "Identity Platform",
    "depends_on": ["session-store"],
    "customer_impact": "Users may have trouble signing in or maintaining sessions.",
    "change_freeze_required": false
  },
  {
    "service": "payments-ledger",
    "tier": "critical",
    "owner_team": "Payments Platform",
    "depends_on": ["settlement-provider", "ledger-db"],
    "customer_impact": "Deposits or withdrawals may be delayed.",
    "change_freeze_required": true
  },
  {
    "service": "risk-engine",
    "tier": "critical",
    "owner_team": "Risk & Compliance",
    "depends_on": ["feature-store", "model-serving"],
    "customer_impact": "Risk decisions may become inaccurate or inconsistent.",
    "change_freeze_required": true
  },
  {
    "service": "internal-dev-agent",
    "tier": "medium",
    "owner_team": "Developer Productivity",
    "depends_on": ["llm-gateway", "job-runner"],
    "customer_impact": "Internal engineering workflows may slow down.",
    "change_freeze_required": false
  }
]
```

---

## SAMPLE `runbooks.json`

```json
[
  {
    "service": "market-data-stream",
    "allowed_actions": ["shift_traffic", "throttle_non_critical_consumers", "restart_consumer_group"],
    "forbidden_actions": ["drop_live_ticks", "disable_price_validation"],
    "notes": "Prefer regional isolation before restart. Avoid actions that can produce stale or missing tick streams globally."
  },
  {
    "service": "trade-execution",
    "allowed_actions": ["degrade_nonessential_features", "pause_low_priority_orders", "fail_over_read_path"],
    "forbidden_actions": ["bypass_risk_checks", "force_accept_orders"],
    "notes": "Any action affecting execution flow must preserve risk and audit controls."
  },
  {
    "service": "auth-session",
    "allowed_actions": ["roll_back_recent_deploy", "scale_out", "restart_pods"],
    "forbidden_actions": ["disable_authentication", "extend_session_validity_globally"],
    "notes": "If issue follows a deployment and traffic is normal, rollback is preferred over speculative tuning."
  },
  {
    "service": "payments-ledger",
    "allowed_actions": ["pause_retries", "switch_provider_route", "scale_workers"],
    "forbidden_actions": ["mark_withdrawals_complete_without_confirmation", "skip_reconciliation"],
    "notes": "Do not create accounting inconsistency for the sake of clearing backlog."
  },
  {
    "service": "risk-engine",
    "allowed_actions": ["roll_back_model", "route_to_previous_policy", "raise_review_threshold"],
    "forbidden_actions": ["disable_risk_scoring", "auto_approve_all_transactions"],
    "notes": "If rollout-correlated drift is present, rollback or controlled policy fallback is safer than threshold-only tuning."
  },
  {
    "service": "internal-dev-agent",
    "allowed_actions": ["rate_limit_jobs", "cap_token_budget", "disable_nonessential_agents"],
    "forbidden_actions": ["grant_broader_repo_permissions", "auto_merge_changes"],
    "notes": "Optimise spend and agent scope without expanding blast radius."
  }
]
```

---

## CONTROLLED VOCABULARIES

Define and enforce these vocabularies in code.

Allowed incident statuses:

```text
open
monitoring
mitigated
closed
```

Allowed severities:

```text
sev0
sev1
sev2
sev3
```

Allowed blast radius values:

```text
single_service
multi_service
regional
global
unknown
```

Allowed action safety levels:

```text
safe
needs_approval
forbidden
```

Allowed operator review actions:

```text
accepted
corrected
skipped
```

---

## PIPELINE STAGES

Your implementation must enforce these stages in code:

```text
INIT
 -> INPUTS_LOADED
 -> PRIOR_FEEDBACK_LOADED, if available
 -> INCIDENT_GROUPING_COMPLETE
 -> SEVERITY_ASSESSMENT_COMPLETE
 -> ACTION_PROPOSALS_COMPLETE
 -> SAFETY_GUARDRAILS_COMPLETE
 -> STAKEHOLDER_DRAFTING_COMPLETE
 -> OPERATOR_REVIEW_COLLECTED
 -> FEEDBACK_EXAMPLES_BUILT
 -> REDECISION_COMPLETE
 -> BEFORE_AFTER_COMPARISON_COMPLETE
 -> ANALYTICS_GENERATED
 -> VALIDATION_COMPLETE
 -> RESULTS_FINALISED
```

Final output must not be produced before alerts have been grouped, severities assessed, actions proposed, safety-checked, reviewed by an operator, and compared before/after feedback.

---

## MUST COMPLETE

### 1. Incident Grouping

Make one Stage 1 LLM call using all alerts plus service metadata.

The prompt must include:

- all alerts
- relevant service metadata
- output JSON schema
- instruction to cluster potentially related alerts into incidents
- instruction to assign one primary incident per alert
- instruction to produce confidence from `0.0` to `1.0`

Each grouped incident must include:

```json
{
  "incident_id": "inc-001",
  "title": "string",
  "summary": "string",
  "alert_ids": ["a-101", "a-102", "a-103"],
  "primary_service": "market-data-stream",
  "suspected_cause": "string",
  "blast_radius": "multi_service",
  "confidence": 0.84,
  "needs_human_review": false
}
```

Incidents with confidence below `0.70` must be flagged with `needs_human_review: true`.

Save output to `incident_groups.json`.

---

### 2. Severity Assessment

Make a separate Stage 2 LLM call.

This call must receive all grouped incidents, plus service metadata, and must explicitly define severity criteria in the prompt, including:

- customer-facing impact
- trading or transaction impact
- security or compliance exposure
- breadth of affected services or regions
- whether the issue is rollout-correlated
- whether safe degradation exists
- whether data correctness or financial correctness may be at risk

Each severity decision must include:

```json
{
  "incident_id": "inc-001",
  "severity": "sev1",
  "status": "open",
  "business_impact": "string",
  "technical_impact": "string",
  "reasoning": "string"
}
```

Save output to `severity_assessments.json`.

For the public fixture, incidents involving trade execution dependency failure, payments reconciliation backlog, or rollout-related risk-engine drift should not be treated as low-importance events without strong justification.

---

### 3. Action Proposals

Make a separate Stage 3 LLM call for each incident.

This stage must use:

- grouped incident data
- severity assessment
- matching runbook excerpt

The model must propose 1 to 3 recommended first-response actions.

Each proposed action must include:

```json
{
  "incident_id": "inc-001",
  "action": "shift_traffic",
  "why": "string",
  "expected_effect": "string",
  "risk": "string",
  "safety_level": "safe"
}
```

At least one proposed action per incident must be traceable to the allowed actions for the relevant service unless the model explicitly explains why no listed action fits.

Save output to `action_proposals.json`.

---

### 4. Safety Guardrails

Implement a deterministic safety pass in code after Stage 3.

This pass must check every proposed action against runbook constraints and service metadata.

Minimum checks:

- actions listed in `forbidden_actions` must be marked `forbidden`
- if `change_freeze_required` is `true`, any non-rollback or traffic-altering action must be escalated to `needs_approval`
- actions that imply bypassing risk, reconciliation, auth, or audit controls must be rejected even if phrased differently
- output must preserve the original model proposal and the deterministic safety verdict

Each safety result must include:

```json
{
  "incident_id": "inc-001",
  "action": "shift_traffic",
  "model_safety_level": "safe",
  "final_safety_level": "needs_approval",
  "guardrail_reasons": ["change freeze on critical dependency path"]
}
```

Save output to `safety_review.json`.

---

### 5. Stakeholder Drafting

Generate two separate outputs per incident in Stage 4:

- an engineering commander update
- an executive update

You may use one batched call or separate calls, but the drafting stage must be distinct from prior stages.

Engineering update requirements:

- 4 to 6 sentences
- include affected systems, likely impact, immediate next steps, and any blocked actions due to safety checks

Executive update requirements:

- 3 to 5 sentences
- concise, low-jargon, no speculation stated as fact
- mention customer or business impact and current containment posture

Each record must include:

```json
{
  "incident_id": "inc-001",
  "engineering_update": "string",
  "executive_update": "string"
}
```

Save output to `stakeholder_updates.json`.

---

### 6. Operator Review Interface

After the first full pass, display each incident in the terminal with:

- grouped alerts
- assigned severity
- top proposed action
- final safety verdict

For each incident, ask the operator to review both:

1. severity
2. top action

Supported inputs:

```text
severity correct? (y / correct to: [sev0|sev1|sev2|sev3] / skip)
action correct? (y / correct to: [action_name] / skip)
```

Save feedback to `operator_feedback.jsonl`.

Each record must include:

```json
{
  "incident_id": "inc-001",
  "original_severity": "sev1",
  "corrected_severity": "sev2",
  "original_action": "shift_traffic",
  "corrected_action": "restart_consumer_group",
  "action_status": "corrected",
  "severity_status": "corrected",
  "timestamp": "ISO-8601 timestamp"
}
```

---

### 7. Feedback-Driven Re-decision

Build a few-shot or retrieval-style feedback block from operator feedback.

Re-run incident grouping only if needed, but you must re-run at least severity assessment and action proposal with the feedback examples injected into the prompts.

Save outputs to:

- `severity_redecision.json`
- `actions_redecision.json`

Produce a before/after comparison showing:

- original severity
- operator severity, if provided
- re-decided severity
- original top action
- operator action, if provided
- re-decided top action
- whether output moved toward operator feedback

Compute an agreement delta using only incidents where the operator provided accepted or corrected labels.

Save final combined output to `incident_command_output.json`.

---

## SHOULD ATTEMPT

### 8. Analytics Summary

Generate `analytics_summary.json` and a terminal summary including:

- number of incidents formed from raw alerts
- average grouping confidence
- severity distribution
- blast radius distribution
- count of incidents flagged for human review
- count of proposed actions by safety level
- number of operator corrections
- before/after agreement delta
- services most frequently involved in incidents

---

### 9. Prior Feedback Store

Persist operator feedback in `operator_feedback.jsonl`.

On future runs, automatically load prior feedback and use it before the first re-decision stage.

Record whether prior feedback was loaded in `feedback_store_status.json`.

---

### 10. Incident Escalation Bundle

Generate a machine-readable escalation bundle per `sev0` or `sev1` incident:

```json
{
  "incident_id": "inc-001",
  "owner_team": "Core Trading",
  "page": true,
  "suggested_channel": "#incident-core-trading",
  "summary": "string"
}
```

Save to `escalation_bundle.json`.

---

## REQUIRED ARTIFACTS

Your repository must produce:

- `alerts.json`
- `services.json`
- `runbooks.json`
- `incident_groups.json`
- `severity_assessments.json`
- `action_proposals.json`
- `safety_review.json`
- `stakeholder_updates.json`
- `operator_feedback.jsonl`
- `severity_redecision.json`
- `actions_redecision.json`
- `incident_command_output.json`
- `analytics_summary.json`, if attempted
- `feedback_store_status.json`, if attempted
- `escalation_bundle.json`, if attempted
- `llm_calls.jsonl`

---

## `llm_calls.jsonl` REQUIREMENTS

Log one JSON object per LLM call.

Each record must include:

```json
{
  "stage": "string",
  "incident_id": "string | null",
  "timestamp": "ISO-8601 timestamp",
  "provider": "string",
  "model": "string",
  "prompt_hash": "string",
  "input_artifacts": ["path"],
  "output_artifact": "path",
  "few_shot_examples_included": false
}
```

There must be separate records for:

- incident grouping
- severity assessment
- each incident action proposal call
- stakeholder drafting
- each re-decision call

For re-decision calls, `few_shot_examples_included` must be `true` when feedback is injected.

---

## VALIDATION REQUIREMENTS

The repository must include a validation command, for example:

```bash
make validate
```

or:

```bash
python validate.py
```

The validation command must check that:

- required artifacts exist
- JSON and JSONL files are valid
- all alerts are assigned to exactly one incident
- controlled vocabularies are enforced
- confidence values are between `0.0` and `1.0`
- incidents below `0.70` confidence are flagged for human review
- severity assessment is a separate stage from grouping
- action proposals are separate per incident
- deterministic safety checks override forbidden or risky actions
- stakeholder updates exist for every incident
- operator feedback is persisted
- re-decision includes feedback-derived examples
- before/after comparison and agreement delta are computed
- LLM call logs contain separate records for required stages

---

## EXECUTION REQUIREMENTS

The evaluator will run the pipeline from a clean checkout.

Generated artifacts may be deleted before evaluation.

The evaluator may replace the input fixtures with equivalent ones using the same schemas.

Static precomputed outputs are not sufficient.

The solution must actually run the staged pipeline and regenerate required artifacts.

---

## TOOLS

Python or TypeScript may be used.

Any LLM provider or AI tooling may be used.

---

## TECHNICAL CONSTRAINTS

- Grouping, severity assessment, action proposal, and stakeholder drafting must be separate stages.
- Safety guardrails must include deterministic logic in code, not only prompt instructions.
- The system must never execute actions; it may only recommend them.
- Operator review must be interactive.
- Re-decision must incorporate operator feedback into later prompts.
- Agreement delta must be computed in code.
- Do not require external production systems, credentials, or proprietary datasets.
- Design for auditability and safe operation in a high-stakes environment.

---

## WHAT WE ARE LOOKING FOR

We care less about polished UI and more about whether you can decompose an AI-assisted operational workflow into safe, testable components with clear contracts, auditable outputs, and sensible escalation logic. Strong submissions will show a practical balance of AI usefulness and deterministic controls.