"""Unit tests for the deterministic building blocks."""

import pytest

from icp.vocab import coerce, require, VocabularyError, SEVERITY, stricter_safety, severity_distance
from icp.statemachine import StateMachine, StageError
from icp.stages.guardrails import run as guardrails_run, top_action
from icp.stages.comparison import build as comparison_build


def test_vocab_require_and_coerce():
    assert require("sev1", SEVERITY, field="s") == "sev1"
    with pytest.raises(VocabularyError):
        require("sevX", SEVERITY, field="s")
    assert coerce("SEV1", SEVERITY, default="sev3", field="s") == "sev1"
    assert coerce("sev-2", SEVERITY, default="sev3", field="s") == "sev2"
    assert coerce("critical", SEVERITY, default="sev3", field="s") == "sev0"
    assert coerce(None, SEVERITY, default="sev2", field="s") == "sev2"


def test_safety_ordering():
    assert stricter_safety("safe", "needs_approval") == "needs_approval"
    assert stricter_safety("forbidden", "safe") == "forbidden"
    assert severity_distance("sev0", "sev3") == 3


def test_statemachine_forbids_backwards_and_skips():
    sm = StateMachine()
    sm.enter("INPUTS_LOADED", require_prev="INIT")
    sm.enter("INCIDENT_GROUPING_COMPLETE")  # skipping optional PRIOR_FEEDBACK_LOADED is ok
    with pytest.raises(StageError):
        sm.enter("INPUTS_LOADED")  # backwards
    with pytest.raises(StageError):
        sm.enter("SEVERITY_ASSESSMENT_COMPLETE", require_prev="INIT")  # wrong prev


_INCIDENTS = [
    {
        "incident_id": "inc-1",
        "primary_service": "payments-ledger",
        "alert_services": ["payments-ledger"],
    },
    {
        "incident_id": "inc-2",
        "primary_service": "market-data-stream",
        "alert_services": ["market-data-stream", "trade-execution"],
    },
]
_SERVICES = [
    {"service": "payments-ledger", "change_freeze_required": True},
    {"service": "market-data-stream", "change_freeze_required": False},
    {"service": "trade-execution", "change_freeze_required": True},
]
_RUNBOOKS = [
    {"service": "payments-ledger", "allowed_actions": ["pause_retries"], "forbidden_actions": ["skip_reconciliation"]},
    {"service": "market-data-stream", "allowed_actions": ["shift_traffic", "restart_consumer_group"], "forbidden_actions": ["drop_live_ticks"]},
    {"service": "trade-execution", "allowed_actions": ["pause_low_priority_orders"], "forbidden_actions": ["bypass_risk_checks"]},
]


def test_guardrails_forbidden_paraphrase_and_freeze(tmp_path):
    proposals = {
        "inc-1": [
            {"action": "skip_reconciliation", "safety_level": "safe"},          # exact forbidden
            {"action": "skip_secondary_reconciliation_checks", "safety_level": "safe"},  # paraphrase
            {"action": "pause_retries", "safety_level": "safe"},                # allowed but frozen -> needs_approval
        ],
        "inc-2": [
            {"action": "shift_traffic", "safety_level": "safe"},               # trade-execution freeze in blast radius
            {"action": "restart_consumer_group", "safety_level": "safe"},
        ],
    }
    out = guardrails_run(None, proposals, _INCIDENTS, _RUNBOOKS, _SERVICES, outdir=str(tmp_path))
    lv = {r["action"]: r["final_safety_level"] for r in out["inc-1"]}
    assert lv["skip_reconciliation"] == "forbidden"
    assert lv["skip_secondary_reconciliation_checks"] == "forbidden"
    assert lv["pause_retries"] == "needs_approval"  # change freeze escalation
    lv2 = {r["action"]: r["final_safety_level"] for r in out["inc-2"]}
    assert lv2["shift_traffic"] == "needs_approval"  # trade-execution freeze in blast radius


def test_guardrails_never_downgrades_model():
    proposals = {"inc-2": [{"action": "restart_consumer_group", "safety_level": "forbidden"}]}
    out = guardrails_run(None, proposals, _INCIDENTS, _RUNBOOKS, _SERVICES, outdir=".")
    assert out["inc-2"][0]["final_safety_level"] == "forbidden"


def test_top_action_prefers_safest():
    results = [
        {"action": "a", "final_safety_level": "needs_approval"},
        {"action": "b", "final_safety_level": "safe"},
        {"action": "c", "final_safety_level": "safe"},
    ]
    assert top_action(results)["action"] == "b"


def test_agreement_delta_math(tmp_path):
    incidents = [{"incident_id": "i1"}, {"incident_id": "i2"}]
    orig_sev = [{"incident_id": "i1", "severity": "sev2"}, {"incident_id": "i2", "severity": "sev1"}]
    new_sev = [{"incident_id": "i1", "severity": "sev1"}, {"incident_id": "i2", "severity": "sev1"}]
    orig_safety = {"i1": [{"action": "x", "final_safety_level": "safe"}], "i2": [{"action": "y", "final_safety_level": "safe"}]}
    new_safety = {"i1": [{"action": "z", "final_safety_level": "safe"}], "i2": [{"action": "y", "final_safety_level": "safe"}]}
    records = [
        {"incident_id": "i1", "severity_status": "corrected", "corrected_severity": "sev1",
         "action_status": "corrected", "corrected_action": "z"},
        {"incident_id": "i2", "severity_status": "accepted", "corrected_severity": None,
         "action_status": "accepted", "corrected_action": None},
    ]
    rows, agr = comparison_build(incidents, orig_sev, orig_safety, new_sev, new_safety, records, outdir=str(tmp_path))
    # severity: before i1 wrong, i2 right -> 0.5 ; after both right -> 1.0 ; delta 0.5
    assert agr["severity"]["delta"] == 0.5
    # action: before i1 wrong (x vs z), i2 right -> 0.5 ; after i1 right (z) -> 1.0 ; delta 0.5
    assert agr["action"]["delta"] == 0.5
    assert agr["combined_delta"] == 0.5
