# Replayable Incident-Command Pipeline

An AI-assisted incident workflow that ingests production alerts, service metadata,
and runbook excerpts, then runs a **staged, auditable, guardrailed** decision
pipeline: cluster alerts → assess severity → propose first-response actions →
apply deterministic safety guardrails → draft stakeholder updates → collect
operator corrections → **re-decide with that feedback** → compare before/after.

The system **never executes actions**. Every stage emits a structured artifact
with confidence, rationale, and safety flags.

---

## TL;DR — run it

```bash
# Offline, deterministic model, non-interactive operator (CI-friendly)
python run.py --provider heuristic --operator-mode auto --fresh

# Reproducible feedback demo: a scripted operator makes corrections,
# and you can see them move the re-decision
python run.py --provider heuristic --operator-mode script:demo_operator_script.json --fresh

# Real human operator in the terminal
python run.py --provider heuristic --operator-mode interactive

# Real LLM provider (optional)
pip install anthropic
export ANTHROPIC_API_KEY=...        # PowerShell: $env:ANTHROPIC_API_KEY="..."
python run.py --provider anthropic --operator-mode script:demo_operator_script.json --fresh

# Validate every generated artifact
python validate.py

# Unit + e2e tests
pip install pytest && python -m pytest -q
```

`make run` / `make run-demo` / `make run-interactive` / `make validate` / `make test`
wrap the same commands.

Python 3.10+. **No third-party dependencies** for the core pipeline or the
validator — it runs from a clean checkout with no network and no credentials.

---

## Why a "heuristic provider"

The evaluator runs from a clean checkout, may swap the fixtures, and must see the
staged pipeline *actually run* and regenerate artifacts — but must not need
credentials or external systems. Those constraints are in tension for anything
LLM-backed.

Resolution: **all model access goes through one interface**
(`icp/llm/BaseLLMClient`) with two implementations:

| Provider | When | What it does |
|---|---|---|
| `AnthropicClient` | `--provider anthropic`/`auto` **and** `ANTHROPIC_API_KEY` set **and** `anthropic` installed | Sends the real rendered prompt, parses JSON |
| `HeuristicClient` (default) | otherwise | Deterministic, schema-driven reasoning over the *same structured context* |

The prompts in `icp/prompts.py` are genuine and provider-portable (they carry the
required schema + instructions and are hashed & archived every call). Swapping to
a real model is a flag, not a rewrite. The heuristic model is intentionally
**conservative and explainable**, and it is **schema-driven** — it never keys off
fixture ids, so swapped-but-equivalent inputs work (see
`python run.py --indir <dir> --outdir <dir>`).

---

## Architecture

```
run.py ──> icp/pipeline.run_pipeline ──> drives icp/statemachine (STAGES)
                     │
   ┌─────────────────┼───────────────────────────────────────────────┐
   │  each stage = one module in icp/stages/, one artifact on disk    │
   └─────────────────┼───────────────────────────────────────────────┘
 grouping → severity → actions → guardrails → drafting → operator
        → feedback(examples) → redecision → comparison → analytics → escalation
```

### Stage / contract table

| # | Stage | Kind | Input | Output artifact | Key invariants enforced *in code* |
|---|---|---|---|---|---|
| 1 | Incident grouping | 1 LLM call | alerts + services | `incident_groups.json` | every alert in **exactly one** incident (deterministic repair); `confidence∈[0,1]`; `confidence<0.70 ⇒ needs_human_review` |
| 2 | Severity assessment | 1 LLM call (separate prompt) | incidents + services + rubric | `severity_assessments.json` | `severity`/`status` vocab; floor so trade-exec / payments-reconciliation / rollout-risk-drift are never sev3 |
| 3 | Action proposals | **1 LLM call per incident** | incident + its severity + matching runbook | `action_proposals.json` | 1–3 actions; ≥1 traceable to `allowed_actions` or explicitly justified |
| 4 | Safety guardrails | **deterministic, no LLM** | proposals + runbooks + services | `safety_review.json` | forbidden/paraphrased-forbidden ⇒ `forbidden`; change-freeze non-rollback ⇒ `needs_approval`; verdict can only get **stricter**; blast-radius-aware (checks every service the incident touches) |
| 5 | Stakeholder drafting | 1 batched LLM call | decided state | `stakeholder_updates.json` | eng 4–6 sentences incl. blocked actions; exec 3–5 sentences; template fallback if out of range |
| 6 | Operator review | interactive (or scripted/auto) | incidents + severity + top action + safety | `operator_feedback.jsonl` | one `accepted`/`corrected`/`skipped` record per incident |
| 7a | Feedback examples | code | prior + current feedback | `feedback_examples.json`, `feedback_store_status.json` | prior store auto-loaded before re-decision |
| 7b | Re-decision | LLM calls **with feedback injected** | incidents + feedback block | `severity_redecision.json`, `actions_redecision.json` | `few_shot_examples_included=true`; guardrails re-applied |
| — | Before/after + agreement delta | code | original vs operator vs re-decided | `before_after_comparison.json`, `incident_command_output.json` | delta computed over `accepted`/`corrected` incidents only; independently re-derivable |
| 8 | Analytics | code | everything | `analytics_summary.json` | — |
| 9 | Prior feedback store | code | `operator_feedback.jsonl` | `feedback_store_status.json` | records whether prior feedback existed & was loaded |
| 10 | Escalation bundle | code | sev0/sev1 incidents | `escalation_bundle.json` | page + channel + owner team |

