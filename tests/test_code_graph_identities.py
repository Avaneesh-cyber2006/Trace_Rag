"""Exact-payload tests for deterministic code graph identities."""

from hashlib import sha256

from backend.code_graph.identities import (
    edge_id,
    file_node_id,
    reference_id,
    symbol_node_id,
)
from backend.code_graph.models import GraphEdgeKind, ReferenceKind
from backend.code_parser.models import SourceLocation, SymbolInfo, SymbolKind


def _location(
    start_byte: int,
    end_byte: int,
    *,
    start_line: int = 1,
    start_column: int = 0,
    end_line: int = 1,
    end_column: int = 1,
) -> SourceLocation:
    return SourceLocation(
        start_byte,
        end_byte,
        start_line,
        start_column,
        end_line,
        end_column,
    )


def _symbol(
    qualified_name: str,
    start_byte: int,
    end_byte: int,
    *,
    kind: SymbolKind = SymbolKind.FUNCTION,
    location: SourceLocation | None = None,
) -> SymbolInfo:
    return SymbolInfo(
        name=qualified_name.rsplit(".", 1)[-1],
        kind=kind,
        qualified_name=qualified_name,
        parent_qualified_name=None,
        location=location or _location(start_byte, end_byte),
        parameters=(),
        return_type=None,
        modifiers=(),
        base_types=(),
        implemented_types=(),
    )


def test_unicode_file_identity_uses_exact_utf8_payload():
    payload = '["tracerag-code-graph-node-v1","repo","file","café.py"]'

    assert file_node_id("repo", "café.py") == sha256(
        payload.encode("utf-8")
    ).hexdigest()


def test_nested_symbol_identity_uses_exact_kind_name_and_byte_range_payload():
    payload = (
        '["tracerag-code-graph-node-v1","repo","symbol","src/Case.py",'
        '"method","Outer.Inner.Run",11,29]'
    )
    symbol = _symbol("Outer.Inner.Run", 11, 29, kind=SymbolKind.METHOD)

    assert symbol_node_id("repo", "src/Case.py", symbol) == sha256(
        payload.encode("utf-8")
    ).hexdigest()


def test_located_edge_identity_uses_exact_location_payload():
    payload = (
        '["tracerag-code-graph-edge-v1","repo","calls","source-id",'
        '"target-id",[31,44]]'
    )

    assert edge_id(
        "repo",
        GraphEdgeKind.CALLS,
        "source-id",
        "target-id",
        _location(31, 44),
    ) == sha256(payload.encode("utf-8")).hexdigest()


def test_structural_edge_identity_uses_json_null_location():
    payload = (
        '["tracerag-code-graph-edge-v1","repo","defines","file-id",'
        '"symbol-id",null]'
    )

    assert edge_id(
        "repo", GraphEdgeKind.DEFINES, "file-id", "symbol-id", None
    ) == sha256(payload.encode("utf-8")).hexdigest()


def test_null_source_reference_identity_uses_exact_unicode_payload():
    payload = (
        '["tracerag-code-graph-unresolved-v1","repo","src/café.py","call",'
        'null,[50,61],"Módulo.Run"]'
    )

    assert reference_id(
        "repo",
        "src/café.py",
        ReferenceKind.CALL,
        None,
        _location(50, 61),
        "Módulo.Run",
    ) == sha256(payload.encode("utf-8")).hexdigest()


def test_namespace_changes_each_identity_family():
    symbol = _symbol("run", 1, 5)
    location = _location(7, 10)

    assert file_node_id("repo-a", "a.py") != file_node_id("repo-b", "a.py")
    assert symbol_node_id("repo-a", "a.py", symbol) != symbol_node_id(
        "repo-b", "a.py", symbol
    )
    assert edge_id("repo-a", GraphEdgeKind.CALLS, "s", "t", location) != edge_id(
        "repo-b", GraphEdgeKind.CALLS, "s", "t", location
    )
    assert reference_id(
        "repo-a", "a.py", ReferenceKind.CALL, "s", location, "run"
    ) != reference_id(
        "repo-b", "a.py", ReferenceKind.CALL, "s", location, "run"
    )


def test_byte_range_changes_symbol_edge_and_reference_identities():
    first = _location(7, 10)
    second = _location(7, 11)

    assert symbol_node_id("repo", "a.py", _symbol("run", 7, 10)) != symbol_node_id(
        "repo", "a.py", _symbol("run", 7, 11)
    )
    assert edge_id("repo", GraphEdgeKind.CALLS, "s", "t", first) != edge_id(
        "repo", GraphEdgeKind.CALLS, "s", "t", second
    )
    assert reference_id(
        "repo", "a.py", ReferenceKind.CALL, "s", first, "run"
    ) != reference_id(
        "repo", "a.py", ReferenceKind.CALL, "s", second, "run"
    )


def test_kind_changes_symbol_edge_and_reference_identities():
    location = _location(7, 10)

    assert symbol_node_id(
        "repo", "a.py", _symbol("run", 7, 10, kind=SymbolKind.FUNCTION)
    ) != symbol_node_id(
        "repo", "a.py", _symbol("run", 7, 10, kind=SymbolKind.METHOD)
    )
    assert edge_id("repo", GraphEdgeKind.CALLS, "s", "t", location) != edge_id(
        "repo", GraphEdgeKind.IMPORTS, "s", "t", location
    )
    assert reference_id(
        "repo", "a.py", ReferenceKind.CALL, "s", location, "run"
    ) != reference_id(
        "repo", "a.py", ReferenceKind.IMPORT, "s", location, "run"
    )


def test_repeated_nested_definitions_with_distinct_ranges_have_distinct_ids():
    first = _symbol("Outer.run.<locals>.run", 20, 30)
    repeated = _symbol("Outer.run.<locals>.run", 40, 50)

    assert symbol_node_id("repo", "nested.py", first) != symbol_node_id(
        "repo", "nested.py", repeated
    )


def test_path_and_qualified_name_case_are_preserved():
    lower = _symbol("service.run", 4, 9)
    upper = _symbol("Service.Run", 4, 9)

    assert file_node_id("repo", "src/case.py") != file_node_id(
        "repo", "src/Case.py"
    )
    assert symbol_node_id("repo", "src/Case.py", lower) != symbol_node_id(
        "repo", "src/Case.py", upper
    )


def test_line_and_column_changes_do_not_change_location_based_identities():
    first = _location(
        12,
        25,
        start_line=2,
        start_column=3,
        end_line=4,
        end_column=5,
    )
    moved_lines = _location(
        12,
        25,
        start_line=20,
        start_column=30,
        end_line=40,
        end_column=50,
    )
    first_symbol = _symbol("run", 12, 25, location=first)
    moved_symbol = _symbol("run", 12, 25, location=moved_lines)

    assert symbol_node_id("repo", "a.py", first_symbol) == symbol_node_id(
        "repo", "a.py", moved_symbol
    )
    assert edge_id("repo", GraphEdgeKind.CALLS, "s", "t", first) == edge_id(
        "repo", GraphEdgeKind.CALLS, "s", "t", moved_lines
    )
    assert reference_id(
        "repo", "a.py", ReferenceKind.CALL, "s", first, "run"
    ) == reference_id(
        "repo", "a.py", ReferenceKind.CALL, "s", moved_lines, "run"
    )
