"""Canonical structural identities and exact-content hashes."""

from __future__ import annotations

from hashlib import sha256
import json

from backend.code_parser.models import SymbolInfo

from .models import ChunkKind


def make_chunk_id(
    repository_namespace: str,
    relative_path: str,
    kind: ChunkKind,
    owner: SymbolInfo | None,
    start: int,
    end: int,
    fragment_index: int,
) -> str:
    """Hash the versioned repository-namespaced structural provenance."""
    material = [
        "tracerag-code-chunk-v1",
        repository_namespace,
        relative_path,
        kind.value,
        "" if owner is None else owner.kind.value,
        "" if owner is None else owner.qualified_name,
        "" if owner is None or owner.parent_qualified_name is None else owner.parent_qualified_name,
        start,
        end,
        fragment_index,
    ]
    canonical = json.dumps(
        material, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def make_content_hash(content_bytes: bytes) -> str:
    """Hash one exact verified source slice."""
    return sha256(content_bytes).hexdigest()
