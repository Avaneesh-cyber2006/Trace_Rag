"""Deterministic V1 embedding-document renderer."""

import json

from backend.code_chunker.models import CodeChunk
from backend.code_parser.models import ParsedLanguage

from .models import EmbeddingDocument


EMBEDDING_DOCUMENT_VERSION = "tracerag-embedding-document-v1"


def build_embedding_document(
    chunk: CodeChunk, relative_path: str, language: ParsedLanguage
) -> EmbeddingDocument:
    metadata = {
        "chunk_kind": chunk.kind.value,
        "language": language.value,
        "parent_qualified_name": chunk.parent_qualified_name,
        "qualified_name": chunk.qualified_name,
        "relative_path": relative_path,
        "symbol_kind": chunk.symbol_kind.value if chunk.symbol_kind else None,
    }
    canonical_metadata_json = json.dumps(
        metadata, ensure_ascii=False, separators=(",", ":")
    )
    text = (
        EMBEDDING_DOCUMENT_VERSION
        + "\n"
        + canonical_metadata_json
        + "\n---TRACERAG-SOURCE---\n"
        + chunk.content
    )
    return EmbeddingDocument(chunk.chunk_id, text)
