"""Optional real-provider client.

Only imported when ``ANTHROPIC_API_KEY`` is present *and* the ``anthropic``
package is installed. The pipeline is fully functional without it via
:class:`icp.llm.heuristic_client.HeuristicClient`; this class exists to show the
prompts are genuine and provider-portable.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import BaseLLMClient, CallLogger

_DEFAULT_MODEL = "claude-sonnet-5"


def _extract_json(text: str) -> Any:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # find the first balanced JSON value
    start = min(
        [i for i in (text.find("{"), text.find("[")) if i != -1] or [-1]
    )
    if start > 0:
        text = text[start:]
    return json.loads(text)


class AnthropicClient(BaseLLMClient):
    provider = "anthropic"

    def __init__(self, logger: CallLogger, *, model: str | None = None) -> None:
        super().__init__(logger)
        import anthropic  # noqa: F401

        self._anthropic = anthropic
        self._client = anthropic.Anthropic()
        self.model = model or _DEFAULT_MODEL

    def _run(
        self, task: str, prompt: str, context: dict, few_shot_examples: list[dict]
    ) -> tuple[Any, str]:
        system = (
            "You are an incident-command decision assistant. You only produce "
            "structured recommendations, never actions. Respond with a single "
            "JSON value that matches the schema in the user message and nothing "
            "else."
        )
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        )
        return _extract_json(raw), raw
