"""Tree-sitter-backed Java structure extraction."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tree_sitter import Node, Tree

from backend.code_parser.models import (
    CallKind,
    CallSite,
    ImportInfo,
    ParameterInfo,
    ParseIssue,
    ParseIssueKind,
    SymbolInfo,
    SymbolKind,
)
from backend.code_parser.reader import SourceBuffer

from .base import (
    ENTER,
    ExtractionResult,
    bounded_node_text,
    iter_events,
    normalize_extraction,
    source_location,
)


_BOUNDED_TEXT_MESSAGE = "Extracted text exceeded the 1,000-byte limit."
_TYPE_DECLARATIONS = {
    "class_declaration": SymbolKind.CLASS,
    "interface_declaration": SymbolKind.INTERFACE,
    "enum_declaration": SymbolKind.ENUM,
}
_TYPE_BODIES = {"class_body", "interface_body", "enum_body"}
_QUALIFIED_NAME_NODES = {"identifier", "scoped_identifier"}
_REFERENCE_TYPE_NODES = {
    "annotated_type",
    "array_type",
    "generic_type",
    "scoped_type_identifier",
    "type_identifier",
}
_ASCII_WHITESPACE = b" \t\n\r\f\v"
_ELLIPSIS = "…"
_ELLIPSIS_BYTES = _ELLIPSIS.encode("utf-8")
_MAX_TEXT_BYTES = 1000


@dataclass(frozen=True, slots=True)
class _LexicalScope:
    qualified_name: str
    kind: str


def _modifiers(node: Node, capture: Callable[[Node], str]) -> tuple[str, ...]:
    modifiers = next(
        (child for child in node.named_children if child.type == "modifiers"),
        None,
    )
    if modifiers is None:
        return ()
    return tuple(
        capture(child)
        for child in modifiers.children
        if child.type not in {"annotation", "marker_annotation"}
    )


def _qualified_name(
    scopes: list[_LexicalScope],
    package_name: str,
    name: str,
) -> tuple[str, str | None]:
    if scopes:
        parent = scopes[-1].qualified_name
        return f"{parent}.{name}", parent
    return (f"{package_name}.{name}" if package_name else name), None


def _clause_types(
    clause: Node | None,
    capture: Callable[[Node], str],
) -> tuple[str, ...]:
    if clause is None:
        return ()
    type_list = next(
        (child for child in clause.named_children if child.type == "type_list"),
        None,
    )
    if type_list is not None:
        return tuple(
            capture(child)
            for child in type_list.named_children
            if child.type in _REFERENCE_TYPE_NODES
        )
    return tuple(
        capture(child)
        for child in clause.named_children
        if child.type in _REFERENCE_TYPE_NODES
    )


def _type_relations(
    node: Node,
    capture: Callable[[Node], str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if node.type == "interface_declaration":
        extends = next(
            (child for child in node.named_children if child.type == "extends_interfaces"),
            None,
        )
        return _clause_types(extends, capture), ()

    bases = _clause_types(node.child_by_field_name("superclass"), capture)
    implemented = _clause_types(node.child_by_field_name("interfaces"), capture)
    return bases, implemented


def _parameter_type(
    type_node: Node | None,
    dimensions: Node | None,
    capture: Callable[[Node], str],
    *,
    spread: bool = False,
) -> str | None:
    if type_node is None:
        return None
    value = capture(type_node)
    if spread:
        value += "..."
    if dimensions is not None:
        value += capture(dimensions)
    return value


def _formal_parameter(
    node: Node,
    capture: Callable[[Node], str],
    capture_range: Callable[[int, int], str],
) -> ParameterInfo | None:
    name = node.child_by_field_name("name")
    type_node = node.child_by_field_name("type")
    dimensions = node.child_by_field_name("dimensions")

    if node.type == "spread_parameter":
        declarator = next(
            (
                child
                for child in node.named_children
                if child.type == "variable_declarator"
            ),
            None,
        )
        if declarator is not None:
            name = declarator.child_by_field_name("name")
            dimensions = declarator.child_by_field_name("dimensions")
        type_node = next(
            (
                child
                for child in node.named_children
                if child.type not in {"modifiers", "variable_declarator"}
            ),
            None,
        )
        if name is None:
            return None
        return ParameterInfo(
            capture(name),
            _parameter_type(type_node, dimensions, capture, spread=True),
            None,
        )

    if node.type == "receiver_parameter":
        named = [
            child
            for child in node.named_children
            if child.type not in {"modifiers", "annotation", "marker_annotation"}
        ]
        if len(named) < 2:
            return None
        return ParameterInfo(
            capture_range(named[1].start_byte, node.end_byte),
            capture(named[0]),
            None,
        )

    if name is None:
        return None
    return ParameterInfo(
        capture(name),
        _parameter_type(type_node, dimensions, capture),
        None,
    )


def _parameters(
    node: Node | None,
    capture: Callable[[Node], str],
    capture_range: Callable[[int, int], str],
) -> tuple[ParameterInfo, ...]:
    if node is None:
        return ()
    parameters: list[ParameterInfo] = []
    for child in node.named_children:
        parameter = _formal_parameter(child, capture, capture_range)
        if parameter is not None:
            parameters.append(parameter)
    return tuple(parameters)


def _field_constants(
    node: Node,
    scopes: list[_LexicalScope],
    source: SourceBuffer,
    capture: Callable[[Node], str],
) -> tuple[SymbolInfo, ...]:
    if (
        not scopes
        or scopes[-1].kind != "type"
        or node.parent is None
        or node.parent.type not in _TYPE_BODIES
    ):
        return ()
    modifiers = _modifiers(node, capture)
    if "final" not in modifiers:
        return ()

    symbols: list[SymbolInfo] = []
    for declarator in node.children_by_field_name("declarator"):
        name_node = declarator.child_by_field_name("name")
        if name_node is None or name_node.type != "identifier":
            continue
        name = capture(name_node)
        qualified, parent = _qualified_name(scopes, "", name)
        symbols.append(
            SymbolInfo(
                name,
                SymbolKind.CONSTANT,
                qualified,
                parent,
                source_location(node, source),
                (),
                None,
                modifiers,
                (),
                (),
            )
        )
    return tuple(symbols)


def _import_info(
    node: Node,
    source: SourceBuffer,
    capture: Callable[[Node], str],
) -> ImportInfo | None:
    target = next(
        (
            child
            for child in node.named_children
            if child.type in _QUALIFIED_NAME_NODES
        ),
        None,
    )
    if target is None:
        return None
    wildcard = any(child.type == "asterisk" for child in node.named_children)
    static = any(child.type == "static" for child in node.children)
    return ImportInfo(
        capture(target),
        (),
        wildcard,
        ("static",) if static else (),
        source_location(node, source),
    )


class JavaExtractor:
    """Extract Java declarations, imports, and syntactic calls."""

    def extract(self, tree: Tree, source: SourceBuffer) -> ExtractionResult:
        symbols: list[SymbolInfo] = []
        imports: list[ImportInfo] = []
        calls: list[CallSite] = []
        scopes: list[_LexicalScope] = []
        ownership_scopes: list[str | None] = []
        captured_truncated_text = False

        def capture(node: Node) -> str:
            nonlocal captured_truncated_text
            bounded = bounded_node_text(node, source)
            captured_truncated_text |= bounded.was_truncated
            return bounded.text

        def capture_range(start_byte: int, end_byte: int) -> str:
            nonlocal captured_truncated_text
            value = source.parse_bytes[start_byte:end_byte].strip(_ASCII_WHITESPACE)
            if len(value) <= _MAX_TEXT_BYTES:
                return value.decode("utf-8", errors="strict")
            prefix = value[: _MAX_TEXT_BYTES - len(_ELLIPSIS_BYTES)]
            while prefix:
                try:
                    text = prefix.decode("utf-8", errors="strict")
                    captured_truncated_text = True
                    return text + _ELLIPSIS
                except UnicodeDecodeError:
                    prefix = prefix[:-1]
            captured_truncated_text = True
            return _ELLIPSIS

        package_name = ""
        for child in tree.root_node.named_children:
            if child.type != "package_declaration":
                continue
            name_node = next(
                (
                    item
                    for item in child.named_children
                    if item.type in _QUALIFIED_NAME_NODES
                ),
                None,
            )
            if name_node is not None:
                package_name = capture(name_node)
            break

        for event in iter_events(tree.root_node):
            node = event.node
            if event.kind is not ENTER:
                if node.type in _TYPE_DECLARATIONS:
                    name = node.child_by_field_name("name")
                    if name is not None:
                        scopes.pop()
                        ownership_scopes.pop()
                elif node.type in {"constructor_declaration", "method_declaration"}:
                    name = node.child_by_field_name("name")
                    if name is not None and node.parent is not None and node.parent.type in _TYPE_BODIES:
                        scopes.pop()
                        ownership_scopes.pop()
                continue

            type_kind = _TYPE_DECLARATIONS.get(node.type)
            if type_kind is not None:
                name_node = node.child_by_field_name("name")
                if name_node is None:
                    continue
                name = capture(name_node)
                qualified, parent = _qualified_name(scopes, package_name, name)
                bases, implemented = _type_relations(node, capture)
                symbols.append(
                    SymbolInfo(
                        name,
                        type_kind,
                        qualified,
                        parent,
                        source_location(node, source),
                        (),
                        None,
                        _modifiers(node, capture),
                        bases,
                        implemented,
                    )
                )
                scopes.append(_LexicalScope(qualified, "type"))
                ownership_scopes.append(None)
                continue

            if node.type in {"constructor_declaration", "method_declaration"}:
                if node.parent is None or node.parent.type not in _TYPE_BODIES:
                    continue
                name_node = node.child_by_field_name("name")
                if name_node is None:
                    continue
                name = capture(name_node)
                qualified, parent = _qualified_name(scopes, package_name, name)
                parameters = _parameters(
                    node.child_by_field_name("parameters"),
                    capture,
                    capture_range,
                )
                return_node = node.child_by_field_name("type")
                kind = (
                    SymbolKind.CONSTRUCTOR
                    if node.type == "constructor_declaration"
                    else SymbolKind.METHOD
                )
                symbols.append(
                    SymbolInfo(
                        name,
                        kind,
                        qualified,
                        parent,
                        source_location(node, source),
                        parameters,
                        None if kind is SymbolKind.CONSTRUCTOR or return_node is None else capture(return_node),
                        _modifiers(node, capture),
                        (),
                        (),
                    )
                )
                scopes.append(_LexicalScope(qualified, "callable"))
                ownership_scopes.append(qualified)
                continue

            if node.type == "field_declaration":
                symbols.extend(_field_constants(node, scopes, source, capture))
                continue

            if node.type == "import_declaration":
                imported = _import_info(node, source, capture)
                if imported is not None:
                    imports.append(imported)
                continue

            if node.type == "method_invocation":
                arguments = node.child_by_field_name("arguments")
                if arguments is not None:
                    calls.append(
                        CallSite(
                            ownership_scopes[-1] if ownership_scopes else None,
                            capture_range(node.start_byte, arguments.start_byte),
                            CallKind.CALL,
                            source_location(node, source),
                        )
                    )
                continue

            if node.type == "object_creation_expression":
                type_node = node.child_by_field_name("type")
                if type_node is not None:
                    calls.append(
                        CallSite(
                            ownership_scopes[-1] if ownership_scopes else None,
                            capture(type_node),
                            CallKind.CONSTRUCTOR,
                            source_location(node, source),
                        )
                    )

        issues = (
            (ParseIssue(ParseIssueKind.EXTRACTION_ERROR, _BOUNDED_TEXT_MESSAGE, None),)
            if captured_truncated_text
            else ()
        )
        return normalize_extraction(
            ExtractionResult(tuple(symbols), tuple(imports), tuple(calls), issues)
        )
