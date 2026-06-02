"""Tests for mneme_core.modes: the domain mode policy registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mneme_core.modes import (
    artifact_upload_allowed,
    external_extraction_allowed,
    get_mode,
    list_modes,
    load_user_modes,
    mode_from_dict,
    resolve_modes,
    validate_modes,
)
from mneme_core.vault.frontmatter import KNOWN_TYPES


class TestRegistry:
    def test_list_modes_returns_builtins_sorted(self) -> None:
        names = list_modes()
        assert names == sorted(names)
        assert set(names) == {"code", "research", "clinical-research", "security-review"}

    def test_get_mode_known(self) -> None:
        mode = get_mode("code")
        assert mode is not None
        assert mode.name == "code"

    def test_get_mode_unknown_is_none(self) -> None:
        assert get_mode("does-not-exist") is None


class TestRegistryIntegrity:
    def test_validate_modes_is_clean(self) -> None:
        assert validate_modes() == []

    def test_allowed_types_are_canonical(self) -> None:
        for name in list_modes():
            mode = get_mode(name)
            assert mode is not None
            assert mode.allowed_types <= KNOWN_TYPES, name

    def test_default_type_is_allowed(self) -> None:
        for name in list_modes():
            mode = get_mode(name)
            assert mode is not None
            assert mode.write.default_type in mode.allowed_types

    def test_redaction_required_everywhere(self) -> None:
        for name in list_modes():
            mode = get_mode(name)
            assert mode is not None
            assert mode.privacy.redaction_required is True


class TestPrivacyPolicyPA6:
    def test_clinical_research_blocks_external_extraction(self) -> None:
        # PA6: clinical / PII data must never auto-leave the vault.
        assert external_extraction_allowed("clinical-research") is False
        assert artifact_upload_allowed("clinical-research") is False

    def test_code_mode_allows_extraction(self) -> None:
        assert external_extraction_allowed("code") is True
        assert artifact_upload_allowed("code") is True

    def test_security_review_blocks_extraction(self) -> None:
        assert external_extraction_allowed("security-review") is False

    def test_unknown_mode_denies_by_default(self) -> None:
        # An unrecognised context is treated as the safe (deny) default.
        assert external_extraction_allowed("nope") is False
        assert artifact_upload_allowed("nope") is False


# ---------------------------------------------------------------------------
# mode_from_dict
# ---------------------------------------------------------------------------


class TestModeFromDict:
    def _minimal(self) -> dict[str, object]:
        return {
            "language": "en",
            "allowed_types": ["topic", "reference"],
        }

    def test_builds_valid_mode(self) -> None:
        mode = mode_from_dict("mymode", self._minimal())
        assert mode.name == "mymode"
        assert mode.language == "en"
        assert mode.allowed_types == frozenset({"topic", "reference"})
        assert mode.write.default_type in mode.allowed_types
        assert mode.privacy.redaction_required is True

    def test_rejects_unknown_allowed_type(self) -> None:
        data = self._minimal()
        data["allowed_types"] = ["topic", "not_a_real_type"]  # type: ignore[list-item]
        with pytest.raises(ValueError, match="unknown types"):
            mode_from_dict("bad", data)

    def test_forces_redaction_required_true(self) -> None:
        data = self._minimal()
        data["privacy"] = {"block_external_extraction": False, "block_artifact_upload": False}
        # Even if someone were to sneak in redaction_required=False via data,
        # mode_from_dict always sets it True (the key is simply ignored).
        mode = mode_from_dict("safe", data)
        assert mode.privacy.redaction_required is True

    def test_rejects_default_type_outside_allowed_types(self) -> None:
        data = self._minimal()
        data["write"] = {"default_type": "claim"}  # "claim" not in allowed_types
        with pytest.raises(ValueError, match="not in allowed_types"):
            mode_from_dict("bad", data)

    def test_default_type_falls_back_to_first_sorted(self) -> None:
        # No "write" key — default_type should be first sorted allowed_type.
        mode = mode_from_dict("auto", {"language": "tr", "allowed_types": ["topic", "reference"]})
        assert mode.write.default_type == "reference"  # "reference" < "topic" alphabetically

    def test_optional_write_fields(self) -> None:
        data: dict[str, object] = {
            "language": "en",
            "allowed_types": ["topic"],
            "write": {"default_type": "topic", "required_tags": ["important"]},
        }
        mode = mode_from_dict("tagged", data)
        assert mode.write.required_tags == ("important",)

    def test_privacy_block_flags_read_from_config(self) -> None:
        data = self._minimal()
        data["privacy"] = {"block_external_extraction": True, "block_artifact_upload": True}
        mode = mode_from_dict("locked", data)
        assert mode.privacy.block_external_extraction is True
        assert mode.privacy.block_artifact_upload is True
        assert mode.privacy.redaction_required is True  # invariant preserved

    def test_retrieval_defaults(self) -> None:
        mode = mode_from_dict("r", self._minimal())
        assert mode.retrieval.profile == "standard"
        assert mode.retrieval.default_backends == ("fts5",)

    def test_custom_retrieval(self) -> None:
        data = self._minimal()
        data["retrieval"] = {"profile": "full", "default_backends": ["fts5", "kg"]}
        mode = mode_from_dict("full", data)
        assert mode.retrieval.profile == "full"
        assert mode.retrieval.default_backends == ("fts5", "kg")


# ---------------------------------------------------------------------------
# load_user_modes
# ---------------------------------------------------------------------------

_GOOD_MODE_JSON: dict[str, object] = {
    "mymode": {
        "language": "tr",
        "allowed_types": ["topic", "reference"],
        "write": {"default_type": "topic"},
    }
}


class TestLoadUserModes:
    def test_builds_user_mode_from_temp_json(self, tmp_path: Path) -> None:
        cfg = tmp_path / "modes.json"
        cfg.write_text(json.dumps(_GOOD_MODE_JSON), encoding="utf-8")
        result = load_user_modes(cfg)
        assert "mymode" in result
        assert result["mymode"].language == "tr"

    def test_rejects_builtin_name_collision(self, tmp_path: Path) -> None:
        cfg = tmp_path / "modes.json"
        payload = {
            "code": {  # collides with built-in
                "language": "en",
                "allowed_types": ["topic"],
            }
        }
        cfg.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match="collides with a built-in"):
            load_user_modes(cfg)

    def test_raises_on_malformed_json(self, tmp_path: Path) -> None:
        cfg = tmp_path / "modes.json"
        cfg.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid JSON"):
            load_user_modes(cfg)

    def test_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_user_modes(tmp_path / "nonexistent.json")

    def test_empty_object_returns_empty_dict(self, tmp_path: Path) -> None:
        cfg = tmp_path / "modes.json"
        cfg.write_text("{}", encoding="utf-8")
        assert load_user_modes(cfg) == {}

    def test_rejects_invalid_mode_shape(self, tmp_path: Path) -> None:
        cfg = tmp_path / "modes.json"
        payload = {"badmode": {"language": "en", "allowed_types": ["not_a_type"]}}
        cfg.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match="unknown types"):
            load_user_modes(cfg)


# ---------------------------------------------------------------------------
# resolve_modes
# ---------------------------------------------------------------------------


class TestResolveModes:
    def test_absent_config_returns_builtins_only(self, tmp_path: Path) -> None:
        # vault_root with no .mneme/modes.json
        result = resolve_modes(vault_root=tmp_path)
        assert set(result.keys()) == {"code", "research", "clinical-research", "security-review"}

    def test_with_config_path_returns_builtins_plus_user(self, tmp_path: Path) -> None:
        cfg = tmp_path / "modes.json"
        cfg.write_text(json.dumps(_GOOD_MODE_JSON), encoding="utf-8")
        result = resolve_modes(config_path=cfg)
        assert "mymode" in result
        # built-ins still present
        assert "code" in result

    def test_vault_root_with_modes_json_loads_it(self, tmp_path: Path) -> None:
        mneme_dir = tmp_path / ".mneme"
        mneme_dir.mkdir()
        (mneme_dir / "modes.json").write_text(json.dumps(_GOOD_MODE_JSON), encoding="utf-8")
        result = resolve_modes(vault_root=tmp_path)
        assert "mymode" in result
        assert "code" in result

    def test_no_args_returns_builtins(self) -> None:
        # resolve_modes() with no args should return built-ins only
        # (CWD likely has no .mneme/modes.json in test env).
        result = resolve_modes()
        builtin_names = {"code", "research", "clinical-research", "security-review"}
        assert builtin_names.issubset(result.keys())

    def test_invalid_config_propagates_value_error(self, tmp_path: Path) -> None:
        cfg = tmp_path / "modes.json"
        cfg.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ValueError):
            resolve_modes(config_path=cfg)


# ---------------------------------------------------------------------------
# Optional registry parameter on enforcement functions
# ---------------------------------------------------------------------------


class TestOptionalRegistryParam:
    def _merged(self, tmp_path: Path) -> dict[str, object]:
        from mneme_core.modes import Mode, PrivacyPolicy, RetrievalPolicy, WritePolicy

        extra = Mode(
            name="mymode",
            language="en",
            allowed_types=frozenset({"topic"}),
            write=WritePolicy(default_type="topic"),
            retrieval=RetrievalPolicy(profile="standard"),
            privacy=PrivacyPolicy(
                redaction_required=True,
                block_external_extraction=False,
                block_artifact_upload=False,
            ),
        )
        from mneme_core.modes import _BUILTIN

        merged: dict[str, object] = dict(_BUILTIN)
        merged["mymode"] = extra  # type: ignore[assignment]
        return merged

    def test_external_extraction_allowed_with_registry(self, tmp_path: Path) -> None:
        registry = self._merged(tmp_path)
        assert external_extraction_allowed("mymode", registry=registry)  # type: ignore[arg-type]

    def test_unknown_still_denies_with_custom_registry(self, tmp_path: Path) -> None:
        registry = self._merged(tmp_path)
        assert not external_extraction_allowed("ghost", registry=registry)  # type: ignore[arg-type]

    def test_list_modes_with_registry(self, tmp_path: Path) -> None:
        registry = self._merged(tmp_path)
        names = list_modes(registry=registry)  # type: ignore[arg-type]
        assert "mymode" in names
        assert names == sorted(names)

    def test_validate_modes_with_custom_registry(self, tmp_path: Path) -> None:
        registry = self._merged(tmp_path)
        problems = validate_modes(registry=registry)  # type: ignore[arg-type]
        assert problems == []
