"""Small IO / hashing / text helpers shared across stages."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    """UTC ISO-8601 timestamp with a trailing ``Z``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def read_json(path: str | Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: str | Path, data: Any) -> None:
    # ensure_ascii=True: artifacts stay pure ASCII so a naive reader on any
    # platform default encoding (e.g. cp1252 on Windows) parses them correctly.
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=True)
        fh.write("\n")


def read_jsonl(path: str | Path) -> list[Any]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[Any] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def append_jsonl(path: str | Path, record: Any) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=True) + "\n")


def prompt_hash(prompt: str) -> str:
    """Stable content hash used to correlate a logged call with its saved prompt."""
    return "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "unknown"


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def count_sentences(text: str) -> int:
    text = (text or "").strip()
    if not text:
        return 0
    parts = [p for p in _SENTENCE_SPLIT.split(text) if p.strip()]
    return len(parts)


def parse_minutes_before(details: str) -> int | None:
    """Extract '<n> minutes earlier' / '<n> minutes ago' style hints from text."""
    if not details:
        return None
    m = re.search(r"(\d+)\s*(?:minutes?|mins?)\s*(?:earlier|ago|before)?", details.lower())
    if m:
        return int(m.group(1))
    return None
