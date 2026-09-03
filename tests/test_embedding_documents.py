"""Byte-exact tests for deterministic embedding document rendering."""
import json

from backend.code_chunker.models import ChunkKind, CodeChunk
from backend.code_parser.models import ParsedLanguage, SourceLocation, SymbolKind
from backend.embedding_vector_store.documents import EMBEDDING_DOCUMENT_VERSION, build_embedding_document


def _chunk(*, kind, content, qualified_name, parent_qualified_name, symbol_kind):
    return CodeChunk(
        chunk_id="a" * 64, kind=kind,
        location=SourceLocation(0, len(content.encode("utf-8")), 1, 0, 1, 0),
        content=content, content_hash="b" * 64, symbol_kind=symbol_kind,
        symbol_name="private-name", qualified_name=qualified_name,
        parent_qualified_name=parent_qualified_name, parameters=(),
        return_type="private-return-type", modifiers=("private-modifier",),
        base_types=("private-base",), implemented_types=("private-interface",),
        fragment_index=2, fragment_count=3,
    )


def test_build_embedding_document_is_byte_exact_for_languages_kinds_and_source_bytes():
    cases = (
        (ParsedLanguage.PYTHON, ChunkKind.SYMBOL, "café\n\t'quote' " + "\\" + "\n", "pkg.fn", "pkg", SymbolKind.FUNCTION),
        (ParsedLanguage.JAVA, ChunkKind.SYMBOL, "class Ω {\r\n\treturn \\\"x\\\";\r\n}", "A.m", "A", SymbolKind.METHOD),
        (ParsedLanguage.JAVASCRIPT, ChunkKind.CONTEXT, "// 日本語\r\nconst x = `q`;", None, None, None),
        (ParsedLanguage.TYPESCRIPT, ChunkKind.FRAGMENT, "type T = \\\"x\\\";\n\\\\", None, None, None),
        (ParsedLanguage.PYTHON, ChunkKind.CONTEXT, "# tabs\tand\\slashes\n", None, None, None),
        (ParsedLanguage.JAVA, ChunkKind.FRAGMENT, "/* quotes: \\\" and ' */\r\n", None, None, None),
    )
    forbidden = {"chunk_id", "content_hash", "symbol_name", "parameters", "return_type", "modifiers", "base_types", "implemented_types", "fragment_index", "fragment_count", "private-name", "private-return-type"}
    for language, kind, content, qualified, parent, symbol in cases:
        chunk = _chunk(kind=kind, content=content, qualified_name=qualified, parent_qualified_name=parent, symbol_kind=symbol)
        expected_metadata = {
            "chunk_kind": kind.value, "language": language.value,
            "parent_qualified_name": parent, "qualified_name": qualified,
            "relative_path": "src/λ.py", "symbol_kind": symbol.value if symbol else None,
        }
        canonical_metadata_json = json.dumps(expected_metadata, ensure_ascii=False, separators=(",", ":"))
        expected = "tracerag-embedding-document-v1\n" + canonical_metadata_json + "\n---TRACERAG-SOURCE---\n" + chunk.content
        document = build_embedding_document(chunk, "src/λ.py", language)
        assert EMBEDDING_DOCUMENT_VERSION == "tracerag-embedding-document-v1"
        assert document.text == expected
        assert document.text.encode("utf-8").endswith(chunk.content.encode("utf-8"))
        prefix = document.text.split("\n---TRACERAG-SOURCE---\n", 1)[0]
        assert not any(value in prefix for value in forbidden)
