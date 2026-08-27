"""Incident Command Pipeline (icp).

A replayable, stage-separated, guardrailed incident-command workflow.

The public entry point is :func:`icp.pipeline.run_pipeline`. Individual decision
stages live in :mod:`icp.stages`. LLM access is abstracted behind
:mod:`icp.llm` so the pipeline runs identically with a real provider or with the
bundled deterministic offline model.
"""

__version__ = "1.0.0"
