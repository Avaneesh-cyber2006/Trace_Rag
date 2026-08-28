"""Tree-sitter-backed Python structure extraction."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tree_sitter import Node, Tree

from backend.code_parser.models import (
    CallKind,
    CallSite,
    ImportBinding,
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
    ScopeStack,
    bounded_node_text,
    iter_events,
    normalize_extraction,
    source_location,
)


_BOUNDED_TEXT_MESSAGE = "Extracted text exceeded the 1,000-byte limit."


@dataclass(frozen=True, slots=True)
class _LexicalScope:
    qualified_name: str
    kind: str


def _qualified_name(scopes: list[_LexicalScope], name: str) -> tuple[str, str | None]:
    parent = scopes[-1].qualified_name if scopes else None
    return (name if parent is None else f"{parent}.{name}"), parent


def _parameters(node: Node, capture: Callable[[Node], str]) -> tuple[ParameterInfo, ...]:
    parameters: list[ParameterInfo] = []
    for child in node.named_children:
        name_node = child if child.type == "identifier" else child.child_by_field_name("name")
        if name_node is None and child.type == "typed_parameter" and child.named_children:
            name_node = child.named_children[0]
        if name_node is None:
            continue

        if name_node.type in {"list_splat", "dictionary_splat"}:
            nested_name = name_node.child_by_field_name("name")
            if nested_name is None and name_node.named_children:
                nested_name = name_node.named_children[0]
            if nested_name is not None:
                name_node = nested_name

        type_node = child.child_by_field_name("type")
        default_node = child.child_by_field_name("value")
        parameters.append(
            ParameterInfo(
                capture(name_node),
                None if type_node is None else capture(type_node),
                None if default_node is None else capture(default_node),
            )
        )
    return tuple(parameters)


def _class_bases(node: Node, capture: Callable[[Node], str]) -> tuple[str, ...]:
    superclasses = node.child_by_field_name("superclasses")
    if superclasses is None:
        return ()
    return tuple(
        capture(child)
        for child in superclasses.named_children
        if child.type != "keyword_argument"
    )


def _binding(node: Node, capture: Callable[[Node], str]) -> ImportBinding:
    if node.type != "aliased_import":
        return ImportBinding(capture(node), None)
    name = node.child_by_field_name("name")
    alias = node.child_by_field_name("alias")
    if name is None:
        name = node.named_children[0]
    return ImportBinding(
        capture(name),
        None if alias is None else capture(alias),
    )


def _direct_imports(
    node: Node,
    source: SourceBuffer,
    capture: Callable[[Node], str],
) -> tuple[ImportInfo, ...]:
    imports: list[ImportInfo] = []
    location = source_location(node, source)
    for imported in node.children_by_field_name("name"):
        binding = _binding(imported, capture)
        imports.append(
            ImportInfo(binding.imported_name, (binding,), False, (), location)
        )
    return tuple(imports)


def _from_import(
    node: Node,
    source: SourceBuffer,
    capture: Callable[[Node], str],
) -> ImportInfo | None:
    module = node.child_by_field_name("module_name")
    if module is None:
        return None
    wildcard = any(child.type == "wildcard_import" for child in node.named_children)
    bindings = () if wildcard else tuple(
        _binding(imported, capture)
        for imported in node.children_by_field_name("name")
    )
    return ImportInfo(
        capture(module),
        bindings,
        wildcard,
        (),
        source_location(node, source),
    )


class PythonExtractor:
    """Extract Python declarations, imports, and calls from a parsed tree."""

    def extract(self, tree: Tree, source: SourceBuffer) -> ExtractionResult:
        symbols: list[SymbolInfo] = []
        imports: list[ImportInfo] = []
        calls: list[CallSite] = []
        scopes: list[_LexicalScope] = []
        callable_scopes = ScopeStack()
        captured_truncated_text = False

        def capture(node: Node) -> str:
            nonlocal captured_truncated_text
            bounded = bounded_node_text(node, source)
            captured_truncated_text |= bounded.was_truncated
            return bounded.text

        for event in iter_events(tree.root_node):
            node = event.node
            if event.kind is not ENTER:
                if node.type in {"class_definition", "function_definition"}:
                    name = node.child_by_field_name("name")
                    if name is not None:
                        scopes.pop()
                        callable_scopes.pop()
                continue

            if node.type == "class_definition":
                name_node = node.child_by_field_name("name")
                if name_node is None:
                    continue
                name = capture(name_node)
                qualified, parent = _qualified_name(scopes, name)
                symbols.append(
                    SymbolInfo(
                        name,
                        SymbolKind.CLASS,
                        qualified,
                        parent,
                        source_location(node, source),
                        (),
                        None,
                        (),
                        _class_bases(node, capture),
                        (),
                    )
                )
                scopes.append(_LexicalScope(qualified, "class"))
                callable_scopes.push(qualified, is_callable=False)
                continue

            if node.type == "function_definition":
                name_node = node.child_by_field_name("name")
                if name_node is None:
                    continue
                name = capture(name_node)
                qualified, parent = _qualified_name(scopes, name)
                directly_in_class = bool(scopes and scopes[-1].kind == "class")
                if directly_in_class and name == "__init__":
                    kind = SymbolKind.CONSTRUCTOR
                elif directly_in_class:
                    kind = SymbolKind.METHOD
                else:
                    kind = SymbolKind.FUNCTION
                parameter_node = node.child_by_field_name("parameters")
                return_node = node.child_by_field_name("return_type")
                modifiers = (
                    ("async",)
                    if any(child.type == "async" for child in node.children)
                    else ()
                )
                symbols.append(
                    SymbolInfo(
                        name,
                        kind,
                        qualified,
                        parent,
                        source_location(node, source),
                        () if parameter_node is None else _parameters(parameter_node, capture),
                        None if return_node is None else capture(return_node),
                        modifiers,
                        (),
                        (),
                    )
                )
                scopes.append(_LexicalScope(qualified, "function"))
                callable_scopes.push(qualified, is_callable=True)
                continue

            if node.type == "assignment":
                left = node.child_by_field_name("left")
                constant_scope = not scopes or scopes[-1].kind == "class"
                if left is not None and left.type == "identifier" and constant_scope:
                    name = capture(left)
                    if name.isupper():
                        qualified, parent = _qualified_name(scopes, name)
                        symbols.append(
                            SymbolInfo(
                                name,
                                SymbolKind.CONSTANT,
                                qualified,
                                parent,
                                source_location(node, source),
                                (),
                                None,
                                (),
                                (),
                                (),
                            )
                        )
                continue

            if node.type == "import_statement":
                imports.extend(_direct_imports(node, source, capture))
                continue

            if node.type == "import_from_statement":
                imported = _from_import(node, source, capture)
                if imported is not None:
                    imports.append(imported)
                continue

            if node.type == "call":
                function = node.child_by_field_name("function")
                if function is not None:
                    calls.append(
                        CallSite(
                            callable_scopes.nearest_callable,
                            capture(function),
                            CallKind.CALL,
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

