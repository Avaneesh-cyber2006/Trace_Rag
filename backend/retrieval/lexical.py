"""Deterministic, bounded, repository-scoped BM25 over supplied source chunks.

This index is an immutable, in-memory *snapshot*; the caller owns the source
records and must rebuild it when the published repository generation changes.
It performs no filesystem, network, database, embedding or code execution.
The BM25 score is not a calibrated relevance probability; hybrid fusion must
combine ranked identities rather than compare BM25 with cosine scores directly.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import math
import re
from types import MappingProxyType
from typing import Mapping
import unicodedata

from .errors import InvalidLexicalIndex, InvalidRetrievalQuery


_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_CAMEL_FIRST_RE = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_CAMEL_SECOND_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_MAX_DOCUMENTS = 8_192
_MAX_DOCUMENT_CHARS = 16_384
_MAX_CORPUS_CHARS = 16_777_216
_MAX_QUERY_CHARS = 4_096
_MAX_TOP_K = 100
_BM25_K1 = 1.2
_BM25_B = 0.75

_INDEX_ERROR = "Lexical index input is invalid or exceeds its limit."
_QUERY_ERROR = "Lexical retrieval request is invalid or exceeds its limit."


def _valid_text(value: object) -> bool:
    return type(value) is str and bool(value) and not any(
        ord(char) == 0 for char in value
    )


def _valid_path(value: object) -> bool:
    return (
        _valid_text(value)
        and not value.startswith("/")
        and not value.startswith("\\")
        and "\\" not in value
        and all(part not in ("", ".", "..") for part in value.split("/"))
    )


def _valid_hash(value: object) -> bool:
    return type(value) is str and _SHA256_RE.fullmatch(value) is not None


def _tokens(value: str) -> tuple[str, ...]:
    # Separate common code identifiers without importing code or executing it.
    text = unicodedata.normalize("NFKC", value)
    text = _CAMEL_FIRST_RE.sub(" ", text)
    text = _CAMEL_SECOND_RE.sub(" ", text)
    return tuple(_WORD_RE.findall(text.casefold()))


@dataclass(frozen=True, slots=True)
class LexicalDocument:
    """One trusted, published source chunk, supplied explicitly by the caller."""

    repository_namespace: str
    chunk_id: str
    content_hash: str
    relative_path: str
    language: str
    chunk_kind: str
    symbol_kind: str | None
    qualified_name: str | None
    parent_qualified_name: str | None
    content: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not _valid_text(self.repository_namespace)
            or not _valid_hash(self.chunk_id)
            or not _valid_hash(self.content_hash)
            or not _valid_path(self.relative_path)
            or not _valid_text(self.language)
            or not _valid_text(self.chunk_kind)
            or any(
                value is not None and not _valid_text(value)
                for value in (
                    self.symbol_kind,
                    self.qualified_name,
                    self.parent_qualified_name,
                )
            )
            or not _valid_text(self.content)
            or len(self.content) > _MAX_DOCUMENT_CHARS
        ):
            raise InvalidLexicalIndex(_INDEX_ERROR)


@dataclass(frozen=True, slots=True)
class LexicalHit:
    """One verified lexical match; score is raw BM25, not a probability."""

    repository_namespace: str
    chunk_id: str
    content_hash: str
    relative_path: str
    language: str
    chunk_kind: str
    symbol_kind: str | None
    qualified_name: str | None
    parent_qualified_name: str | None
    content: str = field(repr=False)
    score: float
    rank: int


class LexicalIndex:
    """Frozen postings constructed exclusively from an explicit record tuple."""

    __slots__ = (
        "repository_namespace",
        "_documents",
        "_by_id",
        "_postings",
        "_lengths",
        "_average_length",
        "_frozen",
    )

    def __init__(
        self,
        repository_namespace: str,
        documents: tuple[LexicalDocument, ...],
    ) -> None:
        if (
            not _valid_text(repository_namespace)
            or type(documents) is not tuple
            or len(documents) > _MAX_DOCUMENTS
        ):
            raise InvalidLexicalIndex(_INDEX_ERROR)

        identifiers: set[str] = set()
        corpus_chars = 0
        for document in documents:
            if (
                type(document) is not LexicalDocument
                or document.repository_namespace != repository_namespace
                or document.chunk_id in identifiers
            ):
                raise InvalidLexicalIndex(_INDEX_ERROR)
            identifiers.add(document.chunk_id)
            corpus_chars += len(document.content)
            if corpus_chars > _MAX_CORPUS_CHARS:
                raise InvalidLexicalIndex(_INDEX_ERROR)

        sorted_documents = tuple(sorted(documents, key=lambda record: record.chunk_id))
        postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        lengths: list[int] = []
        for document_id, document in enumerate(sorted_documents):
            frequencies = Counter(_tokens(document.content))
            lengths.append(sum(frequencies.values()))
            for term, frequency in frequencies.items():
                postings[term].append((document_id, frequency))

        object.__setattr__(self, "repository_namespace", repository_namespace)
        object.__setattr__(self, "_documents", sorted_documents)
        object.__setattr__(
            self, "_by_id",
            MappingProxyType({record.chunk_id: record for record in sorted_documents}),
        )
        object.__setattr__(
            self,
            "_postings",
            MappingProxyType({term: tuple(pairs) for term, pairs in postings.items()}),
        )
        object.__setattr__(self, "_lengths", tuple(lengths))
        object.__setattr__(
            self,
            "_average_length",
            math.fsum(lengths) / len(lengths) if lengths else 0.0,
        )
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, key: str, value: object) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError("LexicalIndex is immutable")
        object.__setattr__(self, key, value)

    @property
    def document_count(self) -> int:
        return len(self._documents)

    @property
    def documents(self) -> tuple[LexicalDocument, ...]:
        """Immutable, chunk-ID-sorted evidence for bounded graph projection."""
        return self._documents

    def lookup(self, chunk_id: str) -> LexicalDocument | None:
        """Look up source evidence from this immutable snapshot by exact ID."""
        if not _valid_hash(chunk_id):
            raise InvalidRetrievalQuery(_QUERY_ERROR)
        return self._by_id.get(chunk_id)

    def search(self, query_text: str, top_k: int) -> tuple[LexicalHit, ...]:
        """Return only positive-score hits, ordered by score then chunk identity."""
        if (
            type(query_text) is not str
            or not query_text.strip()
            or len(query_text) > _MAX_QUERY_CHARS
            or "\x00" in query_text
            or type(top_k) is not int
            or not 1 <= top_k <= _MAX_TOP_K
        ):
            raise InvalidRetrievalQuery(_QUERY_ERROR)
        if not self._documents:
            return ()

        # Deduplicate query terms: repetitions must not amplify a rank.
        query_terms = sorted(set(_tokens(query_text)))
        if not query_terms:
            return ()

        doc_scores: dict[int, list[float]] = defaultdict(list)
        total = len(self._documents)
        avg_length = self._average_length
        for term in query_terms:
            pairs = self._postings.get(term, ())
            if not pairs:
                continue
            df = len(pairs)
            idf = math.log1p((total - df + 0.5) / (df + 0.5))
            for doc_id, tf in pairs:
                length_factor = 1 - _BM25_B + _BM25_B * self._lengths[doc_id] / avg_length
                weight = idf * (tf * (_BM25_K1 + 1)) / (tf + _BM25_K1 * length_factor)
                doc_scores[doc_id].append(weight)

        ordered = sorted(
            ((math.fsum(components), doc_id) for doc_id, components in doc_scores.items()),
            key=lambda entry: (-entry[0], self._documents[entry[1]].chunk_id),
        )
        hits: list[LexicalHit] = []
        for score, doc_id in ordered[:top_k]:
            if not math.isfinite(score) or score <= 0:
                continue
            document = self._documents[doc_id]
            hits.append(
                LexicalHit(
                    repository_namespace=document.repository_namespace,
                    chunk_id=document.chunk_id,
                    content_hash=document.content_hash,
                    relative_path=document.relative_path,
                    language=document.language,
                    chunk_kind=document.chunk_kind,
                    symbol_kind=document.symbol_kind,
                    qualified_name=document.qualified_name,
                    parent_qualified_name=document.parent_qualified_name,
                    content=document.content,
                    score=score,
                    rank=len(hits) + 1,
                )
            )
        return tuple(hits)
