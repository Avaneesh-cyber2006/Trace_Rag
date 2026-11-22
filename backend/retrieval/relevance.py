"""Opt-in, deterministic relevance gates over authoritative pinned hybrid hits.

This is an explicitly lexical evidence policy, not an LLM, cross encoder,
calibrated relevance probability, or proof that a semantic-only hit is bad.
HybridRetriever supplies a 100-hit RRF candidate pool from one pinned index.
The gate never reads files, database state, or calls embedding services.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math

from .errors import InvalidHybridEvidence, InvalidRetrievalQuery
from .hybrid import HybridHit
from .lexical import LexicalIndex, _tokens

_QUERY_ERROR = "Relevance policy or request is invalid or exceeds its limit."
_EVIDENCE_ERROR = "Relevance evidence is invalid or inconsistent."
_MAX_CANDIDATES = 100
_MAX_QUERY_CHARS = 4_096
_MAX_MIN_MATCHES = 64
_MAX_SEARCHABLE_CHARS = 32_768


@dataclass(frozen=True, slots=True)
class RelevancePolicy:
    """Opt-in lexical term gate and stable query-coverage reranking.

    min_query_matches=0 disables the term gate but can still request the
    two-channel filter or coverage reranking. Matching is over *distinct*
    normalized query tokens in the authoritative chunk content, source path
    and symbol metadata. A semantic paraphrase may match zero tokens.
    """

    min_query_matches: int = 1
    require_dual_channel: bool = False
    rerank_by_query_coverage: bool = True

    def __post_init__(self) -> None:
        if (
            type(self.min_query_matches) is not int
            or not 0 <= self.min_query_matches <= _MAX_MIN_MATCHES
            or type(self.require_dual_channel) is not bool
            or type(self.rerank_by_query_coverage) is not bool
        ):
            raise InvalidRetrievalQuery(_QUERY_ERROR)


class RelevanceGate:
    """Filter/reorder up to 100 already-validated hybrid candidates.

    The gate validates every candidate before filtering, including candidates
    that will be excluded, and emits newly contiguous one-based ranks.
    ``score`` remains the original RRF score; it is not recalibrated when
    coverage reranking changes the order.
    """

    __slots__ = ("_index", "_policy")

    def __init__(self, index: LexicalIndex, policy: RelevancePolicy) -> None:
        if type(index) is not LexicalIndex or type(policy) is not RelevancePolicy:
            raise InvalidRetrievalQuery(_QUERY_ERROR)
        self._index = index
        self._policy = policy

    def select(
        self, query_text: str, candidates: tuple[HybridHit, ...], top_k: int
    ) -> tuple[HybridHit, ...]:
        if (
            type(query_text) is not str
            or not query_text.strip()
            or "\x00" in query_text
            or len(query_text) > _MAX_QUERY_CHARS
            or type(top_k) is not int
            or not 1 <= top_k <= _MAX_CANDIDATES
            or type(candidates) is not tuple
            or len(candidates) > _MAX_CANDIDATES
        ):
            raise InvalidRetrievalQuery(_QUERY_ERROR)
        query_terms = frozenset(_tokens(query_text))
        matched: list[tuple[int, HybridHit]] = []
        seen_ids: set[str] = set()

        for original_rank, hit in enumerate(candidates, 1):
            if (
                type(hit) is not HybridHit
                or hit.rank != original_rank
                or type(hit.rank) is not int
                or type(hit.score) is not float
                or not math.isfinite(hit.score)
                or hit.score <= 0.0
                or (hit.lexical_rank is None and hit.semantic_rank is None)
                or hit.chunk_id in seen_ids
            ):
                raise InvalidHybridEvidence(_EVIDENCE_ERROR)
            for source_rank in (hit.lexical_rank, hit.semantic_rank):
                if source_rank is not None and (
                    type(source_rank) is not int
                    or not 1 <= source_rank <= _MAX_CANDIDATES
                ):
                    raise InvalidHybridEvidence(_EVIDENCE_ERROR)
            try:
                source = self._index.lookup(hit.chunk_id)
            except InvalidRetrievalQuery:
                raise InvalidHybridEvidence(_EVIDENCE_ERROR) from None
            if source is None or any(
                getattr(hit, name) != getattr(source, name)
                for name in (
                    "repository_namespace", "content_hash", "relative_path",
                    "language", "chunk_kind", "symbol_kind", "qualified_name",
                    "parent_qualified_name", "content",
                )
            ):
                raise InvalidHybridEvidence(_EVIDENCE_ERROR)
            seen_ids.add(hit.chunk_id)

            # Source names can be informative for code searches. Include only
            # proven, immutable Module 5 source metadata, never graph labels.
            source_parts = (
                source.content, source.relative_path, source.qualified_name or "",
                source.parent_qualified_name or "", source.symbol_kind or "",
            )
            if sum(map(len, source_parts)) + 4 > _MAX_SEARCHABLE_CHARS:
                raise InvalidHybridEvidence(_EVIDENCE_ERROR)
            searchable = " ".join(source_parts)
            coverage = len(query_terms.intersection(_tokens(searchable)))
            if (
                coverage >= self._policy.min_query_matches
                and (not self._policy.require_dual_channel or (
                    hit.lexical_rank is not None and hit.semantic_rank is not None
                ))
            ):
                matched.append((coverage, hit))

        if self._policy.rerank_by_query_coverage:
            # Preserve original RRF order for equal coverage: no mixing of
            # lexical BM25 magnitudes or vector similarity scores.
            matched.sort(key=lambda item: (-item[0], item[1].rank))

        return tuple(
            replace(hit, rank=new_rank)
            for new_rank, (_, hit) in enumerate(matched[:top_k], 1)
        )
