"""Increment 7: bounded, opt-in relevance gates over pinned RRF hits."""

from dataclasses import replace
from hashlib import sha256
import math

import pytest

from backend.retrieval import (
    HybridHit, InvalidHybridEvidence, InvalidRetrievalQuery,
    LexicalDocument, LexicalIndex, RelevanceGate, RelevancePolicy,
)

NS = "tracerag-repository-v1:github:example/repo"


def source(letter, content, path=None, qualified=None):
    return LexicalDocument(
        repository_namespace=NS,
        chunk_id=letter * 64,
        content_hash=sha256(content.encode()).hexdigest(),
        relative_path=path or f"src/{letter}.py",
        language="python", chunk_kind="symbol", symbol_kind="function",
        qualified_name=qualified or f"demo.{letter}",
        parent_qualified_name=None, content=content,
    )


def hit(source, rank, *, lexical_rank=None, semantic_rank=None, score=0.01):
    return HybridHit(
        repository_namespace=source.repository_namespace,
        chunk_id=source.chunk_id, content_hash=source.content_hash,
        relative_path=source.relative_path, language=source.language,
        chunk_kind=source.chunk_kind, symbol_kind=source.symbol_kind,
        qualified_name=source.qualified_name,
        parent_qualified_name=source.parent_qualified_name, content=source.content,
        score=score, rank=rank, lexical_rank=lexical_rank,
        semantic_rank=semantic_rank,
    )


def fixture():
    a = source("a", "cache read")
    b = source("b", "cache invoice payment")
    c = source("c", "invoice processing")
    index = LexicalIndex(NS, (c, b, a))
    candidates = (
        hit(a, 1, semantic_rank=1, lexical_rank=2, score=0.04),
        hit(b, 2, semantic_rank=2, lexical_rank=1, score=0.03),
        hit(c, 3, semantic_rank=3, score=0.02),
    )
    return index, candidates


def test_relevance_gate_before_final_top_k_reorders_by_distinct_term_coverage():
    index, candidates = fixture()
    kept = RelevanceGate(index, RelevancePolicy()).select("invoice payment", candidates, 1)
    assert len(kept) == 1 and kept[0].chunk_id == "b" * 64
    assert kept[0].rank == 1 and kept[0].score == 0.03
    assert kept[0].lexical_rank == 1 and kept[0].semantic_rank == 2
    assert candidates[0].rank == 1  # Input objects remain unchanged.


def test_disable_reranking_preserves_rrf_order_and_compacts_rank():
    index, candidates = fixture()
    kept = RelevanceGate(index, RelevancePolicy(rerank_by_query_coverage=False)).select(
        "invoice payment", candidates, 3
    )
    assert [x.chunk_id for x in kept] == ["b" * 64, "c" * 64]
    assert [x.rank for x in kept] == [1, 2]


def test_repeated_query_terms_count_once_and_metadata_is_searchable():
    doc = source("d", "no content match", path="src/InvoiceService.py", qualified="PaymentHandler.handle")
    index = LexicalIndex(NS, (doc,))
    gate = RelevanceGate(index, RelevancePolicy(min_query_matches=2))
    assert gate.select("invoice invoice payment", (hit(doc, 1, semantic_rank=1),), 1)
    assert gate.select("unknown unknown payment", (hit(doc, 1, semantic_rank=1),), 1) == ()


def test_default_may_reject_paraphrase_but_optout_preserves_semantic_only():
    doc = source("a", "postage dispatch")
    index = LexicalIndex(NS, (doc,))
    hits = (hit(doc, 1, semantic_rank=1),)
    assert RelevanceGate(index, RelevancePolicy()).select("shipping", hits, 1) == ()
    kept = RelevanceGate(index, RelevancePolicy(min_query_matches=0)).select("shipping", hits, 1)
    assert kept == hits


def test_optional_dual_channel_requires_both_rank_sources():
    index, candidates = fixture()
    gate = RelevanceGate(index, RelevancePolicy(min_query_matches=0, require_dual_channel=True))
    kept = gate.select("invoice", candidates, 3)
    assert [x.chunk_id for x in kept] == ["b" * 64, "a" * 64]


def test_equal_coverage_preserves_original_rrf_order():
    index, candidates = fixture()
    kept = RelevanceGate(index, RelevancePolicy()).select("cache", candidates, 3)
    assert [x.chunk_id for x in kept] == ["a" * 64, "b" * 64]


@pytest.mark.parametrize("policy", [
    {"min_query_matches": -1}, {"min_query_matches": 65},
    {"min_query_matches": True}, {"min_query_matches": 1.0},
    {"require_dual_channel": 0}, {"rerank_by_query_coverage": "true"},
])
def test_invalid_policy_rejected(policy):
    with pytest.raises(InvalidRetrievalQuery):
        RelevancePolicy(**policy)


@pytest.mark.parametrize("query,top_k", [
    ("", 1), ("\x00", 1), ("x" * 4097, 1), (None, 1),
    ("valid", 0), ("valid", True), ("valid", 101),
])
def test_invalid_gate_request_rejected(query, top_k):
    index, candidates = fixture()
    with pytest.raises(InvalidRetrievalQuery):
        RelevanceGate(index, RelevancePolicy()).select(query, candidates, top_k)


@pytest.mark.parametrize("mutation", [
    lambda h: replace(h, content="forged source"),
    lambda h: replace(h, content_hash="f" * 64),
    lambda h: replace(h, repository_namespace="other"),
    lambda h: replace(h, chunk_id="not-a-sha256"),
    lambda h: replace(h, rank=2),
    lambda h: replace(h, score=float("nan")),
    lambda h: replace(h, score=0),
    lambda h: replace(h, semantic_rank=None, lexical_rank=None),
    lambda h: replace(h, lexical_rank=True),
])
def test_forged_hit_rejected_even_if_later_filtered(mutation):
    index, candidates = fixture()
    forged = (mutation(candidates[0]), *candidates[1:])
    with pytest.raises(InvalidHybridEvidence):
        RelevanceGate(index, RelevancePolicy(min_query_matches=64)).select(
            "no-match", forged, 1
        )


def test_duplicate_chunk_identity_and_bad_containers_rejected():
    index, candidates = fixture()
    gate = RelevanceGate(index, RelevancePolicy())
    with pytest.raises(InvalidHybridEvidence):
        gate.select("cache", (candidates[0], replace(candidates[0], rank=2)), 2)
    with pytest.raises(InvalidRetrievalQuery):
        gate.select("cache", list(candidates), 2)
    with pytest.raises(InvalidRetrievalQuery):
        RelevanceGate(index, object())


def test_input_pool_cannot_exceed_100_hits():
    index, candidates = fixture()
    with pytest.raises(InvalidRetrievalQuery):
        RelevanceGate(index, RelevancePolicy()).select("cache", candidates * 34, 3)


def test_unbounded_metadata_does_not_trigger_unbounded_tokenization():
    doc = source("a", "cache", qualified="x" * 33_000)
    index = LexicalIndex(NS, (doc,))
    with pytest.raises(InvalidHybridEvidence):
        RelevanceGate(index, RelevancePolicy()).select(
            "cache", (hit(doc, 1, semantic_rank=1),), 1
        )
