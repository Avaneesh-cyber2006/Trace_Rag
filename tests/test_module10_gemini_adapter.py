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
    assert factories == [{"api_key": KEY, "http_options": {
        "timeout": 20000, "retry_options": {"attempts": 1},
    }}]
    assert config["automatic_function_calling"] == {"disable": True}
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


class FakeHttpError(RuntimeError):
    def __init__(self, code):
        super().__init__(KEY + " sensitive provider error")
        self.code = code


class SequencedClient(FakeClient):
    def __init__(self, *outcomes):
        super().__init__()
        self.outcomes = iter(outcomes)

    def generate_content(self, *, model, contents, config):
        self.calls.append((model, contents, config))
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return type("Result", (), {"text": outcome})()


def _retry_request(client, attempts):
    raw, prepared = _pair()
    adapter = GeminiAdapter(
        GeminiSettings("gemini-test-model", KEY, enabled=True, max_attempts=attempts),
        _client_factory=lambda **kwargs: client,
    )
    return adapter, prepare_generation_request(raw, prepared, external_processing_approved=True)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_opt_in_transient_retry_reuses_same_redacted_payload(status, monkeypatch):
    sleeps = []
    monkeypatch.setattr("backend.causal_reasoner.providers.gemini.time.sleep", sleeps.append)
    fake = SequencedClient(FakeHttpError(status), _answer())
    adapter, request = _retry_request(fake, 2)
    assert adapter.generate(request).claims[0].citations == ("S1",)
    assert len(fake.calls) == 2 and fake.calls[0] == fake.calls[1]
    assert sleeps == [1.0] and fake.closed


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, None, "504"])
def test_non_transient_failure_is_not_retried(status, monkeypatch):
    sleeps = []
    monkeypatch.setattr("backend.causal_reasoner.providers.gemini.time.sleep", sleeps.append)
    fake = SequencedClient(FakeHttpError(status), _answer())
    adapter, request = _retry_request(fake, 2)
    with pytest.raises(GeminiUnavailable) as info:
        adapter.generate(request)
    assert KEY not in str(info.value)
    assert len(fake.calls) == 1 and not sleeps


def test_retry_is_bounded_and_default_remains_one_attempt(monkeypatch):
    sleeps = []
    monkeypatch.setattr("backend.causal_reasoner.providers.gemini.time.sleep", sleeps.append)
    for attempts in (1, 2):
        fake = SequencedClient(FakeHttpError(504), FakeHttpError(504), _answer())
        adapter, request = _retry_request(fake, attempts)
        with pytest.raises(GeminiUnavailable):
            adapter.generate(request)
        assert len(fake.calls) == attempts
    assert sleeps == [1.0]
    assert GeminiSettings("gemini-test-model", KEY).max_attempts == 1


def test_invalid_response_is_not_retried(monkeypatch):
    sleeps = []
    monkeypatch.setattr("backend.causal_reasoner.providers.gemini.time.sleep", sleeps.append)
    fake = SequencedClient(_answer(label="S99"), _answer())
    adapter, request = _retry_request(fake, 2)
    with pytest.raises(GeminiInvalidResponse):
        adapter.generate(request)
    assert len(fake.calls) == 1 and not sleeps


@pytest.mark.parametrize("attempts", [0, 3, True, 1.5, "2"])
def test_retry_settings_require_bounded_integer(attempts):
    with pytest.raises(ValueError):
        GeminiSettings("gemini-test-model", KEY, max_attempts=attempts)


