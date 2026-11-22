"""Hybrid retrieval against one pinned, validated Module 5 generation.

The active-generation lease covers reading the authoritative stored manifest,
constructing the lexical snapshot, embedding the query, searching Chroma and
fusing ranks.  Source bytes are never read from the working tree.  This first
orchestrator intentionally rebuilds its bounded index for each request rather
than caching a mutable generation-dependent object without an invalidation key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256

from backend.embedding_vector_store.documents import EMBEDDING_DOCUMENT_VERSION
from backend.embedding_vector_store.exceptions import (
    EmbeddingSpaceMismatch,
    VectorStoreCorruptionError,
)
from backend.embedding_vector_store.providers.base import EmbeddingProvider
from backend.embedding_vector_store.retry import RetryPolicy, run_with_embedding_retries
from backend.embedding_vector_store.search import _resolve_snapshot
from backend.embedding_vector_store.stores.base import StoredRecord, VectorStore
from backend.embedding_vector_store.validation import (
    validate_and_normalize_search_results,
    validate_embedding_vector,
    validate_search_request,
)

from .errors import InvalidHybridEvidence, InvalidLexicalIndex, InvalidRetrievalQuery
from .hybrid import HybridHit, HybridRanker, SemanticCandidate
from .lexical import LexicalDocument, LexicalIndex
from .graph import GraphContext, GraphExpander
from .provenance import GraphRevision
from .relevance import RelevanceGate, RelevancePolicy
from .history import HistoricalContext, HistoryAnnotator
from backend.git_history.models import GitHistoryInventory
from backend.runtime_evidence.models import RuntimeEvidenceInventory
from .runtime import RuntimeAnnotator, RuntimeContext


_MAX_QUERY_CHARS = 4_096
_MAX_TOP_K = 100
_MAX_DOCUMENTS = 8_192  # Must not exceed LexicalIndex's bounded corpus limit.
_INVALID_CONFIGURATION = "Hybrid retrieval configuration is invalid."
_INVALID_QUERY = "Hybrid retrieval request is invalid or exceeds its limit."
_INVALID_MANIFEST = "Published lexical evidence is invalid or inconsistent."
_STALE_SEMANTIC = "Hybrid retrieval evidence is invalid or inconsistent."
_MISMATCH = "Semantic index embedding space is incompatible."


@dataclass(frozen=True, slots=True)
class GraphRetrievalResult:
    """Ranked primary hits plus source-verified one-hop context."""

    hits: tuple[HybridHit, ...] = field(repr=False)
    context: tuple[GraphContext, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class HistoricalRetrievalResult:
    """Pinned ranked hits plus explicitly unverified Git history clues."""

    hits: tuple[HybridHit, ...] = field(repr=False)
    graph_context: tuple[GraphContext, ...] = field(repr=False)
    history: HistoricalContext = field(repr=False)


@dataclass(frozen=True, slots=True)
class RuntimeRetrievalResult:
    """Pinned verified hits with independent, unverified runtime/Git clues."""

    hits: tuple[HybridHit, ...] = field(repr=False)
    graph_context: tuple[GraphContext, ...] = field(repr=False)
    runtime: RuntimeContext = field(repr=False)
    history: HistoricalContext | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class HybridRetriever:
    """Run both retrieval paths on exactly the same active Module 5 snapshot.

    The provider and store satisfy Module 5's existing protocols.  The
    semantic candidate pool is always bounded to 100; fusion uses identities,
    not incomparable vector and BM25 scores.  Empty snapshots do not embed.
    """

    provider: EmbeddingProvider = field(repr=False)
    store: VectorStore = field(repr=False)
    rrf_k: int = 60
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy, repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.provider, EmbeddingProvider)
            or not isinstance(self.store, VectorStore)
            or type(self.rrf_k) is not int
            or not 1 <= self.rrf_k <= 10_000
            or not isinstance(self.retry_policy, RetryPolicy)
        ):
            raise InvalidRetrievalQuery(_INVALID_CONFIGURATION)

    def search(
        self,
        repository_namespace: str,
        query_text: str,
        top_k: int,
    ) -> tuple[HybridHit, ...]:
        """Fetch and rank authoritative evidence within one publication lease."""
        hits, _ = self._search_pinned(repository_namespace, query_text, top_k, None, 0)
        return hits

    def search_relevant(
        self,
        repository_namespace: str,
        query_text: str,
        top_k: int,
        policy: RelevancePolicy,
    ) -> tuple[HybridHit, ...]:
        """Opt-in relevance gate over a 100-hit pinned RRF candidate pool."""
        if type(policy) is not RelevancePolicy:
            raise InvalidRetrievalQuery(_INVALID_QUERY)
        hits, _ = self._search_pinned(
            repository_namespace, query_text, top_k, None, 0, policy
        )
        return hits

    def search_with_graph(
        self,
        repository_namespace: str,
        query_text: str,
        top_k: int,
        graph_revision: GraphRevision,
        *,
        graph_top_k: int = 20,
    ) -> GraphRetrievalResult:
        """Expand only a graph bound to the complete pinned source manifest.

        Graphs must be built via ``build_graph_revision`` at indexing time,
        using the same original file digests as Module 4 chunks. The check
        runs before embedding and expansion occurs before lease release.
        """
        if type(graph_revision) is not GraphRevision or type(graph_top_k) is not int or not 1 <= graph_top_k <= 100:
            raise InvalidRetrievalQuery(_INVALID_QUERY)
        hits, context = self._search_pinned(
            repository_namespace, query_text, top_k, graph_revision, graph_top_k
        )
        return GraphRetrievalResult(hits, context)

    def search_with_graph_relevant(
        self,
        repository_namespace: str,
        query_text: str,
        top_k: int,
        graph_revision: GraphRevision,
        policy: RelevancePolicy,
        *,
        graph_top_k: int = 20,
    ) -> GraphRetrievalResult:
        """Expand graph only from gated hits within the same source lease."""
        if (
            type(policy) is not RelevancePolicy
            or type(graph_revision) is not GraphRevision
            or type(graph_top_k) is not int
            or not 1 <= graph_top_k <= 100
        ):
            raise InvalidRetrievalQuery(_INVALID_QUERY)
        hits, context = self._search_pinned(
            repository_namespace, query_text, top_k,
            graph_revision, graph_top_k, policy,
        )
        return GraphRetrievalResult(hits, context)

    def search_with_history(
        self,
        repository_namespace: str,
        query_text: str,
        top_k: int,
        git_history: GitHistoryInventory,
        *,
        history_top_k: int = 5,
        relevance_policy: RelevancePolicy | None = None,
        graph_revision: GraphRevision | None = None,
        graph_top_k: int = 20,
    ) -> HistoricalRetrievalResult:
        """Attach bounded path-level Git clues to pinned, verified hits.

        Module 7 history has no authenticated revision relation to Module 5;
        this method does *not* use Git to rank, filter, or expand source hits.
        Graph expansion, if requested, retains its existing strict binding.
        """
        if (
            (relevance_policy is not None and type(relevance_policy) is not RelevancePolicy)
            or (graph_revision is not None and type(graph_revision) is not GraphRevision)
            or type(graph_top_k) is not int or not 1 <= graph_top_k <= 100
        ):
            raise InvalidRetrievalQuery(_INVALID_QUERY)
        # Reject malformed requests before traversing a potentially large
        # user-supplied history inventory.  Source validation is repeated
        # inside the pinned path to keep all other entry points unchanged.
        validate_search_request(
            repository_namespace, query_text, top_k, _MAX_QUERY_CHARS, _MAX_TOP_K
        )
        if "\x00" in query_text or "\x00" in repository_namespace:
            raise InvalidRetrievalQuery(_INVALID_QUERY)
        annotator = HistoryAnnotator(repository_namespace, git_history, history_top_k)
        hits, context = self._search_pinned(
            repository_namespace, query_text, top_k, graph_revision,
            graph_top_k if graph_revision is not None else 0, relevance_policy,
        )
        # Only frozen, already source-validated hit records are read after
        # lease release. No additional index or filesystem read occurs.
        return HistoricalRetrievalResult(hits, context, annotator.annotate(hits))

    def search_with_runtime(
        self,
        repository_namespace: str,
        query_text: str,
        top_k: int,
        runtime_evidence: RuntimeEvidenceInventory,
        *,
        runtime_top_k: int = 5,
        relevance_policy: RelevancePolicy | None = None,
        graph_revision: GraphRevision | None = None,
        graph_top_k: int = 20,
        git_history: GitHistoryInventory | None = None,
        history_top_k: int = 5,
    ) -> RuntimeRetrievalResult:
        """Attach unverified runtime clues without ranking or trust promotion.

        Module 8's observations and graph association have no authenticated
        relation to the currently pinned source revision.  Optional Module 7
        history is similarly separate.  Neither can mutate verified hits.
        """
        if (
            (relevance_policy is not None and type(relevance_policy) is not RelevancePolicy)
            or (graph_revision is not None and type(graph_revision) is not GraphRevision)
            or type(graph_top_k) is not int or not 1 <= graph_top_k <= 100
            or type(history_top_k) is not int or not 1 <= history_top_k <= 20
        ):
            raise InvalidRetrievalQuery(_INVALID_QUERY)
        # Bound the request before expensive validation of runtime evidence.
        validate_search_request(
            repository_namespace, query_text, top_k, _MAX_QUERY_CHARS, _MAX_TOP_K
        )
        if "\x00" in query_text or "\x00" in repository_namespace:
            raise InvalidRetrievalQuery(_INVALID_QUERY)
        # Preflight all externally supplied inventories before embedding, and
        # before holding the Module 5 publication lease.
        runtime_annotator = RuntimeAnnotator(
            repository_namespace, runtime_evidence, runtime_top_k
        )
        history_annotator = (
            None if git_history is None
            else HistoryAnnotator(repository_namespace, git_history, history_top_k)
        )
        hits, context = self._search_pinned(
            repository_namespace, query_text, top_k,
            graph_revision, graph_top_k if graph_revision is not None else 0,
            relevance_policy,
        )
        # No filesystem or vector-store accesses after release; clues use
        # immutable, already checked retrieval hits only.
        return RuntimeRetrievalResult(
            hits=hits, graph_context=context,
            runtime=runtime_annotator.annotate(hits),
            history=None if history_annotator is None else history_annotator.annotate(hits),
        )

    def _search_pinned(
        self,
        repository_namespace: str,
        query_text: str,
        top_k: int,
        graph_revision: GraphRevision | None,
        graph_top_k: int,
        relevance_policy: RelevancePolicy | None = None,
    ) -> tuple[tuple[HybridHit, ...], tuple[GraphContext, ...]]:
        # All user-controlled request bounds precede collaborator inspection.
        validate_search_request(
            repository_namespace, query_text, top_k, _MAX_QUERY_CHARS, _MAX_TOP_K
        )
        if "\x00" in query_text or "\x00" in repository_namespace:
            raise InvalidRetrievalQuery(_INVALID_QUERY)

        with self.store.acquire_active(repository_namespace) as state:
            snapshot = _resolve_snapshot(state, repository_namespace)
            if snapshot.identity != self.provider.identity:
                raise EmbeddingSpaceMismatch(_MISMATCH)
            if snapshot.document_version != EMBEDDING_DOCUMENT_VERSION:
                raise VectorStoreCorruptionError(_INVALID_MANIFEST)
            if snapshot.expected_chunk_count > _MAX_DOCUMENTS:
                raise InvalidLexicalIndex(_INVALID_MANIFEST)

            manifest = self.store.read_manifest(snapshot)
            if type(manifest) is not tuple or len(manifest) != snapshot.expected_chunk_count:
                raise VectorStoreCorruptionError(_INVALID_MANIFEST)

            # Validate the complete manifest before embedding or vector search.
            documents: list[LexicalDocument] = []
            for record in manifest:
                if (
                    type(record) is not StoredRecord
                    or record.repository_namespace != repository_namespace
                    or record.embedding_identity != snapshot.identity
                    or record.document_version != snapshot.document_version
                    or record.schema_version != snapshot.schema_version
                    or len(record.content) > 16_384
                ):
                    raise VectorStoreCorruptionError(_INVALID_MANIFEST)
                try:
                    digest = sha256(record.content.encode("utf-8")).hexdigest()
                except UnicodeEncodeError:
                    raise VectorStoreCorruptionError(_INVALID_MANIFEST) from None
                if digest != record.content_hash:
                    raise VectorStoreCorruptionError(_INVALID_MANIFEST)
                try:
                    documents.append(
                        LexicalDocument(
                            repository_namespace=record.repository_namespace,
                            chunk_id=record.chunk_id,
                            content_hash=record.content_hash,
                            relative_path=record.relative_path,
                            language=record.language,
                            chunk_kind=record.chunk_kind,
                            symbol_kind=record.symbol_kind,
                            qualified_name=record.qualified_name,
                            parent_qualified_name=record.parent_qualified_name,
                            content=record.content,
                        )
                    )
                except InvalidLexicalIndex:
                    raise VectorStoreCorruptionError(_INVALID_MANIFEST) from None

            # LexicalIndex checks duplicate IDs, individual/corpus size caps,
            # and all other index invariants before any provider call.
            index = LexicalIndex(repository_namespace, tuple(documents))
            ranker = HybridRanker(index, self.rrf_k)
            expander = None
            if graph_revision is not None:
                # A mismatch must fail before any query embedding.
                graph_revision.validate(index, snapshot.source_revision)
                expander = GraphExpander(index, graph_revision.graph)
            if snapshot.expected_chunk_count == 0:
                hits = ranker.search(query_text, (), top_k)
                return hits, (() if expander is None else expander.expand(hits, graph_top_k))

            query_vector = run_with_embedding_retries(
                lambda: self.provider.embed_query(query_text), self.retry_policy
            )
            validate_embedding_vector(query_vector, snapshot.identity)
            store_results = self.store.search(snapshot, query_vector, _MAX_TOP_K)
            semantic_results = validate_and_normalize_search_results(
                store_results, repository_namespace, _MAX_TOP_K
            )

            semantic_candidates: list[SemanticCandidate] = []
            for result in semantic_results:
                source = index.lookup(result.chunk_id)
                if source is None or any(
                    getattr(source, attr) != getattr(result, attr)
                    for attr in (
                        "content_hash", "relative_path", "language", "chunk_kind",
                        "symbol_kind", "qualified_name", "parent_qualified_name", "content",
                    )
                ):
                    raise InvalidHybridEvidence(_STALE_SEMANTIC)
                semantic_candidates.append(
                    SemanticCandidate(
                        repository_namespace=repository_namespace,
                        chunk_id=result.chunk_id,
                        content_hash=result.content_hash,
                    )
                )

            # Filtering must happen *before* final top_k selection: a weak
            # top hit must not hide a stronger match lower in the candidate
            # pool. Legacy retrieval keeps its exact original ranking path.
            pool_size = _MAX_TOP_K if relevance_policy is not None else top_k
            hits = ranker.search(query_text, tuple(semantic_candidates), pool_size)
            if relevance_policy is not None:
                hits = RelevanceGate(index, relevance_policy).select(query_text, hits, top_k)
            context = () if expander is None else expander.expand(hits, graph_top_k)
            return hits, context
