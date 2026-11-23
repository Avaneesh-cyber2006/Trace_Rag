"""Increment 4: offline one-shot Module 9 -> validated causal answer workflow."""

from dataclasses import FrozenInstanceError, replace
from hashlib import sha256

import pytest

from backend.causal_reasoner import (
    AnswerStatus, CausalDraftError, CausalEvidenceError,
    CausalProviderError, CausalRequestError, CausalResult,
    answer_question, parse_answer,
)
from backend.causal_reasoner.evidence import InvalidEvidenceBundle
from backend.retrieval.errors import InvalidHybridEvidence
from backend.retrieval.history import HistoricalContext
from backend.retrieval.hybrid import HybridHit
from backend.retrieval.orchestrator import (
    GraphRetrievalResult, HistoricalRetrievalResult, RuntimeRetrievalResult,
)
from backend.retrieval.provenance import GraphRevision
from backend.retrieval.relevance import RelevancePolicy
from backend.git_history.models import GitHistoryInventory
from backend.runtime_evidence.models import RuntimeEvidenceInventory
from backend.retrieval.runtime import RuntimeContext


NS = "trace-rag/repo"
SRC = "def alpha():\n    return 42\n"


def hit(text=SRC, *, namespace=NS, rank=1):
    return HybridHit(
        namespace, sha256(("src/a.py" + text).encode()).hexdigest(),
        sha256(text.encode()).hexdigest(), "src/a.py", "python", "symbol",
        "function", "alpha", None, text, 0.01, rank, rank, rank,
    )


def drafted(label="S1", *, category="indexed_fact", excerpt="return 42", status="answered"):
    return parse_answer({
        "status": status,
        "overview": "Indexed code returns a literal; execution is not verified.",
        "claims": [{
            "text": "Indexed code has a return statement.",
            "category": category,
            "citations": [label],
            "excerpt": excerpt,
        }],
        "hypotheses": [{
            "summary": "The return value might explain the observation.",
            "supporting_citations": ["S1"],
            "contradicting_citations": [],
            "missing_observations": ["An authenticated runtime trace"],
            "status": "possible" if status == "answered" else "insufficient_evidence",
        }],
        "limitations": ["Indexed code is not runtime evidence."],
    })


