#!/usr/bin/env python3
"""Entry point for the incident-command pipeline.

Examples
--------
    python run.py                                  # interactive operator review
    python run.py --operator-mode auto             # accept all (CI)
    python run.py --operator-mode script:demo_operator_script.json
    python run.py --provider anthropic             # use a real model (needs ANTHROPIC_API_KEY)
    python run.py --fresh                          # also wipe the persisted feedback store

Runs offline with a deterministic model by default; set ANTHROPIC_API_KEY (and
`pip install anthropic`) with --provider auto/anthropic to use a real provider.
"""

from __future__ import annotations

import argparse
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from icp.pipeline import run_pipeline


def _parse_operator_mode(value: str):
    if value.startswith("script:"):
        return "script", value.split(":", 1)[1]
    if value in ("interactive", "auto", "script"):
        return value, None
    raise argparse.ArgumentTypeError(f"invalid --operator-mode {value!r}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Replayable incident-command pipeline")
    p.add_argument("--indir", default=".", help="directory containing alerts/services/runbooks json")
    p.add_argument("--outdir", default=".", help="directory to write artifacts into")
    p.add_argument("--provider", default="auto", choices=["auto", "heuristic", "anthropic"])
    p.add_argument("--model", default=None, help="override model id for a real provider")
    p.add_argument(
        "--operator-mode",
        default="interactive",
        help="interactive | auto | script:<path>",
    )
    p.add_argument("--fresh", action="store_true", help="also delete operator_feedback.jsonl before running")
    args = p.parse_args(argv)

    mode, script = _parse_operator_mode(args.operator_mode)
    run_pipeline(
        indir=args.indir,
        outdir=args.outdir,
        provider=args.provider,
        model=args.model,
        operator_mode=mode,
        operator_script=script,
        fresh=args.fresh,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
