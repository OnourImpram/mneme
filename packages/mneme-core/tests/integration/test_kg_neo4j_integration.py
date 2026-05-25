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
from pathlib import Path

import pytest

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

    driver = neo4j.GraphDatabase.driver(
        loaded.bolt_url, auth=(loaded.user, loaded.password)
    )
    try:
        records, _, _ = driver.execute_query("RETURN 1 AS one")
        assert records[0]["one"] == 1
    finally:
        driver.close()
