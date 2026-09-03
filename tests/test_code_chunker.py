from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from enum import Enum
from hashlib import sha256
from pathlib import Path

import pytest
import backend.code_chunker as code_chunker_public

from backend.code_chunker.exceptions import (
    ChunkerConfigurationError,
    CodeChunkerError,
    InvalidChunkInventory,
    RepositoryChunkError,
)
from backend.code_chunker.models import (
    ChunkFileStatus,
    ChunkIssue,
    ChunkIssueKind,
    ChunkKind,
    ChunkedFile,
    ChunkerConfig,
    CodeChunk,
    CodeChunkInventory,
)
from backend.code_parser.models import (
    CodeParseInventory,
    ImportInfo,
    ParameterInfo,
    ParsedFile,
    ParseStatus,
    ParsedLanguage,
    SkippedParseFile,
    ParseSkipReason,
    SourceLocation,
    SymbolKind,
)
from backend.file_scanner.models import (
    FileCategory,
    FileInventory,
    IgnoredFile,
    ScannedFile,
    SkippedDirectory,
)
from backend.code_chunker.validation import ValidatedInputs, validate_inputs
from backend.code_chunker.chunker import CodeChunker
from backend.code_chunker.locations import (
    build_line_starts,
    location_from_offsets,
    validate_parsed_locations,
)
from backend.code_chunker.intervals import (
    ChunkCandidate,
    InvalidSymbolIntervals,
    build_symbol_intervals,
    select_candidates,
)
from backend.code_chunker.identity import make_chunk_id, make_content_hash
from backend.code_chunker.fragmenter import fragment_candidate
import json
from backend.code_parser import CodeParser
from backend.code_parser.models import ParseIssueKind
from backend.code_parser.models import CallKind, CallSite, ParseIssue, SymbolInfo
from backend.code_parser.reader import SourceReadError
from backend.file_scanner import FileScanner


_NAMESPACE = "tracerag-repository-v1:github:example/repo"


def _pipeline(tmp_path: Path, data: bytes, *, relative_path: str = "app.py"):
    source_path = tmp_path / relative_path
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(data)
    scanned = FileScanner().scan(tmp_path, repository_namespace=_NAMESPACE)
    parsed = CodeParser().parse_inventory(scanned)
    return scanned, parsed


def test_same_size_source_mutation_fails_with_source_changed(tmp_path: Path):
    scanner, parser = _pipeline(tmp_path, b"answer = 1\n")
    (tmp_path / "app.py").write_bytes(b"answer = 2\n")

    result = CodeChunker().chunk_inventory(scanner, parser)

    assert result.files[0].status is ChunkFileStatus.FAILED
    assert result.files[0].chunks == ()
    assert result.files[0].issues == (
        ChunkIssue(ChunkIssueKind.SOURCE_CHANGED, "Source changed after parsing.", None),
    )


def test_verified_bom_original_bytes_match_parser_digest(tmp_path: Path):
    data = b"\xef\xbb\xbfanswer = 1\r\n"
    scanner, parser = _pipeline(tmp_path, data)

    result = CodeChunker().chunk_inventory(scanner, parser)

    assert parser.files[0].source_sha256 == sha256(data).hexdigest()
    assert result.files[0].status is ChunkFileStatus.SUCCESS
    assert result.files[0].source_sha256 == sha256(data).hexdigest()


def test_failed_parse_is_not_read_and_emits_parse_unavailable(tmp_path: Path, monkeypatch):
    scanner, parser = _pipeline(tmp_path, b"answer = 1\n")
    failed = replace(parser.files[0], status=ParseStatus.FAILED, source_sha256=None)
    parser = replace(parser, success_files=0, failed_files=1, files=(failed,))

    def forbidden_read(*args, **kwargs):
        raise AssertionError("FAILED parse must not be read")

    monkeypatch.setattr("backend.code_chunker.chunker.SafeSourceReader.read", forbidden_read)
    result = CodeChunker().chunk_inventory(scanner, parser)

    assert result.files[0].status is ChunkFileStatus.FAILED
    assert result.files[0].chunks == ()
    assert result.files[0].issues[0].kind is ChunkIssueKind.PARSE_UNAVAILABLE


@pytest.mark.parametrize(
    ("parse_kind", "chunk_kind"),
    (
        (ParseIssueKind.READ_ERROR, ChunkIssueKind.READ_ERROR),
        (ParseIssueKind.FILE_CHANGED, ChunkIssueKind.SOURCE_CHANGED),
        (ParseIssueKind.PATH_INVALID, ChunkIssueKind.PATH_INVALID),
        (ParseIssueKind.LINK_UNSAFE, ChunkIssueKind.LINK_UNSAFE),
        (ParseIssueKind.DECODING_ERROR, ChunkIssueKind.DECODING_ERROR),
    ),
)
def test_safe_reader_failures_map_to_fixed_chunk_issues(
    tmp_path: Path, monkeypatch, parse_kind: ParseIssueKind, chunk_kind: ChunkIssueKind
):
    scanner, parser = _pipeline(tmp_path, b"answer = 1\n")

    def fail_read(*args, **kwargs):
        raise SourceReadError(parse_kind, "repository-controlled details")

    monkeypatch.setattr("backend.code_chunker.chunker.SafeSourceReader.read", fail_read)
    result = CodeChunker().chunk_inventory(scanner, parser)

    file = result.files[0]
    assert file.status is ChunkFileStatus.FAILED
    assert file.chunks == ()
    assert file.issues[0].kind is chunk_kind
    assert "repository-controlled" not in file.issues[0].message


def test_unavailable_repository_root_is_fatal():
    scanner, parser = _inventories(root="Z:/definitely/missing/tracerag")

    with pytest.raises(RepositoryChunkError):
        CodeChunker().chunk_inventory(scanner, parser)


@pytest.mark.parametrize(
    ("source", "start", "end", "expected"),
    (
        (b"abc\nxyz", 0, 3, SourceLocation(0, 3, 1, 0, 1, 3)),
        ("नमस्ते\nX".encode(), 0, 18, SourceLocation(0, 18, 1, 0, 1, 18)),
        (b"\xef\xbb\xbfx\n", 3, 4, SourceLocation(3, 4, 1, 3, 1, 4)),
        (b"a\r\nb", 3, 4, SourceLocation(3, 4, 2, 0, 2, 1)),
        (b"a\n", 2, 2, SourceLocation(2, 2, 2, 0, 2, 0)),
    ),
)
def test_location_reconstruction_uses_original_utf8_byte_coordinates(
    source, start, end, expected
):
    starts = build_line_starts(source)
    assert location_from_offsets(source, starts, start, end) == expected


