"""LongMemEval dataset loader.

Supports two input formats with the same schema:

1. Official LongMemEval JSON — a list of dicts matching LongMemEvalCase fields.
2. Synthetic fixture format — same schema, generated locally for CI.

The loader is intentionally lenient on missing optional fields so both
the real dataset and the synthetic fixture can be parsed by one function.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Valid case_type values in LongMemEval.
VALID_CASE_TYPES: frozenset[str] = frozenset(
    {
        "single-session-user",
        "multi-session-user",
        "single-session-assistant",
        "temporal",
        "knowledge-update",
        "abstention",
    }
)


@dataclass
class LongMemEvalCase:
    """One evaluation case from the LongMemEval dataset.

    Attributes:
        session_id: unique identifier for this evaluation case.
        query: the retrieval query to issue against the vault.
        gold_answer: expected answer string (used for abstention checks).
        gold_doc_paths: list of relative document paths that should appear
            in the retrieval results for a correct answer.
        case_type: one of VALID_CASE_TYPES. Abstention cases are handled
            separately: the expected correct behaviour is an empty result list.
    """

    session_id: str
    query: str
    gold_answer: str
    gold_doc_paths: list[str]
    case_type: str

    def is_abstention(self) -> bool:
        """Return True when the correct response is to return no results."""
        return self.case_type == "abstention"


def _coerce_str(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value
    return default


def _coerce_list_of_str(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def _parse_case(raw: dict[str, Any], index: int) -> LongMemEvalCase:
    """Parse one raw dict into a LongMemEvalCase.

    Lenient: unknown fields are ignored, missing optional fields get defaults.
    """
    session_id = _coerce_str(raw.get("session_id"), default=f"case-{index}")
    query = _coerce_str(raw.get("query"))
    gold_answer = _coerce_str(raw.get("gold_answer"))
    gold_doc_paths = _coerce_list_of_str(raw.get("gold_doc_paths"))
    case_type = _coerce_str(raw.get("case_type"), default="single-session-user")
    return LongMemEvalCase(
        session_id=session_id,
        query=query,
        gold_answer=gold_answer,
        gold_doc_paths=gold_doc_paths,
        case_type=case_type,
    )


def load_dataset(path: Path) -> list[LongMemEvalCase]:
    """Load a LongMemEval dataset file and return parsed cases.

    Args:
        path: path to a JSON file containing a list of case dicts.

    Returns:
        list of LongMemEvalCase, one per entry.

    Raises:
        FileNotFoundError: if the path does not exist.
        ValueError: if the file is not valid JSON or not a list.
    """
    if not path.exists():
        raise FileNotFoundError(f"LongMemEval dataset not found: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in dataset file {path}: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError(f"Dataset must be a JSON list, got {type(raw).__name__}")
    return [_parse_case(item, i) for i, item in enumerate(raw) if isinstance(item, dict)]


# ---------------------------------------------------------------------------
# Synthetic fixture
# ---------------------------------------------------------------------------

# The synthetic fixture uses a small closed-vocabulary corpus so CI does not
# need the full LongMemEval download. The fixture format is identical to the
# real dataset schema so load_dataset() handles both.

_SYNTHETIC_DOCS = [
    {
        "path": "notes/attention-mechanisms.md",
        "title": "Attention Mechanisms in Transformers",
        "body": "Attention mechanisms allow models to focus on relevant parts of the input. "
                "The transformer architecture uses self-attention and cross-attention layers.",
    },
    {
        "path": "notes/fts5-indexing.md",
        "title": "FTS5 Full-Text Search",
        "body": "FTS5 is SQLite's full-text search engine. It uses the BM25 ranking algorithm "
                "and supports Unicode tokenization for multilingual content.",
    },
    {
        "path": "notes/rrf-fusion.md",
        "title": "Reciprocal Rank Fusion",
        "body": "Reciprocal Rank Fusion combines ranked lists from multiple retrieval backends. "
                "The RRF score is 1/(k + rank) where k=60 by default.",
    },
    {
        "path": "notes/privacy-redaction.md",
        "title": "Privacy Redaction",
        "body": "The privacy module redacts <private> tagged content before indexing. "
                "This ensures sensitive information does not appear in the FTS5 index.",
    },
    {
        "path": "notes/vault-config.md",
        "title": "Vault Configuration",
        "body": "VaultConfig resolves the vault root from environment variable MNEME_VAULT, "
                "explicit path, config.toml, or the nearest ancestor with a .mneme marker.",
    },
    {
        "path": "notes/retrieval-telemetry.md",
        "title": "Retrieval Telemetry",
        "body": "Per-backend telemetry records query hash, hit counts, and latency in milliseconds. "
                "Events are written to JSONL files in the vault telemetry directory.",
    },
]

_SYNTHETIC_CASES: list[dict[str, Any]] = [
    {
        "session_id": "s001",
        "query": "how does attention mechanism work in transformers",
        "gold_answer": "Attention allows models to focus on relevant parts of the input.",
        "gold_doc_paths": ["notes/attention-mechanisms.md"],
        "case_type": "single-session-user",
    },
    {
        "session_id": "s002",
        "query": "FTS5 BM25 ranking full text search SQLite",
        "gold_answer": "FTS5 uses BM25 ranking and Unicode tokenization.",
        "gold_doc_paths": ["notes/fts5-indexing.md"],
        "case_type": "single-session-user",
    },
    {
        "session_id": "s003",
        "query": "reciprocal rank fusion RRF score formula",
        "gold_answer": "RRF score is 1/(k + rank) where k=60.",
        "gold_doc_paths": ["notes/rrf-fusion.md"],
        "case_type": "single-session-assistant",
    },
    {
        "session_id": "s004",
        "query": "private tag redaction before indexing vault",
        "gold_answer": "The privacy module redacts <private> tagged content before indexing.",
        "gold_doc_paths": ["notes/privacy-redaction.md"],
        "case_type": "single-session-user",
    },
    {
        "session_id": "s005",
        "query": "how is vault root resolved MNEME_VAULT environment",
        "gold_answer": "VaultConfig resolves from MNEME_VAULT env, explicit path, or .mneme marker.",
        "gold_doc_paths": ["notes/vault-config.md"],
        "case_type": "multi-session-user",
    },
    {
        "session_id": "s006",
        "query": "retrieval telemetry backend latency JSONL",
        "gold_answer": "Telemetry records query hash, hit counts, and latency in JSONL.",
        "gold_doc_paths": ["notes/retrieval-telemetry.md"],
        "case_type": "temporal",
    },
    {
        "session_id": "s007",
        "query": "luminescent crystallography photon refraction prism quantum",
        "gold_answer": "",
        "gold_doc_paths": [],
        "case_type": "abstention",
    },
    {
        "session_id": "s008",
        "query": "seismic tectonic lithosphere mantle subduction volcano",
        "gold_answer": "",
        "gold_doc_paths": [],
        "case_type": "abstention",
    },
]


def build_synthetic_fixture() -> list[LongMemEvalCase]:
    """Return the built-in synthetic fixture cases (CI-safe, no download)."""
    return [_parse_case(c, i) for i, c in enumerate(_SYNTHETIC_CASES)]


def synthetic_fixture_docs() -> list[dict[str, Any]]:
    """Return the synthetic vault documents for the fixture."""
    return list(_SYNTHETIC_DOCS)
