"""Offline HTTP -> actual Module 10 orchestration, with fake Module 9/provider."""

from hashlib import sha256

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from backend.api import ApiSecurity, RepositorySession, create_app
from backend.causal_reasoner import parse_answer
from backend.retrieval.hybrid import HybridHit
from backend.retrieval.errors import InvalidHybridEvidence

NS = "module10/registry"
CODE = "def calculate():\n    return 7\n"
TOKEN = "test-only-local-api-token-0123456789"


class Retriever:
    def __init__(self, *, value=None, error=None):
        self.calls = []
        self.value = (HybridHit(
            NS, sha256(("src/calculation.py" + CODE).encode()).hexdigest(),
            sha256(CODE.encode()).hexdigest(), "src/calculation.py", "python",
            "symbol", "function", "calculate", None, CODE,
            0.05, 1, 1, 1,
        ),) if value is None else value
        self.error = error

    def search(self, namespace, query, top_k):
        self.calls.append((namespace, query, top_k))
        if self.error:
            raise self.error
        return self.value


class Provider:
    def __init__(self, *, invalid=False, error=None):
        self.calls = []
        self.invalid = invalid
        self.error = error

    def generate(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return parse_answer({
            "status": "answered", "overview": "Indexed source has a return statement.",
            "claims": [{
                "text": "Indexed code returns a literal.", "category": "indexed_fact",
                "citations": ["S999" if self.invalid else "S1"],
                "excerpt": "return 7",
            }],
            "hypotheses": [], "limitations": ["Execution not verified."],
        })


def _app(*, retriever=None, provider=None, enabled=True, permitted=True):
    retriever = Retriever() if retriever is None else retriever
    provider = Provider() if provider is None else provider
    session = RepositorySession(
        "repo1", "Test repo", NS, retriever, provider,
        external_processing_permitted=permitted,
    )
    app = create_app(
        security=ApiSecurity(TOKEN, allow_external_processing=enabled),
        repositories=[session],
    )
    return TestClient(app, raise_server_exceptions=False), retriever, provider


def _ask(client, question="What is returned?", approved=True):
    return client.post("/api/v1/answer", headers={"Authorization": "Bearer " + TOKEN},
                       json={"repository_id": "repo1", "question": question,
                             "approve_external_processing": approved, "top_k": 1})


def test_real_orchestration_one_retrieval_one_generation_and_verified_citation():
    client, retrieval, generation = _app()
    resp = _ask(client)
    assert resp.status_code == 200, resp.text
    obj = resp.json()
    assert obj["answer"]["status"] == "answered"
    assert obj["citations"][0]["label"] == "S1"
    assert obj["citations"][0]["content"] == CODE
    assert "src/calculation.py" not in resp.text
    assert retrieval.calls == [(NS, "What is returned?", 1)]
    assert len(generation.calls) == 1
    assert generation.calls[0].external_processing_approved is True


def test_no_indexed_evidence_abstains_without_generation():
    client, retrieval, generation = _app(retriever=Retriever(value=()))
    resp = _ask(client)
    assert resp.status_code == 200
    assert resp.json()["answer"]["status"] == "insufficient_evidence"
    assert not resp.json()["citations"] and not generation.calls
    assert len(retrieval.calls) == 1


def test_fabricated_model_citation_returns_sanitized_gateway_error():
    client, retrieval, generation = _app(provider=Provider(invalid=True))
    resp = _ask(client)
    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "generation_unavailable"
    assert len(retrieval.calls) == len(generation.calls) == 1
    assert "S999" not in resp.text


def test_provider_error_never_leaks_to_http():
    secret = "this-is-a-secret-provider-response"
    client, _, _ = _app(provider=Provider(error=RuntimeError(secret)))
    resp = _ask(client)
    assert resp.status_code == 502
    assert secret not in resp.text


def test_invalid_module9_evidence_maps_to_safe_conflict():
    client, retriever, provider = _app(retriever=Retriever(error=InvalidHybridEvidence("local-path-secret")))
    resp = _ask(client)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "evidence_unavailable"
    assert "local-path-secret" not in resp.text
    assert len(retriever.calls) == 1 and not provider.calls


def test_recognized_credentials_in_query_never_retrieve_or_generate():
    client, retriever, provider = _app()
    resp = _ask(client, question="Why does foo use api_key=supersecret-token-value?")
    assert resp.status_code == 422
    assert not retriever.calls and not provider.calls
    assert "supersecret-token-value" not in resp.text


def test_no_approval_stops_before_module9():
    client, retriever, provider = _app()
    resp = _ask(client, approved=False)
    assert resp.status_code == 403
    assert not retriever.calls and not provider.calls
