"""Provider-independent synchronization helpers for semantic indexes."""

from dataclasses import dataclass

from .documents import build_embedding_document
from .exceptions import EmbeddingVectorStoreConfigurationError
from .models import VectorRecord
from .providers.base import EmbeddingProvider
from .retry import RetryPolicy, run_with_embedding_retries
from .stores.base import StoredRecord
from .validation import ChunkInput, validate_embedding_batch


@dataclass(frozen=True, slots=True)
class SyncDiff:
    """Classification of current chunks against one stored manifest."""

    unchanged: tuple[StoredRecord, ...]
    new: tuple[ChunkInput, ...]
    updated: tuple[ChunkInput, ...]
    deleted: tuple[StoredRecord, ...]
    compatible: bool


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
