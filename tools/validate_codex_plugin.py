"""Validate the repo-local Codex plugin manifest shape.

This intentionally mirrors the small subset of the Codex plugin
contract used by mneme so GitHub Actions can validate the public
plugin without relying on a developer's local Codex runtime.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

ALLOWED_TOP_LEVEL = {
    "name",
    "version",
    "description",
    "skills",
    "apps",
    "mcpServers",
    "interface",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
}
REQUIRED_INTERFACE = {
    "displayName",
    "shortDescription",
    "longDescription",
    "developerName",
    "category",
    "capabilities",
}
EXPECTED_SKILLS = {"mneme-prime", "mneme-search"}
EXPECTED_HOOK_COMMANDS = {
    "SessionStart": "mneme hook session-start",
    "PostToolUse": "mneme hook post-tool-use",
    "Stop": "mneme hook stop",
    "PreCompact": "mneme hook pre-compact",
}


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


def _validate_hooks(plugin_root: Path, errors: list[str]) -> None:
    hooks_path = plugin_root / "hooks" / "hooks.json"
    if not hooks_path.is_file():
        errors.append("hooks/hooks.json is missing")
        return
    try:
        payload = _load_json(hooks_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"invalid hooks/hooks.json: {exc}")
        return
    hooks = payload.get("hooks")
    if not isinstance(hooks, dict) or set(hooks) != set(EXPECTED_HOOK_COMMANDS):
        errors.append(
            "hooks/hooks.json must declare exactly "
            f"{sorted(EXPECTED_HOOK_COMMANDS)}"
        )
        return
    for event, expected_command in EXPECTED_HOOK_COMMANDS.items():
        handlers = hooks[event]
        if not isinstance(handlers, list) or len(handlers) != 1:
            errors.append(f"hooks/hooks.json: {event} must contain one handler group")
            continue
        nested = handlers[0].get("hooks") if isinstance(handlers[0], dict) else None
        if not isinstance(nested, list) or len(nested) != 1:
            errors.append(f"hooks/hooks.json: {event} must contain one command handler")
            continue
        entry = nested[0]
        if not isinstance(entry, dict) or entry.get("type") != "command":
            errors.append(f"hooks/hooks.json: {event} must use a command handler")
        elif entry.get("command") != expected_command:
            errors.append(f"hooks/hooks.json: {event} must run {expected_command}")


def validate_plugin(plugin_root: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    if not manifest_path.is_file():
        return ["missing .codex-plugin/plugin.json"]
    try:
        manifest = _load_json(manifest_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return [f"invalid plugin.json: {exc}"]

    unknown = sorted(set(manifest) - ALLOWED_TOP_LEVEL)
    errors.extend(f"unsupported plugin.json field: {key}" for key in unknown)
    if _string(manifest, "name", errors) != "mneme":
        errors.append("name must be mneme")
    version = _string(manifest, "version", errors)
    if version is not None and SEMVER_RE.fullmatch(version) is None:
        errors.append("version must be strict MAJOR.MINOR.PATCH semver")
    _string(manifest, "description", errors)
    if manifest.get("license") != "Apache-2.0":
        errors.append("license must be Apache-2.0")

    author = manifest.get("author")
    if not isinstance(author, dict):
        errors.append("author must be an object")
    elif not isinstance(author.get("name"), str) or not author["name"].strip():
        errors.append("author.name must be a non-empty string")

    if manifest.get("skills") != "./skills/":
        errors.append("skills must be ./skills/")
    skills_root = plugin_root / "skills"
    if not skills_root.is_dir():
        errors.append("skills directory is missing")
    else:
        skill_dirs = {path.name: path for path in skills_root.iterdir() if path.is_dir()}
        if set(skill_dirs) != EXPECTED_SKILLS:
            errors.append(f"skills must equal {sorted(EXPECTED_SKILLS)}")
        for skill_dir in sorted(skill_dirs.values()):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                errors.append(f"{skill_dir.name} is missing SKILL.md")
                continue
            text = skill_md.read_text(encoding="utf-8")
            if not text.startswith("---\n"):
                errors.append(f"{skill_dir.name} SKILL.md must start with YAML frontmatter")
                continue
            frontmatter = text.split("---", 2)[1]
            if "name:" not in frontmatter:
                errors.append(f"{skill_dir.name} SKILL.md frontmatter lacks name")
            if "description:" not in frontmatter:
                errors.append(f"{skill_dir.name} SKILL.md frontmatter lacks description")

    if manifest.get("mcpServers") != "./.mcp.json":
        errors.append("mcpServers must be ./.mcp.json")
    mcp_path = plugin_root / ".mcp.json"
    if not mcp_path.is_file():
        errors.append(".mcp.json is missing")
    else:
        try:
            mcp = _load_json(mcp_path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"invalid .mcp.json: {exc}")
        else:
            if set(mcp) != {"mcpServers"}:
                errors.append(".mcp.json must contain only mcpServers")
            expected_mcp = {"mneme": {"command": "mneme-mcp", "args": []}}
            if mcp.get("mcpServers") != expected_mcp:
                errors.append(".mcp.json must invoke only mneme-mcp without arguments")

    interface = manifest.get("interface")
    if not isinstance(interface, dict):
        errors.append("interface must be an object")
    else:
        for key in REQUIRED_INTERFACE:
            if key == "capabilities":
                value = interface.get(key)
                if not isinstance(value, list) or not all(
                    isinstance(item, str) and item.strip() for item in value
                ):
                    errors.append("interface.capabilities must be a string array")
            elif not isinstance(interface.get(key), str) or not interface[key].strip():
                errors.append(f"interface.{key} must be a non-empty string")
        prompts = interface.get("defaultPrompt")
        if (
            not isinstance(prompts, list)
            or not prompts
            or not all(isinstance(prompt, str) and prompt.strip() for prompt in prompts)
        ):
            errors.append("interface.defaultPrompt must be a non-empty string array")

    _validate_hooks(plugin_root, errors)

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plugin_root", type=Path)
    args = parser.parse_args()
    errors = validate_plugin(args.plugin_root.resolve())
    if errors:
        print("Codex plugin validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Codex plugin validation passed: {args.plugin_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