The **stage machine** (`icp/statemachine.py`) refuses to move backwards or skip a
required step; the combined `incident_command_output.json` is written **only
after** `RESULTS_FINALISED`, so it can never predate a stage.

---

## How human feedback changes downstream decisions

`demo_operator_script.json` (used by `make run-demo`) simulates an operator who:

* **inc-001** – accepts severity, **corrects the action** `shift_traffic → restart_consumer_group`
* **inc-006** – **corrects severity** `sev2 → sev3`, and *skips* the action

Re-decision then:

* applies the **direct corrections** (inc-001 action, inc-006 severity), and
* **generalises**: inc-006's action was skipped, but because its sibling inc-001
  (same `primary_service`) was corrected to `restart_consumer_group`, the
  re-decision promotes that action for inc-006 too — recorded as
  `"feedback_influence": "generalised_from_sibling_feedback"`.

`before_after_comparison.json` shows per-incident movement
(`moved_toward` / `maintained_agreement` / `moved_away` / `no_change`) and the
**agreement delta** — the change in how often the (re-)decision matches the
operator's stated preference, over the incidents they labelled. Typical demo run:
`severity Δ = +0.167`, `action Δ = +0.200`.

Run it a **second time** without `--fresh` and `feedback_store_status.json` shows
`existed_before_run: true` with the prior records loaded and folded into the
re-decision (the most recent operator label wins on conflicts).

---

## Auditability / replay

* `llm_calls.jsonl` — one record per call (`stage`, `incident_id`, `timestamp`,
  `provider`, `model`, `prompt_hash`, `input_artifacts`, `output_artifact`,
  `few_shot_examples_included`). Separate records for grouping, severity, **each**
  action-proposal call, drafting, and **each** re-decision call.
* `audit/prompts/<stage>__<incident>__<hash>.json` — the *exact* rendered prompt
  and raw response for every call, so any decision can be reproduced/explained.
* `pipeline_state.json` — every stage transition with timestamps.
* Original model proposal **and** deterministic verdict are both kept in
  `safety_review.json` (`model_safety_level` vs `final_safety_level` +
  `guardrail_reasons`).

---

## Design notes / tradeoffs

* **Rollback safety.** The system only *recommends*. Under a change freeze on any
  service in the incident's blast radius, only rollback-type actions may stay
  `safe`; everything else (including traffic shifts) is escalated to
  `needs_approval`. Conservative by design.
* **Guardrails are code, not prompt text.** Forbidden actions are caught by exact
  match **and** by intent/paraphrase ("skip secondary reconciliation checks" is
  rejected as a reconciliation-bypass). An explicitly runbook-sanctioned action
  is trusted over fuzzy keyword matches.
* **Model output is never trusted blindly.** Each stage has a deterministic
  repair/validation pass: partition repair for grouping, vocabulary coercion,
  sentence-range enforcement for drafts, severity floors for high-stakes
  categories.
* **Explainability.** Every severity carries a scored factor list; every action
  carries why / expected effect / risk; every guardrail carries reasons.
* **Reliability.** No network needed; deterministic default model ⇒ byte-stable
  re-runs; `--fresh` for a clean slate; the append-only feedback store is an
  audit log (dedup is deliberately *not* done — `make clean-feedback` resets it).
* **What a production version would add:** real retrieval over historical
  incidents for the feedback block, per-tenant runbook versioning, a proper
  approval workflow integration, and human-in-the-loop gating before any
  `needs_approval` action is surfaced to automation.

---

## File map

```
run.py                     CLI entry
validate.py                standalone artifact validator (make validate)
demo_operator_script.json  canned operator answers for the reproducible demo
alerts.json services.json runbooks.json   input fixtures (evaluator may swap)
icp/
  pipeline.py              orchestrator + internal invariant checks
  statemachine.py          enforced stage ordering
  vocab.py                 controlled vocabularies + coercion
  prompts.py               genuine prompt builders + JSON schemas
  util.py                  io / hashing / sentence counting
  llm/
    __init__.py            BaseLLMClient, CallLogger (llm_calls.jsonl), factory
    anthropic_client.py    optional real provider
    heuristic_client.py    deterministic offline model (schema-driven)
  stages/
    grouping.py severity.py actions.py guardrails.py drafting.py
    operator.py feedback.py redecision.py comparison.py analytics.py escalation.py
tests/                     unit + end-to-end (offline)
```
