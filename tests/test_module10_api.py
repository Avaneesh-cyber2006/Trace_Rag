"""Increment 5: offline local HTTP boundary tests; no real Gemini or Chroma I/O."""

from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from backend.api import ApiSecurity, RepositorySession, create_app

TOKEN = "test-only-local-session-secret-abcdef"
ORIGIN = "http://localhost:5173"


class Retriever:
    def search(self, *_args):
        raise AssertionError("The test should not perform a real retrieval")


class Provider:
    def generate(self, *_args):
        raise AssertionError("The test should not call Gemini")


def _e(value):
    return SimpleNamespace(value=value)


def _result():
    claim = SimpleNamespace(
        text="Indexed return is visible.", category=_e("indexed_fact"),
        citations=("S1",), excerpt="return 42",
    )
    hypothesis = SimpleNamespace(
        summary="Possible reason.", supporting_citations=("S1",),
        contradicting_citations=(), missing_observations=("Runtime capture",),
        status=_e("possible"),
    )
    answer = SimpleNamespace(
        status=_e("answered"), overview="Source code is indexed, not executed.",
        claims=(claim,), hypotheses=(hypothesis,), limitations=("No runtime proof.",),
    )
    evidence = SimpleNamespace(
        label="S1", kind=_e("indexed_source"),
        content="def foo():\n    return 42", source_label=None,
        details=(), redacted=False,
    )
    return SimpleNamespace(answer=answer, citations=(evidence,), warnings=("partial data",))


def _session(*, allowed=True):
    return RepositorySession(
        repository_id="sample", display_name="Sample repository",
        repository_namespace="private/internal-repo", retriever=Retriever(),
        provider=Provider(), external_processing_permitted=allowed,
        graph_revision=None, git_history=None, runtime_evidence=None,
        relevance_policy=None,
    )


def _setup(*, enabled=True, allowed=True, origins=(), rate=30, max_bytes=12288, service=None, clock=None):
    calls = []
    def fake_service(*args, **kwargs):
        calls.append((args, kwargs))
        return _result() if service is None else service(*args, **kwargs)
    security = ApiSecurity(
        bearer_token=TOKEN, allow_external_processing=enabled,
        allowed_origins=origins, max_requests_per_minute=rate,
        max_body_bytes=max_bytes,
    )
    options = {"security": security, "repositories": (_session(allowed=allowed),),
               "answer_service": fake_service}
    if clock is not None:
        options["clock"] = clock
    return TestClient(create_app(**options), raise_server_exceptions=False), calls


def _post(client, *, question="Why does foo return 42?", token=TOKEN,
          approved=True, repository_id="sample", **fields):
    headers = {"authorization": f"Bearer {token}"}
    return client.post("/api/v1/answer", headers=headers, json={
        "repository_id": repository_id, "question": question,
        "approve_external_processing": approved, **fields,
    })


def test_health_is_content_free_and_openapi_disabled():
    client, _ = _setup()
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404


@pytest.mark.parametrize("path,method", [
    ("/api/v1/repositories", "get"), ("/api/v1/answer", "post"),
])
def test_auth_required_before_retrieval(path, method):
    client, calls = _setup()
    response = getattr(client, method)(path)
    assert response.status_code == 401
    assert response.json() == {"detail": {"code": "unauthorized"}}
    assert not calls


@pytest.mark.parametrize("header", [
    "Bearer wrong", "Basic Zm9v", f"bearer {TOKEN}", "Bearer ",
])
def test_wrong_auth_rejected(header):
    client, calls = _setup()
    resp = client.get("/api/v1/repositories", headers={"authorization": header})
    assert resp.status_code == 401 and not calls
    assert TOKEN not in resp.text


def test_repositories_only_expose_authorized_public_ids():
    client, _ = _setup()
    resp = client.get("/api/v1/repositories", headers={"authorization": "Bearer " + TOKEN})
    assert resp.status_code == 200
    assert resp.json() == {"repositories": [{"repository_id": "sample", "display_name": "Sample repository"}]}
    assert "private/internal-repo" not in resp.text