def test_line_starts_treat_crlf_as_one_terminator_and_include_trailing_line():
    assert build_line_starts(b"a\r\nb\nc\r\n") == (0, 3, 5, 8)


@pytest.mark.parametrize(
    "location",
    (
        SourceLocation(-1, 1, 1, 0, 1, 1),
        SourceLocation(2, 1, 1, 2, 1, 1),
        SourceLocation(0, 4, 1, 0, 1, 4),
        SourceLocation(0, 1, 0, 0, 1, 1),
        SourceLocation(0, 1, 1, 1, 1, 1),
        SourceLocation(0, 0, 1, 0, 1, 0),
    ),
)
def test_location_validation_rejects_invalid_symbol_ranges(location):
    symbol = SymbolInfo("x", SymbolKind.FUNCTION, "x", None, location, (), None, (), (), ())
    parsed = replace(_parsed(digest=sha256(b"abc").hexdigest()), symbols=(symbol,))
    assert validate_parsed_locations(parsed, b"abc", build_line_starts(b"abc")) is False


def test_location_validation_checks_import_call_and_issue_locations():
    valid = SourceLocation(0, 1, 1, 0, 1, 1)
    invalid = SourceLocation(0, 1, 1, 1, 1, 1)
    parsed = replace(
        _parsed(digest=sha256(b"x").hexdigest()),
        imports=(ImportInfo("m", (), False, (), valid),),
        calls=(CallSite(None, "x", CallKind.CALL, valid),),
        issues=(ParseIssue(ParseIssueKind.SYNTAX_ERROR, "Syntax error.", invalid),),
    )
    assert validate_parsed_locations(parsed, b"x", build_line_starts(b"x")) is False


def test_invalid_location_fails_only_affected_file(tmp_path: Path):
    scanner, parser = _pipeline(tmp_path, b"x = 1\n")
    bad = SymbolInfo(
        "x", SymbolKind.CONSTANT, "x", None,
        SourceLocation(0, 99, 1, 0, 1, 99), (), None, (), (), ()
    )
    parser = replace(parser, files=(replace(parser.files[0], symbols=(bad,)),))

    result = CodeChunker().chunk_inventory(scanner, parser)

    assert result.files[0].status is ChunkFileStatus.FAILED
    assert result.files[0].chunks == ()
    assert result.files[0].issues[0].kind is ChunkIssueKind.LOCATION_INVALID


def _symbol(source: bytes, name: str, kind: SymbolKind, start: int, end: int,
            parent: str | None = None) -> SymbolInfo:
    return SymbolInfo(
        name, kind, name if parent is None else f"{parent}.{name}", parent,
        location_from_offsets(source, build_line_starts(source), start, end),
        (), None, (), (), (),
    )


def test_interval_forest_is_sorted_and_records_direct_containment():
    source = b"0123456789abcdefghij"
    outer = _symbol(source, "Outer", SymbolKind.CLASS, 0, 20)
    inner = _symbol(source, "inner", SymbolKind.FUNCTION, 5, 15, "Outer")
    leaf = _symbol(source, "leaf", SymbolKind.FUNCTION, 7, 10, "Outer.inner")

    intervals = build_symbol_intervals((leaf, outer, inner), len(source))

    assert tuple(item.symbol for item in intervals) == (outer, inner, leaf)
    assert intervals[0].children == (1,)
    assert intervals[1].children == (2,)
    assert intervals[2].children == ()


def test_primary_selection_emits_callable_constant_and_leaf_type_wholes():
    source = b"0123456789abcdefghij"
    function = _symbol(source, "f", SymbolKind.FUNCTION, 0, 4)
    constant = _symbol(source, "C", SymbolKind.CONSTANT, 4, 8)
    leaf_type = _symbol(source, "T", SymbolKind.INTERFACE, 8, 20)
    parsed = replace(_parsed(digest=sha256(source).hexdigest()), symbols=(leaf_type, constant, function))

    candidates = select_candidates(parsed, source, build_symbol_intervals(parsed.symbols, len(source)))

    assert [(item.kind, item.owner.kind) for item in candidates] == [
        (ChunkKind.SYMBOL, SymbolKind.FUNCTION),
        (ChunkKind.SYMBOL, SymbolKind.CONSTANT),
        (ChunkKind.SYMBOL, SymbolKind.INTERFACE),
    ]


def test_outer_callable_and_type_are_not_emitted_whole_around_selected_descendants():
    source = b"0123456789abcdefghij"
    outer = _symbol(source, "Outer", SymbolKind.CLASS, 0, 20)
    method = _symbol(source, "m", SymbolKind.METHOD, 4, 16, "Outer")
    nested = _symbol(source, "n", SymbolKind.FUNCTION, 7, 12, "Outer.m")
    parsed = replace(_parsed(digest=sha256(source).hexdigest()), symbols=(outer, method, nested))

    candidates = select_candidates(parsed, source, build_symbol_intervals(parsed.symbols, len(source)))

    assert [(item.start_byte, item.end_byte, item.owner.name) for item in candidates if item.kind is ChunkKind.SYMBOL] == [(7, 12, "n")]
    assert all(
        item.kind is not ChunkKind.SYMBOL
        for item in candidates
        if item.owner in (outer, method)
    )
    _assert_non_overlapping(candidates)


def test_interval_builder_rejects_crossing_and_incompatible_equal_ranges():
    source = b"0123456789"
    crossing = (
        _symbol(source, "a", SymbolKind.FUNCTION, 0, 6),
        _symbol(source, "b", SymbolKind.FUNCTION, 4, 9),
    )
    with pytest.raises(InvalidSymbolIntervals):
        build_symbol_intervals(crossing, len(source))

    equal = (
        _symbol(source, "a", SymbolKind.FUNCTION, 0, 6),
        _symbol(source, "b", SymbolKind.METHOD, 0, 6),
    )
    with pytest.raises(InvalidSymbolIntervals):
        build_symbol_intervals(equal, len(source))


