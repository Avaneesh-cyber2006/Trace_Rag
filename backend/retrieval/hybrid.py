"""Bounded, deterministic rank-only fusion over one trusted lexical snapshot.

Semantic candidates must already be ordered and validated by Module 5's
semantic-search API. This module checks their source identity against the
caller's lexical snapshot, then fuses *ranks*, not incomparable raw scores.
It does not hold Chroma's active-generation lease; a future orchestrator must
pin the generation across both retrieval branches and index construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re

from .errors import InvalidHybridEvidence, InvalidRetrievalQuery
from .lexical import LexicalIndex


_MAX_RESULTS = 100
_DEFAULT_RRF_K = 60
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_EVIDENCE_ERROR = "Hybrid retrieval evidence is invalid or inconsistent."
_QUERY_ERROR = "Hybrid retrieval request is invalid or exceeds its limit."


@dataclass(frozen=True, slots=True)
class SemanticCandidate:
    """One already-ranked Module 5 hit reduced to identity and content hash.

    The tuple's order defines its one-based semantic rank. Callers must build
    the tuple only from validated, normalized Module 5 search results.
    """

    repository_namespace: str
    chunk_id: str
    content_hash: str

    def __post_init__(self) -> None:
        if (
            type(self.repository_namespace) is not str
            or not self.repository_namespace
            or "\x00" in self.repository_namespace
            or type(self.chunk_id) is not str
            or _SHA256.fullmatch(self.chunk_id) is None
            or type(self.content_hash) is not str
            or _SHA256.fullmatch(self.content_hash) is None
        ):
            raise InvalidHybridEvidence(_EVIDENCE_ERROR)


@dataclass(frozen=True, slots=True)
class HybridHit:
    """One fused hit; its content comes solely from the lexical snapshot."""

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
    lexical_rank: int | None
    semantic_rank: int | None


@dataclass(frozen=True, slots=True)
class HybridRanker:
    """Merge lexical and semantic ranks, rejecting stale semantic evidence.

    The index must be built from the same *pinned published generation* that
    produced semantic candidates; identity checks protect against accidental
    mismatches but cannot replace generation pinning or database validation.
    """

    index: LexicalIndex
    rrf_k: int = _DEFAULT_RRF_K

    def __post_init__(self) -> None:
        if (
            type(self.index) is not LexicalIndex
            or type(self.rrf_k) is not int
            or not 1 <= self.rrf_k <= 10_000
        ):
            raise InvalidRetrievalQuery(_QUERY_ERROR)

    def search(
        self,
        query_text: str,
        semantic_candidates: tuple[SemanticCandidate, ...],
        top_k: int,
    ) -> tuple[HybridHit, ...]:
        """Fuse the top 100 lexical ranks and supplied semantic ranks.

        Results are stable under lexical index input permutation. Equal fused
        scores use source path then chunk identity as deterministic tie-breaks.
        A semantic-only candidate is allowed if present in the trusted index.
        """
        if type(top_k) is not int or not 1 <= top_k <= _MAX_RESULTS:
            raise InvalidRetrievalQuery(_QUERY_ERROR)
        if (
            type(semantic_candidates) is not tuple
            or len(semantic_candidates) > _MAX_RESULTS
        ):
            raise InvalidHybridEvidence(_EVIDENCE_ERROR)

        semantic_ranks: dict[str, int] = {}
        for rank, candidate in enumerate(semantic_candidates, 1):
            if (
                type(candidate) is not SemanticCandidate
                or candidate.repository_namespace != self.index.repository_namespace
                or candidate.chunk_id in semantic_ranks
            ):
                raise InvalidHybridEvidence(_EVIDENCE_ERROR)
            source = self.index.lookup(candidate.chunk_id)
            if source is None or source.content_hash != candidate.content_hash:
                raise InvalidHybridEvidence(_EVIDENCE_ERROR)
            semantic_ranks[candidate.chunk_id] = rank

        # Lexical search owns query validation and uses the full bounded pool.
        lexical = self.index.search(query_text, _MAX_RESULTS)
        lexical_ranks = {hit.chunk_id: hit.rank for hit in lexical}

        scored: list[tuple[float, str]] = []
        for chunk_id in lexical_ranks.keys() | semantic_ranks.keys():
            sources = []
            if chunk_id in lexical_ranks:
                sources.append(1.0 / (self.rrf_k + lexical_ranks[chunk_id]))
            if chunk_id in semantic_ranks:
                sources.append(1.0 / (self.rrf_k + semantic_ranks[chunk_id]))
            scored.append((math.fsum(sources), chunk_id))

        def sort_key(item: tuple[float, str]) -> tuple[float, str, str, str]:
            source = self.index.lookup(item[1])
            assert source is not None  # Proven by lexical or semantic lookup.
            return (-item[0], source.relative_path.casefold(), source.relative_path, item[1])

        scored.sort(key=sort_key)
        hits = []
        for score, chunk_id in scored[:top_k]:
            source = self.index.lookup(chunk_id)
            assert source is not None
            hits.append(
                HybridHit(
                    repository_namespace=source.repository_namespace,
                    chunk_id=chunk_id,
                    content_hash=source.content_hash,
                    relative_path=source.relative_path,
                    language=source.language,
                    chunk_kind=source.chunk_kind,
                    symbol_kind=source.symbol_kind,
                    qualified_name=source.qualified_name,
                    parent_qualified_name=source.parent_qualified_name,
                    content=source.content,
                    score=score,
                    rank=len(hits) + 1,
                    lexical_rank=lexical_ranks.get(chunk_id),
                    semantic_rank=semantic_ranks.get(chunk_id),
                )
            )
        return tuple(hits)
