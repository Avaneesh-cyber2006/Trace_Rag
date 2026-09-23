"""Contract tests for immutable code graph values."""

from dataclasses import FrozenInstanceError, fields
from enum import Enum
from types import MappingProxyType

import pytest

from backend.code_graph import (
    CodeGraphError,
    CodeGraphIdentityError,
    CodeGraphInvariantError,
    CodeGraphResolutionError,
    CodeGraphValidationError,
    CodeGraphInventory,
    GraphEdge,
    GraphEdgeKind,
    GraphNode,
    GraphNodeKind,
    ReferenceKind,
    ResolutionFailureReason,
    UnresolvedReference,
)
from backend.code_parser.models import ParsedLanguage, ParseStatus, SymbolKind


@pytest.mark.parametrize(
    ("enum_type", "expected"),
    [
        (GraphNodeKind, {"FILE": "file", "SYMBOL": "symbol"}),
        (
            GraphEdgeKind,
            {
                "DEFINES": "defines",
                "CONTAINS": "contains",
                "IMPORTS": "imports",
                "CALLS": "calls",
                "INHERITS": "inherits",
                "IMPLEMENTS": "implements",
            },
        ),
        (
            ReferenceKind,
            {
                "IMPORT": "import",
                "CALL": "call",
                "INHERITS": "inherits",
                "IMPLEMENTS": "implements",
            },
        ),
        (
            ResolutionFailureReason,
            {
                "EXTERNAL": "external",
                "NOT_FOUND": "not_found",
                "AMBIGUOUS": "ambiguous",
                "UNSUPPORTED": "unsupported",
                "INCOMPLETE_PARSE": "incomplete_parse",
            },
        ),
    ],
)
def test_graph_enums_have_only_the_public_lowercase_values(enum_type, expected):
    assert issubclass(enum_type, str)
    assert issubclass(enum_type, Enum)
    assert {member.name: member.value for member in enum_type} == expected


def test_file_node_is_frozen_and_has_no_symbol_fields():
    node = GraphNode(
        "a" * 64,
        GraphNodeKind.FILE,
        "repo",
        "a.py",
        ParsedLanguage.PYTHON,
        ParseStatus.SUCCESS,
        None,
        None,
        None,
        None,
    )

    assert not hasattr(node, "__dict__")
    assert (node.symbol_kind, node.name, node.qualified_name, node.location) == (
        None,
    ) * 4
    with pytest.raises(FrozenInstanceError):
        node.relative_path = "b.py"
    assert [field.name for field in fields(GraphNode)] == [
        "node_id",
        "node_kind",
        "repository_namespace",
        "relative_path",
        "language",
        "parse_status",
        "symbol_kind",
        "name",
        "qualified_name",
        "location",
    ]


def test_edge_and_unresolved_reference_are_exact_frozen_slotted_records(cg):
    location = cg.loc(4, 12)
    edge = GraphEdge("b" * 64, GraphEdgeKind.CALLS, "source", "target", location)
    unresolved = UnresolvedReference(
        "c" * 64,
        ReferenceKind.CALL,
        ResolutionFailureReason.AMBIGUOUS,
        "repo",
        "a.py",
        ParsedLanguage.PYTHON,
        "source",
        location,
        "run",
        ("candidate-b", "candidate-a"),
    )

    assert not hasattr(edge, "__dict__")
    assert not hasattr(unresolved, "__dict__")
    assert [field.name for field in fields(GraphEdge)] == [
        "edge_id",
        "edge_kind",
        "source_node_id",
        "target_node_id",
        "location",
    ]
    assert [field.name for field in fields(UnresolvedReference)] == [
        "reference_id",
        "reference_kind",
        "reason",
        "repository_namespace",
        "relative_path",
        "language",
        "source_node_id",
        "location",
        "raw_target",
        "candidate_node_ids",
    ]
    with pytest.raises(FrozenInstanceError):
        edge.location = None
    with pytest.raises(FrozenInstanceError):
        unresolved.raw_target = "other"


def test_unresolved_reference_requires_tuple_candidate_ids(cg):
    with pytest.raises(CodeGraphInvariantError):
        UnresolvedReference(
            "c" * 64,
            ReferenceKind.CALL,
            ResolutionFailureReason.AMBIGUOUS,
            "repo",
            "a.py",
            ParsedLanguage.PYTHON,
            "source",
            cg.loc(),
            "run",
            ["candidate"],
        )