def test_valid_answer_returns_only_redacted_citations_and_no_credentials():
    client, calls = _setup()
    resp = _post(client, top_k=2)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["repository_id"] == "sample"
    assert data["answer"]["status"] == "answered"
    assert data["answer"]["claims"][0]["citations"] == ["S1"]
    assert data["citations"][0]["label"] == "S1"
    assert data["citations"][0]["content"].endswith("return 42")
    assert data["warnings"] == ["partial data"]
    assert TOKEN not in resp.text and "private/internal-repo" not in resp.text
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[2:4] == ("private/internal-repo", "Why does foo return 42?")
    assert kwargs["top_k"] == 2 and kwargs["external_processing_approved"] is True


@pytest.mark.parametrize("enabled,allowed,approved", [
    (False, True, True), (True, False, True), (True, True, False),
    (False, False, False),
])
def test_approval_requires_all_three_server_and_request_gates(enabled, allowed, approved):
    client, calls = _setup(enabled=enabled, allowed=allowed)
    resp = _post(client, approved=approved)
    assert resp.status_code == 403 and not calls


def test_unknown_repository_never_retrieves():
    client, calls = _setup()
    resp = _post(client, repository_id="missing")
    assert resp.status_code == 404 and not calls


@pytest.mark.parametrize("extra", [
    {"top_k": 9}, {"top_k": True}, {"top_k": 2.5}, {"top_k": "2"},
    {"approve_external_processing": "true"}, {"repository_path": "C:\\secret"},
    {"api_key": "exposed"}, {"graph_revision": {"verified": True}},
    {"query_text": "injected"},
])
def test_invalid_extra_and_coerced_fields_rejected_before_service(extra):
    client, calls = _setup()
    resp = _post(client, **extra)
    assert resp.status_code == 422 and not calls
    assert "exposed" not in resp.text


def test_duplicate_json_field_is_rejected():
    client, calls = _setup()
    data = b'{"repository_id":"sample","question":"good","question":"bad","approve_external_processing":true}'
    resp = client.post("/api/v1/answer", content=data,
        headers={"authorization": "Bearer " + TOKEN, "content-type": "application/json"})
    assert resp.status_code == 422 and not calls


@pytest.mark.parametrize("data", [b"not json", b"[1,2]", b'{"x":NaN}', b''])
def test_malformed_json_rejected(data):
    client, calls = _setup()
    resp = client.post("/api/v1/answer", content=data,
        headers={"authorization": "Bearer " + TOKEN, "content-type": "application/json"})
    assert resp.status_code == 422 and not calls


def test_request_body_size_is_enforced_on_stream():
    client, calls = _setup(max_bytes=1024)
    resp = _post(client, question="A" * 2000)
    assert resp.status_code == 413 and not calls


def test_content_type_rejected_before_service():
    client, calls = _setup()
    resp = client.post("/api/v1/answer", content="hi",
        headers={"authorization": "Bearer " + TOKEN, "content-type": "text/plain"})
    assert resp.status_code == 415 and not calls


def test_long_multibyte_query_rejected_even_if_under_character_limit():
    client, calls = _setup()
    resp = _post(client, question="é" * 3000)
    assert resp.status_code == 422 and not calls


def test_unpaired_unicode_surrogate_is_rejected_without_server_error():
    client, calls = _setup()
    payload = b'{"repository_id":"sample","question":"\\ud800","approve_external_processing":true}'
    resp = client.post("/api/v1/answer", content=payload,
        headers={"authorization": "Bearer " + TOKEN, "content-type": "application/json"})
    assert resp.status_code == 422 and not calls


