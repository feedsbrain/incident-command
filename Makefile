# Incident-command pipeline. Python 3.10+; no third-party deps required.
PY ?= python

.PHONY: help run run-demo run-interactive run-anthropic validate test clean clean-feedback

help:
	@echo "make run             - run pipeline, accept-all operator (CI-friendly, offline)"
	@echo "make run-demo        - run pipeline with the scripted operator (shows feedback moving decisions)"
	@echo "make run-interactive - run pipeline, prompt a human operator in the terminal"
	@echo "make run-anthropic   - run pipeline against a real model (needs ANTHROPIC_API_KEY, pip install anthropic)"
	@echo "make validate        - validate all generated artifacts"
	@echo "make test            - unit tests (needs pytest)"
	@echo "make clean           - remove generated artifacts (keeps operator_feedback.jsonl)"
	@echo "make clean-feedback  - also remove the persisted operator_feedback.jsonl"

run:
	$(PY) run.py --provider heuristic --operator-mode auto --fresh

run-demo:
	$(PY) run.py --provider heuristic --operator-mode script:demo_operator_script.json --fresh

run-interactive:
	$(PY) run.py --provider heuristic --operator-mode interactive

run-anthropic:
	$(PY) run.py --provider anthropic --operator-mode script:demo_operator_script.json --fresh

validate:
	$(PY) validate.py

test:
	$(PY) -m pytest -q

clean:
	$(PY) -c "import pathlib,glob; [pathlib.Path(p).unlink() for p in ['incident_groups.json','severity_assessments.json','action_proposals.json','safety_review.json','safety_review_redecision.json','stakeholder_updates.json','severity_redecision.json','actions_redecision.json','incident_command_output.json','analytics_summary.json','feedback_store_status.json','feedback_examples.json','escalation_bundle.json','before_after_comparison.json','llm_calls.jsonl','pipeline_state.json'] if pathlib.Path(p).exists()]"
	$(PY) -c "import shutil,pathlib; shutil.rmtree('audit', ignore_errors=True)"
	@echo "cleaned (operator_feedback.jsonl kept; use 'make clean-feedback' to drop it)"

clean-feedback: clean
	$(PY) -c "import pathlib; p=pathlib.Path('operator_feedback.jsonl'); p.unlink() if p.exists() else None"
	@echo "removed operator_feedback.jsonl"
