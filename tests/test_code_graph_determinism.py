from dataclasses import replace

import pytest

import backend.code_graph.builder as builder_module
from backend.code_graph import CodeGraphIdentityError, build_code_graph
from backend.code_graph.identities import file_node_id, reference_id, symbol_node_id
from backend.code_graph.models import GraphEdgeKind, ReferenceKind, ResolutionFailureReason
from backend.code_graph.resolvers.base import ImportResult, Resolution
from backend.code_parser.models import CallKind, CallSite, ImportInfo, ParameterInfo, ParsedLanguage, SourceLocation, SymbolKind


def test_permuted_inventory_and_symbols_are_equal(cg):
    imports = (
        ImportInfo("pkg.alpha", (), False, (), cg.loc(10, 14)),
        ImportInfo("pkg.beta", (), False, (), cg.loc(20, 24)),
    )
    calls = (
        CallSite("x.run", "alpha", CallKind.CALL, cg.loc(110, 115)),
        CallSite("x.run", "beta", CallKind.CALL, cg.loc(120, 124)),
    )
    symbols = (
        cg.symbol("x", kind=SymbolKind.CLASS, start=90, end=180, base_types=("Base", "Mixin")),
        cg.symbol("run", kind=SymbolKind.METHOD, qualified_name="x.run", parent="x", start=100, end=170),
        cg.symbol("y", start=200, end=250),
    )
    a = cg.parsed("a.py", symbols=symbols, imports=imports, calls=calls)
    b = cg.parsed("b.py")

    first = build_code_graph(*cg.pair(a, b))
    shuffled_a = replace(
        a,
        symbols=tuple(
            replace(symbol, base_types=tuple(reversed(symbol.base_types)))
            for symbol in reversed(a.symbols)
        ),
        imports=tuple(reversed(a.imports)),
        calls=tuple(reversed(a.calls)),
    )
    second = build_code_graph(*cg.pair(b, shuffled_a))

    assert first == second
    assert tuple(first.nodes_by_file) == tuple(second.nodes_by_file)


def test_identical_duplicate_symbols_imports_and_calls_collapse(cg):
    symbol = cg.symbol("caller", start=0, end=100)
    imported = ImportInfo("missing.module", (), False, (), cg.loc(10, 15))
    call = CallSite("caller", "missing", CallKind.CALL, cg.loc(20, 25))
    unique = cg.parsed(symbols=(symbol,), imports=(imported,), calls=(call,))
    repeated = replace(
        unique,
        symbols=(symbol, symbol),
        imports=(imported, imported),
        calls=(call, call),
    )

    expected = build_code_graph(*cg.pair(unique))
    actual = build_code_graph(*cg.pair(repeated))

    assert actual == expected
    assert len(actual.unresolved_references) == 2


@pytest.mark.parametrize(
    "mutate",
    [
        lambda symbol: replace(symbol, modifiers=("public",)),
        lambda symbol: replace(symbol, base_types=("Base",)),
        lambda symbol: replace(symbol, implemented_types=("Runnable",)),
        lambda symbol: replace(symbol, parent_qualified_name="Parent"),
        lambda symbol: replace(
            symbol,
            parameters=(ParameterInfo("value", "int", None),),
        ),
        lambda symbol: replace(
            symbol,
            location=SourceLocation(0, 100, 2, 0, 2, 100),
        ),
    ],
    ids=("modifiers", "base-types", "implemented-types", "parent", "parameters", "full-location"),
)
def test_same_symbol_identity_with_different_full_evidence_is_rejected(cg, mutate):
    original = cg.symbol("run", kind=SymbolKind.METHOD, qualified_name="Service.run")
    conflict = mutate(original)

    with pytest.raises(CodeGraphIdentityError, match="Conflicting symbol evidence"):
        build_code_graph(*cg.pair(cg.parsed(symbols=(original, conflict))))


@pytest.mark.parametrize(
    "resolutions",
    [
        (
            Resolution(None, ResolutionFailureReason.NOT_FOUND),
            Resolution(None, ResolutionFailureReason.EXTERNAL),
        ),
        (
            Resolution(
                None,
                ResolutionFailureReason.AMBIGUOUS,
                (file_node_id("repo", "b.py"),),
            ),
            Resolution(
                None,
                ResolutionFailureReason.AMBIGUOUS,
                (file_node_id("repo", "c.py"),),
            ),
        ),
    ],
    ids=("different-reason", "different-candidates"),
)
def test_same_unresolved_identity_with_different_outcome_is_rejected(
    cg, monkeypatch, resolutions
):
    imported = ImportInfo("external.module", (), False, (), cg.loc(10, 20))
    a = cg.parsed("a.py", imports=(imported, imported))
    b = cg.parsed("b.py")
    c = cg.parsed("c.py")
    results = iter(resolutions)
    monkeypatch.setattr(
        builder_module,
        "resolve_import",
        lambda index, file, item: ImportResult(next(results), ()),
    )

    with pytest.raises(CodeGraphIdentityError, match="Conflicting graph records"):
        build_code_graph(*cg.pair(a, b, c))


def test_reference_identity_does_not_include_resolution_reason(cg):
    location = cg.loc(10, 20)
    unresolved_id = reference_id(
        "repo", "a.py", ReferenceKind.IMPORT, None, location, "external.module"
    )
    repeated_id = reference_id(
        "repo", "a.py", ReferenceKind.IMPORT, None, location, "external.module"
    )

    assert unresolved_id == repeated_id