def test_origin_allowlist_blocks_unlisted_origin_and_allows_local_cors():
    client, calls = _setup(origins=(ORIGIN,))
    denied = client.post("/api/v1/answer", json={
        "repository_id": "sample", "question": "x", "approve_external_processing": True,
    }, headers={"authorization": "Bearer " + TOKEN, "origin": "http://evil.invalid"})
    assert denied.status_code == 403 and not calls
    granted = client.get("/api/v1/repositories", headers={
        "authorization": "Bearer " + TOKEN, "origin": ORIGIN,
    })
    assert granted.status_code == 200
    assert granted.headers["access-control-allow-origin"] == ORIGIN
    assert client.get("/api/v1/repositories", headers={
        "authorization": "Bearer " + TOKEN, "origin": "http://evil.invalid",
    }).status_code == 403


def test_origin_not_allowed_by_default_even_when_bearer_is_known():
    client, calls = _setup()
    resp = client.get("/api/v1/repositories", headers={
        "authorization": "Bearer " + TOKEN, "origin": ORIGIN,
    })
    assert resp.status_code == 403 and not calls


def test_rate_limit_is_per_app_and_uses_window():
    tick = [10.0]
    client, calls = _setup(rate=2, clock=lambda: tick[0])
    assert _post(client).status_code == 200
    assert _post(client).status_code == 200
    assert _post(client).status_code == 429
    assert len(calls) == 2
    tick[0] = 71.0
    assert _post(client).status_code == 200


def test_answer_is_json_not_html_even_with_untrusted_snippet():
    def fake(*args, **kwargs):
        res = _result()
        res.citations[0].content = "<script>alert('x')</script>"
        return res
    client, _ = _setup(service=fake)
    resp = _post(client)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.json()["citations"][0]["content"].startswith("<script>")


@pytest.mark.parametrize("exception_type,code", [
    (ValueError, "internal_error"),
    (RuntimeError, "internal_error"),
])
def test_unrecognized_backend_exception_is_content_free(exception_type, code):
    secret = "dont-print-my-internal-provider-token"
    def fail(*_a, **_k):
        raise exception_type(secret)
    client, _ = _setup(service=fail)
    resp = _post(client)
    assert resp.status_code == 500
    assert resp.json() == {"detail": {"code": code}}
    assert secret not in resp.text


def test_rejected_duplicate_repository_registration():
    with pytest.raises(ValueError):
        create_app(security=ApiSecurity(TOKEN), repositories=[_session(), _session()])


@pytest.mark.parametrize("kwargs", [
    {"bearer_token": "short"},
    {"bearer_token": "A" * 30, "allowed_origins": ("*",)},
    {"bearer_token": "A" * 30, "allowed_origins": ("https://attacker.test",)},
    {"bearer_token": "A" * 30, "max_requests_per_minute": 0},
    {"bearer_token": "A" * 30, "allow_external_processing": 1},
])
def test_fail_closed_security_configuration(kwargs):
    with pytest.raises(ValueError):
        ApiSecurity(**kwargs)


def test_repository_configuration_rejects_file_path_id_and_missing_retriever():
    with pytest.raises(ValueError):
        RepositorySession("C:/local/private", "Bad", "some/repo", Retriever(), Provider())
    with pytest.raises(ValueError):
        RepositorySession("abc", "Good", "some/repo", object(), Provider())


def test_untrusted_browser_inventory_cannot_replace_server_inventory():
    graph = object()
    session = RepositorySession(
        "sample", "Sample", "trusted/ns", Retriever(), Provider(),
        external_processing_permitted=True, graph_revision=graph,
    )
    calls = []
    def fake(*args, **kwargs):
        calls.append(kwargs)
        return _result()
    client = TestClient(create_app(
        security=ApiSecurity(TOKEN, allow_external_processing=True),
        repositories=[session], answer_service=fake,
    ))
    resp = _post(client)
    assert resp.status_code == 200
    assert calls[0]["graph_revision"] is graph
    resp = _post(client, graph_revision={"namespace": "attacker"})
    assert resp.status_code == 422 and len(calls) == 1
