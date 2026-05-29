"""Validate the repo-local Antigravity plugin package shape.

Mirrors the structure of validate_codex_plugin.py for the Gemini
extension contract used by mneme's Antigravity support.

Checks:
- ``gemini-extension.json``: required fields, mcpServers shape.
- ``hooks/hooks.json``: valid event names and ``mneme hook <event>``
  command strings.
- ``skills/``: each skill dir has a ``SKILL.md`` with YAML frontmatter
  containing ``name`` and ``description``.
- ``GEMINI.md``: context rules file is present and non-empty.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

# Valid hook event names in the Antigravity / Claude-Code-compatible schema.
VALID_HOOK_EVENTS = {"SessionStart", "PostToolUse", "Stop", "PreCompact"}

# Every hook command must route through the shared mneme hook dispatcher.
_HOOK_CMD_RE = re.compile(r"^mneme hook (session-start|post-tool-use|stop|pre-compact)$")

REQUIRED_MANIFEST_FIELDS = {"name", "version", "description", "mcpServers"}


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _string(payload: dict[str, Any], key: str, errors: list[str]) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{key} must be a non-empty string")
        return None
    return value


def _validate_manifest(plugin_root: Path, errors: list[str]) -> None:
    manifest_path = plugin_root / "gemini-extension.json"
    if not manifest_path.is_file():
        errors.append("missing gemini-extension.json")
        return
    try:
        manifest = _load_json(manifest_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"invalid gemini-extension.json: {exc}")
        return

    missing = sorted(REQUIRED_MANIFEST_FIELDS - set(manifest))
    errors.extend(f"gemini-extension.json missing required field: {k}" for k in missing)

    _string(manifest, "name", errors)
    version = _string(manifest, "version", errors)
    if version is not None and SEMVER_RE.fullmatch(version) is None:
        errors.append("version must be strict MAJOR.MINOR.PATCH semver")

    _string(manifest, "description", errors)

    mcp = manifest.get("mcpServers")
    if not isinstance(mcp, dict):
        errors.append("mcpServers must be an object")
    else:
        if "mneme" not in mcp:
            errors.append("mcpServers must contain a 'mneme' entry")
        else:
            server = mcp["mneme"]
            if not isinstance(server, dict):
                errors.append("mcpServers.mneme must be an object")
            else:
                if not isinstance(server.get("command"), str) or not server["command"].strip():
                    errors.append("mcpServers.mneme.command must be a non-empty string")
                env = server.get("env")
                if env is not None:
                    if not isinstance(env, dict):
                        errors.append("mcpServers.mneme.env must be an object when present")
                    elif "MNEME_VAULT" not in env:
                        errors.append("mcpServers.mneme.env must contain MNEME_VAULT")


def _validate_hooks(plugin_root: Path, errors: list[str]) -> None:
    hooks_path = plugin_root / "hooks" / "hooks.json"
    if not hooks_path.is_file():
        errors.append("missing hooks/hooks.json")
        return
    try:
        payload = _load_json(hooks_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"invalid hooks/hooks.json: {exc}")
        return

    hooks_map = payload.get("hooks")
    if not isinstance(hooks_map, dict):
        errors.append("hooks/hooks.json: top-level 'hooks' must be an object")
        return

    unknown_events = sorted(set(hooks_map) - VALID_HOOK_EVENTS)
    errors.extend(f"hooks/hooks.json: unknown event name '{e}'" for e in unknown_events)

    for event, handlers in hooks_map.items():
        if not isinstance(handlers, list):
            errors.append(f"hooks/hooks.json: {event} must be an array")
            continue
        for h in handlers:
            if not isinstance(h, dict):
                continue
            inner = h.get("hooks") or []
            for entry in inner:
                if not isinstance(entry, dict):
                    continue
                cmd = entry.get("command", "")
                if not _HOOK_CMD_RE.match(cmd):
                    errors.append(
                        f"hooks/hooks.json: {event} command {cmd!r} does not match "
                        f"'mneme hook <event>' pattern"
                    )


def _validate_skills(plugin_root: Path, errors: list[str]) -> None:
    skills_root = plugin_root / "skills"
    if not skills_root.is_dir():
        errors.append("skills directory is missing")
        return
    skill_dirs = sorted(p for p in skills_root.iterdir() if p.is_dir())
    if not skill_dirs:
        errors.append("skills directory contains no skill subdirectories")
    for skill_dir in skill_dirs:
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            errors.append(f"{skill_dir.name} is missing SKILL.md")
            continue
        text = skill_md.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            errors.append(
                f"{skill_dir.name} SKILL.md must start with YAML frontmatter"
            )
            continue
        frontmatter = text.split("---", 2)[1]
        if "name:" not in frontmatter:
            errors.append(f"{skill_dir.name} SKILL.md frontmatter lacks name")
        if "description:" not in frontmatter:
            errors.append(f"{skill_dir.name} SKILL.md frontmatter lacks description")


def _validate_gemini_md(plugin_root: Path, errors: list[str]) -> None:
    gemini_md = plugin_root / "GEMINI.md"
    if not gemini_md.is_file():
        errors.append("missing GEMINI.md")
        return
    if not gemini_md.read_text(encoding="utf-8").strip():
        errors.append("GEMINI.md is empty")


def validate_plugin(plugin_root: Path) -> list[str]:
    errors: list[str] = []
    _validate_manifest(plugin_root, errors)
    _validate_hooks(plugin_root, errors)
    _validate_skills(plugin_root, errors)
    _validate_gemini_md(plugin_root, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plugin_root", type=Path)
    args = parser.parse_args()
    errors = validate_plugin(args.plugin_root.resolve())
    if errors:
        print("Antigravity plugin validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Antigravity plugin validation passed: {args.plugin_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
