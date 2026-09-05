"""Provider-independent synchronization helpers for semantic indexes."""

from dataclasses import dataclass

from .stores.base import StoredRecord
from .validation import ChunkInput


@dataclass(frozen=True, slots=True)
class SyncDiff:
    """Classification of current chunks against one stored manifest."""

    unchanged: tuple[StoredRecord, ...]
    new: tuple[ChunkInput, ...]
    updated: tuple[ChunkInput, ...]
    deleted: tuple[StoredRecord, ...]
    compatible: bool


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
