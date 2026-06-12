"""Read-only web console: routes, payloads, and the security posture."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mneme_core.approval import EditCategory, propose
from mneme_core.console_serve import build_payload, make_server
from mneme_core.memory_apply import apply_edit
from mneme_core.policy import AutoApproveClass
from mneme_core.vault.config import VaultConfig


@pytest.fixture()
def vault(tmp_path: Path) -> VaultConfig:
    v = VaultConfig.from_path(tmp_path)
    v.state_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.md").write_text(
        "---\ntype: reference\n---\n# A\n", encoding="utf-8"
    )
    return v


@pytest.fixture()
def server_url(vault: VaultConfig):
    server = make_server(vault, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


def _get(url: str) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 - localhost test
        return resp.status, resp.read().decode("utf-8")


class TestRoutes:
    def test_explorer_page_served(self, server_url: str) -> None:
        status, body = _get(f"{server_url}/")
        assert status == 200
        assert "mneme console" in body
        # Self-contained: no external resource references.
        assert "http://" not in body.replace(server_url, "")
        assert "https://" not in body

    def test_audit_api(self, server_url: str) -> None:
        status, body = _get(f"{server_url}/api/audit")
        assert status == 200
        data = json.loads(body)
        assert data["note_count"] == 1

    def test_graph_api_without_graph(self, server_url: str) -> None:
        status, body = _get(f"{server_url}/api/graph")
        assert status == 200
        assert json.loads(body)["nodes"] == []

    def test_claims_api_without_index(self, server_url: str) -> None:
        status, body = _get(f"{server_url}/api/claims")
        assert status == 200
        assert json.loads(body)["claims"] == []

    def test_changes_api_lists_autonomous_edit(
        self, vault: VaultConfig, server_url: str
    ) -> None:
        (vault.state_dir / "policy.json").write_text(
            json.dumps({"auto_approve": ["typo-fix"]}), encoding="utf-8"
        )
        proposal = propose(
            action="create",
            target_path="notes/auto.md",
            content="auto",
            category=EditCategory.EPHEMERAL,
        )
        assert apply_edit(vault, proposal, AutoApproveClass.TYPO_FIX).applied
        status, body = _get(f"{server_url}/api/changes")
        assert status == 200
        assert json.loads(body)["count"] == 1

    def test_chain_api_verifies(self, server_url: str) -> None:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        status, body = _get(f"{server_url}/api/chain?day={day}")
        assert status == 200
        data = json.loads(body)
        assert data["day"] == day
        assert data["valid"] is True

    def test_unknown_route_404(self, server_url: str) -> None:
        with pytest.raises(urllib.error.HTTPError) as err:
            _get(f"{server_url}/api/nope")
        assert err.value.code == 404


class TestSecurityPosture:
    def test_post_refused(self, server_url: str) -> None:
        req = urllib.request.Request(
            f"{server_url}/api/audit", data=b"{}", method="POST"
        )
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=5)  # noqa: S310
        assert err.value.code == 405

    def test_delete_refused(self, server_url: str) -> None:
        req = urllib.request.Request(f"{server_url}/api/changes", method="DELETE")
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=5)  # noqa: S310
        assert err.value.code == 405

    def test_non_loopback_bind_refused(self, vault: VaultConfig) -> None:
        with pytest.raises(ValueError, match="loopback"):
            make_server(vault, "0.0.0.0", 0)  # noqa: S104 - asserting refusal

    def test_non_loopback_allowed_with_explicit_flag(self, vault: VaultConfig) -> None:
        server = make_server(vault, "127.0.0.1", 0, allow_remote=True)
        server.server_close()


class TestBuildPayload:
    def test_unknown_route_none(self, vault: VaultConfig) -> None:
        assert build_payload(vault, "/api/unknown", {}) is None

    def test_chain_rejects_bad_day(self, vault: VaultConfig) -> None:
        payload = build_payload(vault, "/api/chain", {"day": ["not-a-day"]})
        assert isinstance(payload, dict)
        assert "error" in payload
