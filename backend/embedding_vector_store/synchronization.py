"""Provider-independent synchronization helpers for semantic indexes."""

from dataclasses import dataclass

from backend.code_chunker.models import CodeChunkInventory

from .documents import EMBEDDING_DOCUMENT_VERSION, build_embedding_document
from .exceptions import EmbeddingVectorStoreConfigurationError
from .models import (
    EmbeddingModelIdentity,
    IndexSyncResult,
    IndexSyncStatus,
    VectorRecord,
)
from .providers.base import EmbeddingProvider
from .retry import RetryPolicy, run_with_embedding_retries
from .stores.base import StoredRecord, VectorStore
from .validation import (
    ChunkInput,
    validate_and_flatten_inventory,
    validate_embedding_batch,
)


_INVALID_DEPENDENCY_MESSAGE = "Semantic indexer dependency configuration is invalid."
_UNSUPPORTED_INDEX_STATE_MESSAGE = (
    "Semantic indexer requires a compatible active repository index."
)


@dataclass(frozen=True, slots=True)
class SyncDiff:
    """Classification of current chunks against one stored manifest."""

    unchanged: tuple[StoredRecord, ...]
    new: tuple[ChunkInput, ...]
    updated: tuple[ChunkInput, ...]
    deleted: tuple[StoredRecord, ...]
    compatible: bool


class SemanticIndexer:
    """Synchronize validated chunk inventories into complete index candidates."""

    def __init__(
        self,
        provider: EmbeddingProvider,
        store: VectorStore,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        if not isinstance(provider, EmbeddingProvider) or not isinstance(
            store, VectorStore
        ):
            raise EmbeddingVectorStoreConfigurationError(_INVALID_DEPENDENCY_MESSAGE)
        identity = provider.identity
        capacity = provider.max_batch_size
        if (
            not isinstance(identity, EmbeddingModelIdentity)
            or type(capacity) is not int
            or capacity <= 0
            or (retry_policy is not None and not isinstance(retry_policy, RetryPolicy))
        ):
            raise EmbeddingVectorStoreConfigurationError(_INVALID_DEPENDENCY_MESSAGE)
        self._provider = provider
        self._store = store
        self._retry_policy = retry_policy if retry_policy is not None else RetryPolicy()
        self._identity = identity

    def synchronize(self, inventory: CodeChunkInventory) -> IndexSyncResult:
        """Publish a complete replacement for one changed compatible active index."""
        repository_namespace, current = validate_and_flatten_inventory(inventory)
        active_state = self._store.inspect_active(repository_namespace)
        snapshot = active_state.snapshot
        if snapshot is None:
            diff = _classify_chunks(current, (), False, False)
        else:
            manifest = self._store.read_manifest(snapshot)
            diff = _classify_chunks(
                current,
                manifest,
                snapshot.identity == self._identity,
                snapshot.document_version == EMBEDDING_DOCUMENT_VERSION,
            )
        if snapshot is None or not diff.compatible:
            raise EmbeddingVectorStoreConfigurationError(
                _UNSUPPORTED_INDEX_STATE_MESSAGE
            )
        if not diff.new and not diff.updated and not diff.deleted:
            return IndexSyncResult(
                repository_namespace=repository_namespace,
                status=IndexSyncStatus.UNCHANGED,
                total_chunks=len(current),
                reused_chunks=len(diff.unchanged),
                embedded_chunks=0,
                inserted_chunks=0,
                updated_chunks=0,
                deleted_chunks=0,
                embedding_identity=self._identity,
                document_version=EMBEDDING_DOCUMENT_VERSION,
            )

        candidate = self._store.begin_candidate(
            repository_namespace,
            self._identity,
            EMBEDDING_DOCUMENT_VERSION,
            len(current),
        )
        try:
            self._store.add_reused(candidate, diff.unchanged)
            embedded = _embed_chunk_inputs(
                diff.new + diff.updated,
                self._provider,
                self._retry_policy,
            )
            self._store.add_embedded(candidate, embedded)
            self._store.validate_candidate(candidate)
            self._store.publish(candidate)
        except Exception:
            try:
                self._store.abort(candidate)
            except Exception:
                pass
            raise

        return IndexSyncResult(
            repository_namespace=repository_namespace,
            status=IndexSyncStatus.SUCCESS,
            total_chunks=len(current),
            reused_chunks=len(diff.unchanged),
            embedded_chunks=len(embedded),
            inserted_chunks=len(diff.new),
            updated_chunks=len(diff.updated),
            deleted_chunks=len(diff.deleted),
            embedding_identity=self._identity,
            document_version=EMBEDDING_DOCUMENT_VERSION,
        )


def _embed_chunk_inputs(
    inputs: tuple[ChunkInput, ...],
    provider: EmbeddingProvider,
    retry_policy: RetryPolicy,
) -> tuple[VectorRecord, ...]:
    """Embed chunk inputs in provider-bounded batches and preserve their order."""
    capacity = provider.max_batch_size
    if type(capacity) is not int or capacity <= 0:
        raise EmbeddingVectorStoreConfigurationError(
            "Embedding provider batch capacity is invalid."
        )

    records: list[VectorRecord] = []
    for start in range(0, len(inputs), capacity):
        batch_inputs = inputs[start : start + capacity]
        documents = tuple(
            build_embedding_document(
                item.chunk, item.relative_path, item.language
            )
            for item in batch_inputs
        )
        vectors = run_with_embedding_retries(
            lambda: provider.embed_documents(documents), retry_policy
        )
        validate_embedding_batch(documents, vectors, provider.identity)
        records.extend(
            VectorRecord(
                chunk_id=item.chunk.chunk_id,
                content_hash=item.chunk.content_hash,
                relative_path=item.relative_path,
                language=item.language.value,
                chunk_kind=item.chunk.kind.value,
                symbol_kind=(
                    item.chunk.symbol_kind.value if item.chunk.symbol_kind else None
                ),
                qualified_name=item.chunk.qualified_name,
                parent_qualified_name=item.chunk.parent_qualified_name,
                content=item.chunk.content,
                embedding=vector,
            )
            for item, vector in zip(batch_inputs, vectors, strict=True)
        )
    return tuple(records)


def _classify_chunks(
    current: tuple[ChunkInput, ...],
    stored: tuple[StoredRecord, ...],
    identity_compatible: bool,
    document_compatible: bool,
) -> SyncDiff:
    """Classify chunk changes with one dictionary lookup per input side."""
    current_by_id = {item.chunk.chunk_id: item for item in current}
    stored_by_id = {record.chunk_id: record for record in stored}
    compatible = bool(identity_compatible and document_compatible)

    unchanged: list[StoredRecord] = []
    new: list[ChunkInput] = []
    updated: list[ChunkInput] = []
    deleted: list[StoredRecord] = []

    for item in current:
        stored_record = stored_by_id.get(item.chunk.chunk_id)
        if stored_record is None:
            new.append(item)
        elif compatible and item.chunk.content_hash == stored_record.content_hash:
            unchanged.append(stored_record)
        else:
            updated.append(item)

    for record in stored:
        if record.chunk_id not in current_by_id:
            deleted.append(record)

    return SyncDiff(
        tuple(unchanged),
        tuple(new),
        tuple(updated),
        tuple(deleted),
        compatible,
    )
