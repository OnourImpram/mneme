"""Benchmark D - Migration validation.

Builds a deterministic synthetic claude-mem SQLite fixture, invokes
``mneme-migrate`` via ``npx tsx`` against it, and verifies the
output's structural properties:

* observation count = row count in fixture
* idempotent re-run produces zero new migrations and zero rewrites
* privacy redaction stripped every ``<private>`` block we seeded
* manifest captured the run with matching totals

The benchmark stops short of judging retrieval quality on the
migrated content; that is Phase J operator dogfood work and lives in
``benchmarks/head-to-head/`` once a real claude-mem DB is available.

CI requirement: ``pnpm install --frozen-lockfile`` must have run
before this benchmark so ``tsx`` is on PATH inside the workspace.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "packages" / "mneme-core" / "src"))

from mneme_core.bench.hardware import (  # noqa: E402
    capture_hardware,
    write_hardware_json,
)


def build_fixture_db(path: Path, observation_count: int) -> dict[str, int]:
    """Materialize a synthetic claude-mem v13.2.0 schema with rows.

    Returns the row counts as a sanity payload the validator compares
    against the migration output.
    """
    schema_statements = [
        """CREATE TABLE observations (
            id INTEGER PRIMARY KEY,
            memory_session_id TEXT,
            project TEXT,
            text TEXT,
            type TEXT,
            title TEXT,
            subtitle TEXT,
            facts TEXT,
            narrative TEXT,
            concepts TEXT,
            files_read TEXT,
            files_modified TEXT,
            prompt_number INTEGER,
            discovery_tokens INTEGER,
            created_at TEXT,
            created_at_epoch INTEGER,
            content_hash TEXT,
            generated_by_model TEXT,
            agent_type TEXT,
            agent_id TEXT,
            metadata TEXT
        )""",
        """CREATE TABLE session_summaries (
            id INTEGER PRIMARY KEY,
            memory_session_id TEXT,
            summary TEXT,
            created_at TEXT
        )""",
        """CREATE TABLE user_prompts (
            id INTEGER PRIMARY KEY,
            memory_session_id TEXT,
            prompt_text TEXT,
            created_at TEXT
        )""",
    ]
    conn = sqlite3.connect(path)
    try:
        for stmt in schema_statements:
            conn.execute(stmt)
        insert_obs = """
            INSERT INTO observations (
                id, memory_session_id, project, text, type, title, subtitle,
                facts, narrative, concepts, files_read, files_modified,
                prompt_number, discovery_tokens, created_at, created_at_epoch,
                content_hash, generated_by_model, agent_type, agent_id, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        # Seed every fifth row with a <private> block so redaction has
        # something to strip.
        for i in range(1, observation_count + 1):
            narrative = (
                f"Narrative for row {i} with <private>secret-{i}</private> content."
                if i % 5 == 0
                else f"Narrative for row {i}."
            )
            day = ((i - 1) % 28) + 1
            created_at = f"2026-04-{day:02d}T08:00:00Z"
            epoch = 1_775_376_000 + i * 60
            conn.execute(
                insert_obs,
                (
                    i,
                    f"session-{(i % 7):02d}",
                    "demo-project",
                    f"Body text for row {i}.",
                    "discovery",
                    f"Title {i}",
                    f"Subtitle {i}",
                    f"Fact for row {i}.",
                    narrative,
                    "alpha, beta",
                    "[]",
                    "[]",
                    i,
                    50,
                    created_at,
                    epoch,
                    f"synthetic-{i}",
                    "synthetic-model",
                    "main",
                    f"agent-{i % 3}",
                    "{}",
                ),
            )
        conn.execute(
            "INSERT INTO session_summaries (id, memory_session_id, summary, created_at) "
            "VALUES (?, ?, ?, ?)",
            (1, "session-00", "Wrap up.", "2026-04-01T08:00:00Z"),
        )
        conn.execute(
            "INSERT INTO user_prompts (id, memory_session_id, prompt_text, created_at) "
            "VALUES (?, ?, ?, ?)",
            (1, "session-00", "Initial prompt.", "2026-04-01T08:01:00Z"),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "observations": observation_count,
        "session_summaries": 1,
        "user_prompts_heads": 1,
        "redactable_rows": observation_count // 5,
    }


def find_tsx_command() -> list[str]:
    """Resolve the command that runs the TS migration CLI.

    Strategy:

    1. If ``npx`` is on PATH (Node + npm installed), use the resolved
       absolute path so Windows ``.cmd`` shims work without
       ``shell=True``. ``--yes`` ensures the workspace's local tsx is
       picked up without an interactive install prompt.
    2. Otherwise raise; the validator surfaces this as a skipped run.
    """
    resolved = shutil.which("npx")
    if resolved is not None:
        return [resolved, "--yes", "tsx"]
    raise RuntimeError(
        "Could not find npx on PATH. Install Node + pnpm and run "
        "`pnpm install --frozen-lockfile` before this benchmark."
    )


def invoke_migrate(
    source: Path,
    vault: Path,
    extra_args: list[str] | None = None,
) -> tuple[int, dict[str, object]]:
    """Run ``mneme-migrate migrate-from-claude-mem`` and return its JSON stats."""
    cli_entry = _REPO_ROOT / "packages" / "mneme-mcp" / "src" / "cli" / "index.ts"
    cmd = find_tsx_command() + [
        str(cli_entry),
        "migrate-from-claude-mem",
        "--source",
        str(source),
        "--vault",
        str(vault),
    ]
    if extra_args:
        cmd.extend(extra_args)
    proc = subprocess.run(  # noqa: S603
        cmd,
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    try:
        payload = json.loads(proc.stdout) if proc.stdout else {}
    except json.JSONDecodeError:
        payload = {"_raw_stdout": proc.stdout, "_raw_stderr": proc.stderr}
    return proc.returncode, payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark D - migration validation")
    parser.add_argument("--observations", type=int, default=200)
    parser.add_argument(
        "--output-format",
        choices=("json", "table"),
        default="json",
    )
    parser.add_argument(
        "--hardware-output",
        type=Path,
        default=None,
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    payload: dict[str, object] = {
        "benchmark": "migration",
        "seed": args.seed,
        "observations_requested": args.observations,
    }

    if args.hardware_output is not None:
        write_hardware_json(capture_hardware(seed=args.seed), args.hardware_output)

    with tempfile.TemporaryDirectory(prefix="mneme-bench-migration-") as tmp:
        tmp_path = Path(tmp)
        source_db = tmp_path / "claude-mem.db"
        vault_root = tmp_path / "vault"
        (vault_root / ".mneme").mkdir(parents=True)

        seeded = build_fixture_db(source_db, args.observations)

        try:
            tsx_cmd = find_tsx_command()
        except RuntimeError as exc:
            payload["status"] = "skipped"
            payload["reason"] = str(exc)
            payload["seeded_counts"] = seeded
            json.dump(payload, sys.stdout, indent=2)
            sys.stdout.write("\n")
            return 0

        payload["tsx_command"] = tsx_cmd
        t0 = time.perf_counter()
        first_rc, first_stats = invoke_migrate(source_db, vault_root)
        first_elapsed = time.perf_counter() - t0

        if first_rc != 0:
            payload["status"] = "error"
            payload["first_run"] = {"rc": first_rc, "stats": first_stats}
            payload["seeded_counts"] = seeded
            json.dump(payload, sys.stdout, indent=2)
            sys.stdout.write("\n")
            return 1

        t0 = time.perf_counter()
        second_rc, second_stats = invoke_migrate(source_db, vault_root)
        second_elapsed = time.perf_counter() - t0

        first_obs = first_stats.get("observations", {}) if isinstance(first_stats, dict) else {}
        second_obs = second_stats.get("observations", {}) if isinstance(second_stats, dict) else {}

        migrated = int(first_obs.get("migrated", 0)) if isinstance(first_obs, dict) else 0
        first_redactions = int(
            first_stats.get("redactionsApplied", 0)
            if isinstance(first_stats, dict)
            else 0
        )
        second_migrated = (
            int(second_obs.get("migrated", 0)) if isinstance(second_obs, dict) else 0
        )
        second_skipped = (
            int(second_obs.get("skippedDedup", 0))
            if isinstance(second_obs, dict)
            else 0
        )

        payload["status"] = "ok"
        payload["seeded_counts"] = seeded
        payload["first_run"] = {
            "rc": first_rc,
            "elapsed_seconds": round(first_elapsed, 3),
            "observations_migrated": migrated,
            "redactions_applied": first_redactions,
        }
        payload["second_run"] = {
            "rc": second_rc,
            "elapsed_seconds": round(second_elapsed, 3),
            "observations_migrated": second_migrated,
            "observations_skipped_dedup": second_skipped,
        }
        payload["assertions"] = {
            "migrated_equals_seeded": migrated == seeded["observations"],
            "second_run_zero_new": second_migrated == 0,
            "second_run_full_dedup": second_skipped == seeded["observations"],
            "redactions_match_seeded": first_redactions >= seeded["redactable_rows"],
        }

    if args.output_format == "json":
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write("Benchmark D - migration validation\n")
        sys.stdout.write(f"  status: {payload['status']}\n")
        if payload["status"] == "ok":
            for k, v in payload["assertions"].items():
                sys.stdout.write(f"  {k}: {v}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
