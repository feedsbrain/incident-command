"""Controlled vocabularies, enforced in code.

Every stage that emits a categorical value routes it through :func:`coerce` or
:func:`require` so that an out-of-vocabulary value from an LLM (or a buggy
heuristic) can never reach a downstream artifact silently.
"""

from __future__ import annotations

INCIDENT_STATUS = ("open", "monitoring", "mitigated", "closed")
SEVERITY = ("sev0", "sev1", "sev2", "sev3")
BLAST_RADIUS = ("single_service", "multi_service", "regional", "global", "unknown")
ACTION_SAFETY = ("safe", "needs_approval", "forbidden")
OPERATOR_REVIEW = ("accepted", "corrected", "skipped")

# Ordinal helpers ----------------------------------------------------------------
# sev0 is the most severe. Distance is used by the before/after comparison.
_SEVERITY_RANK = {name: idx for idx, name in enumerate(SEVERITY)}
_SAFETY_RANK = {"safe": 0, "needs_approval": 1, "forbidden": 2}


class VocabularyError(ValueError):
    """Raised when a value cannot be mapped into a controlled vocabulary."""


def require(value: str, vocab: tuple[str, ...], *, field: str) -> str:
    """Return ``value`` if it is in ``vocab`` else raise :class:`VocabularyError`."""
    if value in vocab:
        return value
    raise VocabularyError(f"{field}={value!r} is not one of {vocab}")


def coerce(value, vocab: tuple[str, ...], *, default: str, field: str) -> str:
    """Best-effort normalise ``value`` into ``vocab``.

    Used on raw model output: lower-cases, swaps spaces/dashes for underscores,
    and falls back to ``default`` (recording nothing here -- callers log the
    substitution) when no match is possible.
    """
    if isinstance(value, str):
        norm = value.strip().lower().replace("-", "_").replace(" ", "_")
        if norm in vocab:
            return norm
        # also try with all separators removed (e.g. "sev 2" / "SEV-2" -> "sev2")
        collapsed = norm.replace("_", "")
        for candidate in vocab:
            if candidate.replace("_", "") == collapsed:
                return candidate
        # tolerate a few common synonyms
        synonyms = {
            "critical": "sev0",
            "high": "sev1",
            "medium": "sev2",
            "moderate": "sev2",
            "low": "sev3",
            "multi": "multi_service",
            "cross_service": "multi_service",
            "region": "regional",
            "worldwide": "global",
            "needs_review": "needs_approval",
            "approval_required": "needs_approval",
            "blocked": "forbidden",
            "denied": "forbidden",
            "ok": "safe",
            "allow": "safe",
            "accept": "accepted",
            "correct": "corrected",
            "skip": "skipped",
        }
        if norm in synonyms and synonyms[norm] in vocab:
            return synonyms[norm]
    return default


def severity_rank(sev: str) -> int:
    return _SEVERITY_RANK[require(sev, SEVERITY, field="severity")]


def severity_distance(a: str, b: str) -> int:
    return abs(severity_rank(a) - severity_rank(b))


def stricter_safety(a: str, b: str) -> str:
    """Return whichever safety level is more restrictive."""
    return a if _SAFETY_RANK[a] >= _SAFETY_RANK[b] else b