def test_interval_builder_collapses_exact_duplicate_symbols_deterministically():
    source = b"0123456789"
    symbol = _symbol(source, "a", SymbolKind.FUNCTION, 0, 6)
    intervals = build_symbol_intervals((symbol, symbol), len(source))
    assert tuple(item.symbol for item in intervals) == (symbol,)


def _assert_non_overlapping(candidates):
    ordered = sorted(candidates, key=lambda item: (item.start_byte, item.end_byte))
    assert all(left.end_byte <= right.start_byte for left, right in zip(ordered, ordered[1:]))


def test_nested_type_residuals_never_overlap_deep_selected_method():
    source = b"abcdefghijklmnopqrstuvwxyz1234"
    outer = _symbol(source, "Outer", SymbolKind.CLASS, 0, 30)
    inner = _symbol(source, "Inner", SymbolKind.CLASS, 5, 25, "Outer")
    method = _symbol(source, "method", SymbolKind.METHOD, 10, 20, "Outer.Inner")
    parsed = replace(_parsed(digest=sha256(source).hexdigest()), symbols=(outer, inner, method))

    candidates = select_candidates(parsed, source, build_symbol_intervals(parsed.symbols, len(source)))

    method_candidates = [item for item in candidates if item.owner == method]
    assert [(item.start_byte, item.end_byte, item.kind) for item in method_candidates] == [
        (10, 20, ChunkKind.SYMBOL)
    ]
    assert all(
        item.end_byte <= 10 or item.start_byte >= 20
        for item in candidates if item.owner in (outer, inner)
    )
    _assert_non_overlapping(candidates)
    covered = b"".join(source[item.start_byte:item.end_byte] for item in candidates)
    assert covered == source


def test_deeper_nested_type_partition_is_exact_and_pairwise_disjoint():
    source = b"abcdefghijklmnopqrstuvwxyz1234567890"
    outer = _symbol(source, "Outer", SymbolKind.CLASS, 0, 36)
    inner = _symbol(source, "Inner", SymbolKind.CLASS, 4, 32, "Outer")
    nested = _symbol(source, "Nested", SymbolKind.CLASS, 8, 28, "Outer.Inner")
    method = _symbol(source, "method", SymbolKind.METHOD, 12, 24, "Outer.Inner.Nested")
    parsed = replace(_parsed(digest=sha256(source).hexdigest()), symbols=(outer, inner, nested, method))

    candidates = select_candidates(parsed, source, build_symbol_intervals(parsed.symbols, len(source)))

    _assert_non_overlapping(candidates)
    assert sum(item.start_byte == 12 and item.end_byte == 24 for item in candidates) == 1
    assert b"".join(source[item.start_byte:item.end_byte] for item in candidates) == source


def test_success_fallback_covers_all_remaining_non_whitespace_exactly():
    source = b"import x\n\n  def f():\n    pass\nTAIL"
    start = source.index(b"def")
    end = source.index(b"TAIL")
    function = _symbol(source, "f", SymbolKind.FUNCTION, start, end)
    parsed = replace(_parsed(digest=sha256(source).hexdigest()), symbols=(function,))

    candidates = select_candidates(parsed, source, build_symbol_intervals(parsed.symbols, len(source)))

    _assert_non_overlapping(candidates)
    assert any(item.owner is None and item.kind is ChunkKind.CONTEXT for item in candidates)
    covered = [False] * len(source)
    for item in candidates:
        for offset in range(item.start_byte, item.end_byte):
            assert covered[offset] is False
            covered[offset] = True
        assert source[item.start_byte:item.end_byte].decode("utf-8").strip() != ""
    assert all(covered[i] for i, byte in enumerate(source) if chr(byte).isspace() is False)


def test_partial_disables_file_fallback_but_keeps_trusted_symbol():
    source = b"BROKEN prefix defgood suffix"
    symbol = _symbol(source, "good", SymbolKind.FUNCTION, 14, 21)
    issue = ParseIssue(
        ParseIssueKind.SYNTAX_ERROR,
        "Syntax error.",
        location_from_offsets(source, build_line_starts(source), 0, 6),
    )
    parsed = replace(
        _parsed(status=ParseStatus.PARTIAL, digest=sha256(source).hexdigest()),
        symbols=(symbol,), issues=(issue,),
    )

    candidates = select_candidates(parsed, source, build_symbol_intervals(parsed.symbols, len(source)))

    assert candidates == (ChunkCandidate(ChunkKind.SYMBOL, 14, 21, symbol),)


def test_partial_excludes_issue_intersecting_parent_residual_but_keeps_descendant():
    source = b"0123456789abcdefghij"
    outer = _symbol(source, "Outer", SymbolKind.CLASS, 0, 20)
    method = _symbol(source, "m", SymbolKind.METHOD, 5, 15, "Outer")
    issue = ParseIssue(
        ParseIssueKind.MISSING_NODE,
        "Missing syntax.",
        location_from_offsets(source, build_line_starts(source), 1, 2),
    )
    parsed = replace(
        _parsed(status=ParseStatus.PARTIAL, digest=sha256(source).hexdigest()),
        symbols=(outer, method), issues=(issue,),
    )

    candidates = select_candidates(parsed, source, build_symbol_intervals(parsed.symbols, len(source)))

    assert candidates == (ChunkCandidate(ChunkKind.SYMBOL, 5, 15, method),)


def test_partial_allows_issue_free_parent_residual_and_preserves_import_metadata(tmp_path: Path):
    source = b"0123456789abcdefghijXXXXX"
    outer = _symbol(source, "Outer", SymbolKind.CLASS, 0, 20)
    method = _symbol(source, "m", SymbolKind.METHOD, 5, 15, "Outer")
    issue = ParseIssue(
        ParseIssueKind.SYNTAX_ERROR,
        "Syntax error.",
        location_from_offsets(source, build_line_starts(source), 21, 22),
    )
    imported = ImportInfo(
        "pkg", (), False, (),
        location_from_offsets(source, build_line_starts(source), 21, 25),
    )
    parsed_file = replace(
        _parsed(status=ParseStatus.PARTIAL, digest=sha256(source).hexdigest()),
        symbols=(outer, method), issues=(issue,), imports=(imported,),
    )
    candidates = select_candidates(parsed_file, source, build_symbol_intervals(parsed_file.symbols, len(source)))
    assert [(item.start_byte, item.end_byte) for item in candidates] == [(0, 5), (5, 15), (15, 20)]
    assert all(item.end_byte <= 20 for item in candidates)


