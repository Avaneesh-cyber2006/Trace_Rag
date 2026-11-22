"""Module 9 lexical foundation: determinism, bounds and evidence identity."""

from dataclasses import FrozenInstanceError, replace
import math

import pytest

from backend.retrieval import (
    InvalidLexicalIndex,
    InvalidRetrievalQuery,
    LexicalDocument,
    LexicalHit,
    LexicalIndex,
)


NAMESPACE = "tracerag-repository-v1:github:example/資料庫"


def document(letter: str, content: str, **changes: object) -> LexicalDocument:
    fields = dict(
        repository_namespace=NAMESPACE,
        chunk_id=letter * 64,
        content_hash=(letter.upper().lower() if letter in "abcdef" else "0") * 64,
        relative_path=f"src/{letter}.py",
        language="python",
        chunk_kind="symbol",
        symbol_kind="function",
        qualified_name=f"module.{letter}",
        parent_qualified_name=None,
        content=content,
    )
    fields.update(changes)
    return LexicalDocument(**fields)


def test_retrieves_source_identity_and_content() -> None:
    record = document("a", "def calculateTotal(): return invoice_sum")
    index = LexicalIndex(NAMESPACE, (record,))
    (hit,) = index.search("calculate total", 3)
    assert isinstance(hit, LexicalHit)
    assert (hit.chunk_id, hit.content_hash, hit.relative_path) == (
        record.chunk_id, record.content_hash, record.relative_path
    )
    assert hit.content == record.content
    assert hit.rank == 1 and math.isfinite(hit.score) and hit.score > 0


def test_rarer_term_gets_larger_score() -> None:
    index = LexicalIndex(
        NAMESPACE,
        (
            document("a", "loader parser"),
            document("b", "loader"),
            document("c", "loader"),
        ),
    )
    (hit,) = index.search("parser", 5)
    assert hit.chunk_id == "a" * 64
    assert index.search("no_match_found", 5) == ()


def test_higher_term_frequency_ranks_first() -> None:
    index = LexicalIndex(
        NAMESPACE,
        (document("a", "error error error"), document("b", "error other other")),
    )
    assert index.search("error", 2)[0].chunk_id == "a" * 64


def test_order_is_identical_for_document_permutations() -> None:
    records = (document("a", "alpha"), document("b", "alpha"), document("c", "alpha"))
    left = LexicalIndex(NAMESPACE, records).search("alpha", 3)
    right = LexicalIndex(NAMESPACE, tuple(reversed(records))).search("alpha", 3)
    assert left == right
    assert tuple(hit.chunk_id for hit in left) == tuple(letter * 64 for letter in "abc")
    assert [hit.rank for hit in left] == [1, 2, 3]


def test_query_repetitions_cannot_inflate_scores() -> None:
    index = LexicalIndex(NAMESPACE, (document("a", "cache failure"),))
    assert index.search("cache", 1) == index.search("cache cache cache", 1)


def test_tokenizer_handles_snake_camel_acronyms_and_unicode() -> None:
    index = LexicalIndex(
        NAMESPACE,
        (document("a", "HTTPResponse parse_record Δοκιμή"),),
    )
    for query in ("http", "response", "parse", "record", "δοκιμή"):
        assert index.search(query, 1)[0].chunk_id == "a" * 64


def test_nfkc_normalization_and_casefold() -> None:
    index = LexicalIndex(NAMESPACE, (document("a", "ＦＵＬＬＷＩＤＴＨ Café"),))
    assert index.search("fullwidth café", 1)


def test_unmatched_words_return_no_hits() -> None:
    index = LexicalIndex(NAMESPACE, (document("a", "word"),))
    assert index.search("different", 2) == ()
    assert index.search("///", 2) == ()


def test_empty_explicit_snapshot_is_valid() -> None:
    index = LexicalIndex(NAMESPACE, ())
    assert index.document_count == 0
    assert index.search("anything", 1) == ()


def test_top_k_bound_and_ranking() -> None:
    index = LexicalIndex(NAMESPACE, tuple(document(letter, "term") for letter in "abcdef"))
    assert len(index.search("term", 2)) == 2
    assert [hit.rank for hit in index.search("term", 2)] == [1, 2]