def test_different_source_sites_have_distinct_edge_ids(cg):
    caller = cg.symbol("caller", start=0, end=100)
    target = cg.symbol("target", start=110, end=150)
    calls = (
        CallSite("caller", "target", CallKind.CALL, cg.loc(10, 16)),
        CallSite("caller", "target", CallKind.CALL, cg.loc(30, 36)),
    )
    graph = build_code_graph(*cg.pair(cg.parsed("a.py", symbols=(caller, target), calls=calls)))

    call_edges = tuple(edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.CALLS)
    assert len(call_edges) == 2
    assert len({edge.edge_id for edge in call_edges}) == 2


def test_changing_repository_path_does_not_change_ids_but_namespace_does(cg):
    parsed = cg.parsed("Módül.py", symbols=(cg.symbol("Straße"),))
    file_inventory, parse_inventory = cg.pair(parsed, namespace="repo")
    original = build_code_graph(file_inventory, parse_inventory)
    changed_paths = build_code_graph(
        replace(file_inventory, repository_path="/another/scanner/root"),
        replace(parse_inventory, repository_path="/another/parser/root"),
    )
    other_namespace = build_code_graph(*cg.pair(parsed, namespace="repo-2"))

    assert original == changed_paths
    assert tuple(node.node_id for node in original.nodes) != tuple(
        node.node_id for node in other_namespace.nodes
    )




def test_case_distinct_unicode_paths_and_names_remain_distinct(cg):
    upper = cg.parsed("Módül.py", symbols=(cg.symbol("Straße"),))
    lower = cg.parsed("módül.py", symbols=(cg.symbol("straße"),))
    graph = build_code_graph(*cg.pair(upper, lower))

    assert len(graph.nodes_by_file) == 2
    assert tuple(graph.nodes_by_file) == ("Módül.py", "módül.py")
    assert len({node.node_id for node in graph.nodes}) == len(graph.nodes)
    assert {node.name for node in graph.nodes if node.name is not None} == {"Straße", "straße"}


def test_ambiguous_duplicate_qualified_names_are_permutation_stable(cg):
    caller = cg.symbol("caller", start=0, end=200)
    first_target = cg.symbol("target", start=220, end=260)
    second_target = cg.symbol("target", start=280, end=320)
    call = CallSite("caller", "target", CallKind.CALL, cg.loc(20, 26))
    original = cg.parsed(
        symbols=(caller, first_target, second_target), calls=(call,)
    )
    permuted = replace(original, symbols=(second_target, caller, first_target))

    graph = build_code_graph(*cg.pair(original))
    reordered_graph = build_code_graph(*cg.pair(permuted))
    unresolved = tuple(
        item for item in graph.unresolved_references
        if item.reference_kind.value == "call"
    )

    assert graph == reordered_graph
    assert len(unresolved) == 1
    assert unresolved[0].reason is ResolutionFailureReason.AMBIGUOUS
    assert unresolved[0].candidate_node_ids == tuple(sorted((
        symbol_node_id("repo", "a.py", first_target),
        symbol_node_id("repo", "a.py", second_target),
    )))
    assert not any(edge.edge_kind is GraphEdgeKind.CALLS for edge in graph.edges)


def test_wildcard_ambiguity_permutations_keep_candidate_ids(cg):
    from backend.code_parser.models import ParsedLanguage

    imported = ImportInfo("p", (), True, (), cg.loc(1, 10))
    source = cg.parsed("Main.java", language=ParsedLanguage.JAVA, imports=(imported,))
    alpha = cg.parsed(
        "alpha.java",
        language=ParsedLanguage.JAVA,
        symbols=(cg.symbol("A", kind=SymbolKind.CLASS, qualified_name="p.A"),),
    )
    beta = cg.parsed(
        "beta.java",
        language=ParsedLanguage.JAVA,
        symbols=(cg.symbol("B", kind=SymbolKind.CLASS, qualified_name="p.B"),),
    )

    graph = build_code_graph(*cg.pair(source, alpha, beta))
    permuted = build_code_graph(*cg.pair(beta, source, alpha))
    unresolved = graph.unresolved_references

    assert graph == permuted
    assert len(unresolved) == 1
    assert unresolved[0].reason is ResolutionFailureReason.AMBIGUOUS
    assert unresolved[0].candidate_node_ids == tuple(sorted((
        file_node_id("repo", "alpha.java"),
        file_node_id("repo", "beta.java"),
    )))
    assert not any(edge.edge_kind is GraphEdgeKind.IMPORTS for edge in graph.edges)


def test_graph_indexes_snapshot_mutable_mapping_inputs(cg):
    from dataclasses import replace as dataclass_replace

    from backend.code_graph.indexes import build_graph_indexes
    from backend.code_graph.validation import validate_inputs

    parsed = cg.parsed("a.py", symbols=(cg.symbol("value"),))
    index = build_graph_indexes(validate_inputs(*cg.pair(parsed)))
    nodes = dict(index.nodes_by_id)
    names = {key: list(value) for key, value in index.by_qualified_name.items()}
    snapshot = dataclass_replace(index, nodes_by_id=nodes, by_qualified_name=names)
    expected_node_ids = tuple(snapshot.nodes_by_id)
    expected_name_ids = {
        key: tuple(value) for key, value in snapshot.by_qualified_name.items()
    }

    nodes.clear()
    names.clear()

    assert tuple(snapshot.nodes_by_id) == expected_node_ids
    assert dict(snapshot.by_qualified_name) == expected_name_ids
