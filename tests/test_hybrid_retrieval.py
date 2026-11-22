"""Module 9 hybrid rank fusion: evidence identity, ranking and safety."""

from dataclasses import FrozenInstanceError

import pytest

from backend.retrieval import (
    HybridHit,
    HybridRanker,
    InvalidHybridEvidence,
    InvalidRetrievalQuery,
    LexicalDocument,
    LexicalIndex,
    SemanticCandidate,
)


NS = "tracerag-repository-v1:github:example/corpus"


def doc(letter: str, text: str, **changes: object) -> LexicalDocument:
    values = dict(
        repository_namespace=NS,
        chunk_id=letter * 64,
        content_hash=letter * 64,
        relative_path=f"src/{letter}.py",
        language="python",
        chunk_kind="symbol",
        symbol_kind="function",
        qualified_name=f"src.{letter}",
        parent_qualified_name=None,
        content=text,
    )
    values.update(changes)
    return LexicalDocument(**values)


def sem(letter: str, **changes: object) -> SemanticCandidate:
    values = dict(repository_namespace=NS, chunk_id=letter * 64, content_hash=letter * 64)
    values.update(changes)
    return SemanticCandidate(**values)


def index(*documents: LexicalDocument) -> LexicalIndex:
    return LexicalIndex(NS, tuple(documents))


def test_rrf_intersection_outranks_single_source_hit() -> None:
    ranker = HybridRanker(index(doc("a", "needle"), doc("b", "needle"), doc("c", "other")))
    hits = ranker.search("needle", (sem("b"), sem("c")), 3)
    assert [hit.chunk_id for hit in hits] == ["b" * 64, "a" * 64, "c" * 64]
    assert [(hit.lexical_rank, hit.semantic_rank) for hit in hits] == [
        (2, 1), (1, None), (None, 2)
    ]
    assert hits[0].score == pytest.approx(1 / 62 + 1 / 61)
    assert isinstance(hits[0], HybridHit)


def test_empty_semantics_falls_back_to_lexical() -> None:
    ranker = HybridRanker(index(doc("a", "error"), doc("b", "other")))
    (hit,) = ranker.search("error", (), 10)
    assert hit.chunk_id == "a" * 64 and hit.lexical_rank == 1
    assert hit.semantic_rank is None


def test_semantic_only_hit_keeps_trusted_source_metadata() -> None:
    source = doc("a", "private source text", relative_path="lib/main.py")
    result = HybridRanker(index(source)).search("not present", (sem("a"),), 3)
    (hit,) = result
    assert hit.content == source.content
    assert hit.relative_path == "lib/main.py"
    assert hit.semantic_rank == 1 and hit.lexical_rank is None
    assert hit.repository_namespace == NS


def test_zero_results_are_valid() -> None:
    assert HybridRanker(index(doc("a", "alpha"))).search("beta", (), 3) == ()
    assert HybridRanker(index()).search("word", (), 3) == ()


def test_lexical_and_semantic_same_source_yield_one_hit() -> None:
    hits = HybridRanker(index(doc("a", "token"))).search("token", (sem("a"),), 5)
    assert len(hits) == 1
    assert hits[0].lexical_rank == hits[0].semantic_rank == 1


def test_semantic_rank_order_changes_result() -> None:
    ranker = HybridRanker(index(doc("a", "x"), doc("b", "y")))
    left = ranker.search("absent", (sem("a"), sem("b")), 2)
    right = ranker.search("absent", (sem("b"), sem("a")), 2)
    assert left[0].chunk_id == "a" * 64
    assert right[0].chunk_id == "b" * 64


def test_score_not_computed_from_raw_semantic_similarity() -> None:
    ranker = HybridRanker(index(doc("a", "t")))
    result = ranker.search("t", (sem("a"),), 1)
    assert result[0].score == pytest.approx(2 / 61)
    assert result[0].score < 1.0


