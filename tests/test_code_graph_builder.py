"""Structural graph, resolution outcome, and reference behavior."""

import sys
from dataclasses import FrozenInstanceError, replace

import pytest

import backend.code_graph.builder as builder_module
from backend.code_graph import CodeGraphBuilder, build_code_graph
from backend.code_graph import resolvers as resolver_dispatch
from backend.code_graph.resolvers import ecmascript, java, python
from backend.code_graph.resolvers import base as resolver_base
from backend.code_graph.exceptions import (
    CodeGraphIdentityError,
    CodeGraphInvariantError,
    CodeGraphResolutionError,
    CodeGraphValidationError,
)
from backend.code_graph.identities import (
    edge_id,
    file_node_id,
    reference_id,
    symbol_node_id,
)
from backend.code_graph.indexes import build_graph_indexes, finalize_graph
from backend.code_graph.invariants import validate_graph
from backend.code_graph.models import (
    GraphEdge,
    GraphEdgeKind,
    GraphNodeKind,
    ReferenceKind,
    ResolutionFailureReason,
    UnresolvedReference,
)
from backend.code_graph.references import (
    Reference,
    call_reference,
    import_reference,
    materialize,
    type_references,
)
from backend.code_graph.resolvers.base import (
    Binding,
    ImportEnvironment,
    ImportResult,
    Resolution,
    choose_resolution,
    import_scope,
)
from backend.code_graph.structure import structural_edges
from backend.code_graph.validation import validate_inputs
from backend.code_parser.models import (
    CallKind,
    CallSite,
    ImportBinding,
    ImportInfo,
    ParsedLanguage,
    ParseIssue,
    ParseIssueKind,
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


def test_ambiguity_has_no_target_and_sorted_candidates():
    result = choose_resolution(("b" * 64, "a" * 64, "b" * 64))

    assert result.target_node_id is None
    assert result.reason is ResolutionFailureReason.AMBIGUOUS
    assert result.candidate_node_ids == ("a" * 64, "b" * 64)


@pytest.mark.parametrize(
    ("kwargs", "candidates", "target", "reason"),
    [
        pytest.param(
            {"unsupported": True, "incomplete": True, "external": True},
            ("a",),
            None,
            ResolutionFailureReason.UNSUPPORTED,
            id="unsupported-first",
        ),
        pytest.param(
            {"incomplete": True, "external": True},
            ("a",),
            None,
            ResolutionFailureReason.INCOMPLETE_PARSE,
            id="incomplete-second",
        ),
        pytest.param({}, ("a",), "a", None, id="one-target"),
        pytest.param(
            {"external": True},
            (),
            None,
            ResolutionFailureReason.EXTERNAL,
            id="external-empty",
        ),
        pytest.param(
            {}, (), None, ResolutionFailureReason.NOT_FOUND, id="not-found-empty"
        ),
    ],
)
def test_resolution_precedence_is_terminal(kwargs, candidates, target, reason):
    result = choose_resolution(candidates, **kwargs)

    assert result.target_node_id == target
    assert result.reason is reason
    assert result.candidate_node_ids == ()


@pytest.mark.parametrize(
    "outcome",
    [
        pytest.param((None, None, ()), id="neither-target-nor-reason"),
        pytest.param(
            ("a", ResolutionFailureReason.NOT_FOUND, ()), id="target-and-reason"
        ),
        pytest.param(
            (None, ResolutionFailureReason.NOT_FOUND, ("a",)),
            id="candidates-with-non-ambiguity",
        ),
    ],
)
def test_invalid_resolution_shapes_are_rejected(outcome):
    with pytest.raises(CodeGraphResolutionError):
        Resolution(*outcome)


def test_import_environment_snapshots_and_indexes_all_bindings(cg):
    wildcard = ImportInfo("pkg", (), True, (), cg.loc(1, 8))
    target = "a" * 64
    first = Binding("run", "scope", "symbol", Resolution(target, None))
    second = Binding(
        "run",
        "scope",
        "blocked",
        Resolution(None, ResolutionFailureReason.UNSUPPORTED),
    )
    bindings = [first, second]
    result = ImportResult(Resolution(target, None), bindings)
    entries = [(wildcard, result)]
    backing = {"a.py": entries}

    environment = ImportEnvironment(backing)
    bindings.clear()
    entries.clear()
    backing.clear()

    assert environment.by_file == {"a.py": ((wildcard, result),)}
    assert environment.bindings_by_scope_name == {
        ("scope", "run"): (first, second)
    }
    assert environment.wildcards_by_file == {"a.py": (wildcard,)}
    with pytest.raises(TypeError):
        environment.by_file["b.py"] = ()
    with pytest.raises(TypeError):
        environment.bindings_by_scope_name[("scope", "other")] = ()
    with pytest.raises(FrozenInstanceError):
        environment.by_file = {}


def test_import_scope_uses_indexed_unique_innermost_structural_owner(cg):
    owner = cg.symbol("Service", kind=SymbolKind.CLASS, start=0, end=300)
    method = cg.symbol(
        "run",
        kind=SymbolKind.METHOD,
        qualified_name="Service.run",
        parent="Service",
        start=40,
        end=220,
    )
    nested_item = ImportInfo("pkg", (), False, (), cg.loc(80, 90))
    top_level_item = ImportInfo("other", (), False, (), cg.loc(350, 360))
    file = cg.parsed(
        symbols=(owner, method), imports=(nested_item, top_level_item)
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    nested_scope, nested_reason = import_scope(index, file, nested_item)
    top_scope, top_reason = import_scope(index, file, top_level_item)

    assert nested_scope == symbol_node_id("repo", "a.py", method)
    assert nested_reason is None
    assert top_scope == index.file_ids["a.py"]
    assert top_reason is None
    with pytest.raises(TypeError):
        index.symbol_ranges_by_file["other.py"] = ()


def test_overlapping_incomparable_import_scopes_are_blocked_at_file(cg):
    first = cg.symbol("first", start=0, end=200)
    second = cg.symbol("second", start=50, end=250)
    item = ImportInfo("pkg", (), False, (), cg.loc(100, 110))
    file = cg.parsed(symbols=(first, second), imports=(item,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    scope_node_id, reason = import_scope(index, file, item)

    assert scope_node_id == index.file_ids["a.py"]
    assert reason is ResolutionFailureReason.AMBIGUOUS


def test_relevant_partial_parse_blocks_unproven_import_scope(cg):
    item = ImportInfo("pkg", (), False, (), cg.loc(100, 110))
    issue = ParseIssue(ParseIssueKind.MISSING_NODE, "missing", cg.loc(90, 120))
    file = cg.parsed(
        status=ParseStatus.PARTIAL,
        imports=(item,),
        issues=(issue,),
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    scope_node_id, reason = import_scope(index, file, item)

    assert scope_node_id == index.file_ids["a.py"]
    assert reason is ResolutionFailureReason.INCOMPLETE_PARSE


def test_adjacent_parse_issue_does_not_block_import_scope(cg):
    owner = cg.symbol("run", start=50, end=200)
    item = ImportInfo("pkg", (), False, (), cg.loc(100, 110))
    adjacent = ParseIssue(
        ParseIssueKind.SYNTAX_ERROR,
        "ends before import",
        cg.loc(90, 100),
    )
    file = cg.parsed(
        status=ParseStatus.PARTIAL,
        symbols=(owner,),
        imports=(item,),
        issues=(adjacent,),
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    scope_node_id, reason = import_scope(index, file, item)

    assert scope_node_id == symbol_node_id("repo", "a.py", owner)
    assert reason is None


def test_import_scope_query_work_is_bounded_by_index_and_candidates(cg):
    outer = cg.symbol("Outer", kind=SymbolKind.CLASS, end=10_000)
    siblings = tuple(
        cg.symbol(
            f"s{number}",
            qualified_name=f"Outer.s{number}",
            parent="Outer",
            start=10 + number * 10,
            end=15 + number * 10,
        )
        for number in range(256)
    )
    item = ImportInfo("pkg", (), False, (), cg.loc(9_000, 9_010))
    file = cg.parsed(symbols=(outer, *siblings), imports=(item,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))
    base_filename = resolver_base.__file__
    line_operations = 0

    def count_lines(frame, event, arg):
        nonlocal line_operations
        if event == "line" and frame.f_code.co_filename == base_filename:
            line_operations += 1
        return count_lines

    previous_trace = sys.gettrace()
    sys.settrace(count_lines)
    try:
        scope_node_id, reason = import_scope(index, file, item)
    finally:
        sys.settrace(previous_trace)

    assert scope_node_id == symbol_node_id("repo", "a.py", outer)
    assert reason is None
    assert line_operations < 200


def test_import_reference_uses_file_source_and_exact_module_text(cg):
    item = ImportInfo(".Pkg.Mixed", (), False, (), cg.loc(3, 14))
    file = cg.parsed("pkg/main.py", imports=(item,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    reference = import_reference(index, file, item)

    assert reference == Reference(
        ReferenceKind.IMPORT,
        "pkg/main.py",
        ParsedLanguage.PYTHON,
        index.file_ids["pkg/main.py"],
        item.location,
        ".Pkg.Mixed",
    )


def test_call_owner_requires_exact_name_file_and_containing_range(cg):
    first = cg.symbol("run", qualified_name="Service.run", start=0, end=90)
    second = cg.symbol("run", qualified_name="Service.run", start=100, end=220)
    call = CallSite(
        "Service.run", "Target.call", CallKind.CALL, cg.loc(130, 142)
    )
    file = cg.parsed(symbols=(second, first), calls=(call,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    reference = call_reference(index, file, call)

    assert reference.source_node_id == symbol_node_id("repo", "a.py", second)
    assert reference.raw_target == "Target.call"
    assert reference.call_kind is CallKind.CALL
    assert reference.blocked_reason is None


def test_overlapping_callable_owners_block_resolution_as_ambiguous(cg):
    first = cg.symbol("run", qualified_name="Service.run", start=0, end=200)
    second = cg.symbol("run", qualified_name="Service.run", start=50, end=250)
    call = CallSite("Service.run", "target", CallKind.CALL, cg.loc(100, 110))
    file = cg.parsed(symbols=(first, second), calls=(call,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    reference = call_reference(index, file, call)

    assert reference.source_node_id is None
    assert reference.blocked_reason is ResolutionFailureReason.AMBIGUOUS


@pytest.mark.parametrize(
    ("status", "symbols", "caller", "expected"),
    [
        pytest.param(
            ParseStatus.SUCCESS,
            (),
            None,
            ResolutionFailureReason.UNSUPPORTED,
            id="top-level",
        ),
        pytest.param(
            ParseStatus.SUCCESS,
            (),
            "missing",
            ResolutionFailureReason.UNSUPPORTED,
            id="missing-owner",
        ),
        pytest.param(
            ParseStatus.SUCCESS,
            ("non-callable",),
            "Service",
            ResolutionFailureReason.UNSUPPORTED,
            id="non-callable-owner",
        ),
        pytest.param(
            ParseStatus.PARTIAL,
            (),
            None,
            ResolutionFailureReason.INCOMPLETE_PARSE,
            id="partial-top-level",
        ),
        pytest.param(
            ParseStatus.PARTIAL,
            (),
            "missing",
            ResolutionFailureReason.INCOMPLETE_PARSE,
            id="partial-missing-owner",
        ),
    ],
)
def test_unproven_call_ownership_is_a_conservative_barrier(
    cg, status, symbols, caller, expected
):
    symbol_records = (
        (cg.symbol("Service", kind=SymbolKind.CLASS, end=200),)
        if symbols
        else ()
    )
    call = CallSite(caller, "target", CallKind.CALL, cg.loc(20, 30))
    issues = (
        (
            ParseIssue(
                ParseIssueKind.SYNTAX_ERROR, "missing owner", cg.loc(10, 40)
            ),
        )
        if status is ParseStatus.PARTIAL
        else ()
    )
    file = cg.parsed(
        status=status,
        symbols=symbol_records,
        calls=(call,),
        issues=issues,
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    reference = call_reference(index, file, call)

    assert reference.source_node_id is None
    assert reference.blocked_reason is expected


def test_adjacent_parse_issue_does_not_reclassify_top_level_call(cg):
    call = CallSite(None, "target", CallKind.CALL, cg.loc(100, 110))
    adjacent = ParseIssue(
        ParseIssueKind.SYNTAX_ERROR,
        "ends before call",
        cg.loc(90, 100),
    )
    file = cg.parsed(
        status=ParseStatus.PARTIAL,
        calls=(call,),
        issues=(adjacent,),
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    reference = call_reference(index, file, call)

    assert reference.source_node_id is None
    assert reference.blocked_reason is ResolutionFailureReason.UNSUPPORTED


def test_type_references_keep_declaring_source_location_and_exact_strings(cg):
    symbol = cg.symbol(
        "Child",
        kind=SymbolKind.CLASS,
        start=20,
        end=180,
        base_types=(" pkg.Base[T] ",),
        implemented_types=("IThing",),
    )
    file = cg.parsed(symbols=(symbol,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))
    symbol_id = symbol_node_id("repo", "a.py", symbol)

    references = type_references(index, file, symbol_id)

    assert [(ref.reference_kind, ref.raw_target) for ref in references] == [
        (ReferenceKind.INHERITS, " pkg.Base[T] "),
        (ReferenceKind.IMPLEMENTS, "IThing"),
    ]
    assert all(ref.source_node_id == symbol_id for ref in references)
    assert all(ref.location == symbol.location for ref in references)


def test_materialize_returns_exact_edge_for_one_proven_target(cg):
    source = cg.symbol("source", start=0, end=100)
    target = cg.symbol("target", start=200, end=300)
    file = cg.parsed(symbols=(source, target))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))
    source_id = symbol_node_id("repo", "a.py", source)
    target_id = symbol_node_id("repo", "a.py", target)
    location = cg.loc(30, 42)
    reference = Reference(
        ReferenceKind.CALL,
        "a.py",
        ParsedLanguage.PYTHON,
        source_id,
        location,
        "target()",
        CallKind.CALL,
    )

    result = materialize(index, reference, Resolution(target_id, None))

    assert result == GraphEdge(
        edge_id("repo", GraphEdgeKind.CALLS, source_id, target_id, location),
        GraphEdgeKind.CALLS,
        source_id,
        target_id,
        location,
    )


def test_materialize_uses_blocked_reason_before_successful_target(cg):
    target_file = cg.parsed("target.py")
    source_file = cg.parsed("source.py")
    index = build_graph_indexes(
        validate_inputs(*cg.pair(source_file, target_file))
    )
    location = cg.loc(4, 12)
    reference = Reference(
        ReferenceKind.CALL,
        "source.py",
        ParsedLanguage.PYTHON,
        None,
        location,
        "dynamic()",
        CallKind.CALL,
        ResolutionFailureReason.UNSUPPORTED,
    )

    result = materialize(
        index,
        reference,
        Resolution(index.file_ids["target.py"], None),
    )

    assert result == UnresolvedReference(
        reference_id(
            "repo",
            "source.py",
            ReferenceKind.CALL,
            None,
            location,
            "dynamic()",
        ),
        ReferenceKind.CALL,
        ResolutionFailureReason.UNSUPPORTED,
        "repo",
        "source.py",
        ParsedLanguage.PYTHON,
        None,
        location,
        "dynamic()",
        (),
    )


def test_ambiguous_outcome_materializes_only_unresolved_candidates(cg):
    source = cg.symbol("source", start=0, end=100)
    first = cg.symbol("first", start=200, end=250)
    second = cg.symbol("second", start=300, end=350)
    file = cg.parsed(symbols=(source, first, second))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))
    source_id = symbol_node_id("repo", "a.py", source)
    candidates = tuple(
        sorted(
            (
                symbol_node_id("repo", "a.py", first),
                symbol_node_id("repo", "a.py", second),
            )
        )
    )
    reference = Reference(
        ReferenceKind.CALL,
        "a.py",
        ParsedLanguage.PYTHON,
        source_id,
        cg.loc(20, 30),
        "maybe",
        CallKind.CALL,
    )

    result = materialize(
        index,
        reference,
        Resolution(None, ResolutionFailureReason.AMBIGUOUS, candidates),
    )

    assert isinstance(result, UnresolvedReference)
    assert result.reason is ResolutionFailureReason.AMBIGUOUS
    assert result.candidate_node_ids == candidates


def test_ambiguous_call_rejects_existing_ineligible_candidates(cg):
    source = cg.symbol("source", start=0, end=100)
    not_callable = cg.symbol(
        "Service", kind=SymbolKind.CLASS, start=200, end=300
    )
    file = cg.parsed(symbols=(source, not_callable))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))
    source_id = symbol_node_id("repo", "a.py", source)
    candidates = tuple(
        sorted(
            (
                index.file_ids["a.py"],
                symbol_node_id("repo", "a.py", not_callable),
            )
        )
    )
    reference = Reference(
        ReferenceKind.CALL,
        "a.py",
        ParsedLanguage.PYTHON,
        source_id,
        cg.loc(20, 30),
        "maybe",
        CallKind.CALL,
    )

    with pytest.raises(CodeGraphResolutionError):
        materialize(
            index,
            reference,
            Resolution(None, ResolutionFailureReason.AMBIGUOUS, candidates),
        )


def test_inheritance_materialization_uses_declaration_location(cg):
    base = cg.symbol("Base", kind=SymbolKind.CLASS, start=0, end=10)
    child = cg.symbol(
        "Child",
        kind=SymbolKind.CLASS,
        start=20,
        end=100,
        base_types=("Base",),
    )
    file = cg.parsed(symbols=(base, child))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))
    base_id = symbol_node_id("repo", "a.py", base)
    child_id = symbol_node_id("repo", "a.py", child)
    reference = type_references(index, file, child_id)[0]

    result = materialize(index, reference, Resolution(base_id, None))

    assert isinstance(result, GraphEdge)
    assert result.edge_kind is GraphEdgeKind.INHERITS
    assert result.location == child.location


@pytest.mark.parametrize(
    ("source", "target"),
    [
        pytest.param("file", "f" * 64, id="unknown-target"),
        pytest.param(None, "file", id="resolved-null-source"),
    ],
)
def test_materialize_rejects_invalid_resolver_outcomes(cg, source, target):
    file = cg.parsed()
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))
    source_id = index.file_ids["a.py"] if source == "file" else None
    target_id = index.file_ids["a.py"] if target == "file" else target
    reference = Reference(
        ReferenceKind.CALL,
        "a.py",
        ParsedLanguage.PYTHON,
        source_id,
        cg.loc(1, 2),
        "target",
        CallKind.CALL,
    )

    with pytest.raises(CodeGraphResolutionError):
        materialize(index, reference, Resolution(target_id, None))


def test_public_builder_produces_calls(cg):
    call = CallSite("run", "helper", CallKind.CALL, cg.loc(20, 25))
    file = cg.parsed(
        symbols=(cg.symbol("run"), cg.symbol("helper", start=200, end=250)),
        calls=(call,),
    )
    pair = cg.pair(file)

    graph = build_code_graph(*pair)

    assert graph == CodeGraphBuilder().build(*pair)
    assert sum(edge.edge_kind is GraphEdgeKind.CALLS for edge in graph.edges) == 1


def test_builder_publishes_canonical_indexes_for_isolated_and_duplicate_names(cg):
    first = cg.parsed(
        "z.py",
        symbols=(cg.symbol("same", qualified_name="shared"),),
    )
    second = cg.parsed(
        "a.py",
        symbols=(cg.symbol("same", qualified_name="shared"),),
    )
    isolated = cg.parsed("empty.py")

    graph = build_code_graph(*cg.pair(first, isolated, second))

    assert graph.nodes == tuple(sorted(graph.nodes, key=lambda node: node.node_id))
    assert graph.edges == tuple(sorted(graph.edges, key=lambda edge: edge.edge_id))
    assert tuple(graph.outgoing_edges) == tuple(sorted(graph.outgoing_edges))
    assert tuple(graph.incoming_edges) == tuple(sorted(graph.incoming_edges))
    assert tuple(graph.nodes_by_file) == ("a.py", "empty.py", "z.py")
    assert len(graph.nodes_by_file["empty.py"]) == 1
    assert graph.outgoing_edges[graph.nodes_by_file["empty.py"][0].node_id] == ()
    assert graph.incoming_edges[graph.nodes_by_file["empty.py"][0].node_id] == ()
    assert tuple(node.node_id for node in graph.nodes_by_qualified_name["shared"]) == tuple(
        sorted(node.node_id for node in graph.nodes_by_qualified_name["shared"])
    )
    assert len(graph.nodes_by_qualified_name["shared"]) == 2


def test_empty_graph_and_reused_builder_have_no_retained_state(cg):
    builder = CodeGraphBuilder()
    populated = builder.build(*cg.pair(cg.parsed(symbols=(cg.symbol("run"),))))
    empty = builder.build(*cg.pair())

    assert populated.nodes
    assert empty.repository_namespace == "repo"
    assert empty.nodes == ()
    assert empty.edges == ()
    assert empty.unresolved_references == ()
    assert dict(empty.outgoing_edges) == {}
    assert empty == build_code_graph(*cg.pair())


def test_builder_preserves_import_recursion_and_callable_cycles(cg):
    import_b = ImportInfo("b", (ImportBinding("b", None),), False, (), cg.loc(1, 2))
    import_a = ImportInfo("a", (ImportBinding("a", None),), False, (), cg.loc(1, 2))
    a = cg.symbol("a", start=10, end=90)
    b = cg.symbol("b", start=100, end=190)
    first = cg.parsed(
        "a.py",
        symbols=(a, b),
        imports=(import_b,),
        calls=(
            CallSite("a", "a", CallKind.CALL, cg.loc(20, 25)),
            CallSite("a", "b", CallKind.CALL, cg.loc(30, 35)),
            CallSite("b", "a", CallKind.CALL, cg.loc(120, 125)),
        ),
    )
    second = cg.parsed("b.py", imports=(import_a,))

    graph = build_code_graph(*cg.pair(first, second))

    assert sum(edge.edge_kind is GraphEdgeKind.IMPORTS for edge in graph.edges) == 2
    assert sum(edge.edge_kind is GraphEdgeKind.CALLS for edge in graph.edges) == 3


def test_builder_collects_every_import_outcome_before_resolving_calls(cg, monkeypatch):
    call = CallSite("run", "run", CallKind.CALL, cg.loc(20, 25))
    first = cg.parsed("a.py", symbols=(cg.symbol("run"),), calls=(call,))
    late_import = ImportInfo(
        "external", (ImportBinding("external", None),), False, (), cg.loc(1, 2)
    )
    second = cg.parsed("z.py", imports=(late_import,))
    original = resolver_dispatch.resolve_reference
    seen = []

    def checking_resolver(index, environment, reference):
        seen.append(tuple(environment.by_file))
        assert tuple(environment.by_file) == ("a.py", "z.py")
        assert environment.by_file["z.py"][0][0] is late_import
        return original(index, environment, reference)

    monkeypatch.setattr(builder_module, "resolve_reference", checking_resolver)

    build_code_graph(*cg.pair(first, second))

    assert seen == [("a.py", "z.py")]


def test_builder_deduplicates_identical_records_by_identity(cg):
    duplicate = CallSite("run", "run", CallKind.CALL, cg.loc(20, 25))
    file = cg.parsed(
        symbols=(cg.symbol("run"),),
        calls=(duplicate, duplicate),
    )

    graph = build_code_graph(*cg.pair(file))

    call_edges = [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.CALLS]
    assert len(call_edges) == 1


def test_builder_rejects_conflicting_same_identity_records(cg, monkeypatch):
    duplicate = CallSite("run", "run", CallKind.CALL, cg.loc(20, 25))
    file = cg.parsed(
        symbols=(cg.symbol("run"), cg.symbol("other", start=200, end=300)),
        calls=(duplicate, duplicate),
    )
    calls = 0

    def conflicting_materialize(index, reference, resolution):
        nonlocal calls
        calls += 1
        ids = tuple(
            node.node_id for node in index.nodes if node.node_kind is GraphNodeKind.SYMBOL
        )
        return GraphEdge("f" * 64, GraphEdgeKind.CALLS, ids[0], ids[calls - 1], reference.location)

    monkeypatch.setattr(builder_module, "materialize", conflicting_materialize)

    with pytest.raises(CodeGraphIdentityError):
        build_code_graph(*cg.pair(file))


def test_builder_does_not_swallow_graph_faults(cg, monkeypatch):
    file = cg.parsed(
        symbols=(cg.symbol("run"),),
        calls=(CallSite("run", "run", CallKind.CALL, cg.loc(20, 25)),),
    )

    def broken_resolver(index, environment, reference):
        raise CodeGraphResolutionError("resolver contract broke")

    monkeypatch.setattr(builder_module, "resolve_reference", broken_resolver)

    with pytest.raises(CodeGraphResolutionError, match="resolver contract broke"):
        build_code_graph(*cg.pair(file))


@pytest.mark.parametrize(
    ("language", "module"),
    [
        (ParsedLanguage.PYTHON, python),
        (ParsedLanguage.JAVA, java),
        (ParsedLanguage.JAVASCRIPT, ecmascript),
        (ParsedLanguage.TYPESCRIPT, ecmascript),
        (ParsedLanguage.TSX, ecmascript),
    ],
)
def test_dispatchers_use_the_static_language_module(language, module, cg, monkeypatch):
    suffix = {
        ParsedLanguage.PYTHON: ".py",
        ParsedLanguage.JAVA: ".java",
        ParsedLanguage.JAVASCRIPT: ".js",
        ParsedLanguage.TYPESCRIPT: ".ts",
        ParsedLanguage.TSX: ".tsx",
    }[language]
    file = cg.parsed("a" + suffix, language=language)
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))
    item = ImportInfo("target", (), False, (), cg.loc(1, 2))
    import_result = ImportResult(Resolution(None, ResolutionFailureReason.EXTERNAL), ())
    reference_result = Resolution(None, ResolutionFailureReason.NOT_FOUND)
    reference = Reference(
        ReferenceKind.CALL,
        file.relative_path,
        language,
        None,
        cg.loc(3, 4),
        "target",
        CallKind.CALL,
    )
    monkeypatch.setattr(module, "resolve_import", lambda *_: import_result)
    monkeypatch.setattr(module, "resolve_reference", lambda *_: reference_result)

    assert resolver_dispatch.resolve_import(index, file, item) is import_result
    assert resolver_dispatch.resolve_reference(
        index, ImportEnvironment({file.relative_path: ()}), reference
    ) is reference_result


def _invariant_fixture(cg):
    owner = cg.symbol("Owner", kind=SymbolKind.CLASS, end=300)
    method = cg.symbol(
        "run",
        kind=SymbolKind.METHOD,
        qualified_name="Owner.run",
        parent="Owner",
        start=20,
        end=100,
    )
    helper = cg.symbol("helper", start=400, end=500)
    first = cg.parsed(
        "a.py",
        symbols=(owner, method, helper),
        calls=(CallSite("Owner.run", "missing", CallKind.CALL, cg.loc(30, 35)),),
    )
    other = cg.parsed("b.py", symbols=(cg.symbol("other"),))
    inputs = validate_inputs(*cg.pair(first, other))
    index = build_graph_indexes(inputs)
    graph = build_code_graph(*cg.pair(first, other))
    return graph, index


def test_validate_graph_rejects_duplicate_identity_and_projection_corruption(cg):
    graph, index = _invariant_fixture(cg)
    symbol = next(node for node in graph.nodes if node.node_kind is GraphNodeKind.SYMBOL)
    edge = graph.edges[0]
    cases = (
        replace(graph, nodes=graph.nodes + (symbol,)),
        replace(graph, edges=graph.edges + (edge,)),
        replace(graph, repository_namespace="other"),
        replace(
            graph,
            nodes=tuple(
                replace(node, name="fabricated") if node.node_id == symbol.node_id else node
                for node in graph.nodes
            ),
        ),
    )

    for malformed in cases:
        with pytest.raises(CodeGraphInvariantError):
            validate_graph(malformed, index)


def test_validate_graph_rejects_dangling_and_invalid_reference_endpoints(cg):
    graph, index = _invariant_fixture(cg)
    call = next(item for item in graph.unresolved_references if item.reference_kind is ReferenceKind.CALL)
    callable_node = next(
        node for node in graph.nodes if node.symbol_kind is SymbolKind.FUNCTION
    )
    class_node = next(node for node in graph.nodes if node.symbol_kind is SymbolKind.CLASS)
    bad_source = replace(
        graph.edges[0],
        edge_id=edge_id("repo", GraphEdgeKind.DEFINES, "f" * 64, graph.edges[0].target_node_id, None),
        source_node_id="f" * 64,
    )
    bad_target = replace(
        graph.edges[0],
        edge_id=edge_id("repo", GraphEdgeKind.DEFINES, graph.edges[0].source_node_id, "e" * 64, None),
        target_node_id="e" * 64,
    )
    class_call = GraphEdge(
        edge_id("repo", GraphEdgeKind.CALLS, class_node.node_id, callable_node.node_id, cg.loc(1, 2)),
        GraphEdgeKind.CALLS,
        class_node.node_id,
        callable_node.node_id,
        cg.loc(1, 2),
    )
    cases = (
        replace(graph, edges=(bad_source, *graph.edges[1:])),
        replace(graph, edges=(bad_target, *graph.edges[1:])),
        replace(
            graph,
            unresolved_references=tuple(
                replace(item, reason=ResolutionFailureReason.AMBIGUOUS, candidate_node_ids=("d" * 64,))
                if item.reference_id == call.reference_id
                else item
                for item in graph.unresolved_references
            ),
        ),
        replace(graph, edges=graph.edges + (class_call,)),
    )

    for malformed in cases:
        with pytest.raises(CodeGraphInvariantError):
            validate_graph(malformed, index)


def test_validate_graph_rejects_structural_parent_mismatch_and_cycle(cg):
    graph, index = _invariant_fixture(cg)
    structural = tuple(
        edge for edge in graph.edges if edge.edge_kind in (GraphEdgeKind.DEFINES, GraphEdgeKind.CONTAINS)
    )
    nonstructural = tuple(edge for edge in graph.edges if edge not in structural)
    symbols = [node for node in graph.nodes if node.node_kind is GraphNodeKind.SYMBOL]
    cycle_edges = tuple(
        GraphEdge(
            edge_id("repo", GraphEdgeKind.CONTAINS, source.node_id, target.node_id, None),
            GraphEdgeKind.CONTAINS,
            source.node_id,
            target.node_id,
            None,
        )
        for source, target in ((symbols[0], symbols[1]), (symbols[1], symbols[0]))
    )
    extra = GraphEdge(
        edge_id("repo", GraphEdgeKind.CONTAINS, symbols[0].node_id, symbols[-1].node_id, None),
        GraphEdgeKind.CONTAINS,
        symbols[0].node_id,
        symbols[-1].node_id,
        None,
    )
    cases = (
        replace(graph, edges=graph.edges[1:]),
        replace(graph, edges=graph.edges + (extra,)),
        replace(graph, edges=nonstructural + cycle_edges),
    )

    for malformed in cases:
        with pytest.raises(CodeGraphInvariantError):
            validate_graph(malformed, index)


def test_validate_graph_rejects_incorrect_indexes_and_cross_file_source(cg):
    graph, index = _invariant_fixture(cg)
    call = next(item for item in graph.unresolved_references if item.reference_kind is ReferenceKind.CALL)
    other = next(
        node
        for node in graph.nodes
        if node.relative_path == "b.py" and node.node_kind is GraphNodeKind.SYMBOL
    )
    moved = replace(
        call,
        reference_id=reference_id(
            "repo", call.relative_path, call.reference_kind, other.node_id, call.location, call.raw_target
        ),
        source_node_id=other.node_id,
    )
    bad_outgoing = dict(graph.outgoing_edges)
    bad_outgoing.pop(next(iter(bad_outgoing)))

    with pytest.raises(CodeGraphInvariantError):
        validate_graph(replace(graph, outgoing_edges=bad_outgoing), index)
    with pytest.raises(CodeGraphInvariantError):
        validate_graph(
            replace(
                graph,
                unresolved_references=tuple(
                    moved if item.reference_id == call.reference_id else item
                    for item in graph.unresolved_references
                ),
            ),
            index,
        )
