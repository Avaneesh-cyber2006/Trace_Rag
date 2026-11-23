"""Real HTTP -> Module 10 reasoning -> fake provider; no external traffic."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from scripts.module10_local_demo import create_demo_app

TOKEN = "unit-test-demo-token-0123456789"


def test_demo_requires_local_session_token():
    client = TestClient(create_demo_app(TOKEN))
    assert client.get("/api/v1/repositories").status_code == 401
    response = client.get("/api/v1/repositories", headers={"Authorization": "Bearer " + TOKEN})
    assert response.status_code == 200
    assert response.json() == {"repositories": [{
        "repository_id": "offline-demo", "display_name": "Offline demo · synthetic code",
    }]}
    assert "tracerag/local-demo" not in response.text


def test_demo_runs_actual_citation_validation_without_gemini():
    client = TestClient(create_demo_app(TOKEN))
    response = client.post("/api/v1/answer", headers={"Authorization": "Bearer " + TOKEN}, json={
        "repository_id": "offline-demo", "question": "What does calculate return?",
        "top_k": 1, "approve_external_processing": True,
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["answer"]["status"] == "answered"
    assert data["answer"]["claims"][0]["citations"] == ["S1"]
    assert data["citations"][0]["label"] == "S1"
    assert "return 7" in data["citations"][0]["content"]
    assert "tracerag/local-demo" not in response.text
    assert TOKEN not in response.text


def test_demo_denial_is_not_a_generation_call():
    client = TestClient(create_demo_app(TOKEN))
    response = client.post("/api/v1/answer", headers={"Authorization": "Bearer " + TOKEN}, json={
        "repository_id": "offline-demo", "question": "What does calculate return?",
        "approve_external_processing": False,
    })
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "forbidden"


def test_demo_origin_restriction():
    client = TestClient(create_demo_app(TOKEN))
    response = client.get("/api/v1/repositories", headers={
        "Authorization": "Bearer " + TOKEN, "Origin": "https://untrusted.invalid",
    })
    assert response.status_code == 403