def test_corpus_order_does_not_change_fused_results() -> None:
    documents = (doc("a", "alpha"), doc("b", "alpha"), doc("c", "z"))
    left = HybridRanker(index(*documents)).search("alpha", (sem("b"), sem("c")), 3)
    right = HybridRanker(index(*reversed(documents))).search("alpha", (sem("b"), sem("c")), 3)
    assert left == right


def test_ties_break_by_path_then_chunk_identity() -> None:
    # A lexical-only rank-one and semantic-only rank-one have equal RRF scores.
    documents = (doc("a", "needle", relative_path="src/Z.py"),
                 doc("b", "different", relative_path="src/A.py"))
    results = HybridRanker(index(*documents)).search("needle", (sem("b"),), 2)
    assert [hit.chunk_id for hit in results] == ["b" * 64, "a" * 64]
    assert results[0].score == results[1].score
    # An equal path falls back to the immutable chunk identity.
    identical_paths = (doc("a", "needle", relative_path="src/one.py"),
                       doc("b", "different", relative_path="src/one.py"))
    equal_path_results = HybridRanker(index(*identical_paths)).search(
        "needle", (sem("b"),), 2
    )
    assert [hit.chunk_id for hit in equal_path_results] == ["a" * 64, "b" * 64]


def test_top_k_truncates_after_fusion() -> None:
    ranker = HybridRanker(index(doc("a", "test"), doc("b", "test"), doc("c", "test")))
    result = ranker.search("test", (sem("c"),), 2)
    assert len(result) == 2
    assert [h.rank for h in result] == [1, 2]


def test_document_lookup_is_immutable_and_identity_strict() -> None:
    src = doc("a", "text")
    ix = index(src)
    assert ix.lookup(src.chunk_id) == src
    assert ix.lookup("b" * 64) is None
    with pytest.raises(TypeError):
        ix._by_id[src.chunk_id] = src
    with pytest.raises(InvalidRetrievalQuery):
        ix.lookup("INVALID")
    with pytest.raises(InvalidRetrievalQuery):
        ix.lookup(True)
    with pytest.raises(AttributeError):
        ix._by_id = {}


@pytest.mark.parametrize("bad", [None, {}, [], "a", (sem("a"),) * 101])
def test_rejects_malformed_semantic_sequence(bad: object) -> None:
    ranker = HybridRanker(index(doc("a", "x")))
    with pytest.raises(InvalidHybridEvidence):
        ranker.search("x", bad, 3)


def test_rejects_semantic_non_candidate_element() -> None:
    with pytest.raises(InvalidHybridEvidence):
        HybridRanker(index(doc("a", "x"))).search("x", (object(),), 3)


def test_rejects_duplicate_semantic_ids() -> None:
    with pytest.raises(InvalidHybridEvidence):
        HybridRanker(index(doc("a", "x"))).search("x", (sem("a"), sem("a")), 3)


def test_rejects_foreign_namespace_even_if_id_and_hash_match() -> None:
    with pytest.raises(InvalidHybridEvidence):
        HybridRanker(index(doc("a", "x"))).search(
            "x", (sem("a", repository_namespace="other-repository"),), 3
        )


def test_rejects_stale_content_hash() -> None:
    with pytest.raises(InvalidHybridEvidence):
        HybridRanker(index(doc("a", "x"))).search("x", (sem("a", content_hash="f" * 64),), 3)


def test_rejects_unknown_semantic_id() -> None:
    with pytest.raises(InvalidHybridEvidence):
        HybridRanker(index(doc("a", "x"))).search("x", (sem("b"),), 3)


def test_does_not_silently_drop_invalid_evidence_at_top_k_boundary() -> None:
    with pytest.raises(InvalidHybridEvidence):
        HybridRanker(index(doc("a", "test"))).search("test", (sem("a"), sem("b")), 1)


