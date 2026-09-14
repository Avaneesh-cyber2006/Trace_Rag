"""Node indexing and direct structural graph behavior."""

from dataclasses import replace

import pytest

from backend.code_graph.exceptions import (
    CodeGraphIdentityError,
    CodeGraphValidationError,
)
from backend.code_graph.identities import file_node_id, symbol_node_id
from backend.code_graph.indexes import build_graph_indexes
from backend.code_graph.models import GraphEdgeKind, GraphNodeKind
from backend.code_graph.structure import structural_edges
from backend.code_graph.validation import validate_inputs
from backend.code_parser.models import (
    ParsedLanguage,
    ParseSkipReason,
    ParseStatus,
    SkippedParseFile,
    SymbolKind,
)
from backend.file_scanner.models import FileCategory, ScannedFile


def test_only_direct_structural_edges(cg):
    parent = cg.symbol("A", kind=SymbolKind.CLASS, end=200)
    method = cg.symbol(
        "run",
        kind=SymbolKind.METHOD,
        qualified_name="A.run",
        parent="A",
        start=20,
        end=80,
    )
    index = build_graph_indexes(
        validate_inputs(*cg.pair(cg.parsed(symbols=(method, parent))))
    )

    edges = structural_edges(index)

    assert sorted(edge.edge_kind.value for edge in edges) == [
        "contains",
        "defines",
    ]
    assert all(edge.location is None for edge in edges)
    assert len(index.nodes) == 3


def test_duplicate_qualified_parents_are_selected_only_by_containing_range(cg):
    first = cg.symbol("A", kind=SymbolKind.CLASS, start=0, end=100)
    second = cg.symbol("A", kind=SymbolKind.CLASS, start=100, end=240)
    child = cg.symbol(
        "run",
        kind=SymbolKind.METHOD,
        qualified_name="A.run",
        parent="A",
        start=120,
        end=180,
    )
    parsed = cg.parsed(symbols=(child, second, first))

    index = build_graph_indexes(validate_inputs(*cg.pair(parsed)))

    child_id = symbol_node_id("repo", "a.py", child)
    second_id = symbol_node_id("repo", "a.py", second)
    assert index.parent_ids[child_id] == second_id
    assert index.children[second_id] == (child_id,)


def test_multiple_containing_parent_ranges_are_rejected_without_tie_breaking(cg):
    outer = cg.symbol("A", kind=SymbolKind.CLASS, start=0, end=240)
    inner = cg.symbol("A", kind=SymbolKind.CLASS, start=10, end=200)
    child = cg.symbol(
        "run",
        kind=SymbolKind.METHOD,
        qualified_name="A.run",
        parent="A",
        start=20,
        end=80,
    )

    with pytest.raises(CodeGraphValidationError):
        build_graph_indexes(
            validate_inputs(*cg.pair(cg.parsed(symbols=(child, inner, outer))))
        )


@pytest.mark.parametrize(
    "symbols",
    [
        pytest.param(
            lambda cg: (
                cg.symbol(
                    "child",
                    qualified_name="A.child",
                    parent="missing",
                    start=10,
                    end=20,
                ),
            ),
            id="missing-parent",
        ),
        pytest.param(
            lambda cg: (
                cg.symbol("A", parent="A", start=0, end=20),
            ),
            id="self-parent",
        ),
        pytest.param(
            lambda cg: (
                cg.symbol("A", parent="B", start=0, end=20),
                cg.symbol("B", parent="A", start=0, end=20),
            ),
            id="parent-cycle",
        ),
    ],
)
def test_invalid_parent_relationships_are_rejected(cg, symbols):
    with pytest.raises(CodeGraphValidationError):
        build_graph_indexes(
            validate_inputs(*cg.pair(cg.parsed(symbols=symbols(cg))))
        )


def test_zero_empty_and_failed_inputs_retain_only_proven_file_nodes(cg):
    empty_index = build_graph_indexes(validate_inputs(*cg.pair()))
    parsed_empty = cg.parsed("empty.py")
    failed = cg.parsed("failed.py", status=ParseStatus.FAILED)
    file_index = build_graph_indexes(
        validate_inputs(*cg.pair(parsed_empty, failed))
    )

    assert empty_index.nodes == ()
    assert structural_edges(empty_index) == ()
    assert file_index.nodes == tuple(
        node for node in file_index.nodes if node.node_kind is GraphNodeKind.FILE
    )
    assert {
        (node.relative_path, node.parse_status, node.symbol_kind, node.location)
        for node in file_index.nodes
    } == {
        ("empty.py", ParseStatus.SUCCESS, None, None),
        ("failed.py", ParseStatus.FAILED, None, None),
    }
    assert structural_edges(file_index) == ()


