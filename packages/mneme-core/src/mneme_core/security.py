"""Defensive security scanner for the vault: secret and prompt-injection audit.

This is a *detection* tool, complementary to the redaction kernel
(``mneme_core.privacy.redact``, which neutralises content before it reaches a
derived store) and the MCP injection neutraliser. ``scan_text`` / ``scan_vault``
walk the markdown ground truth and surface:

  * secret-like material (API keys, tokens, JWTs, private-key headers) sitting
    in a note. A match inside a ``<private>...</private>`` wrapper is downgraded
    (it is already marked for redaction); an unwrapped match is high severity.
  * prompt-injection phrasing (instruction-override text, fake role/tool tags)
    that a retrieved memory could use to subvert an agent.

Findings never echo the raw secret: secret evidence is masked, and injection
evidence is passed through ``redact`` before it is surfaced.

No LLM, no network. Pure regex + lookups. Deferred (documented): runtime
capability firewall, human-approval gate, Agent Security Bench adapter, and
data-flow taint tracking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .privacy import redact

# --- Secret detectors (name, pattern) ---------------------------------------
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private_key_header", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|access[_-]?key)\b"
            r"\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+]{16,}"
        ),
    ),
    # Structural JWT: three dot-separated base64url segments. A bearer token
    # pasted into a note carries no assignment keyword, so `assigned_secret`
    # misses it; this detector matches on shape alone.
    #
    # The first segment is anchored on `eyJ` — the base64url encoding of the
    # `{"` that opens every JOSE header — because an unanchored three-segment
    # match would fire on ordinary dotted text (`module.sub-module.name`) in
    # markdown prose. Two-segment strings never match: the third segment is
    # required. No trailing `\b`: base64url may end in `-`, where `\b` would
    # demand a following word character and drop a real match.
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    ),
)

# --- Injection detectors (name, pattern) ------------------------------------
_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ignore_instructions",
        re.compile(
            r"\b(?:ignore|disregard)\b[^.\n]{0,40}"
            r"\b(?:previous|above|prior|earlier|all)\b[^.\n]{0,20}\binstructions?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "forget_context",
        re.compile(
            r"\bforget\b[^.\n]{0,20}\b(?:everything|all|the above|previous)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "role_override",
        re.compile(
            r"\byou are now\b|\bact as\b[^.\n]{0,30}\b(?:admin|root|system|developer)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "fake_role_tag",
        re.compile(
            r"</?\s*(?:system|assistant|tool|tool_call|function_call)\s*>",
            re.IGNORECASE,
        ),
    ),
    (
        "system_prompt_inject",
        re.compile(r"^\s*system\s*:\s*\S", re.IGNORECASE | re.MULTILINE),
    ),
)


@dataclass(frozen=True)
class Finding:
    """A single security finding. Evidence is masked/redacted — never raw secret."""

    kind: str  # "secret" | "injection"
    detector: str
    line: int  # 1-based
    severity: str  # "high" | "medium" | "low"
    evidence: str


def _mask(match_text: str) -> str:
    """Mask a secret match so the finding never carries the raw value."""
    head = match_text[:4]
    return f"{head}… (masked, {len(match_text)} chars)"


def scan_text(text: str) -> list[Finding]:
    """Scan a block of text for secret and injection findings.

    A secret match inside a ``<private>...</private>`` wrapper on the same line
    is downgraded to ``low`` (already marked for redaction). Injection evidence
    is passed through ``redact`` before being surfaced.
    """
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        wrapped = "<private>" in line and "</private>" in line
        for name, pattern in _SECRET_PATTERNS:
            match = pattern.search(line)
            if match is not None:
                findings.append(
                    Finding(
                        kind="secret",
                        detector=name,
                        line=lineno,
                        severity="low" if wrapped else "high",
                        evidence=_mask(match.group(0)),
                    )
                )
        for name, pattern in _INJECTION_PATTERNS:
            match = pattern.search(line)
            if match is not None:
                findings.append(
                    Finding(
                        kind="injection",
                        detector=name,
                        line=lineno,
                        severity="medium",
                        evidence=redact(match.group(0))[:80],
                    )
                )
    return findings


def scan_vault(vault_root: Path) -> dict[str, list[Finding]]:
    """Scan every markdown file under ``vault_root`` for security findings.

    Returns a mapping of vault-relative posix path -> findings, including only
    files that have at least one finding. Symlinks that escape the vault are
    skipped (mirrors the indexer's containment guard). Never raises on a single
    unreadable file.
    """
    out: dict[str, list[Finding]] = {}
    root_resolved = vault_root.resolve()
    for md_path in sorted(vault_root.rglob("*.md")):
        try:
            resolved = md_path.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(root_resolved):
            continue
        try:
            text = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        findings = scan_text(text)
        if findings:
            rel = str(md_path.relative_to(vault_root)).replace("\\", "/")
            out[rel] = findings
    return out


def summarize(results: dict[str, list[Finding]]) -> dict[str, Any]:
    """Aggregate scan results into counts by kind and severity."""
    by_kind: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    total = 0
    for findings in results.values():
        for f in findings:
            total += 1
            by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
            by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
    return {
        "files_flagged": len(results),
        "total_findings": total,
        "by_kind": by_kind,
        "by_severity": by_severity,
    }
