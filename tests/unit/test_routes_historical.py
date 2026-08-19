"""Phase 15 routes: POST /historical/start, GET /historical/status, and
POST /historical/cancel.

These only check the HTTP wiring (the connected-account guard, the response
shape, parameter pass-through). Deep coverage of the sweep itself
(pagination, the write gate, safety-invariant abort) lives in
test_historical_service.py; the "only one run at a time" guarantee is
covered at the ``HistoricalRunner`` level in test_historical_runner.py
rather than here — Starlette's synchronous ``TestClient`` runs each request
through its own short-lived event loop and cancels-and-awaits any task still
pending when a request completes, so a background task started by one
``.post()`` call is always resolved before that call returns control to the
test. That's specific to this test transport, not to the real server
(uvicorn keeps one persistent loop for the process's whole lifetime, which
is what actually lets the task outlive the request that started it) — so
concurrency is verified directly against ``HistoricalRunner``, using real
``async def`` test functions that control the event loop themselves, rather
than through this harness.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.gmail.tokens import StoredToken, save_token
from app.oauth_scopes import ACTIVE_SCOPES
from tests.fixtures.emails import DEFAULT_USER


def test_status_reports_idle_before_anything_runs(client: TestClient) -> None:
    resp = client.get("/historical/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "idle"
    assert body["messages_processed"] == 0


def test_start_refuses_without_a_connected_account(client: TestClient) -> None:
    resp = client.post("/historical/start")
    assert resp.status_code == 409


def test_start_returns_immediately_without_blocking_on_the_sweep(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_token(
        StoredToken(refresh_token="r", scopes=list(ACTIVE_SCOPES), account_email=DEFAULT_USER)
    )

    started_with: dict = {}

    def _slow_run(status, **kwargs):
        started_with.update(kwargs)
        status.state = "running"  # never resolves during this test

    monkeypatch.setattr("app.historical.service.run_historical_cleanup", _slow_run)

    resp = client.post("/historical/start?months=6&confirm=true&max_messages=100")
    assert resp.status_code == 200
    body = resp.json()
    assert body["started"] is True

    assert started_with.get("months") == 6
    assert started_with.get("confirm") is True
    assert started_with.get("max_messages") == 100


def test_cancel_reports_false_when_nothing_is_running(client: TestClient) -> None:
    resp = client.post("/historical/cancel")
    assert resp.status_code == 200
    assert resp.json()["cancel_requested"] is False
