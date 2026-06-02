"""ProceduralMemory dataclass and vault-ready markdown serialization for AGENTS.md.

Parses an AGENTS.md (or any conventions markdown) into structured procedural
memories, one per level-2 (``##``) heading section.

Design invariants (mirror stacktrace.py / failure.py):
* Pure, deterministic — no clock, no random, no I/O inside parse functions.
* Redact-before-store — ``mneme_core.privacy.redact`` is applied to heading
  text, body text, and fenced-command text before any dataclass field is set.
* Never raises — ``parse_agents_md`` catches all exceptions and returns ``[]``.
* Frozen dataclasses for all public types.
* ``procedural_to_markdown`` accepts ``created: datetime`` injected by caller.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from mneme_core.privacy import redact

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

# Level-2 heading: "## Some Title"  (with optional trailing whitespace)
_H2 = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

# Fenced code block: ```[lang]\n...\n```  (DOTALL so body can span lines)
_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProceduralMemory:
    """A single procedural-memory record extracted from one ## section.

    All string fields have already been passed through ``redact()`` before
    construction; callers must not bypass this by constructing
    ``ProceduralMemory`` directly with raw user content.

    Attributes:
        memory_id     Deterministic UUID5(NAMESPACE_URL, source_path + NUL + heading).
        title         Redacted section heading text (without the "## " prefix).
        content       Redacted section body (prose text, fenced blocks stripped out).
        commands      Tuple of fenced code-block contents found in the section,
                      redacted, in document order.
        source_path   Vault-relative posix path supplied by the caller.
        content_hash  SHA-256 hex of ``redact(full_file_text)``.
        trust         Provenance tier; default ``"user"``.
        confidence    Confidence label; default ``"EXTRACTED"``.
    """

    memory_id: str
    title: str
    content: str
    commands: tuple[str, ...]
    source_path: str
    content_hash: str
    trust: str = field(default="user")
    confidence: str = field(default="EXTRACTED")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_agents_md(text: str, *, source_path: str) -> list[ProceduralMemory]:
    """Parse *text* into a list of :class:`ProceduralMemory` records.

    Each level-2 (``## ``) heading produces one record.  Content that appears
    before the first ``## `` heading (including any level-1 ``# `` title line)
    is discarded — it is typically a document title with no actionable procedure.

    Edge cases:
    * Empty string or whitespace-only → ``[]``.
    * No ``##`` headings → ``[]``.
    * A section with no body text and no commands → ``content=""`` / ``commands=()``.
    * Never raises; all exceptions are swallowed and return ``[]``.

    Args:
        text:        Raw AGENTS.md file content (may be empty).
        source_path: Vault-relative posix path to embed in each record
                     (used for ``memory_id`` derivation and the frontmatter).

    Returns:
        Ordered list of :class:`ProceduralMemory`, one per ``##`` section.
    """
    try:
        return _parse_inner(text, source_path=source_path)
    except Exception:  # noqa: BLE001
        return []


def _parse_inner(text: str, *, source_path: str) -> list[ProceduralMemory]:
    """Inner parser — may raise; wrapped by ``parse_agents_md``."""
    if not text or not text.strip():
        return []

    # content_hash is sha256 of the redacted *full* file text.
    content_hash = hashlib.sha256(redact(text).encode("utf-8")).hexdigest()

    # Walk lines tracking fenced-code state so a "## " line INSIDE a ``` fence is
    # treated as code, NOT as a heading (otherwise a bash comment like
    # "## install deps" would split a phantom section). Collect (heading, body)
    # pairs; any text before the first heading is preamble and discarded.
    sections: list[tuple[str, str]] = []
    current_heading: str | None = None
    current_body: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            if current_heading is not None:
                current_body.append(line)
            continue
        if not in_fence:
            heading_match = _H2.match(line)
            if heading_match is not None:
                if current_heading is not None:
                    sections.append((current_heading, "\n".join(current_body)))
                current_heading = heading_match.group(1).strip()
                current_body = []
                continue
        if current_heading is not None:
            current_body.append(line)
    if current_heading is not None:
        sections.append((current_heading, "\n".join(current_body)))

    if not sections:
        # No ## headings found.
        return []

    memories: list[ProceduralMemory] = []
    for raw_heading, raw_body in sections:
        # Extract fenced commands before removing them from the prose body.
        raw_commands: list[str] = [m.group(1) for m in _FENCE.finditer(raw_body)]

        # Prose content: remove fenced blocks, strip leading/trailing whitespace.
        prose = _FENCE.sub("", raw_body).strip()

        # Redact everything before storing.
        title = redact(raw_heading)
        content = redact(prose)
        commands = tuple(redact(cmd) for cmd in raw_commands)

        # Deterministic UUID5: namespace URL + "source_path\x00heading" (pre-redaction
        # heading used here so identity is stable across redaction-token changes).
        key = f"{source_path}\x00{raw_heading}"
        memory_id = str(uuid.uuid5(uuid.NAMESPACE_URL, key))

        memories.append(
            ProceduralMemory(
                memory_id=memory_id,
                title=title,
                content=content,
                commands=commands,
                source_path=source_path,
                content_hash=content_hash,
            )
        )

    return memories


# ---------------------------------------------------------------------------
# Markdown serializer
# ---------------------------------------------------------------------------


def procedural_to_markdown(mem: ProceduralMemory, *, created: datetime) -> str:
    """Render a :class:`ProceduralMemory` as a vault-ready markdown note.

    Frontmatter fields (manual construction, no yaml import — mirrors
    ``failure_to_markdown`` style):
        id            mem.memory_id
        type          ``"reference"``
        created       *created* normalised to tz-aware UTC ISO 8601 w/ microseconds
        tags          ``["procedure", "agents-md"]``
        source_path   JSON-encoded (safe for arbitrary vault paths)
        content_hash  mem.content_hash
        trust         mem.trust

    Body: ``# {title}``, blank line, prose content, then (if commands are
    present) a ``## Commands`` section with each command as a fenced block.

    The *created* datetime is injected by the caller; this function never
    calls ``datetime.now()``.  All content is already redacted upstream.

    Args:
        mem:     :class:`ProceduralMemory` to render.
        created: Tz-aware (or naive-UTC) datetime for the ``created`` field.

    Returns:
        Vault-ready markdown string (trailing newline included).
    """
    # Normalise to tz-aware UTC.
    ts = created.replace(tzinfo=UTC) if created.tzinfo is None else created.astimezone(UTC)
    created_str = ts.isoformat(timespec="microseconds")

    fm_lines = [
        "---",
        f"id: {mem.memory_id}",
        "type: reference",
        f"created: {created_str}",
        "tags:",
        "  - procedure",
        "  - agents-md",
        f"source_path: {json.dumps(mem.source_path)}",
        f"content_hash: {mem.content_hash}",
        f"trust: {mem.trust}",
        "---",
    ]

    body_lines: list[str] = [
        f"# {mem.title}",
        "",
        mem.content,
    ]

    if mem.commands:
        body_lines.append("")
        body_lines.append("## Commands")
        body_lines.append("")
        for cmd in mem.commands:
            body_lines.append("```")
            body_lines.append(cmd)
            body_lines.append("```")
            body_lines.append("")

    return "\n".join(fm_lines) + "\n" + "\n".join(body_lines) + "\n"
