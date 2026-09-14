"""Private lookup indexes used while building a code graph."""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypeVar

from backend.code_parser.models import (
    ParsedFile,
    ParsedLanguage,
    SymbolInfo,
    SymbolKind,
)

from .exceptions import CodeGraphIdentityError
from .identities import file_node_id, symbol_node_id
from .models import GraphNode, GraphNodeKind
from .structure import _resolve_parent_ids
from .validation import ValidatedInputs


_K = TypeVar("_K")
_V = TypeVar("_V")
_JAVA_TYPE_KINDS = (SymbolKind.CLASS, SymbolKind.INTERFACE, SymbolKind.ENUM)


def _snapshot(mapping: Mapping[_K, _V]) -> Mapping[_K, _V]:
    return MappingProxyType(dict(mapping))


def _snapshot_plural(
    mapping: Mapping[_K, Iterable[str]],
) -> Mapping[_K, tuple[str, ...]]:
    return MappingProxyType(
        {key: tuple(values) for key, values in mapping.items()}
    )


@dataclass(frozen=True, slots=True)
class GraphIndexes:
    repository_namespace: str
    nodes: tuple[GraphNode, ...]
    nodes_by_id: Mapping[str, GraphNode]
    files_by_path: Mapping[str, ParsedFile]
    file_ids: Mapping[str, str]
    symbols_by_id: Mapping[str, SymbolInfo]
    by_file_name: Mapping[tuple[str, str], tuple[str, ...]]
    by_qualified_name: Mapping[str, tuple[str, ...]]
    parent_ids: Mapping[str, str | None]
    children: Mapping[str, tuple[str, ...]]
    python_modules: Mapping[str, tuple[str, ...]]
    java_packages: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", tuple(self.nodes))
        for field_name in (
            "nodes_by_id",
            "files_by_path",
            "file_ids",
            "symbols_by_id",
            "parent_ids",
        ):
            object.__setattr__(self, field_name, _snapshot(getattr(self, field_name)))
        for field_name in (
            "by_file_name",
            "by_qualified_name",
            "children",
            "python_modules",
            "java_packages",
        ):
            object.__setattr__(
                self,
                field_name,
                _snapshot_plural(getattr(self, field_name)),
            )


def _python_module_key(relative_path: str) -> str | None:
    key = relative_path[:-3].replace("/", ".")
    if key == "__init__":
        return None
    if key.endswith(".__init__"):
        key = key[: -len(".__init__")]
    return key or None


def _sorted_buckets(
    buckets: Mapping[_K, Iterable[str]],
) -> dict[_K, tuple[str, ...]]:
    return {
        key: tuple(sorted(values))
        for key, values in sorted(buckets.items(), key=lambda item: item[0])
    }


def build_graph_indexes(inputs: ValidatedInputs) -> GraphIndexes:
    """Project validated parser evidence into nodes and reusable indexes."""

    namespace = inputs.repository_namespace
    file_nodes: list[GraphNode] = []
    symbol_nodes: list[GraphNode] = []
    files_by_path: dict[str, ParsedFile] = {}
    file_ids: dict[str, str] = {}
    symbols_by_id: dict[str, SymbolInfo] = {}
    by_file_name: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
    by_qualified_name: defaultdict[str, list[str]] = defaultdict(list)
    python_modules: defaultdict[str, set[str]] = defaultdict(set)

    for _, parsed in inputs.pairs:
        path = parsed.relative_path
        node_id = file_node_id(namespace, path)
        file_ids[path] = node_id
        files_by_path[path] = parsed
        file_nodes.append(
            GraphNode(
                node_id,
                GraphNodeKind.FILE,
                namespace,
                path,
                parsed.language,
                parsed.status,
                None,
                None,
                None,
                None,
            )
        )
        if parsed.language is ParsedLanguage.PYTHON:
            module_key = _python_module_key(path)
            if module_key is not None:
                python_modules[module_key].add(node_id)

    for _, parsed in inputs.pairs:
        for symbol in parsed.symbols:
            node_key = symbol_node_id(namespace, parsed.relative_path, symbol)
            previous = symbols_by_id.get(node_key)
            if previous is not None and previous != symbol:
                raise CodeGraphIdentityError("Conflicting symbol evidence.")
            symbols_by_id[node_key] = symbol
            if previous is not None:
                continue

            node = GraphNode(
                node_key,
                GraphNodeKind.SYMBOL,
                namespace,
                parsed.relative_path,
                parsed.language,
                parsed.status,
                symbol.kind,
                symbol.name,
                symbol.qualified_name,
                symbol.location,
            )
            symbol_nodes.append(node)
            by_file_name[(parsed.relative_path, symbol.name)].append(node_key)
            by_qualified_name[symbol.qualified_name].append(node_key)

    nodes = tuple(
        (
            *sorted(file_nodes, key=lambda node: node.node_id),
            *sorted(symbol_nodes, key=lambda node: node.node_id),
        )
    )
    nodes_by_id = {node.node_id: node for node in nodes}
    parent_ids = _resolve_parent_ids(nodes_by_id, symbols_by_id)

    children: dict[str, list[str]] = {node_id: [] for node_id in nodes_by_id}
    java_packages: defaultdict[str, set[str]] = defaultdict(set)
    for symbol_id, parent_id in parent_ids.items():
        node = nodes_by_id[symbol_id]
        source_id = file_ids[node.relative_path] if parent_id is None else parent_id
        children[source_id].append(symbol_id)

        symbol = symbols_by_id[symbol_id]
        suffix = f".{symbol.name}"
        if (
            parent_id is None
            and node.language is ParsedLanguage.JAVA
            and symbol.kind in _JAVA_TYPE_KINDS
            and symbol.qualified_name.endswith(suffix)
        ):
            package = symbol.qualified_name[: -len(suffix)]
            if package:
                java_packages[package].add(file_ids[node.relative_path])

    return GraphIndexes(
        namespace,
        nodes,
        {key: nodes_by_id[key] for key in sorted(nodes_by_id)},
        {key: files_by_path[key] for key in sorted(files_by_path)},
        {key: file_ids[key] for key in sorted(file_ids)},
        {key: symbols_by_id[key] for key in sorted(symbols_by_id)},
        _sorted_buckets(by_file_name),
        _sorted_buckets(by_qualified_name),
        {key: parent_ids[key] for key in sorted(parent_ids)},
        _sorted_buckets(children),
        _sorted_buckets(python_modules),
        _sorted_buckets(java_packages),
    )
