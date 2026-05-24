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
    _string(manifest, "name", errors)
    version = _string(manifest, "version", errors)
    if version is not None and SEMVER_RE.fullmatch(version) is None:
        errors.append("version must be strict MAJOR.MINOR.PATCH semver")
    _string(manifest, "description", errors)

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
        for skill_dir in sorted(path for path in skills_root.iterdir() if path.is_dir()):
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
            if not isinstance(mcp.get("mcpServers"), dict):
                errors.append(".mcp.json mcpServers must be an object")

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
        if not isinstance(prompts, list) or not prompts:
            errors.append("interface.defaultPrompt must be a non-empty array")

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
