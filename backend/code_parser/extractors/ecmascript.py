"""Shared Tree-sitter-backed ECMAScript structure extraction."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

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
    bounded_node_text,
    collect_syntax_issues,
    is_trustworthy_capture,
    iter_events,
    normalize_extraction,
    source_location,
)


_BOUNDED_TEXT_MESSAGE = "Extracted text exceeded the 1,000-byte limit."
_FUNCTION_DECLARATIONS = frozenset(
    {"function_declaration", "function_signature", "generator_function_declaration"}
)
_FUNCTION_VALUES = frozenset(
    {"arrow_function", "function_expression", "generator_function"}
)
_DIRECT_MODIFIERS = frozenset(
    {
        "abstract",
        "async",
        "declare",
        "get",
        "override",
        "private",
        "protected",
        "public",
        "readonly",
        "set",
        "static",
    }
)
_MAX_TEXT_BYTES = 1000
_ELLIPSIS = "…"
_ELLIPSIS_BYTES = _ELLIPSIS.encode("utf-8")
_SCOPE_KEY = tuple[str, int, int]


class _EcmaScriptMode(str, Enum):
    """Closed grammar modes; later modes must opt into their own node sets."""

    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    TSX = "tsx"


JAVASCRIPT = _EcmaScriptMode.JAVASCRIPT
TYPESCRIPT = _EcmaScriptMode.TYPESCRIPT
TSX = _EcmaScriptMode.TSX


@dataclass(frozen=True, slots=True)
class _ModeNodeSets:
    class_declarations: frozenset[str]
    class_expressions: frozenset[str]
    interface_declarations: frozenset[str]
    method_declarations: frozenset[str]
    typed_declarations: bool


_MODE_NODE_SETS = {
    JAVASCRIPT: _ModeNodeSets(
        class_declarations=frozenset({"class_declaration"}),
        class_expressions=frozenset({"class"}),
        interface_declarations=frozenset(),
        method_declarations=frozenset({"method_definition"}),
        typed_declarations=False,
    ),
    TYPESCRIPT: _ModeNodeSets(
        class_declarations=frozenset(
            {"abstract_class_declaration", "class_declaration"}
        ),
        class_expressions=frozenset({"class"}),
        interface_declarations=frozenset({"interface_declaration"}),
        method_declarations=frozenset(
            {"abstract_method_signature", "method_definition", "method_signature"}
        ),
        typed_declarations=True,
    ),
    TSX: _ModeNodeSets(
        class_declarations=frozenset(
            {"abstract_class_declaration", "class_declaration"}
        ),
        class_expressions=frozenset({"class"}),
        interface_declarations=frozenset({"interface_declaration"}),
        method_declarations=frozenset(
            {"abstract_method_signature", "method_definition", "method_signature"}
        ),
        typed_declarations=True,
    ),
}


@dataclass(frozen=True, slots=True)
class _LexicalScope:
    qualified_name: str
    kind: str


def _node_key(node: Node) -> _SCOPE_KEY:
    return node.type, node.start_byte, node.end_byte


def _same_node(first: Node | None, second: Node) -> bool:
    return first is not None and _node_key(first) == _node_key(second)


def _qualified_name(
    scopes: list[_LexicalScope],
    name: str,
) -> tuple[str, str | None]:
    parent = scopes[-1].qualified_name if scopes else None
    return (name if parent is None else f"{parent}.{name}"), parent


def _export_modifiers(node: Node) -> tuple[str, ...]:
    ancestor = node.parent
    while ancestor is not None and ancestor.type not in {
        "class_body",
        "program",
        "statement_block",
    }:
        if ancestor.type == "export_statement":
            return tuple(
                child.type
                for child in ancestor.children
                if child.type in {"export", "default"}
            )
        ancestor = ancestor.parent
    return ()


def _ambient_modifiers(node: Node) -> tuple[str, ...]:
    ancestor = node.parent
    while ancestor is not None and ancestor.type not in {
        "class_body",
        "program",
        "statement_block",
    }:
        if ancestor.type == "ambient_declaration":
            return tuple(
                child.type for child in ancestor.children if child.type == "declare"
            )
        ancestor = ancestor.parent
    return ()


def _direct_modifiers(node: Node) -> tuple[str, ...]:
    modifiers: list[str] = []
    for child in node.children:
        if child.type in _DIRECT_MODIFIERS:
            modifiers.append(child.type)
        elif child.type == "accessibility_modifier":
            modifiers.extend(
                nested.type
                for nested in child.children
                if nested.type in _DIRECT_MODIFIERS
            )
        elif child.type == "*":
            modifiers.append("generator")
    return tuple(modifiers)


def _modifiers(node: Node) -> tuple[str, ...]:
    return (
        *_direct_modifiers(node),
        *_ambient_modifiers(node),
        *_export_modifiers(node),
    )


def _semantic_named_children(node: Node) -> tuple[Node, ...]:
    return tuple(child for child in node.named_children if child.type != "comment")


def _type_text(node: Node | None, capture: Callable[[Node], str]) -> str | None:
    if node is None:
        return None
    declared = next(iter(_semantic_named_children(node)), None)
    return capture(node) if declared is None else capture(declared)


def _parameter_name(pattern: Node, capture: Callable[[Node], str]) -> str:
    if pattern.type != "rest_pattern":
        return capture(pattern)
    declared = next(
        (
            child
            for child in pattern.named_children
            if child.type in {"array_pattern", "identifier", "object_pattern"}
        ),
        None,
    )
    return capture(pattern) if declared is None else capture(declared)


def _typed_parameter(node: Node, capture: Callable[[Node], str]) -> ParameterInfo:
    pattern = node.child_by_field_name("pattern")
    declared_type = _type_text(node.child_by_field_name("type"), capture)
    default = node.child_by_field_name("value")
    return ParameterInfo(
        capture(node) if pattern is None else _parameter_name(pattern, capture),
        declared_type,
        None if default is None else capture(default),
    )


def _parameter(
    node: Node,
    capture: Callable[[Node], str],
    typed_declarations: bool,
) -> ParameterInfo:
    if typed_declarations and node.type in {"optional_parameter", "required_parameter"}:
        return _typed_parameter(node, capture)

    if node.type == "assignment_pattern":
        name = node.child_by_field_name("left")
        default = node.child_by_field_name("right")
        return ParameterInfo(
            capture(node) if name is None else capture(name),
            None,
            None if default is None else capture(default),
        )

    if node.type == "rest_pattern":
        return ParameterInfo(_parameter_name(node, capture), None, None)

    return ParameterInfo(capture(node), None, None)


def _parameters(
    node: Node,
    capture: Callable[[Node], str],
    typed_declarations: bool,
) -> tuple[ParameterInfo, ...]:
    parameters = node.child_by_field_name("parameters")
    if parameters is not None:
        return tuple(
            _parameter(child, capture, typed_declarations)
            for child in parameters.named_children
        )
    parameter = node.child_by_field_name("parameter")
    return (
        ()
        if parameter is None
        else (_parameter(parameter, capture, typed_declarations),)
    )


def _return_type(
    node: Node,
    capture: Callable[[Node], str],
    typed_declarations: bool,
) -> str | None:
    if not typed_declarations:
        return None
    return _type_text(node.child_by_field_name("return_type"), capture)


def _class_bases(node: Node, capture: Callable[[Node], str]) -> tuple[str, ...]:
    heritage = next(
        (child for child in node.named_children if child.type == "class_heritage"),
        None,
    )
    if heritage is None:
        return ()
    return tuple(capture(child) for child in _semantic_named_children(heritage))


def _typescript_class_relationships(
    node: Node,
    capture: Callable[[Node], str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    heritage = next(
        (child for child in node.named_children if child.type == "class_heritage"),
        None,
    )
    if heritage is None:
        return (), ()
    bases: list[str] = []
    implemented: list[str] = []
    for clause in _semantic_named_children(heritage):
        if clause.type == "extends_clause":
            bases.extend(
                capture(child) for child in _semantic_named_children(clause)
            )
        elif clause.type == "implements_clause":
            implemented.extend(
                capture(child) for child in _semantic_named_children(clause)
            )
    return tuple(bases), tuple(implemented)


def _interface_bases(
    node: Node,
    capture: Callable[[Node], str],
) -> tuple[str, ...]:
    clause = next(
        (child for child in node.named_children if child.type == "extends_type_clause"),
        None,
    )
    return (
        ()
        if clause is None
        else tuple(capture(child) for child in _semantic_named_children(clause))
    )


def _bounded_raw_text(value: bytes) -> tuple[str, bool]:
    if len(value) <= _MAX_TEXT_BYTES:
        return value.decode("utf-8", errors="strict"), False
    prefix = value[: _MAX_TEXT_BYTES - len(_ELLIPSIS_BYTES)]
    while prefix:
        try:
            return prefix.decode("utf-8", errors="strict") + _ELLIPSIS, True
        except UnicodeDecodeError:
            prefix = prefix[:-1]
    return _ELLIPSIS, True


def _string_value(
    node: Node | None,
    source: SourceBuffer,
) -> tuple[str, bool] | None:
    if node is None or node.type != "string":
        return None
    if not 0 <= node.start_byte <= node.end_byte <= len(source.parse_bytes):
        raise ValueError("Tree-sitter string range is outside the source buffer.")
    value = source.parse_bytes[node.start_byte : node.end_byte]
    if len(value) < 2 or value[:1] not in {b'"', b"'"} or value[-1:] != value[:1]:
        return None
    return _bounded_raw_text(value[1:-1])


def _import_specifier(
    node: Node,
    capture: Callable[[Node], str],
) -> ImportBinding | None:
    name = node.child_by_field_name("name")
    alias = node.child_by_field_name("alias")
    if name is None:
        return None
    return ImportBinding(capture(name), None if alias is None else capture(alias))


def _es_import(
    node: Node,
    source: SourceBuffer,
    capture: Callable[[Node], str],
    capture_module: Callable[[Node | None], str | None],
) -> ImportInfo | None:
    module = capture_module(node.child_by_field_name("source"))
    if module is None:
        return None

    bindings: list[ImportBinding] = []
    wildcard = False
    clause = next(
        (child for child in node.named_children if child.type == "import_clause"),
        None,
    )
    if clause is not None:
        for child in clause.named_children:
            if child.type == "identifier":
                bindings.append(ImportBinding("default", capture(child)))
            elif child.type == "namespace_import":
                alias = next(iter(child.named_children), None)
                if alias is not None:
                    bindings.append(ImportBinding("*", capture(alias)))
                    wildcard = True
            elif child.type == "named_imports":
                for specifier in child.named_children:
                    binding = _import_specifier(specifier, capture)
                    if binding is not None:
                        bindings.append(binding)

    return ImportInfo(
        module,
        tuple(bindings),
        wildcard,
        (),
        source_location(node, source),
    )


def _commonjs_bindings(
    call: Node,
    capture: Callable[[Node], str],
) -> tuple[tuple[ImportBinding, ...], bool]:
    parent = call.parent
    if parent is None or parent.type != "variable_declarator":
        return (), False
    if not _same_node(parent.child_by_field_name("value"), call):
        return (), False

    name = parent.child_by_field_name("name")
    if name is None:
        return (), False
    if name.type == "identifier":
        return (ImportBinding("*", capture(name)),), True
    if name.type != "object_pattern":
        return (), False

    bindings: list[ImportBinding] = []
    for child in name.named_children:
        if child.type == "shorthand_property_identifier_pattern":
            bindings.append(ImportBinding(capture(child), None))
            continue
        if child.type != "pair_pattern":
            continue
        imported = child.child_by_field_name("key")
        alias = child.child_by_field_name("value")
        if (
            imported is None
            or alias is None
            or imported.type not in {"identifier", "property_identifier"}
            or alias.type != "identifier"
        ):
            continue
        imported_name = capture(imported)
        alias_name = capture(alias)
        bindings.append(
            ImportBinding(imported_name, None if alias_name == imported_name else alias_name)
        )
    return tuple(bindings), False


def _commonjs_import(
    node: Node,
    source: SourceBuffer,
    capture: Callable[[Node], str],
    capture_module: Callable[[Node | None], str | None],
) -> ImportInfo | None:
    function = node.child_by_field_name("function")
    if function is None or function.type != "identifier" or capture(function) != "require":
        return None
    arguments = node.child_by_field_name("arguments")
    if arguments is None or len(arguments.named_children) != 1:
        return None
    module = capture_module(arguments.named_children[0])
    if module is None:
        return None
    bindings, wildcard = _commonjs_bindings(node, capture)
    return ImportInfo(
        module,
        bindings,
        wildcard,
        (),
        source_location(node, source),
    )


def _is_module_declaration(node: Node) -> bool:
    parent = node.parent
    if parent is None:
        return False
    if parent.type == "ambient_declaration":
        parent = parent.parent
        if parent is None:
            return False
    if parent.type == "program":
        return True
    return (
        parent.type == "export_statement"
        and parent.parent is not None
        and parent.parent.type == "program"
    )


def _stable_function_binding(node: Node) -> tuple[Node, Node] | None:
    if node.type == "variable_declarator":
        name = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
    elif node.type == "assignment_expression":
        name = node.child_by_field_name("left")
        value = node.child_by_field_name("right")
    else:
        return None
    if (
        name is None
        or name.type != "identifier"
        or value is None
        or value.type not in _FUNCTION_VALUES
    ):
        return None
    return name, value


@dataclass(frozen=True, slots=True)
class EcmaScriptExtractor:
    """Extract syntax shared by ECMAScript-family grammars in one closed mode."""

    mode: _EcmaScriptMode = JAVASCRIPT

    def __post_init__(self) -> None:
        if self.mode not in _MODE_NODE_SETS:
            raise ValueError("Unsupported ECMAScript extractor mode.")

    def extract(self, tree: Tree, source: SourceBuffer) -> ExtractionResult:
        node_sets = _MODE_NODE_SETS[self.mode]
        symbols: list[SymbolInfo] = []
        imports: list[ImportInfo] = []
        calls: list[CallSite] = []
        scopes: list[_LexicalScope] = []
        ownership_scopes: list[str | None] = []
        pushed_scopes: set[_SCOPE_KEY] = set()
        pushed_ownership_barriers: set[_SCOPE_KEY] = set()
        extracted_class_bodies: set[_SCOPE_KEY] = set()
        captured_truncated_text = False
        syntax_issues = collect_syntax_issues(tree, source)

        def capture(node: Node) -> str:
            nonlocal captured_truncated_text
            bounded = bounded_node_text(node, source)
            captured_truncated_text |= bounded.was_truncated
            return bounded.text

        def capture_module(node: Node | None) -> str | None:
            nonlocal captured_truncated_text
            bounded = _string_value(node, source)
            if bounded is None:
                return None
            text, was_truncated = bounded
            captured_truncated_text |= was_truncated
            return text

        def push_scope(node: Node, qualified_name: str, kind: str) -> None:
            scopes.append(_LexicalScope(qualified_name, kind))
            ownership_scopes.append(qualified_name if kind == "callable" else None)
            pushed_scopes.add(_node_key(node))

        def call_owner(node: Node) -> str | None:
            ancestor = node.parent
            while ancestor is not None:
                if ancestor.type == "decorator":
                    decorated = ancestor.parent
                    if decorated is not None and _node_key(decorated) in pushed_scopes:
                        return ownership_scopes[-2] if len(ownership_scopes) > 1 else None
                    return ownership_scopes[-1] if ownership_scopes else None
                ancestor = ancestor.parent
            return ownership_scopes[-1] if ownership_scopes else None

        for event in iter_events(tree.root_node):
            node = event.node
            key = _node_key(node)
            if event.kind is not ENTER:
                if key in pushed_ownership_barriers:
                    pushed_ownership_barriers.remove(key)
                    ownership_scopes.pop()
                if key in pushed_scopes:
                    pushed_scopes.remove(key)
                    scopes.pop()
                    ownership_scopes.pop()
                continue

            if node.type in node_sets.class_expressions:
                ownership_scopes.append(None)
                pushed_ownership_barriers.add(key)
                continue

            if node.type in node_sets.class_declarations:
                name_node = node.child_by_field_name("name")
                if name_node is None or not is_trustworthy_capture(
                    node, source, syntax_issues, name_node
                ):
                    continue
                name = capture(name_node)
                qualified, parent = _qualified_name(scopes, name)
                if node_sets.typed_declarations:
                    base_types, implemented_types = _typescript_class_relationships(
                        node,
                        capture,
                    )
                else:
                    base_types = _class_bases(node, capture)
                    implemented_types = ()
                symbols.append(
                    SymbolInfo(
                        name,
                        SymbolKind.CLASS,
                        qualified,
                        parent,
                        source_location(node, source),
                        (),
                        None,
                        _modifiers(node),
                        base_types,
                        implemented_types,
                    )
                )
                body = node.child_by_field_name("body")
                if body is not None:
                    extracted_class_bodies.add(_node_key(body))
                push_scope(node, qualified, "class")
                continue

            if node.type in node_sets.interface_declarations:
                name_node = node.child_by_field_name("name")
                if name_node is None or not is_trustworthy_capture(
                    node, source, syntax_issues, name_node
                ):
                    continue
                name = capture(name_node)
                qualified, parent = _qualified_name(scopes, name)
                symbols.append(
                    SymbolInfo(
                        name,
                        SymbolKind.INTERFACE,
                        qualified,
                        parent,
                        source_location(node, source),
                        (),
                        None,
                        _modifiers(node),
                        _interface_bases(node, capture),
                        (),
                    )
                )
                body = node.child_by_field_name("body")
                if body is not None:
                    extracted_class_bodies.add(_node_key(body))
                push_scope(node, qualified, "interface")
                continue

            if node.type in _FUNCTION_DECLARATIONS:
                name_node = node.child_by_field_name("name")
                if name_node is None or not is_trustworthy_capture(
                    node, source, syntax_issues, name_node
                ):
                    continue
                name = capture(name_node)
                qualified, parent = _qualified_name(scopes, name)
                symbols.append(
                    SymbolInfo(
                        name,
                        SymbolKind.FUNCTION,
                        qualified,
                        parent,
                        source_location(node, source),
                        _parameters(node, capture, node_sets.typed_declarations),
                        _return_type(node, capture, node_sets.typed_declarations),
                        _modifiers(node),
                        (),
                        (),
                    )
                )
                push_scope(node, qualified, "callable")
                continue

            if node.type in node_sets.method_declarations:
                if (
                    node.parent is None
                    or _node_key(node.parent) not in extracted_class_bodies
                    or not scopes
                    or scopes[-1].kind not in {"class", "interface"}
                ):
                    continue
                name_node = node.child_by_field_name("name")
                if name_node is None or not is_trustworthy_capture(
                    node, source, syntax_issues, name_node
                ):
                    continue
                name = capture(name_node)
                qualified, parent = _qualified_name(scopes, name)
                kind = (
                    SymbolKind.CONSTRUCTOR
                    if name == "constructor" and scopes[-1].kind == "class"
                    else SymbolKind.METHOD
                )
                symbols.append(
                    SymbolInfo(
                        name,
                        kind,
                        qualified,
                        parent,
                        source_location(node, source),
                        _parameters(node, capture, node_sets.typed_declarations),
                        _return_type(node, capture, node_sets.typed_declarations),
                        _modifiers(node),
                        (),
                        (),
                    )
                )
                push_scope(node, qualified, "callable")
                continue

            stable_binding = _stable_function_binding(node)
            if stable_binding is not None:
                name_node, value = stable_binding
                if not is_trustworthy_capture(
                    node, source, syntax_issues, name_node, value
                ):
                    continue
                name = capture(name_node)
                qualified, parent = _qualified_name(scopes, name)
                symbols.append(
                    SymbolInfo(
                        name,
                        SymbolKind.FUNCTION,
                        qualified,
                        parent,
                        source_location(node, source),
                        _parameters(value, capture, node_sets.typed_declarations),
                        _return_type(value, capture, node_sets.typed_declarations),
                        (*_direct_modifiers(value), *_export_modifiers(node)),
                        (),
                        (),
                    )
                )
                push_scope(node, qualified, "callable")
                continue

            if (
                node_sets.typed_declarations
                and node.type == "public_field_definition"
                and node.parent is not None
                and _node_key(node.parent) in extracted_class_bodies
                and scopes
                and scopes[-1].kind == "class"
            ):
                field_modifiers = _direct_modifiers(node)
                if not {"static", "readonly"}.issubset(field_modifiers):
                    continue
                name_node = node.child_by_field_name("name")
                if name_node is None or name_node.type not in {
                    "identifier",
                    "property_identifier",
                }:
                    continue
                if not is_trustworthy_capture(
                    node, source, syntax_issues, name_node
                ):
                    continue
                name = capture(name_node)
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
                        field_modifiers,
                        (),
                        (),
                    )
                )
                continue

            if node.type == "lexical_declaration" and _is_module_declaration(node):
                kind_node = node.child_by_field_name("kind")
                if kind_node is None or kind_node.type != "const":
                    continue
                for declarator in (
                    child
                    for child in node.named_children
                    if child.type == "variable_declarator"
                ):
                    name_node = declarator.child_by_field_name("name")
                    value = declarator.child_by_field_name("value")
                    if (
                        name_node is None
                        or name_node.type != "identifier"
                        or (value is not None and value.type in _FUNCTION_VALUES)
                    ):
                        continue
                    if not is_trustworthy_capture(
                        declarator, source, syntax_issues, name_node
                    ):
                        continue
                    name = capture(name_node)
                    qualified, parent = _qualified_name(scopes, name)
                    symbols.append(
                        SymbolInfo(
                            name,
                            SymbolKind.CONSTANT,
                            qualified,
                            parent,
                            source_location(declarator, source),
                            (),
                            None,
                            _modifiers(node),
                            (),
                            (),
                        )
                    )
                continue

            if node.type == "import_statement":
                module_node = node.child_by_field_name("source")
                imported = (
                    _es_import(node, source, capture, capture_module)
                    if is_trustworthy_capture(
                        node, source, syntax_issues, module_node
                    )
                    else None
                )
                if imported is not None:
                    imports.append(imported)
                continue

            if node.type == "call_expression":
                function = node.child_by_field_name("function")
                if function is None or not is_trustworthy_capture(
                    node, source, syntax_issues, function
                ):
                    continue
                imported = _commonjs_import(
                    node,
                    source,
                    capture,
                    capture_module,
                )
                if imported is not None:
                    imports.append(imported)
                calls.append(
                    CallSite(
                        call_owner(node),
                        capture(function),
                        CallKind.CALL,
                        source_location(node, source),
                    )
                )
                continue

            if node.type == "new_expression":
                constructor = node.child_by_field_name("constructor")
                if constructor is not None and is_trustworthy_capture(
                    node, source, syntax_issues, constructor
                ):
                    calls.append(
                        CallSite(
                            call_owner(node),
                            capture(constructor),
                            CallKind.CONSTRUCTOR,
                            source_location(node, source),
                        )
                    )

        issues = syntax_issues + (
            (ParseIssue(ParseIssueKind.EXTRACTION_ERROR, _BOUNDED_TEXT_MESSAGE, None),)
            if captured_truncated_text
            else ()
        )
        return normalize_extraction(
            ExtractionResult(tuple(symbols), tuple(imports), tuple(calls), issues)
        )
