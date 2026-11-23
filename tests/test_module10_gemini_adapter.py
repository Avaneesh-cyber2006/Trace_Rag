"""Entirely offline: verify outbound Gemini adapter semantics with fake SDK clients."""

from dataclasses import replace
from hashlib import sha256
import json

import pytest

from backend.causal_reasoner import (
    InvalidReasonedAnswer, assemble_evidence, redact_evidence, validate_answer,
)
from backend.causal_reasoner.providers import (
    GenerationBoundaryError, GenerationRequest, ReasoningProvider,
    prepare_generation_request,
)
from backend.causal_reasoner.providers.gemini import (
    GeminiAdapter, GeminiDisabled, GeminiInvalidResponse, GeminiSettings,
    GeminiUnavailable,
)
from backend.retrieval.hybrid import HybridHit

NAMESPACE = "sample/repo"
CODE = "def detect():\n    return 4"
KEY = "private-test-api-key-that-must-not-be-logged"


def _pair(text=CODE):
    hit = HybridHit(
        NAMESPACE, sha256(("src/detect.py" + text).encode()).hexdigest(),
        sha256(text.encode()).hexdigest(), "src/detect.py", "python", "symbol",
        "function", "detect", None, text, 0.1, 1, 1, 1,
    )
    raw = assemble_evidence(NAMESPACE, "Why return 4?", (hit,))
    return raw, redact_evidence(raw)


def _answer(*, label="S1", category="indexed_fact"):
    return json.dumps({
        "status": "answered", "overview": "An indexed chunk returns 4.",
        "claims": [{"text": "This chunk contains a return statement.",
                    "category": category, "citations": [label], "excerpt": "return 4"}],
        "hypotheses": [], "limitations": ["Actual runtime behavior is unknown."],
    })


class FakeClient:
    def __init__(self, output=_answer(), error=None):
        self.calls = []
        self.output = output
        self.error = error
        self.models = self
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def generate_content(self, *, model, contents, config):
        self.calls.append((model, contents, config))
        if self.error:
            raise self.error
        return type("Result", (), {"text": self.output})()


def _provider(fake, *, enabled=True, key=KEY):
    builds = []

    def factory(**kwargs):
        builds.append(kwargs)
        return fake

    return GeminiAdapter(GeminiSettings(model="gemini-test-model", api_key=key, enabled=enabled),
                         _client_factory=factory), builds


def test_explicit_opt_in_returns_only_draft_and_can_be_independently_validated():
    raw, redacted = _pair()
    request = prepare_generation_request(raw, redacted, external_processing_approved=True)
    client = FakeClient()
    adapter, factories = _provider(client)
    assert isinstance(adapter, ReasoningProvider)
    result = adapter.generate(request)
    assert result.claims[0].citations == ("S1",)
    assert validate_answer(raw, redacted, json.loads(_answer())).answer == result
    assert client.closed is True
    assert len(client.calls) == 1
    model, contents, config = client.calls[0]
    assert model == "gemini-test-model"
    assert config["response_mime_type"] == "application/json"
    assert config["max_output_tokens"] == 2048
    assert "system_instruction" in config and "untrusted data" in config["system_instruction"]
    assert "return 4" in contents and "Why return 4?" in contents
    assert "src/detect.py" not in contents
    assert NAMESPACE not in contents
    assert KEY not in contents
    assert factories == [{"api_key": KEY, "http_options": {"timeout": 20000}}]
    assert "private-test" not in repr(adapter._settings)
    assert "return 4" not in repr(request)


@pytest.mark.parametrize("approved,enabled", [(False, False), (False, True), (True, False)])
def test_no_client_creation_without_both_authorizations(approved, enabled):
    raw, redacted = _pair()
    request = prepare_generation_request(raw, redacted, external_processing_approved=approved)
    fake = FakeClient()
    adapter, calls = _provider(fake, enabled=enabled)
    with pytest.raises(GeminiDisabled):
        adapter.generate(request)
    assert not calls and not fake.calls


