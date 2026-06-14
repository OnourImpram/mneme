"""Deterministic salience scoring for CCE working-set items.

Pure functions only — no IO, no randomness, no external state. The
scoring model is a weighted sum of signal values, clamped to [0, 1].

The weights dict is operator-configurable via :class:`CceConfig` and
defaults are defined in :mod:`mneme_core.cce.config`.
"""

from __future__ import annotations


def score_item(
    kind: str,
    signals: dict[str, float],
    weights: dict[str, float],
) -> float:
    """Compute a salience score for one working-set item.

    The score is a weighted sum of the signal values that appear in
    ``weights``, clamped to [0.0, 1.0]. Signals not present in
    ``weights`` are ignored; weights with no matching signal contribute
    zero. The ``kind`` of the item is looked up in ``weights`` as an
    additional signal with value 1.0 when present.

    Args:
        kind: item kind string (e.g. ``"recent_edit"``, ``"todo"``).
        signals: mapping of signal name to raw signal value.
        weights: mapping of signal name to weight coefficient.

    Returns:
        Salience score in [0.0, 1.0].
    """
    # Include the item kind as a boolean signal (1.0) if it has a weight.
    effective_signals: dict[str, float] = dict(signals)
    if kind not in effective_signals:
        effective_signals[kind] = 1.0

    total = 0.0
    for signal_name, weight in weights.items():
        value = effective_signals.get(signal_name, 0.0)
        total += float(weight) * float(value)

    # Clamp to [0, 1].
    return max(0.0, min(1.0, total))
