"""Domain mode packs: named policy bundles for write, retrieval, and privacy.

A :class:`Mode` binds a language, an ontology (the subset of memory types the
mode uses), and three policies under a name. Modes are *configuration*: they
constrain how mneme captures, retrieves, and protects memories in a given
domain. The built-in registry is defined in code (deterministic, no file IO,
no network); user-defined modes are loaded from a ``modes.json`` config file
via :func:`load_user_modes` and merged with built-ins via :func:`resolve_modes`.

PA6 invariant: the ``clinical-research`` mode BLOCKS external semantic
extraction and artifact upload, so clinical / PII data never leaves the vault
automatically. The enforcement API (:func:`external_extraction_allowed`,
:func:`artifact_upload_allowed`) is what the KG worker and connectors consult;
wiring those call sites is completed in the connectors phase.

Every mode's ``allowed_types`` is validated against the canonical
``KNOWN_TYPES`` registry by :func:`validate_modes` (exercised in tests), so a
mode can never reference a memory type the kernel does not know.

Security invariant: :func:`mode_from_dict` ALWAYS forces
``PrivacyPolicy.redaction_required=True`` regardless of the config file value.
User config can never disable the kernel redaction invariant.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .vault.frontmatter import KNOWN_TYPES


@dataclass(frozen=True)
class WritePolicy:
    """How a mode captures new memories."""

    default_type: str
    required_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetrievalPolicy:
    """Which retrieval surface a mode uses by default."""

    profile: str  # "lite" | "standard" | "full"
    default_backends: tuple[str, ...] = ("fts5",)


@dataclass(frozen=True)
class PrivacyPolicy:
    """What a mode permits to leave the vault.

    ``redaction_required`` is always honoured by the kernel; the two ``block_*``
    flags gate the optional background extraction / artifact-upload paths.
    """

    redaction_required: bool = True
    block_external_extraction: bool = False
    block_artifact_upload: bool = False


@dataclass(frozen=True)
class Mode:
    """A named domain policy bundle."""

    name: str
    language: str
    allowed_types: frozenset[str]
    write: WritePolicy
    retrieval: RetrievalPolicy
    privacy: PrivacyPolicy


_BUILTIN: dict[str, Mode] = {
    "code": Mode(
        name="code",
        language="en",
        allowed_types=frozenset(
            {
                "session",
                "topic",
                "reference",
                "pattern",
                "trajectory",
                "failure",
                "observation",
                "session_summary",
                "user_prompt",
            }
        ),
        write=WritePolicy(default_type="topic"),
        retrieval=RetrievalPolicy(profile="standard", default_backends=("fts5",)),
        privacy=PrivacyPolicy(),
    ),
    "research": Mode(
        name="research",
        language="tr",
        allowed_types=frozenset(
            {
                "session",
                "topic",
                "reference",
                "claim",
                "observation",
                "session_summary",
                "user_prompt",
            }
        ),
        write=WritePolicy(default_type="topic"),
        retrieval=RetrievalPolicy(profile="standard", default_backends=("fts5",)),
        privacy=PrivacyPolicy(),
    ),
    "clinical-research": Mode(
        name="clinical-research",
        language="tr",
        allowed_types=frozenset({"session", "topic", "reference", "claim", "session_summary"}),
        write=WritePolicy(default_type="topic", required_tags=("clinical",)),
        retrieval=RetrievalPolicy(profile="full", default_backends=("fts5", "kg")),
        # PA6: clinical / PII data must never auto-leave the vault.
        privacy=PrivacyPolicy(
            redaction_required=True,
            block_external_extraction=True,
            block_artifact_upload=True,
        ),
    ),
    "security-review": Mode(
        name="security-review",
        language="en",
        allowed_types=frozenset(
            {"session", "topic", "reference", "pattern", "failure", "observation"}
        ),
        write=WritePolicy(default_type="topic"),
        retrieval=RetrievalPolicy(profile="standard", default_backends=("fts5",)),
        # Security findings are sensitive: keep them fully local by default
        # (block both external extraction and artifact upload).
        privacy=PrivacyPolicy(
            redaction_required=True,
            block_external_extraction=True,
            block_artifact_upload=True,
        ),
    ),
}


def list_modes(registry: Mapping[str, Mode] | None = None) -> list[str]:
    """Return the sorted names of all modes in *registry*.

    When *registry* is ``None`` (the default) the built-in registry is used,
    preserving backwards compatibility with all existing call sites.
    """
    reg = _BUILTIN if registry is None else registry
    return sorted(reg)


def get_mode(name: str, registry: Mapping[str, Mode] | None = None) -> Mode | None:
    """Return the mode named ``name``, or ``None`` if it is absent.

    When *registry* is ``None`` (the default) the built-in registry is used.
    """
    reg = _BUILTIN if registry is None else registry
    return reg.get(name)


def external_extraction_allowed(
    name: str,
    registry: Mapping[str, Mode] | None = None,
) -> bool:
    """Whether the mode permits background external semantic extraction.

    Unknown modes default to ``False`` (deny) — the safe choice for an
    unrecognised context. When *registry* is ``None`` the built-in registry
    is used.
    """
    reg = _BUILTIN if registry is None else registry
    mode = reg.get(name)
    if mode is None:
        return False
    return not mode.privacy.block_external_extraction


def artifact_upload_allowed(
    name: str,
    registry: Mapping[str, Mode] | None = None,
) -> bool:
    """Whether the mode permits uploading derived artifacts off the vault.

    Unknown modes default to ``False`` (deny). When *registry* is ``None``
    the built-in registry is used.
    """
    reg = _BUILTIN if registry is None else registry
    mode = reg.get(name)
    if mode is None:
        return False
    return not mode.privacy.block_artifact_upload


def validate_modes(registry: Mapping[str, Mode] | None = None) -> list[str]:
    """Return a list of integrity problems with the modes in *registry*.

    Checks that every mode's ``allowed_types`` is a subset of the canonical
    ``KNOWN_TYPES`` registry and that each ``write.default_type`` is itself an
    allowed type. An empty list means the registry is internally consistent.
    When *registry* is ``None`` the built-in registry is validated (preserving
    the existing zero-arg call contract).
    """
    reg = _BUILTIN if registry is None else registry
    problems: list[str] = []
    for name, mode in reg.items():
        unknown = mode.allowed_types - KNOWN_TYPES
        if unknown:
            problems.append(f"mode {name!r} references unknown types: {sorted(unknown)}")
        if mode.write.default_type not in mode.allowed_types:
            problems.append(
                f"mode {name!r} default_type {mode.write.default_type!r} not in allowed_types"
            )
    return problems


# ---------------------------------------------------------------------------
# Config-file loading and registry resolution
# ---------------------------------------------------------------------------

_MNEME_CONFIG_SUBDIR = ".mneme"
_MODES_CONFIG_FILENAME = "modes.json"


def mode_from_dict(name: str, data: dict[str, Any]) -> Mode:
    """Build a :class:`Mode` from a parsed config dict.

    Expected shape::

        {
            "language": "tr",
            "allowed_types": ["topic", "reference"],
            "write": {"default_type": "topic", "required_tags": ["x"]},
            "retrieval": {"profile": "standard", "default_backends": ["fts5"]},
            "privacy": {
                "block_external_extraction": false,
                "block_artifact_upload": false
            }
        }

    Validation rules:

    * ``allowed_types`` must be a non-empty subset of :data:`KNOWN_TYPES`.
    * ``write.default_type`` must be in ``allowed_types``.

    Security invariant: ``PrivacyPolicy.redaction_required`` is ALWAYS forced
    to ``True`` regardless of the config value.  The kernel redaction invariant
    cannot be disabled by user config.

    Missing sub-dicts use sane defaults: ``write.default_type`` defaults to the
    first ``allowed_type`` sorted; ``retrieval`` defaults to profile
    ``"standard"`` with backend ``("fts5",)``.
    """
    # --- allowed_types -------------------------------------------------------
    raw_types = data.get("allowed_types", [])
    if not isinstance(raw_types, list):
        raise ValueError(f"mode {name!r}: 'allowed_types' must be a list")
    allowed_types: frozenset[str] = frozenset(str(t) for t in raw_types)
    # Check non-empty first so the subset check below never operates on an
    # empty set by coincidence (clearer ordering; both still raise ValueError).
    if not allowed_types:
        raise ValueError(f"mode {name!r}: 'allowed_types' must not be empty")
    unknown = allowed_types - KNOWN_TYPES
    if unknown:
        raise ValueError(
            f"mode {name!r}: allowed_types references unknown types: {sorted(unknown)}"
        )

    # --- write policy --------------------------------------------------------
    write_data: dict[str, Any] = data.get("write") or {}
    default_type: str = write_data.get("default_type") or sorted(allowed_types)[0]
    if default_type not in allowed_types:
        raise ValueError(f"mode {name!r}: write.default_type {default_type!r} not in allowed_types")
    raw_tags = write_data.get("required_tags") or []
    required_tags: tuple[str, ...] = tuple(str(t) for t in raw_tags)
    write = WritePolicy(default_type=default_type, required_tags=required_tags)

    # --- retrieval policy ----------------------------------------------------
    retrieval_data: dict[str, Any] = data.get("retrieval") or {}
    profile: str = retrieval_data.get("profile") or "standard"
    raw_backends = retrieval_data.get("default_backends") or ["fts5"]
    default_backends: tuple[str, ...] = tuple(str(b) for b in raw_backends)
    retrieval = RetrievalPolicy(profile=profile, default_backends=default_backends)

    # --- privacy policy (security invariant: redaction_required always True) -
    privacy_data: dict[str, Any] = data.get("privacy") or {}
    block_ext: bool = bool(privacy_data.get("block_external_extraction", False))
    block_upload: bool = bool(privacy_data.get("block_artifact_upload", False))
    privacy = PrivacyPolicy(
        redaction_required=True,  # invariant: cannot be overridden by config
        block_external_extraction=block_ext,
        block_artifact_upload=block_upload,
    )

    language: str = str(data.get("language") or "en")
    return Mode(
        name=name,
        language=language,
        allowed_types=allowed_types,
        write=write,
        retrieval=retrieval,
        privacy=privacy,
    )


def load_user_modes(path: Path) -> dict[str, Mode]:
    """Read a JSON file mapping ``{mode_name: mode_dict}`` and build each mode.

    Built-in mode names are authoritative: any name that collides with a
    built-in raises :exc:`ValueError` so user config can never weaken a PA6
    mode (``clinical-research`` / ``security-review``).

    Raises:
        FileNotFoundError: if *path* does not exist.
        ValueError: on malformed JSON, invalid mode shape, or name collision
            with a built-in.

    Returns an empty dict for an empty JSON object ``{}``.
    """
    raw_text = path.read_text(encoding="utf-8")
    try:
        raw: Any = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"modes config {path}: invalid JSON — {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"modes config {path}: expected a JSON object, got {type(raw).__name__}")

    result: dict[str, Mode] = {}
    for name, mode_data in raw.items():
        if name in _BUILTIN:
            raise ValueError(
                f"modes config {path}: {name!r} collides with a built-in mode name; "
                "built-ins are authoritative and cannot be overridden by user config"
            )
        if not isinstance(mode_data, dict):
            raise ValueError(f"modes config {path}: value for {name!r} must be a JSON object")
        result[name] = mode_from_dict(name, mode_data)
    return result


def resolve_modes(
    vault_root: Path | None = None,
    *,
    config_path: Path | None = None,
) -> dict[str, Mode]:
    """Return the built-in modes merged with any user-defined modes.

    Resolution order:

    1. If *config_path* is given, load user modes from that file.
    2. Else if *vault_root* is given and
       ``<vault_root>/.mneme/modes.json`` exists, load from there.
    3. Otherwise (no config present) return just the built-ins.

    An absent config file is silently treated as an empty extension (built-ins
    only).  A *present but invalid* file propagates the :exc:`ValueError` from
    :func:`load_user_modes` so the operator knows something is wrong.
    """
    user_modes: dict[str, Mode] = {}
    if config_path is not None:
        user_modes = load_user_modes(config_path)
    elif vault_root is not None:
        candidate = vault_root / _MNEME_CONFIG_SUBDIR / _MODES_CONFIG_FILENAME
        if candidate.exists():
            user_modes = load_user_modes(candidate)

    merged = dict(_BUILTIN)
    merged.update(user_modes)
    return merged