def test_scanner_only_and_parser_skipped_files_have_no_graph_nodes(cg):
    parsed = cg.parsed("kept.py")
    file_inventory, parse_inventory = cg.pair(parsed)
    documentation = ScannedFile(
        "README.md",
        "README.md",
        ".md",
        None,
        FileCategory.DOCUMENTATION,
        12,
    )
    unsupported = ScannedFile(
        "asset.custom",
        "asset.custom",
        ".custom",
        None,
        FileCategory.SOURCE,
        12,
    )
    file_inventory = replace(
        file_inventory,
        total_files_seen=3,
        included_files=3,
        files=(*file_inventory.files, documentation, unsupported),
    )
    parse_inventory = replace(
        parse_inventory,
        total_files_requested=2,
        skipped_files=1,
        skipped=(
            SkippedParseFile(
                "asset.custom", ParseSkipReason.UNSUPPORTED_LANGUAGE
            ),
        ),
    )

    index = build_graph_indexes(validate_inputs(file_inventory, parse_inventory))

    assert tuple(index.files_by_path) == ("kept.py",)
    assert {node.relative_path for node in index.nodes} == {"kept.py"}


def test_every_symbol_kind_and_exact_names_are_projected_unchanged(cg):
    symbols = tuple(
        cg.symbol(
            f"MiXeD_{kind.value}",
            kind=kind,
            qualified_name=f"Scope.MiXeD_{kind.value}",
            start=index * 20,
            end=index * 20 + 10,
        )
        for index, kind in enumerate(SymbolKind)
    )
    parsed = cg.parsed("pkg/module.py", symbols=symbols)

    index = build_graph_indexes(validate_inputs(*cg.pair(parsed)))

    projected = {
        node.symbol_kind: (node.name, node.qualified_name)
        for node in index.nodes
        if node.node_kind is GraphNodeKind.SYMBOL
    }
    assert projected == {
        kind: (f"MiXeD_{kind.value}", f"Scope.MiXeD_{kind.value}")
        for kind in SymbolKind
    }


def test_nested_function_method_and_constant_form_only_direct_children(cg):
    owner = cg.symbol("A", kind=SymbolKind.CLASS, start=0, end=300)
    method = cg.symbol(
        "run",
        kind=SymbolKind.METHOD,
        qualified_name="A.run",
        parent="A",
        start=10,
        end=220,
    )
    nested = cg.symbol(
        "local",
        kind=SymbolKind.FUNCTION,
        qualified_name="A.run.local",
        parent="A.run",
        start=30,
        end=150,
    )
    constant = cg.symbol(
        "VALUE",
        kind=SymbolKind.CONSTANT,
        qualified_name="A.run.local.VALUE",
        parent="A.run.local",
        start=50,
        end=60,
    )
    parsed = cg.parsed(symbols=(constant, nested, method, owner))

    index = build_graph_indexes(validate_inputs(*cg.pair(parsed)))

    identifiers = {
        symbol.qualified_name: symbol_node_id("repo", "a.py", symbol)
        for symbol in (owner, method, nested, constant)
    }
    file_id = file_node_id("repo", "a.py")
    assert index.children[file_id] == (identifiers["A"],)
    assert index.children[identifiers["A"]] == (identifiers["A.run"],)
    assert index.children[identifiers["A.run"]] == (
        identifiers["A.run.local"],
    )
    assert index.children[identifiers["A.run.local"]] == (
        identifiers["A.run.local.VALUE"],
    )
    assert len(structural_edges(index)) == 4


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(
            lambda symbol: replace(symbol, modifiers=("static",)),
            id="conflicting-modifiers",
        ),
        pytest.param(
            lambda symbol: replace(symbol, parent_qualified_name="Owner"),
            id="conflicting-parent",
        ),
    ],
)
def test_same_symbol_identity_with_conflicting_evidence_is_rejected(cg, change):
    symbol = cg.symbol("run", start=10, end=20)
    parsed = cg.parsed(symbols=(symbol, change(symbol)))

    with pytest.raises(
        CodeGraphIdentityError, match=r"Conflicting symbol evidence\."
    ):
        build_graph_indexes(validate_inputs(*cg.pair(parsed)))