@pytest.mark.parametrize("bad", [None, 1, "", "x" * 63, "X" * 64, "a" * 65])
def test_rejects_bad_semantic_chunk_id(bad: object) -> None:
    with pytest.raises(InvalidHybridEvidence):
        sem("a", chunk_id=bad)


@pytest.mark.parametrize("bad", [None, 1, "", "A" * 64, "a" * 65])
def test_rejects_bad_semantic_content_hash(bad: object) -> None:
    with pytest.raises(InvalidHybridEvidence):
        sem("a", content_hash=bad)


@pytest.mark.parametrize("bad", [None, 1, "", "\x00"])
def test_rejects_bad_semantic_namespace(bad: object) -> None:
    with pytest.raises(InvalidHybridEvidence):
        sem("a", repository_namespace=bad)


@pytest.mark.parametrize("bad", [None, -1, 0, 101, True, 1.2, "2"])
def test_rejects_invalid_top_k(bad: object) -> None:
    with pytest.raises(InvalidRetrievalQuery):
        HybridRanker(index(doc("a", "x"))).search("x", (), bad)


@pytest.mark.parametrize("bad", [None, "", " ", "\x00", "x" * 4097])
def test_rejects_invalid_queries(bad: object) -> None:
    with pytest.raises(InvalidRetrievalQuery):
        HybridRanker(index(doc("a", "x"))).search(bad, (), 1)


@pytest.mark.parametrize("bad", [None, True, 0, -1, 10001, 1.0, "60"])
def test_rejects_bad_rrf_k(bad: object) -> None:
    with pytest.raises(InvalidRetrievalQuery):
        HybridRanker(index(doc("a", "x")), rrf_k=bad)


def test_non_default_rrf_k_is_applied() -> None:
    hit = HybridRanker(index(doc("a", "x")), rrf_k=10).search("x", (sem("a"),), 1)[0]
    assert hit.score == pytest.approx(2 / 11)


def test_rejects_non_index() -> None:
    with pytest.raises(InvalidRetrievalQuery):
        HybridRanker(object())


def test_result_models_are_frozen_and_source_not_repr_visible() -> None:
    hit = HybridRanker(index(doc("a", "SECRET_MARKER_X"))).search("secret", (sem("a"),), 1)[0]
    assert "SECRET_MARKER_X" not in repr(hit)
    with pytest.raises(FrozenInstanceError):
        hit.rank = 3
    with pytest.raises(FrozenInstanceError):
        SemanticCandidate(NS, "a" * 64, "a" * 64).content_hash = "b" * 64


def test_errors_do_not_echo_source_evidence() -> None:
    content = "DO_NOT_ECHO_9345"
    with pytest.raises(InvalidHybridEvidence) as captured:
        HybridRanker(index(doc("a", content))).search(content, (sem("a", content_hash="f" * 64),), 1)
    assert content not in str(captured.value)


def test_full_pool_not_truncated_before_fusion() -> None:
    docs = tuple(
        doc(
            f"{i:064x}"[-1], "matched", chunk_id=f"{i:064x}",
            content_hash=f"{i:064x}",
            relative_path="aaa.py" if i == 100 else "zzz.py",
        )
        for i in range(101)
    )
    ranker = HybridRanker(index(*docs))
    hits = ranker.search("matched", (SemanticCandidate(NS, f"{100:064x}", f"{100:064x}"),), 1)
    assert len(hits) == 1
    assert hits[0].semantic_rank == 1
    assert hits[0].lexical_rank is None  # 101st document is outside lexical top 100


def test_rejects_non_source_evidence_without_external_io(monkeypatch: pytest.MonkeyPatch) -> None:
    import pathlib
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("should not access filesystem")
    monkeypatch.setattr(pathlib.Path, "read_text", forbidden)
    monkeypatch.setattr(pathlib.Path, "read_bytes", forbidden)
    ranker = HybridRanker(index(doc("a", "test")))
    assert ranker.search("test", (sem("a"),), 1)