@pytest.mark.parametrize("bad", ["", " ", None, 1, 1.0, True, ("text",), "\x00"])
def test_rejects_invalid_queries(bad: object) -> None:
    index = LexicalIndex(NAMESPACE, ())
    with pytest.raises(InvalidRetrievalQuery):
        index.search(bad, 1)


@pytest.mark.parametrize("bad", [0, -1, 101, True, 1.0, "2", None])
def test_rejects_invalid_top_k(bad: object) -> None:
    index = LexicalIndex(NAMESPACE, ())
    with pytest.raises(InvalidRetrievalQuery):
        index.search("valid", bad)


def test_rejects_oversize_query() -> None:
    with pytest.raises(InvalidRetrievalQuery):
        LexicalIndex(NAMESPACE, ()).search("x" * 4097, 1)


@pytest.mark.parametrize(
    "changes",
    [
        {"repository_namespace": ""},
        {"chunk_id": "invalid"},
        {"chunk_id": "A" * 64},
        {"content_hash": "invalid"},
        {"relative_path": "../secret.py"},
        {"relative_path": "src//module.py"},
        {"relative_path": "C:\\private.py"},
        {"relative_path": "/absolute.py"},
        {"language": ""},
        {"chunk_kind": ""},
        {"symbol_kind": ""},
        {"content": ""},
        {"content": "x" * 16385},
        {"content": "x\x00y"},
    ],
)
def test_rejects_bad_source_documents(changes: dict[str, object]) -> None:
    with pytest.raises(InvalidLexicalIndex):
        replace(document("a", "safe"), **changes)


def test_rejects_duplicate_ids() -> None:
    first = document("a", "one")
    second = replace(first, content="other")
    with pytest.raises(InvalidLexicalIndex):
        LexicalIndex(NAMESPACE, (first, second))


def test_rejects_mixed_namespaces() -> None:
    with pytest.raises(InvalidLexicalIndex):
        LexicalIndex(NAMESPACE, (document("a", "one", repository_namespace="other"),))


def test_index_requires_explicit_tuple_and_own_namespace() -> None:
    with pytest.raises(InvalidLexicalIndex):
        LexicalIndex(NAMESPACE, [document("a", "one")])
    with pytest.raises(InvalidLexicalIndex):
        LexicalIndex("", ())
    with pytest.raises(InvalidLexicalIndex):
        LexicalIndex(NAMESPACE, (object(),))


def test_corpus_budget_is_checked_before_tokenization() -> None:
    large = "x" * 16384
    # 1,025 documents exceed the 16-MiB total corpus budget.
    documents = tuple(
        document(f"{number:064x}"[-1], large, chunk_id=f"{number:064x}")
        for number in range(1025)
    )
    with pytest.raises(InvalidLexicalIndex):
        LexicalIndex(NAMESPACE, documents)


def test_records_are_immutable_and_reprs_do_not_disclose_source() -> None:
    record = document("a", "PRIVATE_SOURCE_123")
    hit = LexicalIndex(NAMESPACE, (record,)).search("private", 1)[0]
    assert "PRIVATE_SOURCE_123" not in repr(record)
    assert "PRIVATE_SOURCE_123" not in repr(hit)
    with pytest.raises(FrozenInstanceError):
        record.content = "modified"
    with pytest.raises(FrozenInstanceError):
        hit.score = 0


def test_index_cannot_be_mutated_after_build() -> None:
    index = LexicalIndex(NAMESPACE, (document("a", "word"),))
    with pytest.raises(AttributeError):
        index.repository_namespace = "other"
    with pytest.raises(AttributeError):
        index._documents = ()
    with pytest.raises(TypeError):
        index._postings["word"] = ()


def test_contents_not_in_error_messages() -> None:
    content = "SENSITIVE_MARKER_123"
    with pytest.raises(InvalidLexicalIndex) as exc:
        LexicalIndex(NAMESPACE, (document("a", content), document("a", "other")))
    assert content not in str(exc.value)


def test_query_does_not_read_files_or_chroma(monkeypatch: pytest.MonkeyPatch) -> None:
    # This layer works from supplied strings; it must not call file or DB readers.
    import pathlib
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("index must not access filesystem")
    monkeypatch.setattr(pathlib.Path, "read_bytes", forbidden)
    monkeypatch.setattr(pathlib.Path, "read_text", forbidden)
    index = LexicalIndex(NAMESPACE, (document("a", "source token"),))
    assert index.search("token", 1)
