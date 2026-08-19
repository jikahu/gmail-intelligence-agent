"""Health endpoint tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import CURRENT_PHASE


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["phase"] == CURRENT_PHASE
    assert "version" in body
    assert "gmail_connected" in body
    assert "reconnect_required" in body


def test_health_reports_safety_defaults(client: TestClient) -> None:
    """Phase 0 defaults must keep the app fully hands-off."""
    body = client.get("/health").json()
    assert body["dry_run"] is True
    assert body["gmail_processing_enabled"] is False


def test_root_page_renders(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Gmail Intelligence Agent" in response.text
