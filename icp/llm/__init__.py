"""LLM abstraction.

Every model interaction in the pipeline goes through :class:`BaseLLMClient`.
The client is responsible for:

* running the call (real provider or deterministic offline model),
* appending an audit record to ``llm_calls.jsonl`` for *every* call, and
* persisting the exact rendered prompt + raw response under ``audit/prompts/``
  keyed by prompt hash, so any decision can be replayed and explained.

Stages never touch a provider SDK directly and never write ``llm_calls.jsonl``
themselves -- they call :meth:`BaseLLMClient.generate`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..util import append_jsonl, now_iso, prompt_hash, write_json


@dataclass
class LLMResult:
    data: Any
    raw: str
    provider: str
    model: str


@dataclass
class CallLogger:
    """Writes the ``llm_calls.jsonl`` audit trail and per-call prompt dumps."""

    log_path: Path
    audit_dir: Path
    records: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.audit_dir.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        *,
        stage: str,
        incident_id: str | None,
        provider: str,
        model: str,
        prompt: str,
        raw_response: str,
        input_artifacts: list[str],
        output_artifact: str,
        few_shot_examples_included: bool,
    ) -> dict:
        h = prompt_hash(prompt)
        record = {
            "stage": stage,
            "incident_id": incident_id,
            "timestamp": now_iso(),
            "provider": provider,
            "model": model,
            "prompt_hash": h,
            "input_artifacts": list(input_artifacts),
            "output_artifact": output_artifact,
            "few_shot_examples_included": bool(few_shot_examples_included),
        }
        append_jsonl(self.log_path, record)
        self.records.append(record)
        # full prompt + response dump for replay / explainability
        dump_name = f"{stage}"
        if incident_id:
            dump_name += f"__{incident_id}"
        dump_name += f"__{h.split(':', 1)[1][:12]}.json"
        write_json(
            self.audit_dir / dump_name,
            {
                "stage": stage,
                "incident_id": incident_id,
                "prompt_hash": h,
                "prompt": prompt,
                "raw_response": raw_response,
                "provider": provider,
                "model": model,
                "few_shot_examples_included": bool(few_shot_examples_included),
                "at": record["timestamp"],
            },
        )
        return record


class BaseLLMClient:
    provider: str = "base"
    model: str = "base"

    def __init__(self, logger: CallLogger) -> None:
        self.logger = logger

    # --- public API used by stages -------------------------------------------------
    def generate(
        self,
        *,
        stage: str,
        task: str,
        prompt: str,
        context: dict,
        input_artifacts: list[str],
        output_artifact: str,
        incident_id: str | None = None,
        few_shot_examples: list[dict] | None = None,
    ) -> LLMResult:
        data, raw = self._run(task, prompt, context, few_shot_examples or [])
        self.logger.log(
            stage=stage,
            incident_id=incident_id,
            provider=self.provider,
            model=self.model,
            prompt=prompt,
            raw_response=raw,
            input_artifacts=input_artifacts,
            output_artifact=output_artifact,
            few_shot_examples_included=bool(few_shot_examples),
        )
        return LLMResult(data=data, raw=raw, provider=self.provider, model=self.model)

    # --- provider hook ----------------------------------------------------------
    def _run(
        self, task: str, prompt: str, context: dict, few_shot_examples: list[dict]
    ) -> tuple[Any, str]:  # pragma: no cover - abstract
        raise NotImplementedError


def build_client(provider: str, logger: CallLogger, *, model: str | None = None) -> BaseLLMClient:
    """Factory.

    ``provider`` may be ``auto`` (use Anthropic iff ``ANTHROPIC_API_KEY`` is set
    and the SDK imports, else fall back to the offline model), ``heuristic``
    (force offline) or ``anthropic`` (force real; errors if unavailable).
    """
    provider = (provider or "auto").lower()
    from .heuristic_client import HeuristicClient

    if provider == "heuristic":
        return HeuristicClient(logger)

    if provider in ("auto", "anthropic"):
        has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        try:
            from .anthropic_client import AnthropicClient  # noqa: F401

            sdk_ok = True
        except Exception:
            sdk_ok = False
        if has_key and sdk_ok:
            from .anthropic_client import AnthropicClient

            return AnthropicClient(logger, model=model)
        if provider == "anthropic":
            raise RuntimeError(
                "provider=anthropic requested but ANTHROPIC_API_KEY is unset or "
                "the 'anthropic' package is not installed"
            )
        return HeuristicClient(logger)

    raise ValueError(f"unknown provider {provider!r}")
