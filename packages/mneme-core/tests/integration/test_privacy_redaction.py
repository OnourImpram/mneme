"""Cross-store privacy redaction integration tests.

This file is the TDD anchor for P0-3. It:

1. Loads the shared conformance fixture
   (``packages/mneme-mcp/tests/fixtures/redaction_cases.json``) and
   asserts that :func:`mneme_core.privacy.redact` satisfies every case,
   so Python and TypeScript cannot drift from one another.

2. Verifies that every store (staging, kg episode_stage, telemetry,
   patterns, trajectory) redacts ``<private>`` content before writing to
   disk.

Run with::

    pytest packages/mneme-core/tests/integration/test_privacy_redaction.py --no-cov
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mneme_core.compression.staging import StagingConfig, capture_event, redact_private
from mneme_core.kg.episode_stage import KgConfig, stage_event
from mneme_core.patterns import Pattern, store_pattern
from mneme_core.privacy import redact, redact_value
from mneme_core.telemetry.writer import TelemetryConfig, write_event
from mneme_core.trajectory import record_step
from mneme_core.vault.config import VaultConfig

# ---------------------------------------------------------------------------
# Fixture path — shared between Python and TypeScript tests.
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    Path(__file__).parent.parent.parent  # packages/mneme-core
    .parent                               # packages/
    / "mneme-mcp"
    / "tests"
    / "fixtures"
    / "redaction_cases.json"
)


def _load_cases() -> list[dict[str, str]]:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Conformance: privacy.redact vs shared fixture
# ---------------------------------------------------------------------------


class TestPrivacyRedactConformance:
    """Every case in redaction_cases.json must pass through privacy.redact."""

    @pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id"])
    def test_fixture_case(self, case: dict[str, str]) -> None:
        assert redact(case["input"]) == case["expected"], (
            f"Case {case['id']!r}: redact({case['input']!r}) "
            f"!= {case['expected']!r}"
        )


# ---------------------------------------------------------------------------
# redact_value walker
# ---------------------------------------------------------------------------


class TestRedactValue:
    def test_string(self) -> None:
        assert redact_value("a <private>s</private> b") == "a [REDACTED] b"

    def test_dict_recursive(self) -> None:
        result = redact_value({"k": "a <private>s</private> b", "n": 42})
        assert result == {"k": "a [REDACTED] b", "n": 42}

    def test_list_recursive(self) -> None:
        result = redact_value(["<private>x</private>", 1, None])
        assert result == ["[REDACTED]", 1, None]

    def test_nested(self) -> None:
        data: dict[str, Any] = {
            "outer": {"inner": "<private>secret</private>"},
            "arr": ["<private>a</private>", 99],
        }
        result = redact_value(data)
        assert result == {"outer": {"inner": "[REDACTED]"}, "arr": ["[REDACTED]", 99]}

    def test_non_string_scalar_untouched(self) -> None:
        assert redact_value(42) == 42
        assert redact_value(None) is None
        assert redact_value(True) is True


# ---------------------------------------------------------------------------
# Staging store
# ---------------------------------------------------------------------------


@pytest.fixture
def staging_config(tmp_path: Path) -> StagingConfig:
    return StagingConfig(
        staging_dir=tmp_path / "staging",
        audit_dir=tmp_path / "audit",
        host="testhost",
    )


class TestStagingRedaction:
    def test_capture_event_strips_private(
        self, staging_config: StagingConfig
    ) -> None:
        capture_event(
            {"tool_name": "Bash", "command": "echo <private>TOKEN</private>"},
            staging_config,
        )
        line = next(staging_config.staging_dir.rglob("*-events.jsonl")).read_text(
            encoding="utf-8"
        )
        assert "TOKEN" not in line
        assert "[REDACTED]" in line

    def test_redact_private_uses_canonical_token(
        self, staging_config: StagingConfig
    ) -> None:
        event = {"content": "before <private>secret</private> after"}
        redact_private(event, staging_config)
        assert event["content"] == "before [REDACTED] after"

    def test_uppercase_tag_redacted(self, staging_config: StagingConfig) -> None:
        event = {"content": "<PRIVATE>secret</PRIVATE>"}
        redact_private(event, staging_config)
        assert "secret" not in event["content"]
        assert "[REDACTED]" in event["content"]

    def test_attribute_tag_redacted(self, staging_config: StagingConfig) -> None:
        event = {"content": '<private reason="x">val</private>'}
        redact_private(event, staging_config)
        assert "val" not in event["content"]
        assert "[REDACTED]" in event["content"]

    def test_unbalanced_open_redacted(self, staging_config: StagingConfig) -> None:
        event = {"content": "prefix <private>no close"}
        redact_private(event, staging_config)
        assert "no close" not in event["content"]
        assert "[REDACTED]" in event["content"]


# ---------------------------------------------------------------------------
# KG episode_stage store
# ---------------------------------------------------------------------------


@pytest.fixture
def kg_config(tmp_path: Path) -> KgConfig:
    state = tmp_path / "state"
    cfg = KgConfig(
        queue_dir=state / "kg-queue",
        processed_dir=state / "kg-processed",
        active_flag=state / "kg-active.flag",
        credentials_path=state / "neo4j-credentials.json",
        community_refresh_flag=state / "kg-community-refresh.flag",
        worker_log=state / "telemetry" / "kg-worker.jsonl",
        cost_ledger=state / "cost-ledger.jsonl",
        host="testhost",
    )
    cfg.active_flag.parent.mkdir(parents=True, exist_ok=True)
    cfg.active_flag.write_text("on\n", encoding="utf-8")
    return cfg


class TestKgEpisodeStageRedaction:
    def test_private_stripped_before_queue_write(self, kg_config: KgConfig) -> None:
        import json as _json

        stage_event(
            {"tool_name": "Bash", "command": "echo <private>SECRET</private>"},
            kg_config,
        )
        files = list(kg_config.queue_dir.rglob("*-events.jsonl"))
        assert files, "Expected a queue file to be written"
        record = _json.loads(files[0].read_text(encoding="utf-8").strip())
        cmd = record["payload"]["command"]
        assert "SECRET" not in cmd

    def test_uppercase_private_stripped(self, kg_config: KgConfig) -> None:
        import json as _json

        stage_event(
            {"tool_name": "Edit", "data": "<PRIVATE>uppersecret</PRIVATE>"},
            kg_config,
        )
        files = list(kg_config.queue_dir.rglob("*-events.jsonl"))
        record = _json.loads(files[0].read_text(encoding="utf-8").strip())
        assert "uppersecret" not in _json.dumps(record)

    def test_unbalanced_open_stripped(self, kg_config: KgConfig) -> None:
        import json as _json

        stage_event(
            {"tool_name": "Write", "data": "lead <private>no close"},
            kg_config,
        )
        files = list(kg_config.queue_dir.rglob("*-events.jsonl"))
        record = _json.loads(files[0].read_text(encoding="utf-8").strip())
        assert "no close" not in _json.dumps(record)


# ---------------------------------------------------------------------------
# Telemetry writer store
# ---------------------------------------------------------------------------


@pytest.fixture
def telemetry_config(tmp_path: Path) -> TelemetryConfig:
    return TelemetryConfig(
        telemetry_dir=tmp_path / "telemetry",
        audit_dir=tmp_path / "audit",
        host="testhost",
    )


class TestTelemetryRedaction:
    def test_private_tag_stripped_from_string_kwarg(
        self, telemetry_config: TelemetryConfig
    ) -> None:
        import json as _json

        write_event(
            "tool_use",
            "run",
            telemetry_config,
            command="echo <private>TOKEN</private>",
        )
        path = next(telemetry_config.telemetry_dir.rglob("tool-usage.jsonl"))
        rec = _json.loads(path.read_text(encoding="utf-8").strip())
        assert "TOKEN" not in rec["command"]
        assert "[REDACTED]" in rec["command"]

    def test_private_tag_stripped_from_event_name(
        self, telemetry_config: TelemetryConfig
    ) -> None:
        import json as _json

        write_event(
            "tool_use",
            "name <private>hidden</private>",
            telemetry_config,
        )
        path = next(telemetry_config.telemetry_dir.rglob("tool-usage.jsonl"))
        rec = _json.loads(path.read_text(encoding="utf-8").strip())
        assert "hidden" not in rec["name"]
        assert "[REDACTED]" in rec["name"]


# ---------------------------------------------------------------------------
# Patterns store
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> VaultConfig:
    v = VaultConfig.from_path(tmp_path)
    (v.root / ".mneme").mkdir(parents=True, exist_ok=True)
    return v


class TestPatternsRedaction:
    def test_private_tag_stripped_before_write(self, vault: VaultConfig) -> None:
        p = Pattern(
            name="sensitive-pattern",
            signal="<private>secret signal</private>",
            action="<private>secret action</private>",
            outcome="safe outcome",
        )
        path = store_pattern(vault, p)
        content = path.read_text(encoding="utf-8")
        assert "secret signal" not in content
        assert "secret action" not in content
        assert "[REDACTED]" in content

    def test_uppercase_private_stripped(self, vault: VaultConfig) -> None:
        p = Pattern(
            name="upper-pattern",
            signal="signal <PRIVATE>uppersecret</PRIVATE>",
            action="action",
        )
        path = store_pattern(vault, p)
        content = path.read_text(encoding="utf-8")
        assert "uppersecret" not in content
        assert "[REDACTED]" in content

    def test_attribute_tag_stripped(self, vault: VaultConfig) -> None:
        p = Pattern(
            name="attr-pattern",
            signal='sig <private reason="x">attr-val</private>',
            action="act",
        )
        path = store_pattern(vault, p)
        assert "attr-val" not in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Trajectory store
# ---------------------------------------------------------------------------


class TestTrajectoryRedaction:
    def test_private_stripped_from_action(self, vault: VaultConfig) -> None:
        record_step(
            vault,
            "sess-priv",
            action="<private>secret-action</private>",
            observation="normal obs",
        )
        traj_file = next(vault.trajectories_dir.rglob("*.md"))
        content = traj_file.read_text(encoding="utf-8")
        assert "secret-action" not in content
        assert "[REDACTED]" in content

    def test_private_stripped_from_observation(self, vault: VaultConfig) -> None:
        record_step(
            vault,
            "sess-obs",
            action="act",
            observation="obs <private>obs-secret</private> end",
        )
        traj_file = next(vault.trajectories_dir.rglob("*.md"))
        content = traj_file.read_text(encoding="utf-8")
        assert "obs-secret" not in content

    def test_unbalanced_open_stripped(self, vault: VaultConfig) -> None:
        record_step(
            vault,
            "sess-unbal",
            action="act",
            input_summary="prefix <private>no close",
        )
        traj_file = next(vault.trajectories_dir.rglob("*.md"))
        assert "no close" not in traj_file.read_text(encoding="utf-8")
