"""Data-flow taint tracking for mneme-core (data-not-instruction invariant).

Every piece of content retrieved from external sources or machine-derived from
the vault must carry a ``TaintLabel`` before it can participate in any
capability decision.  The central rule is simple: only ``USER``-labelled
content is ever considered instruction-safe; ``UNTRUSTED`` and ``DERIVED``
content is *data*, never an instruction (OWASP data-not-instruction; plan
invariant 4).

No LLM, no network, no IO, no clock.  Pure, deterministic, side-effect-free.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum


class TaintLabel(str, Enum):  # noqa: UP042
    """Provenance-based taint classification for vault content.

    * ``UNTRUSTED`` — external or unknown-origin content.  Treated as pure
      data; never elevated to an instruction.
    * ``DERIVED``   — machine-derived (extracted or generated) from the vault.
      May be accurate but has not been authored by the operator.
    * ``USER``       — operator-authored vault content.  The only label that
      passes ``is_instruction_safe``.

    Inherits ``str`` so instances serialise directly as JSON strings and
    compare equal to their string values without an extra ``.value`` call.
    The ``UP042`` noqa suppresses the Ruff suggestion to use ``StrEnum``
    (Python 3.11+) which would break the ``str`` + ``Enum`` pattern already
    used elsewhere in the codebase.
    """

    UNTRUSTED = "UNTRUSTED"
    DERIVED = "DERIVED"
    USER = "USER"


@dataclass(frozen=True)
class TaintedSpan:
    """An immutable text span annotated with its taint label and provenance."""

    text: str
    label: TaintLabel
    source: str  # provenance hint (e.g. vault path or backend name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DERIVED_TRUSTS: frozenset[str] = frozenset({"extracted", "generated", "derived"})


def taint_for_trust(trust: str) -> TaintLabel:
    """Map a provenance trust string to a ``TaintLabel``.

    Mapping rules:

    * ``"user"``                           → ``USER``
    * ``"extracted"`` / ``"generated"`` / ``"derived"`` → ``DERIVED``
    * ``"external"``                       → ``UNTRUSTED``
    * any unknown or empty string          → ``UNTRUSTED``  (safe default —
      deny benefit of the doubt)
    """
    normalised = trust.strip().lower()
    if normalised == "user":
        return TaintLabel.USER
    if normalised in _DERIVED_TRUSTS:
        return TaintLabel.DERIVED
    # "external" and every unknown / empty value → UNTRUSTED
    return TaintLabel.UNTRUSTED


def taint_span(text: str, *, trust: str, source: str) -> TaintedSpan:
    """Build a ``TaintedSpan`` by resolving *trust* via ``taint_for_trust``."""
    return TaintedSpan(text=text, label=taint_for_trust(trust), source=source)


def is_instruction_safe(span: TaintedSpan) -> bool:
    """Return ``True`` only when the span's label is ``USER``.

    ``UNTRUSTED`` and ``DERIVED`` content is data, never to be treated as an
    instruction (OWASP data-not-instruction; plan invariant 4).
    """
    return span.label is TaintLabel.USER


def propagate(labels: Iterable[TaintLabel]) -> TaintLabel:
    """Compute the most-restrictive taint label across a collection.

    Rules (most-restrictive-wins):

    1. Any ``UNTRUSTED`` in the input → ``UNTRUSTED``.
    2. Any ``DERIVED`` (with no ``UNTRUSTED``) → ``DERIVED``.
    3. All ``USER`` → ``USER``.
    4. Empty input → ``UNTRUSTED`` (no provenance known: fail closed, so a
       dropped/missing provenance record is never silently treated as trusted).

    Deterministic.
    """
    saw_label = False
    result = TaintLabel.USER
    for label in labels:
        saw_label = True
        if label is TaintLabel.UNTRUSTED:
            return TaintLabel.UNTRUSTED
        if label is TaintLabel.DERIVED:
            result = TaintLabel.DERIVED
    if not saw_label:
        return TaintLabel.UNTRUSTED
    return result