class Retriever:
    def __init__(self, value=None, exc=None):
        self.value = (hit(),) if value is None else value
        self.exc = exc
        self.calls = []

    def _do(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if self.exc is not None:
            raise self.exc
        return self.value

    def search(self, *args, **kwargs):
        return self._do("search", *args, **kwargs)

    def search_relevant(self, *args, **kwargs):
        return self._do("search_relevant", *args, **kwargs)

    def search_with_graph(self, *args, **kwargs):
        return self._do("search_with_graph", *args, **kwargs)

    def search_with_graph_relevant(self, *args, **kwargs):
        return self._do("search_with_graph_relevant", *args, **kwargs)

    def search_with_history(self, *args, **kwargs):
        return self._do("search_with_history", *args, **kwargs)

    def search_with_runtime(self, *args, **kwargs):
        return self._do("search_with_runtime", *args, **kwargs)


class Provider:
    def __init__(self, value=None, exc=None):
        self.value = drafted() if value is None else value
        self.exc = exc
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        if self.exc is not None:
            raise self.exc
        return self.value


def run(retriever=None, provider=None, *, query="Why does alpha return 42?", **kwargs):
    retriever = Retriever() if retriever is None else retriever
    provider = Provider() if provider is None else provider
    return answer_question(retriever, provider, NS, query,
                           external_processing_approved=True, **kwargs)


def test_end_to_end_returns_only_validated_redacted_citations():
    retriever, provider = Retriever(), Provider()
    result = run(retriever, provider)
    assert type(result) is CausalResult
    assert result.answer.status is AnswerStatus.ANSWERED
    assert result.answer.claims[0].citations == ("S1",)
    assert tuple(item.label for item in result.citations) == ("S1",)
    assert len(retriever.calls) == 1 and retriever.calls[0][0] == "search"
    assert retriever.calls[0][1] == (NS, "Why does alpha return 42?", 8)
    assert len(provider.calls) == 1
    assert provider.calls[0].prepared.items[0].content == SRC
    assert provider.calls[0].external_processing_approved is True
    assert not hasattr(result, "raw") and not hasattr(result, "bundle")
    assert "src/a.py" not in repr(result)
    with pytest.raises(FrozenInstanceError):
        result.warnings = ("tampered",)


def test_unapproved_aborts_before_retrieval_or_provider():
    retriever, provider = Retriever(), Provider()
    with pytest.raises(CausalRequestError, match="not been authorized"):
        answer_question(retriever, provider, NS, "Why?", external_processing_approved=False)
    assert retriever.calls == provider.calls == []


@pytest.mark.parametrize("query", ["", "   ", "secret = private-data", "Hello\x00x", "x" * 4097])
def test_invalid_query_never_retrieves_or_generates(query):
    retriever, provider = Retriever(), Provider()
    with pytest.raises(CausalRequestError):
        run(retriever, provider, query=query)
    assert retriever.calls == provider.calls == []


@pytest.mark.parametrize("top_k", [0, 9, True, "8"])
def test_invalid_top_k_never_retrieves(top_k):
    retriever = Retriever()
    with pytest.raises(CausalRequestError):
        run(retriever, top_k=top_k)
    assert retriever.calls == []


@pytest.mark.parametrize("approved", [0, 1, "yes", None])
def test_approval_must_be_strict_boolean(approved):
    retriever = Retriever()
    with pytest.raises(CausalRequestError):
        answer_question(retriever, Provider(), NS, "Why?",
                        external_processing_approved=approved)
    assert not retriever.calls


def test_no_indexed_results_abstains_without_provider_call():
    retriever, provider = Retriever(value=()), Provider()
    result = run(retriever, provider)
    assert result.answer.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert not result.answer.claims and not result.citations
    assert len(retriever.calls) == 1 and provider.calls == []


def test_privacy_omitted_indexed_result_abstains_without_provider():
    retriever, provider = Retriever(value=(hit("-----BEGIN PRIVATE KEY-----\nsecret"),)), Provider()
    result = run(retriever, provider)
    assert result.answer.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert "Evidence was redacted" in result.warnings[0]
    assert not provider.calls


def test_sensitive_content_is_redacted_before_fake_provider():
    source = "def alpha():\n    API_KEY=some-secret\n    return 42"
    retriever, provider = Retriever(value=(hit(source),)), Provider()
    result = run(retriever, provider)
    assert result.answer.status is AnswerStatus.ANSWERED
    assert "some-secret" not in provider.calls[0].prepared.items[0].content
    assert "[REDACTED]" in provider.calls[0].prepared.items[0].content
    assert result.warnings == ("Evidence was redacted or omitted under privacy and selection limits.",)


@pytest.mark.parametrize("label,category,excerpt", [
    ("S55", "indexed_fact", "return 42"),
    ("S1", "reported_runtime", "return 42"),
    ("S1", "indexed_fact", "return 999"),
])
def test_invalid_draft_cannot_be_displayed(label, category, excerpt):
    provider = Provider(drafted(label, category=category, excerpt=excerpt))
    with pytest.raises(CausalDraftError, match="invalid or unsupported"):
        run(provider=provider)
    assert len(provider.calls) == 1


def test_provider_exception_is_sanitized_and_not_retried():
    provider = Provider(exc=RuntimeError("APIKEY=secret-something; private source"))
    with pytest.raises(CausalProviderError) as error:
        run(provider=provider)
    assert "secret-something" not in str(error.value)
    assert error.value.__cause__ is None
    assert len(provider.calls) == 1


def test_untrusted_provider_wrong_return_type_is_rejected():
    with pytest.raises(CausalDraftError):
        run(provider=Provider(value="{\"status\":\"answered\"}"))


def test_integrity_mismatch_raises_without_provider_call():
    changed = replace(hit(), content_hash="a" * 64)
    retriever, provider = Retriever(value=(changed,)), Provider()
    with pytest.raises(InvalidEvidenceBundle):
        run(retriever, provider)
    assert provider.calls == []


def test_namespace_mismatch_rejected_without_provider():
    retriever, provider = Retriever(value=(hit(namespace="another/repo"),)), Provider()
    with pytest.raises(InvalidEvidenceBundle):
        run(retriever, provider)
    assert not provider.calls


def test_stale_graph_failure_is_not_swallowed_or_downgraded():
    retriever = Retriever(exc=InvalidHybridEvidence("stale graph"))
    graph = object.__new__(GraphRevision)
    with pytest.raises(InvalidHybridEvidence, match="stale graph"):
        run(retriever, graph_revision=graph)
    assert len(retriever.calls) == 1 and retriever.calls[0][0] == "search_with_graph"


def test_unexpected_result_shape_fails_closed():
    retriever, provider = Retriever(value=GraphRetrievalResult((hit(),), ())), Provider()
    with pytest.raises(CausalEvidenceError):
        run(retriever, provider)
    assert not provider.calls


def test_graph_dispatch_makes_one_call():
    graph = object.__new__(GraphRevision)
    retriever = Retriever(value=GraphRetrievalResult((hit(),), ()))
    result = run(retriever, graph_revision=graph)
    assert result.answer.status is AnswerStatus.ANSWERED
    assert len(retriever.calls) == 1
    assert retriever.calls[0][0] == "search_with_graph"


def test_relevance_dispatch_makes_one_call():
    relevance = object.__new__(RelevancePolicy)
    retriever = Retriever()
    assert run(retriever, relevance_policy=relevance).answer.status is AnswerStatus.ANSWERED
    assert retriever.calls[0][0] == "search_relevant"


def test_graph_and_relevance_dispatch_makes_one_call():
    graph = object.__new__(GraphRevision)
    relevance = object.__new__(RelevancePolicy)
    retriever = Retriever(value=GraphRetrievalResult((hit(),), ()))
    run(retriever, graph_revision=graph, relevance_policy=relevance)
    assert [item[0] for item in retriever.calls] == ["search_with_graph_relevant"]


def test_history_dispatch_preserves_incompleteness():
    inventory = object.__new__(GitHistoryInventory)
    history = HistoricalContext(paths=(), history_is_complete=False,
                                source_revision_verified=False)
    retriever = Retriever(value=HistoricalRetrievalResult((hit(),), (), history))
    result = run(retriever, git_history=inventory)
    assert [item[0] for item in retriever.calls] == ["search_with_history"]
    assert any("Git history" in warning for warning in result.warnings)


def test_runtime_dispatch_preserves_incompleteness():
    inventory = object.__new__(RuntimeEvidenceInventory)
    history_inv = object.__new__(GitHistoryInventory)
    history = HistoricalContext(paths=(), history_is_complete=False,
                                source_revision_verified=False)
    runtime = RuntimeContext(paths=(), ingestion_complete=False,
                             source_revision_verified=False, causal_relationship_verified=False)
    retriever = Retriever(value=RuntimeRetrievalResult((hit(),), (), runtime, history))
    result = run(retriever, git_history=history_inv, runtime_evidence=inventory)
    assert [item[0] for item in retriever.calls] == ["search_with_runtime"]
    assert any("Git history" in warning for warning in result.warnings)
    assert any("Runtime" in warning for warning in result.warnings)


def test_runtime_result_with_no_history_still_valid():
    inventory = object.__new__(RuntimeEvidenceInventory)
    runtime = RuntimeContext(paths=(), ingestion_complete=True,
                             source_revision_verified=False, causal_relationship_verified=False)
    retriever = Retriever(value=RuntimeRetrievalResult((hit(),), (), runtime))
    assert run(retriever, runtime_evidence=inventory).answer.status is AnswerStatus.ANSWERED


def test_malformed_provider_answer_cannot_bypass_parser():
    candidate = replace(drafted(), overview="x" * 2100)
    with pytest.raises(CausalDraftError):
        run(provider=Provider(candidate))


def test_history_only_answer_not_promoted_to_source_proof():
    provider = Provider(drafted(label="S1", category="reported_history"))
    with pytest.raises(CausalDraftError):
        run(provider=provider)