def test_exact_duplicate_symbol_evidence_is_deduplicated(cg):
    symbol = cg.symbol("run", start=10, end=20)

    index = build_graph_indexes(
        validate_inputs(*cg.pair(cg.parsed(symbols=(symbol, symbol))))
    )

    assert len(index.nodes) == 2
    assert len(index.symbols_by_id) == 1
    assert len(structural_edges(index)) == 1


def test_python_module_and_java_package_collisions_are_preserved(cg):
    python_module = cg.parsed("pkg.py")
    python_package = cg.parsed("pkg/__init__.py")
    root_package = cg.parsed("__init__.py")
    first_type = cg.symbol(
        "First", kind=SymbolKind.CLASS, qualified_name="com.acme.First"
    )
    nested_type = cg.symbol(
        "Nested",
        kind=SymbolKind.CLASS,
        qualified_name="com.acme.First.Nested",
        parent="com.acme.First",
        start=10,
        end=20,
    )
    second_type = cg.symbol(
        "Second", kind=SymbolKind.INTERFACE, qualified_name="com.acme.Second"
    )
    java_first = cg.parsed(
        "unrelated/First.java",
        language=ParsedLanguage.JAVA,
        symbols=(nested_type, first_type),
    )
    java_second = cg.parsed(
        "elsewhere/Second.java",
        language=ParsedLanguage.JAVA,
        symbols=(second_type,),
    )

    index = build_graph_indexes(
        validate_inputs(
            *cg.pair(
                python_module,
                python_package,
                root_package,
                java_first,
                java_second,
            )
        )
    )

    assert index.python_modules == {
        "pkg": tuple(
            sorted(
                (
                    file_node_id("repo", "pkg.py"),
                    file_node_id("repo", "pkg/__init__.py"),
                )
            )
        )
    }
    assert index.java_packages == {
        "com.acme": tuple(
            sorted(
                (
                    file_node_id("repo", "unrelated/First.java"),
                    file_node_id("repo", "elsewhere/Second.java"),
                )
            )
        )
    }


def test_graph_indexes_are_read_only_and_plural_values_are_tuples(cg):
    symbol = cg.symbol("run")
    index = build_graph_indexes(
        validate_inputs(*cg.pair(cg.parsed(symbols=(symbol,))))
    )
    mappings = (
        index.nodes_by_id,
        index.files_by_path,
        index.file_ids,
        index.symbols_by_id,
        index.by_file_name,
        index.by_qualified_name,
        index.parent_ids,
        index.children,
        index.python_modules,
        index.java_packages,
    )

    for mapping in mappings:
        with pytest.raises(TypeError):
            mapping["new"] = "value"
    for mapping in (
        index.by_file_name,
        index.by_qualified_name,
        index.children,
        index.python_modules,
        index.java_packages,
    ):
        assert all(type(values) is tuple for values in mapping.values())


def test_structural_edge_ids_and_endpoints_match_the_resolved_parent(cg):
    parent = cg.symbol("A", kind=SymbolKind.CLASS, end=100)
    child = cg.symbol(
        "run",
        kind=SymbolKind.METHOD,
        qualified_name="A.run",
        parent="A",
        start=10,
        end=20,
    )
    index = build_graph_indexes(
        validate_inputs(*cg.pair(cg.parsed(symbols=(parent, child))))
    )

    edges = structural_edges(index)

    parent_id = symbol_node_id("repo", "a.py", parent)
    child_id = symbol_node_id("repo", "a.py", child)
    by_target = {edge.target_node_id: edge for edge in edges}
    assert by_target[parent_id].source_node_id == file_node_id("repo", "a.py")
    assert by_target[parent_id].edge_kind is GraphEdgeKind.DEFINES
    assert by_target[child_id].source_node_id == parent_id
    assert by_target[child_id].edge_kind is GraphEdgeKind.CONTAINS