def test_chunk_id_matches_independently_serialized_canonical_material():
    source = b"def f(): pass"
    owner = _symbol(source, "f", SymbolKind.FUNCTION, 0, len(source))
    material = [
        "tracerag-code-chunk-v1", _NAMESPACE, "src/नमस्ते.py", "symbol",
        "function", "f", "", 0, len(source), 0,
    ]
    expected = sha256(json.dumps(material, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    assert make_chunk_id(
        _NAMESPACE, "src/नमस्ते.py", ChunkKind.SYMBOL, owner, 0, len(source), 0
    ) == expected


def test_same_namespace_and_provenance_ignore_different_clone_paths():
    source = b"value = 1"
    owner = _symbol(source, "value", SymbolKind.CONSTANT, 0, len(source))
    first = make_chunk_id(_NAMESPACE, "app.py", ChunkKind.SYMBOL, owner, 0, len(source), 0)
    second = make_chunk_id(_NAMESPACE, "app.py", ChunkKind.SYMBOL, owner, 0, len(source), 0)
    assert first == second


def test_different_repository_namespaces_change_identity_for_identical_source():
    source = b"value = 1"
    owner = _symbol(source, "value", SymbolKind.CONSTANT, 0, len(source))
    first = make_chunk_id("repo:a", "app.py", ChunkKind.SYMBOL, owner, 0, len(source), 0)
    second = make_chunk_id("repo:b", "app.py", ChunkKind.SYMBOL, owner, 0, len(source), 0)
    assert first != second


def test_content_hash_is_exact_bytes_and_independent_of_structural_id():
    before = b"value = 1"
    after = b"value = 2"
    owner = _symbol(before, "value", SymbolKind.CONSTANT, 0, len(before))
    before_id = make_chunk_id(_NAMESPACE, "app.py", ChunkKind.SYMBOL, owner, 0, len(before), 0)
    after_id = make_chunk_id(_NAMESPACE, "app.py", ChunkKind.SYMBOL, owner, 0, len(after), 0)
    assert before_id == after_id
    assert make_content_hash(before) == sha256(before).hexdigest()
    assert make_content_hash(before) != make_content_hash(after)


def test_equal_content_in_different_files_has_equal_hash_but_distinct_ids():
    source = b"return 1"
    owner = _symbol(source, "f", SymbolKind.FUNCTION, 0, len(source))
    assert make_content_hash(source) == make_content_hash(source)
    assert make_chunk_id(_NAMESPACE, "a.py", ChunkKind.SYMBOL, owner, 0, len(source), 0) != make_chunk_id(
        _NAMESPACE, "b.py", ChunkKind.SYMBOL, owner, 0, len(source), 0
    )


@pytest.mark.parametrize("size", (4_096, 8_192))
def test_fragmenter_keeps_candidate_at_or_below_max(size):
    source = b"a" * size
    candidate = ChunkCandidate(ChunkKind.CONTEXT, 0, size, None)
    assert fragment_candidate(candidate, source, ChunkerConfig()) == ((0, size),)


def test_fragmenter_prefers_last_complete_line_before_target():
    source = b"a" * 3000 + b"\n" + b"b" * 3000 + b"\n" + b"c" * 3000
    candidate = ChunkCandidate(ChunkKind.CONTEXT, 0, len(source), None)
    pieces = fragment_candidate(candidate, source, ChunkerConfig())
    assert pieces[0] == (0, 3001)
    assert b"".join(source[a:b] for a, b in pieces) == source


def test_fragmenter_keeps_crlf_together_and_never_exceeds_max():
    source = b"a" * 7 + b"\r\n" + b"b" * 7 + b"\r\n" + b"c" * 7
    candidate = ChunkCandidate(ChunkKind.CONTEXT, 0, len(source), None)
    pieces = fragment_candidate(candidate, source, ChunkerConfig(8, 10))
    assert pieces[0] == (0, 9)
    assert all(len(source[a:b]) <= 10 for a, b in pieces)
    assert all(not (b < len(source) and source[b - 1:b + 1] == b"\r\n") for _, b in pieces)
    assert b"".join(source[a:b] for a, b in pieces) == source


def test_large_single_line_unicode_splits_only_at_utf8_boundaries():
    source = ("नमस्ते" * 1000).encode("utf-8")
    candidate = ChunkCandidate(ChunkKind.SYMBOL, 0, len(source), None)
    pieces = fragment_candidate(candidate, source, ChunkerConfig(16, 31))
    assert len(pieces) > 1
    assert all(0 < end - start <= 31 for start, end in pieces)
    assert all(source[start:end].decode("utf-8") for start, end in pieces)
    assert b"".join(source[a:b] for a, b in pieces) == source


def test_fragmenter_handles_bom_and_eof_without_newline_exactly():
    source = b"\xef\xbb\xbf" + b"a" * 20
    candidate = ChunkCandidate(ChunkKind.CONTEXT, 0, len(source), None)
    pieces = fragment_candidate(candidate, source, ChunkerConfig(8, 10))
    assert pieces[0][0] == 0
    assert b"".join(source[a:b] for a, b in pieces) == source
    assert pieces[-1][1] == len(source)


def test_chunker_constructs_exact_symbol_and_context_chunks_with_owner_metadata(tmp_path: Path):
    data = b"# preamble\ndef greet(name: str) -> str:\n    return name\n"
    scanner, parser = _pipeline(tmp_path, data)

    result = CodeChunker().chunk_inventory(scanner, parser)

    chunks = result.files[0].chunks
    assert chunks
    assert any(chunk.kind is ChunkKind.SYMBOL and chunk.symbol_name == "greet" for chunk in chunks)
    assert any(chunk.kind is ChunkKind.CONTEXT and chunk.symbol_name is None for chunk in chunks)
    for chunk in chunks:
        exact = data[chunk.location.start_byte:chunk.location.end_byte]
        assert chunk.content.encode("utf-8") == exact
        assert chunk.content_hash == sha256(exact).hexdigest()
        assert chunk.fragment_index == 0
        assert chunk.fragment_count == 1


def test_oversized_candidates_become_fragments_with_common_metadata(tmp_path: Path):
    data = b"def long_function():\n" + (b"    value = 1234567890\n" * 8)
    scanner, parser = _pipeline(tmp_path, data)

    result = CodeChunker(ChunkerConfig(32, 48)).chunk_inventory(scanner, parser)

    fragments = [chunk for chunk in result.files[0].chunks if chunk.symbol_name == "long_function"]
    assert len(fragments) > 1
    assert all(chunk.kind is ChunkKind.FRAGMENT for chunk in fragments)
    assert [chunk.fragment_index for chunk in fragments] == list(range(len(fragments)))
    assert {chunk.fragment_count for chunk in fragments} == {len(fragments)}
    assert all(len(chunk.content.encode("utf-8")) <= 48 for chunk in fragments)
    symbol = next(symbol for symbol in parser.files[0].symbols if symbol.name == "long_function")
    assert b"".join(chunk.content.encode("utf-8") for chunk in fragments) == data[
        symbol.location.start_byte:symbol.location.end_byte
    ]


def test_bom_file_context_preserves_exact_original_prefix(tmp_path: Path):
    data = b"\xef\xbb\xbf# note\nvalue = 1\n"
    scanner, parser = _pipeline(tmp_path, data)
    result = CodeChunker().chunk_inventory(scanner, parser)
    first = result.files[0].chunks[0]
    assert first.location.start_byte == 0
    assert first.content.encode("utf-8") == data[:first.location.end_byte]


def test_constructed_chunk_locations_cover_multibyte_lf_and_crlf(tmp_path: Path):
    for data in (
        "# नमस्ते\ndef f():\n    return 1\n".encode("utf-8"),
        "# नमस्ते\r\ndef f():\r\n    return 1\r\n".encode("utf-8"),
    ):
        root = tmp_path / str(len(data))
        scanner, parser = _pipeline(root, data)
        chunks = CodeChunker(ChunkerConfig(12, 24)).chunk_inventory(scanner, parser).files[0].chunks
        assert chunks
        ordered = sorted(chunks, key=lambda chunk: chunk.location.start_byte)
        assert all(
            left.location.end_byte <= right.location.start_byte
            for left, right in zip(ordered, ordered[1:])
        )
        for chunk in chunks:
            assert chunk.content.encode("utf-8") == data[chunk.location.start_byte:chunk.location.end_byte]


def test_repeat_runs_are_byte_for_byte_deterministic(tmp_path: Path):
    scanner, parser = _pipeline(tmp_path, b"# header\nA = 1\ndef f():\n    return A\n")
    chunker = CodeChunker(ChunkerConfig(16, 24))
    assert chunker.chunk_inventory(scanner, parser) == chunker.chunk_inventory(scanner, parser)


def test_equal_content_in_two_files_is_not_globally_deduplicated(tmp_path: Path):
    (tmp_path / "a.py").write_bytes(b"def f():\n    return 1\n")
    (tmp_path / "b.py").write_bytes(b"def f():\n    return 1\n")
    scanner = FileScanner().scan(tmp_path, repository_namespace=_NAMESPACE)
    parser = CodeParser().parse_inventory(scanner)

    result = CodeChunker().chunk_inventory(scanner, parser)

    symbols = [chunk for file in result.files for chunk in file.chunks if chunk.symbol_name == "f"]
    assert len(symbols) == 2
    assert symbols[0].content_hash == symbols[1].content_hash
    assert symbols[0].chunk_id != symbols[1].chunk_id


def test_inventory_counters_partition_final_file_outcomes(tmp_path: Path):
    (tmp_path / "a.py").write_bytes(b"A = 1\n")
    (tmp_path / "b.py").write_bytes(b"B = 2\n")
    scanner = FileScanner().scan(tmp_path, repository_namespace=_NAMESPACE)
    parser = CodeParser().parse_inventory(scanner)
    failed = replace(parser.files[1], status=ParseStatus.FAILED, source_sha256=None)
    parser = replace(parser, success_files=1, failed_files=1, files=(parser.files[0], failed))

    result = CodeChunker().chunk_inventory(scanner, parser)

    assert result.total_files_requested == 2
    assert result.success_files + result.partial_files + result.failed_files == 2
    assert result.total_chunks == sum(len(file.chunks) for file in result.files)


def test_chunk_and_file_order_is_deterministic(tmp_path: Path):
    (tmp_path / "b.py").write_bytes(b"B = 2\n")
    (tmp_path / "A.py").write_bytes(b"A = 1\n")
    scanner = FileScanner().scan(tmp_path, repository_namespace=_NAMESPACE)
    parser = CodeParser().parse_inventory(scanner)
    result = CodeChunker().chunk_inventory(scanner, parser)
    assert [file.relative_path for file in result.files] == ["A.py", "b.py"]
    for file in result.files:
        keys = [
            (
                chunk.location.start_byte, chunk.location.end_byte, chunk.kind.value,
                "" if chunk.symbol_kind is None else chunk.symbol_kind.value,
                "" if chunk.qualified_name is None else chunk.qualified_name,
                chunk.fragment_index, chunk.chunk_id,
            )
            for chunk in file.chunks
        ]
        assert keys == sorted(keys)


def test_logging_is_aggregate_and_sanitized(tmp_path: Path, caplog):
    secret = "SECRET_SOURCE_VALUE"
    scanner, parser = _pipeline(tmp_path, f"value = '{secret}'\n".encode())
    with caplog.at_level("DEBUG", logger="backend.code_chunker.chunker"):
        CodeChunker().chunk_inventory(scanner, parser)
    text = caplog.text
    assert "Code chunk inventory start" in text
    assert "Code chunk inventory complete" in text
    assert "app.py" in text
    assert secret not in text
    assert parser.files[0].source_sha256 not in text


def test_public_api_exports_exact_approved_surface():
    expected = {
        "ChunkFileStatus", "ChunkIssue", "ChunkIssueKind", "ChunkKind",
        "ChunkedFile", "ChunkerConfig", "CodeChunk", "CodeChunkInventory",
        "CodeChunker", "CodeChunkerError", "InvalidChunkInventory",
        "ChunkerConfigurationError", "RepositoryChunkError", "chunk_code_inventory",
    }
    assert set(code_chunker_public.__all__) == expected
    assert all(hasattr(code_chunker_public, name) for name in expected)


def test_convenience_api_delegates_in_pipeline_order(monkeypatch):
    scanner, parser = _inventories()
    sentinel = object()
    calls = []

    def fake(self, file_inventory, parse_inventory):
        calls.append((self, file_inventory, parse_inventory))
        return sentinel

    monkeypatch.setattr(CodeChunker, "chunk_inventory", fake)
    assert code_chunker_public.chunk_code_inventory(scanner, parser) is sentinel
    assert len(calls) == 1
    assert isinstance(calls[0][0], CodeChunker)
    assert calls[0][1:] == (scanner, parser)


def test_code_chunker_rejects_non_config_eagerly():
    with pytest.raises(ChunkerConfigurationError):
        CodeChunker(object())


def test_public_results_do_not_retain_reader_or_tree_structures(tmp_path: Path):
    scanner, parser = _pipeline(tmp_path, b"value = 1\n")
    result = CodeChunker().chunk_inventory(scanner, parser)
    forbidden_fields = {"source", "source_bytes", "original_bytes", "parse_bytes", "tree", "node", "all_chunks"}
    for model in (CodeChunkInventory, ChunkedFile, CodeChunk):
        assert forbidden_fields.isdisjoint(model.__dataclass_fields__)
    assert not any(isinstance(value, bytes) for value in vars(result).values()) if hasattr(result, "__dict__") else True


@pytest.mark.parametrize(
    ("filename", "data", "expected_language", "expected_symbol_kind"),
    (
        ("app.py", b"import os\nclass A:\n    def m(self):\n        return 1\n", ParsedLanguage.PYTHON, SymbolKind.METHOD),
        ("App.java", b"package p; class A { int m() { return 1; } }\n", ParsedLanguage.JAVA, SymbolKind.METHOD),
        ("app.js", b"import x from 'x'; class A { m() { return 1; } }\n", ParsedLanguage.JAVASCRIPT, SymbolKind.METHOD),
        ("view.jsx", b"const App = () => <div>Hello</div>;\n", ParsedLanguage.JAVASCRIPT, SymbolKind.FUNCTION),
        ("types.ts", b"interface A { m(x: number): string; }\n", ParsedLanguage.TYPESCRIPT, SymbolKind.METHOD),
        ("view.tsx", b"const App = () => <div>Hello</div>;\n", ParsedLanguage.TSX, SymbolKind.FUNCTION),
    ),
)
def test_pipeline_language_fixtures_preserve_exact_nonoverlapping_coverage(
    tmp_path: Path, filename, data, expected_language, expected_symbol_kind
):
    scanner, parser = _pipeline(tmp_path, data, relative_path=filename)
    assert parser.files[0].status is ParseStatus.SUCCESS
    assert parser.files[0].language is expected_language

    first = CodeChunker(ChunkerConfig(24, 48)).chunk_inventory(scanner, parser)
    second = CodeChunker(ChunkerConfig(24, 48)).chunk_inventory(scanner, parser)

    assert first == second
    file = first.files[0]
    assert any(chunk.symbol_kind is expected_symbol_kind for chunk in file.chunks)
    ordered = sorted(file.chunks, key=lambda chunk: chunk.location.start_byte)
    assert all(
        left.location.end_byte <= right.location.start_byte
        for left, right in zip(ordered, ordered[1:])
    )
    covered = [False] * len(data)
    for chunk in ordered:
        exact = data[chunk.location.start_byte:chunk.location.end_byte]
        assert chunk.content.encode("utf-8") == exact
        for offset in range(chunk.location.start_byte, chunk.location.end_byte):
            assert covered[offset] is False
            covered[offset] = True
    decoded = data.decode("utf-8")
    byte_offset = 0
    for character in decoded:
        encoded = character.encode("utf-8")
        if not character.isspace():
            assert all(covered[byte_offset:byte_offset + len(encoded)])
        byte_offset += len(encoded)


def _scanned(
    relative_path: str = "src/app.py",
    *,
    extension: str = ".py",
    language: str | None = "python",
    category: FileCategory = FileCategory.SOURCE,
) -> ScannedFile:
    return ScannedFile(
        relative_path=relative_path,
        filename=relative_path.rsplit("/", maxsplit=1)[-1],
        extension=extension,
        language=language,
        category=category,
        size_bytes=1,
    )


def _parsed(
    relative_path: str = "src/app.py",
    *,
    language: ParsedLanguage = ParsedLanguage.PYTHON,
    status: ParseStatus = ParseStatus.SUCCESS,
    digest: str | None = "a" * 64,
) -> ParsedFile:
    return ParsedFile(relative_path, language, status, (), (), (), (), digest)


def _inventories(
    *,
    scanned_files: tuple[ScannedFile, ...] = (_scanned(),),
    parsed_files: tuple[ParsedFile, ...] = (_parsed(),),
    ignored: tuple[IgnoredFile, ...] = (),
    skipped_directories: tuple[SkippedDirectory, ...] = (),
    skipped: tuple[SkippedParseFile, ...] = (),
    root: str = "/repo",
    scanner_namespace: object = _NAMESPACE,
    parser_namespace: object = _NAMESPACE,
) -> tuple[FileInventory, CodeParseInventory]:
    scanner = FileInventory(
        root,
        len(scanned_files) + len(ignored),
        len(scanned_files),
        len(ignored),
        scanned_files,
        ignored,
        skipped_directories,
        scanner_namespace,
    )
    parser = CodeParseInventory(
        root,
        len(parsed_files) + len(skipped),
        sum(item.status is ParseStatus.SUCCESS for item in parsed_files),
        sum(item.status is ParseStatus.PARTIAL for item in parsed_files),
        sum(item.status is ParseStatus.FAILED for item in parsed_files),
        len(skipped),
        parsed_files,
        skipped,
        parser_namespace,
    )
    return scanner, parser


def test_inventory_validation_returns_frozen_ordered_o1_join_pairs():
    scanner, parser = _inventories(
        scanned_files=(
            _scanned("src/A.py"),
            _scanned("src/b.ts", extension=".ts", language="typescript"),
        ),
        parsed_files=(
            _parsed("src/A.py"),
            _parsed("src/b.ts", language=ParsedLanguage.TYPESCRIPT),
        ),
    )

    result = validate_inputs(scanner, parser)

    assert result == ValidatedInputs(
        "/repo",
        _NAMESPACE,
        ((scanner.files[0], parser.files[0]), (scanner.files[1], parser.files[1])),
    )
    assert getattr(ValidatedInputs, "__slots__") == (
        "repository_path",
        "repository_namespace",
        "pairs",
    )
    assert getattr(ValidatedInputs, "__dataclass_params__").frozen is True


@pytest.mark.parametrize("value", (None, object(), "not-an-inventory"))
def test_inventory_validation_rejects_wrong_inventory_types(value):
    scanner, parser = _inventories()

    with pytest.raises(InvalidChunkInventory):
        validate_inputs(value, parser)
    with pytest.raises(InvalidChunkInventory):
        validate_inputs(scanner, value)


@pytest.mark.parametrize(
    "inventory_name, field_name, value",
    (
        ("scanner", "files", []),
        ("scanner", "ignored", []),
        ("scanner", "skipped_directories", []),
        ("parser", "files", []),
        ("parser", "skipped", []),
        ("scanner", "files", (object(),)),
        ("scanner", "ignored", (object(),)),
        ("scanner", "skipped_directories", (object(),)),
        ("parser", "files", (object(),)),
        ("parser", "skipped", (object(),)),
    ),
)
def test_inventory_validation_rejects_non_tuple_or_wrong_member_collections(
    inventory_name, field_name, value
):
    scanner, parser = _inventories()
    if inventory_name == "scanner":
        scanner = replace(scanner, **{field_name: value})
    else:
        parser = replace(parser, **{field_name: value})

    with pytest.raises(InvalidChunkInventory):
        validate_inputs(scanner, parser)


@pytest.mark.parametrize(
    "scanner_changes, parser_changes",
    (
        ({"included_files": 0}, {}),
        ({"ignored_files": 1}, {}),
        ({"total_files_seen": 0}, {}),
        ({}, {"total_files_requested": 0}),
        ({}, {"success_files": 0}),
        ({}, {"partial_files": 1}),
        ({}, {"failed_files": 1}),
        ({}, {"skipped_files": 1}),
    ),
)
def test_inventory_validation_rejects_inconsistent_counters(
    scanner_changes, parser_changes
):
    scanner, parser = _inventories()

    with pytest.raises(InvalidChunkInventory):
        validate_inputs(
            replace(scanner, **scanner_changes), replace(parser, **parser_changes)
        )


def test_inventory_validation_rejects_a_parsed_file_with_an_invalid_status():
    scanner, parser = _inventories(parsed_files=(replace(_parsed(), status=object()),))

    with pytest.raises(InvalidChunkInventory):
        validate_inputs(scanner, parser)


def test_inventory_validation_rejects_duplicate_paths_and_unsorted_parser_files():
    scanner, parser = _inventories(
        scanned_files=(_scanned("src/a.py"), _scanned("src/a.py")),
        parsed_files=(_parsed("src/a.py"), _parsed("src/a.py")),
    )
    with pytest.raises(InvalidChunkInventory):
        validate_inputs(scanner, parser)

    scanner, parser = _inventories(
        scanned_files=(_scanned("src/a.py"), _scanned("src/b.py")),
        parsed_files=(_parsed("src/b.py"), _parsed("src/a.py")),
    )
    with pytest.raises(InvalidChunkInventory):
        validate_inputs(scanner, parser)


@pytest.mark.parametrize(
    "scanner_namespace, parser_namespace",
    (
        (None, _NAMESPACE),
        ("", _NAMESPACE),
        (object(), _NAMESPACE),
        (_NAMESPACE, None),
        (_NAMESPACE, ""),
        (_NAMESPACE, object()),
        (_NAMESPACE, "tracerag-repository-v1:github:other/repo"),
    ),
)
def test_namespace_validation_rejects_missing_empty_wrong_or_different_namespace(
    scanner_namespace, parser_namespace
):
    scanner, parser = _inventories(
        scanner_namespace=scanner_namespace, parser_namespace=parser_namespace
    )

    with pytest.raises(InvalidChunkInventory):
        validate_inputs(scanner, parser)


def test_inventory_validation_rejects_root_mismatch_missing_join_and_non_code_category():
    scanner, parser = _inventories()
    with pytest.raises(InvalidChunkInventory):
        validate_inputs(scanner, replace(parser, repository_path="/other"))

    with pytest.raises(InvalidChunkInventory):
        validate_inputs(scanner, replace(parser, files=(_parsed("src/missing.py"),)))

    scanner, parser = _inventories(
        scanned_files=(_scanned(category=FileCategory.CONFIG),)
    )
    with pytest.raises(InvalidChunkInventory):
        validate_inputs(scanner, parser)


@pytest.mark.parametrize(
    "scanned, parsed",
    (
        (_scanned(extension=".py", language="java"), _parsed()),
        (_scanned(extension=".jsx", language="javascript"), _parsed()),
        (_scanned(extension=".md", language=None), _parsed()),
    ),
)
def test_join_validation_rejects_incompatible_language_selector(scanned, parsed):
    scanner, parser = _inventories(scanned_files=(scanned,), parsed_files=(parsed,))

    with pytest.raises(InvalidChunkInventory):
        validate_inputs(scanner, parser)


@pytest.mark.parametrize(
    "status, digest, expected",
    (
        (ParseStatus.SUCCESS, None, True),
        (ParseStatus.PARTIAL, "A" * 64, True),
        (ParseStatus.SUCCESS, "f" * 63, True),
        (ParseStatus.SUCCESS, "g" * 64, True),
        (ParseStatus.FAILED, None, False),
    ),
)
def test_join_validation_enforces_processable_digest_but_allows_failed_none(
    status, digest, expected
):
    scanner, parser = _inventories(parsed_files=(_parsed(status=status, digest=digest),))

    if expected:
        with pytest.raises(InvalidChunkInventory):
            validate_inputs(scanner, parser)
    else:
        assert validate_inputs(scanner, parser).pairs == ((scanner.files[0], parser.files[0]),)


@pytest.mark.parametrize(
    "extension, scanner_language, parsed_language",
    (
        (".py", "python", ParsedLanguage.PYTHON),
        (".java", "java", ParsedLanguage.JAVA),
        (".js", "javascript", ParsedLanguage.JAVASCRIPT),
        (".jsx", "javascript", ParsedLanguage.JAVASCRIPT),
        (".ts", "typescript", ParsedLanguage.TYPESCRIPT),
        (".tsx", "typescript", ParsedLanguage.TSX),
    ),
)
def test_join_validation_accepts_closed_language_selector(
    extension, scanner_language, parsed_language
):
    scanner, parser = _inventories(
        scanned_files=(
            _scanned(extension=extension, language=scanner_language),
        ),
        parsed_files=(_parsed(language=parsed_language),),
    )

    assert validate_inputs(scanner, parser).pairs == ((scanner.files[0], parser.files[0]),)


def test_chunk_enums_are_string_enums_with_stable_values():
    assert issubclass(ChunkKind, str)
    assert issubclass(ChunkKind, Enum)
    assert {member.name: member.value for member in ChunkKind} == {
        "SYMBOL": "symbol",
        "CONTEXT": "context",
        "FRAGMENT": "fragment",
    }
    assert {member.name: member.value for member in ChunkFileStatus} == {
        "SUCCESS": "success",
        "PARTIAL": "partial",
        "FAILED": "failed",
    }
    assert {member.name: member.value for member in ChunkIssueKind} == {
        "SOURCE_CHANGED": "source_changed",
        "READ_ERROR": "read_error",
        "PATH_INVALID": "path_invalid",
        "LINK_UNSAFE": "link_unsafe",
        "DECODING_ERROR": "decoding_error",
        "LOCATION_INVALID": "location_invalid",
        "PARSE_UNAVAILABLE": "parse_unavailable",
        "FRAGMENTATION_ERROR": "fragmentation_error",
    }
    assert issubclass(ChunkFileStatus, str)
    assert issubclass(ChunkFileStatus, Enum)
    assert issubclass(ChunkIssueKind, str)
    assert issubclass(ChunkIssueKind, Enum)


@pytest.mark.parametrize(
    ("model", "expected_fields"),
    [
        (ChunkerConfig, ["target_chunk_bytes", "max_chunk_bytes"]),
        (
            CodeChunk,
            [
                "chunk_id",
                "kind",
                "location",
                "content",
                "content_hash",
                "symbol_kind",
                "symbol_name",
                "qualified_name",
                "parent_qualified_name",
                "parameters",
                "return_type",
                "modifiers",
                "base_types",
                "implemented_types",
                "fragment_index",
                "fragment_count",
            ],
        ),
        (ChunkIssue, ["kind", "message", "location"]),
        (
            ChunkedFile,
            [
                "relative_path",
                "language",
                "parse_status",
                "status",
                "source_sha256",
                "imports",
                "chunks",
                "issues",
            ],
        ),
        (
            CodeChunkInventory,
            [
                "repository_path",
                "repository_namespace",
                "total_files_requested",
                "success_files",
                "partial_files",
                "failed_files",
                "total_chunks",
                "files",
            ],
        ),
    ],
)
def test_public_models_are_frozen_slotted_dataclasses_with_exact_field_order(
    model, expected_fields
):
    assert is_dataclass(model)
    assert [field.name for field in fields(model)] == expected_fields
    assert getattr(model, "__slots__") == tuple(expected_fields)
    assert getattr(model, "__dataclass_params__").frozen is True


def test_models_are_immutable_and_do_not_have_instance_dicts():
    config = ChunkerConfig()
    with pytest.raises(FrozenInstanceError):
        config.target_chunk_bytes = 1
    with pytest.raises((AttributeError, TypeError)):
        config.extra = "not allowed"
    assert not hasattr(config, "__dict__")


def test_config_defaults_and_eager_validation():
    assert ChunkerConfig() == ChunkerConfig(4_096, 8_192)
    assert ChunkerConfig(1, 1) == ChunkerConfig(1, 1)
    for invalid in (True, False, 0, -1, 1.0, "4096"):
        with pytest.raises(ChunkerConfigurationError):
            ChunkerConfig(invalid, 8_192)
        with pytest.raises(ChunkerConfigurationError):
            ChunkerConfig(4_096, invalid)
    with pytest.raises(ChunkerConfigurationError):
        ChunkerConfig(8_193, 8_192)


def test_fatal_exception_hierarchy_uses_direct_subclasses():
    assert issubclass(ChunkerConfigurationError, CodeChunkerError)
    assert issubclass(InvalidChunkInventory, CodeChunkerError)
    assert issubclass(RepositoryChunkError, CodeChunkerError)
    assert ChunkerConfigurationError.__bases__ == (CodeChunkerError,)
    assert InvalidChunkInventory.__bases__ == (CodeChunkerError,)
    assert RepositoryChunkError.__bases__ == (CodeChunkerError,)


def test_public_models_accept_parser_values_and_tuple_collections():
    location = SourceLocation(0, 8, 1, 0, 1, 8)
    parameter = ParameterInfo("value", "int", None)
    import_info = ImportInfo("pkg", (), False, (), location)
    chunk = CodeChunk(
        "chunk-id",
        ChunkKind.SYMBOL,
        location,
        "value()",
        "content-hash",
        SymbolKind.FUNCTION,
        "value",
        "module.value",
        None,
        (parameter,),
        "int",
        ("public",),
        (),
        (),
        0,
        1,
    )
    issue = ChunkIssue(ChunkIssueKind.READ_ERROR, "could not read", None)
    file = ChunkedFile(
        "src/example.py",
        ParsedLanguage.PYTHON,
        ParseStatus.SUCCESS,
        ChunkFileStatus.SUCCESS,
        "a" * 64,
        (import_info,),
        (chunk,),
        (issue,),
    )
    inventory = CodeChunkInventory(
        ".",
        "tracerag-repository-v1:github:example/repo",
        1,
        1,
        0,
        0,
        1,
        (file,),
    )
    assert chunk.parameters == (parameter,)
    assert file.imports == (import_info,)
    assert inventory.files == (file,)
    assert isinstance(chunk.parameters, tuple)
    assert isinstance(file.chunks, tuple)
    assert isinstance(inventory.files, tuple)
