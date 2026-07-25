"""Validate the Claude Code plugin bundle and marketplace metadata."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
EXPECTED_HOOK_COMMANDS = {
    "PostToolUse": "mneme hook post-tool-use",
    "SessionStart": "mneme hook session-start",
    "Stop": "mneme hook stop",
    "PreCompact": "mneme hook pre-compact",
    "SessionEnd": "mneme hook session-end",
    "UserPromptSubmit": "mneme hook user-prompt-submit",
}
EXPECTED_LEGACY_HOOKS = {
    "PostToolUse",
    "SessionStart",
    "Stop",
    "PreCompact",
    "SessionEnd",
}
EXPECTED_COMMANDS = {
    "/mneme:prime": "commands/prime.md",
    "/mneme:recall": "commands/recall.md",
    "/mneme:migrate": "commands/migrate.md",
}
EXPECTED_SKILLS = {"mneme-prime", "mneme-search"}


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _read_json(path: Path, label: str, errors: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        errors.append(f"missing {label}: {path}")
        return None
    try:
        return _load_json(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"invalid {label}: {exc}")
        return None


def _required_string(
    payload: dict[str, Any],
    key: str,
    label: str,
    errors: list[str],
) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}.{key} must be a non-empty string")
        return None
    return value


def _validate_native_manifest(
    plugin_root: Path,
    errors: list[str],
) -> dict[str, Any] | None:
    manifest = _read_json(
        plugin_root / ".claude-plugin" / "plugin.json",
        "native Claude manifest",
        errors,
    )
    if manifest is None:
        return None

    if _required_string(manifest, "name", "native manifest", errors) != "mneme":
        errors.append("native manifest.name must be mneme")
    version = _required_string(manifest, "version", "native manifest", errors)
    if version is not None and SEMVER_RE.fullmatch(version) is None:
        errors.append("native manifest.version must be strict MAJOR.MINOR.PATCH semver")
    _required_string(manifest, "description", "native manifest", errors)
    if manifest.get("license") != "Apache-2.0":
        errors.append("native manifest.license must be Apache-2.0")

    author = manifest.get("author")
    if not isinstance(author, dict):
        errors.append("native manifest.author must be an object")
    elif not isinstance(author.get("name"), str) or not author["name"].strip():
        errors.append("native manifest.author.name must be a non-empty string")
    return manifest


def _validate_legacy_manifest(
    plugin_root: Path,
    errors: list[str],
) -> dict[str, Any] | None:
    manifest = _read_json(plugin_root / "plugin.json", "legacy Claude manifest", errors)
    if manifest is None:
        return None

    if _required_string(manifest, "name", "legacy manifest", errors) != "mneme":
        errors.append("legacy manifest.name must be mneme")
    version = _required_string(manifest, "version", "legacy manifest", errors)
    if version is not None and SEMVER_RE.fullmatch(version) is None:
        errors.append("legacy manifest.version must be strict MAJOR.MINOR.PATCH semver")
    if manifest.get("license") != "Apache-2.0":
        errors.append("legacy manifest.license must be Apache-2.0")

    engines = manifest.get("engines")
    expected_engines = {
        "claude-code": ">=2.0.0",
        "python": ">=3.11",
        "node": ">=22",
    }
    if engines != expected_engines:
        errors.append(f"legacy manifest.engines must equal {expected_engines}")

    hooks = manifest.get("hooks")
    if not isinstance(hooks, dict) or set(hooks) != EXPECTED_LEGACY_HOOKS:
        errors.append(
            "legacy manifest.hooks must declare exactly "
            f"{sorted(EXPECTED_LEGACY_HOOKS)}"
        )
    else:
        for event, config in hooks.items():
            if not isinstance(config, dict):
                errors.append(f"legacy manifest hook {event} must be an object")
                continue
            module = config.get("module")
            if not isinstance(module, str) or not module.startswith("mneme_cc_plugin.hooks."):
                errors.append(f"legacy manifest hook {event} has an invalid module")
            timeout = config.get("timeout_ms")
            if not isinstance(timeout, int) or timeout <= 0:
                errors.append(f"legacy manifest hook {event} needs a positive timeout_ms")

    if manifest.get("commands") != EXPECTED_COMMANDS:
        errors.append("legacy manifest.commands must declare the three mneme commands")
    skills = manifest.get("skills")
    expected_skill_paths = [f"skills/{name}" for name in sorted(EXPECTED_SKILLS)]
    if not isinstance(skills, list) or sorted(skills) != expected_skill_paths:
        errors.append(f"legacy manifest.skills must equal {expected_skill_paths}")
    mcp_servers = manifest.get("mcp_servers")
    if mcp_servers != {"mneme": {"command": "mneme-mcp", "args": []}}:
        errors.append("legacy manifest.mcp_servers must invoke mneme-mcp without arguments")
    return manifest


def _validate_marketplace(
    repo_root: Path,
    expected_version: str | None,
    errors: list[str],
) -> None:
    marketplace = _read_json(
        repo_root / ".claude-plugin" / "marketplace.json",
        "Claude marketplace manifest",
        errors,
    )
    if marketplace is None:
        return
    if marketplace.get("name") != "mneme":
        errors.append("marketplace.name must be mneme")
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list) or len(plugins) != 1 or not isinstance(plugins[0], dict):
        errors.append("marketplace.plugins must contain exactly one plugin object")
        return
    entry = plugins[0]
    if entry.get("name") != "mneme":
        errors.append("marketplace plugin name must be mneme")
    if entry.get("source") != "./packages/mneme-cc-plugin":
        errors.append("marketplace plugin source must be ./packages/mneme-cc-plugin")
    if entry.get("license") != "Apache-2.0":
        errors.append("marketplace plugin license must be Apache-2.0")
    if expected_version is not None and entry.get("version") != expected_version:
        errors.append("marketplace plugin version must match the native Claude manifest")


def _validate_mcp(plugin_root: Path, errors: list[str]) -> None:
    payload = _read_json(plugin_root / ".mcp.json", "Claude .mcp.json", errors)
    if payload is None:
        return
    expected = {"mcpServers": {"mneme": {"command": "mneme-mcp", "args": []}}}
    if payload != expected:
        errors.append("Claude .mcp.json must invoke only mneme-mcp without arguments")


def _validate_hooks(plugin_root: Path, errors: list[str]) -> None:
    payload = _read_json(plugin_root / "hooks" / "hooks.json", "Claude hooks", errors)
    if payload is None:
        return
    hooks = payload.get("hooks")
    if not isinstance(hooks, dict) or set(hooks) != set(EXPECTED_HOOK_COMMANDS):
        errors.append(
            "Claude hooks must declare exactly "
            f"{sorted(EXPECTED_HOOK_COMMANDS)}"
        )
        return
    for event, expected_command in EXPECTED_HOOK_COMMANDS.items():
        handlers = hooks[event]
        if not isinstance(handlers, list) or len(handlers) != 1:
            errors.append(f"Claude hook {event} must contain one handler group")
            continue
        nested = handlers[0].get("hooks") if isinstance(handlers[0], dict) else None
        if not isinstance(nested, list) or len(nested) != 1:
            errors.append(f"Claude hook {event} must contain one command handler")
            continue
        entry = nested[0]
        if not isinstance(entry, dict) or entry.get("type") != "command":
            errors.append(f"Claude hook {event} must use a command handler")
        elif entry.get("command") != expected_command:
            errors.append(f"Claude hook {event} must run {expected_command}")


def _validate_skills(plugin_root: Path, errors: list[str]) -> None:
    skills_root = plugin_root / "skills"
    if not skills_root.is_dir():
        errors.append("Claude skills directory is missing")
        return
    actual = {path.name for path in skills_root.iterdir() if path.is_dir()}
    if actual != EXPECTED_SKILLS:
        errors.append(f"Claude skills must equal {sorted(EXPECTED_SKILLS)}")
    for skill_name in sorted(EXPECTED_SKILLS):
        skill_md = skills_root / skill_name / "SKILL.md"
        if not skill_md.is_file():
            errors.append(f"Claude skill {skill_name} is missing SKILL.md")
            continue
        text = skill_md.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            errors.append(f"Claude skill {skill_name} must start with YAML frontmatter")
            continue
        frontmatter = text.split("---", 2)[1]
        if "name:" not in frontmatter or "description:" not in frontmatter:
            errors.append(f"Claude skill {skill_name} needs name and description frontmatter")


def validate_plugin(plugin_root: Path, repo_root: Path | None = None) -> list[str]:
    errors: list[str] = []
    plugin_root = plugin_root.resolve()
    repo_root = repo_root.resolve() if repo_root is not None else plugin_root.parents[1]

    native = _validate_native_manifest(plugin_root, errors)
    legacy = _validate_legacy_manifest(plugin_root, errors)
    native_version = native.get("version") if native is not None else None
    if native is not None and legacy is not None and legacy.get("version") != native_version:
        errors.append("native and legacy Claude manifest versions must match")
    _validate_marketplace(
        repo_root,
        native_version if isinstance(native_version, str) else None,
        errors,
    )
    _validate_mcp(plugin_root, errors)
    _validate_hooks(plugin_root, errors)
    _validate_skills(plugin_root, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plugin_root", type=Path)
    parser.add_argument("--repo-root", type=Path)
    args = parser.parse_args()
    errors = validate_plugin(args.plugin_root, args.repo_root)
    if errors:
        print("Claude plugin validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Claude plugin validation passed: {args.plugin_root.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
