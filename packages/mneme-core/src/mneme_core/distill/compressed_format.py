"""Three injection format levels for retrieval results.

A vault doc is expensive at first injection (full markdown) but cheap
on re-injection if the model has already seen it. ``compressed_format``
expresses that:

* ``full`` — the complete markdown section, used for first-injection.
* ``keypoints`` — a bulleted summary of facts plus the vault path,
  used for re-injection of the same doc or when context is tight.
* ``ref`` — a single line ``see vault://path/X`` used when the doc has
  already been seen this session and there is no room for keypoints.

``select_format`` picks the appropriate level from two signals:

1. Has the doc already been injected this session?
2. How tight is the context budget?

``render_injection`` then takes a parsed ``InjectionInput`` and emits
the right markdown shape for the chosen level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class InjectionFormat(str, Enum):
    FULL = "full"
    KEYPOINTS = "keypoints"
    REF = "ref"


@dataclass
class InjectionInput:
    """Minimal record the injector hands to ``render_injection``.

    ``key_points`` is an already-summarized bullet list. Mneme does
    not summarize on the fly during injection; the indexer extracts
    these once at index time so injection stays deterministic.
    """

    path: str
    title: str
    body: str = ""
    key_points: list[str] = field(default_factory=list)


HIGH_PRESSURE_THRESHOLD = 0.75


def select_format(
    *,
    has_been_injected: bool,
    context_pressure: float,
) -> InjectionFormat:
    """Pick a format from the two signals.

    ``context_pressure`` is in ``[0.0, 1.0]``: 0.0 is an empty
    context, 1.0 means the budget is fully consumed. The rule:

    * First injection at low pressure: ``full``.
    * First injection at high pressure: ``keypoints``.
    * Re-injection at low pressure: ``keypoints``.
    * Re-injection at high pressure: ``ref``.
    """
    pressure = max(0.0, min(1.0, float(context_pressure)))
    if has_been_injected:
        return InjectionFormat.REF if pressure >= HIGH_PRESSURE_THRESHOLD else InjectionFormat.KEYPOINTS
    return InjectionFormat.KEYPOINTS if pressure >= HIGH_PRESSURE_THRESHOLD else InjectionFormat.FULL


def render_injection(
    doc: InjectionInput, fmt: InjectionFormat
) -> str:
    """Render the markdown payload for a given format level."""
    title = doc.title or doc.path
    if fmt is InjectionFormat.FULL:
        body = doc.body.strip()
        if body:
            return f"## {title}\n\n{body}\n"
        return f"## {title}\n"
    if fmt is InjectionFormat.KEYPOINTS:
        if doc.key_points:
            bullets = "\n".join(f"- {kp}" for kp in doc.key_points)
            return f"## {title}\n\n{bullets}\n\nSee `{doc.path}`.\n"
        return f"## {title}\n\nSee `{doc.path}`.\n"
    # REF
    return f"see vault://{doc.path}\n"
