"""CLI coverage for the disposable doctor isolation verifier."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import mneme_core.cli as cli_module
from mneme_core.cli import _verify_isolation_boundaries, cli


def _operator_vault(tmp_path: Path) -> Path:
    root = tmp_path / "operator-vault"
    (root / ".mneme").mkdir(parents=True)
    (root / "operator-note.md").write_text(
        "---\nid: operator-note\ntype: observation\nscope: operator\n---\n\n"
        "Operator data <private>REAL_OPERATOR_SECRET</private>.\n",
        encoding="utf-8",
    )
    return root


def _tree_snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


def test_isolation_helper_verifies_all_boundaries() -> None:
    checks = _verify_isolation_boundaries()

    assert {entry["name"] for entry in checks} == {
        "isolation_scope",
        "isolation_storage_redaction",
        "isolation_provider_redaction",
    }
    assert {entry["status"] for entry in checks} == {"ok"}


def test_verify_isolation_does_not_mutate_or_expose_operator_vault(
    tmp_path: Path,
) -> None:
    operator_vault = _operator_vault(tmp_path)
    before = _tree_snapshot(operator_vault)

    result = CliRunner().invoke(
        cli,
        [
            "doctor",
            "--vault",
            str(operator_vault),
            "--verify-isolation",
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    isolation_checks = {
        entry["name"]: entry
        for entry in report["checks"]
        if entry["name"].startswith("isolation_")
    }
    assert set(isolation_checks) == {
        "isolation_scope",
        "isolation_storage_redaction",
        "isolation_provider_redaction",
    }
    assert all(entry["status"] == "ok" for entry in isolation_checks.values())
    assert _tree_snapshot(operator_vault) == before
    assert "REAL_OPERATOR_SECRET" not in result.output
    assert not (operator_vault / ".mneme" / "fts5.sqlite").exists()


def test_doctor_without_flag_skips_isolation_self_test(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator_vault = _operator_vault(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "_verify_isolation_boundaries",
        lambda: pytest.fail("isolation helper must remain opt-in"),
    )

    result = CliRunner().invoke(
        cli,
        ["doctor", "--vault", str(operator_vault)],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert not any(
        entry["name"].startswith("isolation_") for entry in report["checks"]
    )


def test_isolation_failure_sets_failed_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator_vault = _operator_vault(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "_verify_isolation_boundaries",
        lambda: [
            {
                "name": "isolation_scope",
                "status": "fail",
                "detail": "injected verifier failure",
            }
        ],
    )

    result = CliRunner().invoke(
        cli,
        [
            "doctor",
            "--vault",
            str(operator_vault),
            "--verify-isolation",
        ],
    )

    assert result.exit_code == 1
    report = json.loads(result.output)
    assert report["overall"] == "fail"


def test_isolation_setup_exception_is_a_bounded_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator_vault = _operator_vault(tmp_path)

    def fail_setup() -> list[dict[str, str]]:
        raise OSError("REAL_OPERATOR_SECRET")

    monkeypatch.setattr(cli_module, "_verify_isolation_boundaries", fail_setup)

    result = CliRunner().invoke(
        cli,
        [
            "doctor",
            "--vault",
            str(operator_vault),
            "--verify-isolation",
        ],
    )

    assert result.exit_code == 1
    report = json.loads(result.output)
    check = next(
        entry for entry in report["checks"] if entry["name"] == "isolation_fixture"
    )
    assert check == {
        "name": "isolation_fixture",
        "status": "fail",
        "detail": "temporary isolation fixture raised OSError",
    }
    assert "REAL_OPERATOR_SECRET" not in result.output
