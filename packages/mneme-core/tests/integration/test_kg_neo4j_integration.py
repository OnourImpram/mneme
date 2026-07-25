"""Real-Neo4j integration test for the KG connection contract (gap G-1).

This test is a clean skip unless BOTH conditions hold:

* the ``neo4j`` driver is importable (the ``mneme[full]`` profile), and
* ``MNEME_NEO4J_TEST_URI`` is set in the environment.

The CI job ``kg-neo4j-integration`` runs a Neo4j service container and
sets those env vars, so the test exercises a real database there; in the
default matrix and locally it skips without error.

Scope: it verifies the connection contract mneme actually depends on:
the :class:`~mneme_core.kg.client.Neo4jCredentials` file round-trip and
that those credentials open a real bolt session. The LLM-backed Graphiti
drain (``mneme_core.kg.worker.drain_live``) is deliberately out of scope
here because it needs a paid provider key; that gap is documented, not
faked.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from mneme_core.temporal.graphiti_export import group_id_for_scope

neo4j = pytest.importorskip("neo4j")

_URI = os.environ.get("MNEME_NEO4J_TEST_URI")
_USER = os.environ.get("MNEME_NEO4J_TEST_USER", "neo4j")
_PASSWORD = os.environ.get("MNEME_NEO4J_TEST_PASSWORD", "")

pytestmark = pytest.mark.skipif(
    not _URI,
    reason="MNEME_NEO4J_TEST_URI not set; runs only in the Neo4j CI job.",
)


def test_credentials_roundtrip_and_real_connection(tmp_path: Path) -> None:
    from mneme_core.kg.client import (
        Neo4jCredentials,
        read_credentials,
        write_credentials,
    )

    assert _URI is not None  # narrowed by the module-level skipif
    creds = Neo4jCredentials(bolt_url=_URI, user=_USER, password=_PASSWORD)
    cred_path = tmp_path / "neo4j-credentials.json"
    write_credentials(cred_path, creds)
    loaded = read_credentials(cred_path)
    assert loaded == creds

    driver = neo4j.GraphDatabase.driver(loaded.bolt_url, auth=(loaded.user, loaded.password))
    try:
        records, _, _ = driver.execute_query("RETURN 1 AS one")
        assert records[0]["one"] == 1
    finally:
        driver.close()


def _timeline_facts(
    driver: object,
    *,
    test_run: str,
    scope: str,
    as_of: str | None = None,
    valid_from: str | None = None,
    valid_to_exclusive: str | None = None,
) -> set[str]:
    where = ["r.test_run = $test_run"]
    params: dict[str, object] = {"test_run": test_run}
    if scope != "*":
        group_id = group_id_for_scope(scope)
        if scope == "default":
            where.append("(r.group_id = $group_id OR r.group_id IS NULL OR r.group_id = '')")
        else:
            where.append("r.group_id = $group_id")
        params.update({"scope": scope, "group_id": group_id})
    if valid_from is not None:
        where.append("(r.invalid_at IS NULL OR r.invalid_at > datetime($valid_from))")
        params["valid_from"] = valid_from
    if valid_to_exclusive is not None:
        where.append("(r.valid_at IS NULL OR r.valid_at < datetime($valid_to_exclusive))")
        params["valid_to_exclusive"] = valid_to_exclusive
    if as_of is None:
        where.append("r.expired_at IS NULL")
    else:
        where.extend(
            [
                "r.created_at IS NOT NULL AND r.created_at <= datetime($as_of)",
                "(r.expired_at IS NULL OR r.expired_at > datetime($as_of))",
            ]
        )
        params["as_of"] = as_of

    query = (
        "MATCH (:Entity)-[r:RELATES_TO]->(:Entity) WHERE "
        + " AND ".join(where)
        + " RETURN r.fact AS fact"
    )
    records, _, _ = driver.execute_query(query, parameters_=params)  # type: ignore[attr-defined]
    return {str(record["fact"]) for record in records}


def test_real_query_scope_bitemporal_and_supersession_contract() -> None:
    """Exercise the exact Neo4j predicate semantics used by the TS adapter."""
    assert _URI is not None
    test_run = str(uuid4())
    driver = neo4j.GraphDatabase.driver(_URI, auth=(_USER, _PASSWORD))
    seed = """
    UNWIND $edges AS edge
    CREATE (s:Entity {uuid: edge.source, name: 'Subject', test_run: $test_run})
    CREATE (o:Entity {uuid: edge.target, name: edge.fact, test_run: $test_run})
    CREATE (s)-[r:RELATES_TO]->(o)
    SET r = edge.properties, r.test_run = $test_run
    """
    edges = [
        {
            "source": f"{test_run}-old-s",
            "target": f"{test_run}-old-o",
            "fact": "old-clinical",
            "properties": {
                "fact": "old-clinical",
                "group_id": group_id_for_scope("clinical"),
                "valid_at": datetime(2026, 1, 1, tzinfo=UTC),
                "invalid_at": datetime(2026, 2, 15, tzinfo=UTC),
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                "expired_at": datetime(2026, 3, 1, tzinfo=UTC),
            },
        },
        {
            "source": f"{test_run}-new-s",
            "target": f"{test_run}-new-o",
            "fact": "new-clinical",
            "properties": {
                "fact": "new-clinical",
                "group_id": group_id_for_scope("clinical"),
                "valid_at": datetime(2026, 2, 15, tzinfo=UTC),
                "created_at": datetime(2026, 3, 1, tzinfo=UTC),
            },
        },
        {
            "source": f"{test_run}-conflict-s",
            "target": f"{test_run}-conflict-o",
            "fact": "contradicting-clinical",
            "properties": {
                "fact": "contradicting-clinical",
                "group_id": group_id_for_scope("clinical"),
                "valid_at": datetime(2026, 2, 15, tzinfo=UTC),
                "created_at": datetime(2026, 4, 1, tzinfo=UTC),
            },
        },
        {
            "source": f"{test_run}-foreign-s",
            "target": f"{test_run}-foreign-o",
            "fact": "foreign",
            "properties": {
                "fact": "foreign",
                "group_id": group_id_for_scope("research"),
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            },
        },
        {
            "source": f"{test_run}-legacy-s",
            "target": f"{test_run}-legacy-o",
            "fact": "legacy-default",
            "properties": {
                "fact": "legacy-default",
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            },
        },
        {
            "source": f"{test_run}-legacy-scoped-s",
            "target": f"{test_run}-legacy-scoped-o",
            "fact": "legacy-scoped-default",
            "properties": {
                "fact": "legacy-scoped-default",
                "scope": "clinical",
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            },
        },
    ]
    try:
        driver.execute_query(seed, edges=edges, test_run=test_run)

        historical = _timeline_facts(
            driver,
            test_run=test_run,
            scope="clinical",
            as_of="2026-02-01T00:00:00Z",
        )
        assert historical == {"old-clinical"}

        overlap = _timeline_facts(
            driver,
            test_run=test_run,
            scope="clinical",
            as_of="2026-02-01T00:00:00Z",
            valid_from="2026-02-01T00:00:00Z",
            valid_to_exclusive="2026-03-01T00:00:00Z",
        )
        assert overlap == {"old-clinical"}

        current = _timeline_facts(driver, test_run=test_run, scope="clinical")
        assert current == {"new-clinical", "contradicting-clinical"}
        assert _timeline_facts(driver, test_run=test_run, scope="default") == {
            "legacy-default",
            "legacy-scoped-default",
        }
        assert _timeline_facts(driver, test_run=test_run, scope="*") == {
            "new-clinical",
            "contradicting-clinical",
            "foreign",
            "legacy-default",
            "legacy-scoped-default",
        }
    finally:
        driver.execute_query("MATCH (n {test_run: $test_run}) DETACH DELETE n", test_run=test_run)
        driver.close()
