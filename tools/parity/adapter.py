"""Parity adapter contract and the mneme implementation.

Unlike the benchmark Head-to-Head adapter, the parity adapter does not
own migration. The operator is expected to have already migrated their
real data into both systems before invoking the harness. Each adapter
only needs to answer queries.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "packages" / "mneme-core" / "src"))


@dataclass
class ParityAdapterStatus:
    """Per-adapter runtime probe."""

    name: str
    available: bool
    reason: str = ""
    extras: dict[str, object] = field(default_factory=dict)


@dataclass
class ParityHit:
    """A single retrieval hit returned by a parity adapter."""

    doc_id: str
    score: float


class ParityAdapter(Protocol):
    """The minimal contract the parity harness expects."""

    def status(self) -> ParityAdapterStatus: ...

    def search(self, query: str, top_k: int = 10) -> list[ParityHit]: ...


class MnemeParityAdapter:
    """Production mneme adapter pointed at a pre-existing vault.

    Unlike the benchmark MnemeAdapter, this one assumes the vault is
    already indexed. It does not re-migrate from a claude-mem source.
    Operators run ``mneme rebuild-indexes`` once before invoking the
    parity harness, then both adapters answer queries against their
    own backing stores.
    """

    def __init__(self, *, vault: Path, db_path: Path | None = None) -> None:
        self._vault = vault
        self._db = db_path if db_path is not None else (vault / ".mneme" / "fts5.sqlite")

    def status(self) -> ParityAdapterStatus:
        if not self._vault.exists():
            return ParityAdapterStatus(
                name="mneme",
                available=False,
                reason=f"vault not found at {self._vault}",
            )
        if not self._db.exists():
            return ParityAdapterStatus(
                name="mneme",
                available=False,
                reason=(
                    f"FTS5 index missing at {self._db}; "
                    f"run 'mneme rebuild-indexes' first"
                ),
            )
        return ParityAdapterStatus(
            name="mneme",
            available=True,
            extras={"vault": str(self._vault), "db": str(self._db)},
        )

    def search(self, query: str, top_k: int = 10) -> list[ParityHit]:
        from mneme_core.retrieval.rrf import RetrievalConfig, retrieve

        cfg = RetrievalConfig(fts5_db=self._db, top_n_final=top_k)
        hits = retrieve(query, cfg)
        out: list[ParityHit] = []
        for h in hits:
            doc_id = Path(h.path).stem
            score = h.rrf_score if h.rrf_score is not None else h.score
            out.append(ParityHit(doc_id=doc_id, score=float(score)))
        return out


def load_adapter_by_path(spec: str) -> ParityAdapter:
    """Resolve ``module.path:ClassName`` to an instantiated adapter.

    The ground-truth adapter lives outside the mneme repo. The operator
    points at it with a Python import path. Example:

        --ground-truth tools.parity.my_local_adapter:MyAdapter

    The named class is instantiated with no constructor arguments. If
    the class needs configuration, build it as a zero-arg wrapper that
    reads from environment variables.
    """
    if ":" not in spec:
        raise ValueError(
            f"expected 'module.path:ClassName', got {spec!r}"
        )
    module_path, class_name = spec.rsplit(":", 1)
    import importlib

    module = importlib.import_module(module_path)
    cls = getattr(module, class_name, None)
    if cls is None:
        raise AttributeError(
            f"class {class_name} not found in module {module_path}"
        )
    return cls()  # type: ignore[no-any-return]
