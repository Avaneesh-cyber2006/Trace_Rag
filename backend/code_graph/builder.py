"""Public multi-pass code graph construction."""

from backend.code_parser.models import CodeParseInventory
from backend.file_scanner.models import FileInventory

from .exceptions import CodeGraphIdentityError
from .indexes import build_graph_indexes, finalize_graph
from .models import CodeGraphInventory, GraphEdge, GraphNodeKind, UnresolvedReference
from .references import (
    Reference,
    call_reference,
    import_reference,
    materialize,
    type_references,
)
from .resolvers.base import ImportEnvironment, Resolution
from .resolvers import load_resolvers, resolve_import, resolve_reference
from .structure import structural_edges
from .validation import validate_inputs


class CodeGraphBuilder:
    """Build a fresh immutable graph from scanner and parser inventories."""

    def build(
        self,
        file_inventory: FileInventory,
        parse_inventory: CodeParseInventory,
    ) -> CodeGraphInventory:
        inputs = validate_inputs(file_inventory, parse_inventory)
        index = build_graph_indexes(inputs)
        edges = list(structural_edges(index))
        load_resolvers()

        # Pass one resolves every import before any call or type is considered.
        by_file = {
            file.relative_path: tuple(
                (item, resolve_import(index, file, item)) for item in file.imports
            )
            for file in index.files_by_path.values()
        }
        environment = ImportEnvironment(by_file)
        outcomes: list[tuple[Reference, Resolution]] = []
        for file in index.files_by_path.values():
            for item, result in environment.by_file[file.relative_path]:
                outcomes.append((import_reference(index, file, item), result.resolution))

        # Pass two extracts all call/type references and resolves them against
        # the complete import environment.
        symbols_by_file: dict[str, list[str]] = {
            path: [] for path in index.files_by_path
        }
        for node_id, node in index.nodes_by_id.items():
            if node.node_kind is GraphNodeKind.SYMBOL:
                symbols_by_file[node.relative_path].append(node_id)
        for file in index.files_by_path.values():
            for item in file.calls:
                reference = call_reference(index, file, item)
                outcomes.append((reference, resolve_reference(index, environment, reference)))
            for node_id in symbols_by_file[file.relative_path]:
                for reference in type_references(index, file, node_id):
                    outcomes.append((reference, resolve_reference(index, environment, reference)))

        # Pass three publishes records only after all outcomes are collected.
        materialized = {}
        for reference, resolution in outcomes:
            record = materialize(index, reference, resolution)
            identity = (
                record.edge_id
                if isinstance(record, GraphEdge)
                else record.reference_id
            )
            previous = materialized.get(identity)
            if previous is not None and previous != record:
                raise CodeGraphIdentityError("Conflicting graph records share an identity.")
            materialized[identity] = record
        unresolved: list[UnresolvedReference] = []
        for record in materialized.values():
            if isinstance(record, GraphEdge):
                edges.append(record)
            else:
                unresolved.append(record)
        return finalize_graph(index, tuple(edges), tuple(unresolved))


def build_code_graph(
    file_inventory: FileInventory,
    parse_inventory: CodeParseInventory,
) -> CodeGraphInventory:
    return CodeGraphBuilder().build(file_inventory, parse_inventory)