def test_response_schema_uses_only_current_evidence_labels_and_kinds():
    raw, prepared = _pair()
    fake = FakeClient()
    adapter, _ = _provider(fake)
    adapter.generate(prepare_generation_request(raw, prepared, external_processing_approved=True))
    schema = fake.calls[0][2]["response_json_schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"status", "overview", "claims", "hypotheses", "limitations"}
    claim = schema["properties"]["claims"]["items"]
    assert claim["properties"]["citations"]["items"]["enum"] == ["S1"]
    assert claim["properties"]["citations"]["minItems"] == 1
    assert set(claim["properties"]["category"]["enum"]) == {"indexed_fact", "hypothesis"}
    assert "excerpt" not in claim["required"]
    hyp = schema["properties"]["hypotheses"]["items"]["properties"]
    for field in ("supporting_citations", "contradicting_citations"):
        assert hyp[field]["items"]["enum"] == ["S1"]
    assert KEY not in json.dumps(schema) and CODE not in json.dumps(schema)


@pytest.mark.parametrize("output,reason", [
    (None, "NO_TEXT"),
    ("invalid JSON", "ANSWER_SCHEMA_INVALID"),
    (_answer(label="S88"), "LABEL_OR_KIND_INVALID"),
    (_answer(category="reported_history"), "LABEL_OR_KIND_INVALID"),
    ("x" * 32769, "OUTPUT_TOO_LARGE"),
], ids=(
    "no-text", "invalid-json", "unknown-label", "invalid-category", "oversized-output",
))
def test_invalid_response_remains_rejected_with_safe_category(output, reason):
    raw, prepared = _pair()
    fake = FakeClient(output=output)
    adapter, _ = _provider(fake)
    with pytest.raises(GeminiInvalidResponse) as info:
        adapter.generate(prepare_generation_request(raw, prepared, external_processing_approved=True))
    assert str(info.value) == "Generation provider returned an invalid response."
    assert info.value.reason == reason
    assert len(fake.calls) == 1


@pytest.mark.parametrize("field,value,category", [
    ("overview", "x" * 2001, "TEXT_TOO_LONG"),
    ("overview", "  ", "TEXT_EMPTY"),
    ("overview", 42, "TEXT_TYPE"),
    ("overview", "<script>private content</script>", "TEXT_UNSAFE_MARKUP"),
    ("overview", "api_key=synthetic-sensitive-value", "TEXT_PRIVACY"),
    ("status", "made-up", "ENUM_INVALID"),
])
def test_schema_failure_reports_safe_category_and_field(field, value, category, caplog):
    payload = json.loads(_answer())
    payload[field] = value
    fake = FakeClient(output=json.dumps(payload))
    adapter, request = _retry_request(fake, 2)
    with pytest.raises(GeminiInvalidResponse) as info:
        adapter.generate(request)
    assert info.value.reason == "ANSWER_SCHEMA_INVALID"
    assert info.value.validation_reason == category
    assert info.value.validation_field == field
    assert category in caplog.text and field in caplog.text
    assert KEY not in caplog.text and "synthetic-sensitive-value" not in caplog.text
    assert "private content" not in caplog.text
    assert len(fake.calls) == 1


def test_long_excerpt_is_rejected_with_exact_local_diagnostic():
    payload = json.loads(_answer())
    payload["claims"][0]["excerpt"] = "x" * 501
    adapter, request = _retry_request(FakeClient(output=json.dumps(payload)), 1)
    with pytest.raises(GeminiInvalidResponse) as info:
        adapter.generate(request)
    assert info.value.validation_reason == "TEXT_TOO_LONG"
    assert info.value.validation_field == "claims.excerpt"


def test_generation_describes_local_text_limits_without_unsupported_keywords():
    fake = FakeClient()
    adapter, request = _retry_request(fake, 1)
    adapter.generate(request)
    properties = fake.calls[0][2]["response_json_schema"]["properties"]
    for node, limit in [
        (properties["overview"], 2000),
        (properties["claims"]["items"]["properties"]["text"], 1000),
        (properties["claims"]["items"]["properties"]["excerpt"], 500),
        (properties["hypotheses"]["items"]["properties"]["summary"], 1000),
        (properties["hypotheses"]["items"]["properties"]["missing_observations"]["items"], 300),
        (properties["limitations"]["items"], 500),
    ]:
        assert str(limit) in node["description"]
        assert "non-whitespace" in node["description"]
        assert "maxLength" not in node and "minLength" not in node


@pytest.mark.parametrize("finish,reason", [("MAX_TOKENS", "OUTPUT_TRUNCATED"), ("SAFETY", "OUTPUT_BLOCKED")])
def test_incomplete_response_is_rejected_even_when_json_parses(finish, reason):
    from types import SimpleNamespace

    class FinishedClient(FakeClient):
        def generate_content(self, **kwargs):
            result = super().generate_content(**kwargs)
            result.candidates = [SimpleNamespace(finish_reason=finish)]
            return result

    fake = FinishedClient()
    adapter, request = _retry_request(fake, 2)
    with pytest.raises(GeminiInvalidResponse) as info:
        adapter.generate(request)
    assert info.value.reason == reason
    assert len(fake.calls) == 1


@pytest.mark.parametrize("status,reason", [
    (400, "REQUEST_INVALID"), (401, "AUTH_OR_PERMISSION"),
    (403, "AUTH_OR_PERMISSION"), (404, "MODEL_UNAVAILABLE"),
    (429, "QUOTA_OR_RATE_LIMIT"), (503, "SERVICE_ERROR"),
    (504, "SERVICE_ERROR"), (None, "TRANSPORT_OR_SDK_ERROR"),
    ("sensitive-string", "TRANSPORT_OR_SDK_ERROR"),
])
def test_provider_failure_categories_do_not_log_sdk_messages(status, reason, caplog):
    fake = FakeClient(error=FakeHttpError(status))
    adapter, request = _retry_request(fake, 1)
    with pytest.raises(GeminiUnavailable) as info:
        adapter.generate(request)
    assert info.value.reason == reason
    assert reason in caplog.text
    assert KEY not in caplog.text and "sensitive" not in caplog.text


@pytest.mark.parametrize("payload,reason", [
    ('{"untrusted-secret-key":1,"untrusted-secret-key":2}', "JSON_DUPLICATE_KEY"),
    ('{"status":NaN}', "JSON_NONFINITE"),
    ('{"status":', "JSON_INVALID"),
    ('[]', "OBJECT_TYPE"),
    ('{}', "MISSING_FIELDS"),
], ids=["duplicate-key", "nonfinite", "malformed", "wrong-root", "missing-fields"])
def test_json_diagnostics_never_include_untrusted_keys(payload, reason, caplog):
    adapter, request = _retry_request(FakeClient(output=payload), 1)
    with pytest.raises(GeminiInvalidResponse) as info:
        adapter.generate(request)
    assert info.value.validation_reason == reason
    assert info.value.validation_field == "answer"
    assert "untrusted-secret-key" not in caplog.text