def test_default_provider_disabled_even_if_env_holds_an_api_key(monkeypatch):
    monkeypatch.setenv("TRACERAG_GEMINI_API_KEY", "secret-dont-use")
    raw, redacted = _pair()
    fake = FakeClient()
    adapter, calls = _provider(fake, enabled=False)
    with pytest.raises(GeminiDisabled):
        adapter.generate(prepare_generation_request(raw, redacted,
                                                    external_processing_approved=True))
    assert not calls


def test_mismatched_redaction_and_unknown_credentials_are_blocked_before_any_sdk_call():
    raw, prepared = _pair()
    other_raw, other_prepared = _pair("def detect():\n    return 3")
    with pytest.raises(GenerationBoundaryError):
        prepare_generation_request(raw, other_prepared, external_processing_approved=True)
    with pytest.raises(GenerationBoundaryError):
        prepare_generation_request(raw, prepared, external_processing_approved="yes")
    malicious = replace(raw, query_text="Authorization: Bearer mysecrettoken1234567")
    with pytest.raises(GenerationBoundaryError):
        prepare_generation_request(malicious, prepared, external_processing_approved=True)


def test_unsealed_or_tampered_request_blocked_before_sdk():
    raw, prepared = _pair()
    fake = FakeClient()
    adapter, builds = _provider(fake)
    bad = GenerationRequest(prepared, True)
    with pytest.raises(GenerationBoundaryError):
        adapter.generate(bad)
    valid = prepare_generation_request(raw, prepared, external_processing_approved=True)
    with pytest.raises(GenerationBoundaryError):
        adapter.generate(replace(valid, prepared=replace(prepared, query_text="new")))
    assert not builds


@pytest.mark.parametrize("model,key,enabled,timeout,tokens", [
    ("", KEY, True, 20000, 2048), ("foo/bar", KEY, True, 20000, 2048),
    ("foo", "", True, 20000, 2048), ("foo", "bad key", True, 20000, 2048),
    ("foo", KEY, 1, 20000, 2048), ("foo", KEY, True, 5, 2048),
    ("foo", KEY, True, 20000, 10),
])
def test_invalid_settings_are_sanitized(model, key, enabled, timeout, tokens):
    with pytest.raises(ValueError, match="Invalid generation provider settings"):
        GeminiSettings(model, key, enabled, timeout, tokens)


@pytest.mark.parametrize("output", [
    "", "not json", "{\"status\":\"answered\"}", "{\"status\":\"answered\",\"status\":\"answered\"}",
    _answer(label="S88"), _answer(category="reported_history"),
    "A" * 32769,
], ids=(
    "empty", "not-json", "missing-fields", "duplicate-field",
    "unknown-citation", "invalid-category", "oversized-response",
))
def test_bad_provider_response_is_sanitized_without_leaking_content(output):
    raw, redacted = _pair()
    fake = FakeClient(output)
    adapter, _ = _provider(fake)
    with pytest.raises(GeminiInvalidResponse) as info:
        adapter.generate(prepare_generation_request(raw, redacted,
                                                    external_processing_approved=True))
    assert str(info.value) == "Generation provider returned an invalid response."
    assert fake.closed


def test_sdk_failures_are_sanitized_and_not_retried():
    raw, redacted = _pair()
    fake = FakeClient(error=RuntimeError(KEY + " raw sensitive provider failure"))
    adapter, calls = _provider(fake)
    with pytest.raises(GeminiUnavailable) as info:
        adapter.generate(prepare_generation_request(raw, redacted,
                                                    external_processing_approved=True))
    assert str(info.value) == "Generation provider could not complete the request."
    assert KEY not in str(info.value)
    assert len(calls) == 1 and len(fake.calls) == 1 and fake.closed


def test_no_indexed_source_disables_generation_preparation():
    raw, prepared = _pair()
    empty = replace(raw, items=())
    with pytest.raises(GenerationBoundaryError):
        prepare_generation_request(empty, redact_evidence(empty))


def test_no_external_call_when_the_sdk_is_missing_or_not_imported():
    raw, prepared = _pair()
    request = prepare_generation_request(raw, prepared, external_processing_approved=False)
    adapter = GeminiAdapter(GeminiSettings("gemini-test-model", KEY, enabled=True))
    with pytest.raises(GeminiDisabled):
        adapter.generate(request)
