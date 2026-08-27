"""End-to-end: run the whole pipeline offline into a temp dir and validate it."""

import json
import shutil
from pathlib import Path

import pytest

from icp.pipeline import run_pipeline
import validate as validator

REPO = Path(__file__).resolve().parents[1]
FIXTURES = ("alerts.json", "services.json", "runbooks.json")


def _seed(dst: Path):
    for f in FIXTURES:
        shutil.copy(REPO / f, dst / f)


def test_end_to_end_auto(tmp_path):
    _seed(tmp_path)
    out = run_pipeline(indir=str(tmp_path), outdir=str(tmp_path), provider="heuristic",
                       operator_mode="auto", fresh=True)
    assert out["pipeline"]["current_stage"] == "RESULTS_FINALISED"
    for name in validator.REQUIRED:
        assert (tmp_path / name).exists(), name
    assert validator.main(["--dir", str(tmp_path)]) == 0


def test_scripted_feedback_moves_decisions(tmp_path):
    _seed(tmp_path)
    shutil.copy(REPO / "demo_operator_script.json", tmp_path / "demo_operator_script.json")
    out = run_pipeline(indir=str(tmp_path), outdir=str(tmp_path), provider="heuristic",
                       operator_mode="script", operator_script=str(tmp_path / "demo_operator_script.json"),
                       fresh=True)
    agr = out["agreement_delta"]
    # the scripted operator corrects one action and one severity -> re-decision should
    # not regress and should improve at least one dimension
    assert agr["severity"]["delta"] >= 0
    assert agr["action"]["delta"] >= 0
    assert (agr["severity"]["delta"] or 0) + (agr["action"]["delta"] or 0) > 0
    # a row must show movement toward feedback
    assert any(r["moved_toward_feedback"] for r in out["before_after_comparison"])
    # generalisation: inc-006 action was skipped but should still change from sibling feedback
    inc6 = next(r for r in out["before_after_comparison"] if r["incident_id"] == "inc-006")
    assert inc6["feedback_influence"] == "generalised_from_sibling_feedback"


def test_alerts_partition_and_guardrail_catches_forbidden(tmp_path):
    _seed(tmp_path)
    out = run_pipeline(indir=str(tmp_path), outdir=str(tmp_path), provider="heuristic",
                       operator_mode="auto", fresh=True)
    alert_ids = {a["id"] for a in json.loads((tmp_path / "alerts.json").read_text())}
    assigned = [x for g in json.loads((tmp_path / "incident_groups.json").read_text()) for x in g["alert_ids"]]
    assert sorted(assigned) == sorted(alert_ids)
    assert len(assigned) == len(set(assigned))
    safety = json.loads((tmp_path / "safety_review.json").read_text())
    assert any(r["final_safety_level"] == "forbidden" for r in safety)
    assert any(r["final_safety_level"] != r["model_safety_level"] for r in safety)


def test_llm_call_log_has_separate_stage_records(tmp_path):
    _seed(tmp_path)
    run_pipeline(indir=str(tmp_path), outdir=str(tmp_path), provider="heuristic",
                 operator_mode="auto", fresh=True)
    rows = [json.loads(l) for l in (tmp_path / "llm_calls.jsonl").read_text().splitlines() if l.strip()]
    stages = [r["stage"] for r in rows]
    n_inc = len(json.loads((tmp_path / "incident_groups.json").read_text()))
    assert stages.count("incident_grouping") == 1
    assert stages.count("severity_assessment") == 1
    assert stages.count("action_proposal") == n_inc
    assert stages.count("stakeholder_drafting") == 1
    assert stages.count("severity_redecision") == 1
    assert stages.count("action_redecision") == n_inc
    for r in rows:
        if r["stage"] in ("severity_redecision", "action_redecision"):
            assert r["few_shot_examples_included"] is True