def test_inventory_requires_canonical_collections_to_be_tuples(cg):
    node = GraphNode(
        "a" * 64,
        GraphNodeKind.SYMBOL,
        "repo",
        "a.py",
        ParsedLanguage.PYTHON,
        ParseStatus.SUCCESS,
        SymbolKind.FUNCTION,
        "run",
        "run",
        cg.loc(),
    )

    with pytest.raises(CodeGraphInvariantError):
        CodeGraphInventory("repo", [node], (), (), {}, {}, {}, {})
    with pytest.raises(CodeGraphInvariantError):
        CodeGraphInventory("repo", (), [], (), {}, {}, {}, {})
    with pytest.raises(CodeGraphInvariantError):
        CodeGraphInventory("repo", (), (), [], {}, {}, {}, {})


def test_inventory_detaches_and_freezes_nested_mapping_values(cg):
    node = GraphNode(
        "a" * 64,
        GraphNodeKind.FILE,
        "repo",
        "a.py",
        ParsedLanguage.PYTHON,
        ParseStatus.SUCCESS,
        None,
        None,
        None,
        None,
    )
    edge = GraphEdge(
        "b" * 64,
        GraphEdgeKind.IMPORTS,
        node.node_id,
        node.node_id,
        cg.loc(),
    )
    outgoing_values = [edge]
    outgoing = {node.node_id: outgoing_values}
    incoming = {node.node_id: (edge,)}
    by_file = {node.relative_path: [node]}
    by_name = {"run": ()}

    inventory = CodeGraphInventory(
        "repo",
        (node,),
        (edge,),
        (),
        outgoing,
        incoming,
        by_file,
        by_name,
    )
    outgoing_values.append(edge)
    outgoing[node.node_id] = ()
    incoming.clear()
    by_file[node.relative_path].append(node)
    by_name["run"] = (node,)

    assert not hasattr(inventory, "__dict__")
    assert [field.name for field in fields(CodeGraphInventory)] == [
        "repository_namespace",
        "nodes",
        "edges",
        "unresolved_references",
        "outgoing_edges",
        "incoming_edges",
        "nodes_by_file",
        "nodes_by_qualified_name",
    ]
    assert isinstance(inventory.outgoing_edges, MappingProxyType)
    assert inventory.outgoing_edges[node.node_id] == (edge,)
    assert inventory.incoming_edges[node.node_id] == (edge,)
    assert inventory.nodes_by_file[node.relative_path] == (node,)
    assert inventory.nodes_by_qualified_name["run"] == ()
    with pytest.raises(TypeError):
        inventory.outgoing_edges[node.node_id] = ()
    with pytest.raises(FrozenInstanceError):
        inventory.nodes = ()


@pytest.mark.parametrize(
    ("mapping_index", "bad_mapping"),
    [
        (0, {"node": "not-a-sequence"}),
        (1, {"node": 1}),
        (2, {"a.py": None}),
        (3, {"run": object()}),
    ],
)
def test_inventory_rejects_non_iterable_or_wrong_shaped_mapping_values(
    mapping_index, bad_mapping
):
    mappings = [{}, {}, {}, {}]
    mappings[mapping_index] = bad_mapping

    with pytest.raises(CodeGraphInvariantError):
        CodeGraphInventory("repo", (), (), (), *mappings)


@pytest.mark.parametrize(
    "exception_type",
    [
        CodeGraphValidationError,
        CodeGraphIdentityError,
        CodeGraphInvariantError,
        CodeGraphResolutionError,
    ],
)
def test_specialized_errors_are_direct_code_graph_error_subclasses(exception_type):
    assert exception_type.__bases__ == (CodeGraphError,)


def test_inventory_equality_depends_only_on_canonical_values(cg):
    node = GraphNode(
        "a" * 64,
        GraphNodeKind.FILE,
        "repo",
        "a.py",
        ParsedLanguage.PYTHON,
        ParseStatus.SUCCESS,
        None,
        None,
        None,
        None,
    )
    first_backing = {node.node_id: []}
    second_backing = {node.node_id: ()}

    first = CodeGraphInventory(
        "repo", (node,), (), (), first_backing, first_backing, {"a.py": (node,)}, {}
    )
    second = CodeGraphInventory(
        "repo", (node,), (), (), second_backing, second_backing, {"a.py": [node]}, {}
    )
    first_backing.clear()

    assert first == second
