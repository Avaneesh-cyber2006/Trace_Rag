"""Structural graph, resolution outcome, and reference behavior."""

import sys
from dataclasses import FrozenInstanceError, replace

import pytest

from backend.code_graph.resolvers import base as resolver_base
from backend.code_graph.exceptions import (
    CodeGraphIdentityError,
    CodeGraphResolutionError,
    CodeGraphValidationError,
)
from backend.code_graph.identities import (
    edge_id,
    file_node_id,
    reference_id,
    symbol_node_id,
)
from backend.code_graph.indexes import build_graph_indexes
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
