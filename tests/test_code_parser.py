import ast
import codecs
from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest
from tree_sitter import Language, Parser
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_python

from backend.code_parser.extractors import get_extractor
from backend.code_parser.extractors import base as extractor_base
from backend.code_parser.extractors.base import (
    ENTER,
    EXIT,
    ExtractionResult,
    ScopeStack,
    bounded_node_text,
    iter_events,
    normalize_extraction,
    normalize_modifiers,
    source_location,
)
from backend.code_parser.exceptions import (
    CodeParserError,
    InvalidParseInventory,
    ParserConfigurationError,
    RepositoryParseError,
)
from backend.code_parser.registry import (
    ParserHandle,
    ParserRegistry,
    ParserSpec,
    ParserUnavailable,
)
import backend.code_parser.reader as reader_module
from backend.code_parser.reader import (
    SafeSourceReader,
    SourceBuffer,
    SourceReadError,
    is_reparse_metadata,
)
from backend.file_scanner.models import FileCategory, ScannedFile
from backend.code_parser.models import (
    CallKind,
    CallSite,
    CodeParseInventory,
    ImportBinding,
    ImportInfo,
    ParameterInfo,
    ParsedFile,
    ParsedLanguage,
    ParseIssue,
    ParseIssueKind,
    ParseSkipReason,
    ParseStatus,
    SkippedParseFile,
    SourceLocation,
    SymbolInfo,
    SymbolKind,
)


def test_code_parser_enum_values_are_stable() -> None:
    assert [item.value for item in ParsedLanguage] == [
        "python", "java", "javascript", "typescript", "tsx"
    ]
    assert [item.value for item in SymbolKind] == [
        "class", "interface", "function", "method", "constructor", "enum", "constant"
    ]
    assert [item.value for item in CallKind] == ["call", "constructor"]
    assert [item.value for item in ParseStatus] == ["success", "partial", "failed"]
    assert [item.value for item in ParseSkipReason] == ["unsupported_language"]
    assert [item.value for item in ParseIssueKind] == [
        "syntax_error", "missing_node", "read_error", "file_changed",
        "path_invalid", "link_unsafe", "decoding_error", "parser_unavailable",
        "extraction_error",
    ]


def test_source_location_is_frozen_and_uses_declared_field_order() -> None:
    location = SourceLocation(0, 3, 1, 0, 1, 3)
    assert tuple(location.__dataclass_fields__) == (
        "start_byte", "end_byte", "start_line", "start_column", "end_line", "end_column"
    )
    with pytest.raises(FrozenInstanceError):
        location.end_byte = 4  # type: ignore[misc]


def test_extractor_base_parses_at_the_python_311_grammar_boundary() -> None:
    assert extractor_base.__file__ is not None
    source = Path(extractor_base.__file__).read_text(encoding="utf-8")

    ast.parse(source, filename=extractor_base.__file__, feature_version=(3, 11))


def parse_python_fixture(data: bytes):
    parser = Parser(Language(tree_sitter_python.language()))
    return parser.parse(data)


def test_source_location_uses_original_ascii_byte_offsets_and_half_open_points() -> None:
    data = b"def target():\n    pass\n"
    tree = parse_python_fixture(data)
    node = tree.root_node.named_children[0]

    location = source_location(node, SourceBuffer(data, data, 0))

    assert location == SourceLocation(0, 22, 1, 0, 2, 8)
    assert data[location.start_byte : location.end_byte] == b"def target():\n    pass"


def test_source_location_counts_unicode_columns_as_utf8_bytes() -> None:
    data = 'label = "é"; target = 1\n'.encode("utf-8")
    tree = parse_python_fixture(data)
    node = tree.root_node.named_children[-1]

    location = source_location(node, SourceBuffer(data, data, 0))

    assert location == SourceLocation(14, 24, 1, 14, 1, 24)
    assert data[location.start_byte : location.end_byte] == b"target = 1"


def test_source_location_restores_bom_offsets_only_on_first_line() -> None:
    parse_bytes = b"def target():\n    pass\n"
    original_bytes = codecs.BOM_UTF8 + parse_bytes
    tree = parse_python_fixture(parse_bytes)
    node = tree.root_node.named_children[0]

    location = source_location(node, SourceBuffer(original_bytes, parse_bytes, 3))

    assert location == SourceLocation(3, 25, 1, 3, 2, 8)
    assert original_bytes[location.start_byte : location.end_byte] == b"def target():\n    pass"


def test_bounded_text_strips_only_surrounding_ascii_whitespace() -> None:
    data = b" \tvalue  with\tinternal spacing\r\n"
    node = parse_python_fixture(data).root_node

    bounded = bounded_node_text(node, SourceBuffer(data, data, 0))

    assert bounded.text == "value  with\tinternal spacing"
    assert bounded.was_truncated is False


def test_bounded_text_preserves_values_at_the_1000_byte_limit() -> None:
    value = "a" * 1000
    data = value.encode("utf-8")
    node = parse_python_fixture(data).root_node

    bounded = bounded_node_text(node, SourceBuffer(data, data, 0))

    assert bounded.text == value
    assert bounded.was_truncated is False
    assert len(bounded.text.encode("utf-8")) == 1000


def test_bounded_text_truncates_multibyte_values_at_a_valid_utf8_boundary() -> None:
    value = "é" * 600
    data = value.encode("utf-8")
    node = parse_python_fixture(data).root_node

    bounded = bounded_node_text(node, SourceBuffer(data, data, 0))

    assert bounded.was_truncated is True
    assert bounded.text == ("é" * 498) + "…"
    assert len(bounded.text.encode("utf-8")) == 999
    assert bounded.text.encode("utf-8").decode("utf-8") == bounded.text


def test_iter_events_is_source_ordered_and_handles_deep_real_trees_iteratively() -> None:
    data = (b"value = (" * 400) + b"1" + (b")" * 400)
    root = parse_python_fixture(data).root_node

    events = list(iter_events(root))

    assert events[0].kind is ENTER
    assert events[0].node == root
    assert events[-1].kind is EXIT
    assert events[-1].node == root
    assert len(events) == 2 * sum(1 for _ in iter_events(root) if _.kind is ENTER)
    assert [event.node.type for event in events[:4]] == [
        "module",
        "expression_statement",
        "assignment",
        "identifier",
    ]


def test_scope_stack_keeps_the_nearest_named_callable_across_anonymous_nodes() -> None:
    scopes = ScopeStack()

    scopes.push("Outer", is_callable=False)
    scopes.push("Outer.method", is_callable=True)
    scopes.push("Outer.method.callback", is_callable=False)
    assert scopes.nearest_callable == "Outer.method"

    assert scopes.pop() == "Outer.method.callback"
    scopes.push("Outer.method.inner", is_callable=True)
    assert scopes.nearest_callable == "Outer.method.inner"
    assert scopes.pop() == "Outer.method.inner"
    assert scopes.pop() == "Outer.method"
    assert scopes.nearest_callable is None


def extraction_location(start_byte: int, end_byte: int) -> SourceLocation:
    return SourceLocation(start_byte, end_byte, 1, start_byte, 1, end_byte)


def test_normalize_extraction_deduplicates_first_values_and_sorts_by_design_keys() -> None:
    first_symbol = SymbolInfo(
        "same", SymbolKind.FUNCTION, "first.same", None, extraction_location(20, 24), (), None,
        ("ASYNC", "public", "unknown", "async"), (), (),
    )
    duplicate_symbol = SymbolInfo(
        "same", SymbolKind.FUNCTION, "second.same", None, extraction_location(20, 24), (), None,
        ("final",), (), (),
    )
    earlier_symbol = SymbolInfo(
        "classy", SymbolKind.CLASS, "classy", None, extraction_location(3, 9), (), None,
        (), (), (),
    )
    first_import = ImportInfo(
        "z.module", (ImportBinding("item", None),), False, ("STATIC", "unknown"),
        extraction_location(30, 38),
    )
    duplicate_import = ImportInfo(
        "z.module", (ImportBinding("item", None),), False, ("final",), extraction_location(30, 38),
    )
    earlier_import = ImportInfo("a.module", (), False, (), extraction_location(4, 8))
    first_call = CallSite("first.same", "run", CallKind.CALL, extraction_location(40, 43))
    duplicate_call = CallSite("first.same", "run", CallKind.CALL, extraction_location(40, 43))
    earlier_call = CallSite(None, "build", CallKind.CONSTRUCTOR, extraction_location(2, 7))
    first_issue = ParseIssue(ParseIssueKind.SYNTAX_ERROR, "first", extraction_location(50, 51))
    duplicate_issue = ParseIssue(ParseIssueKind.SYNTAX_ERROR, "second", extraction_location(50, 51))
    earlier_issue = ParseIssue(ParseIssueKind.MISSING_NODE, "missing", extraction_location(1, 2))
    locationless_issue = ParseIssue(ParseIssueKind.EXTRACTION_ERROR, "later", None)

    normalized = normalize_extraction(
        ExtractionResult(
            (first_symbol, duplicate_symbol, earlier_symbol),
            (first_import, duplicate_import, earlier_import),
            (first_call, duplicate_call, earlier_call),
            (first_issue, duplicate_issue, earlier_issue, locationless_issue),
        )
    )

    assert [symbol.qualified_name for symbol in normalized.symbols] == ["classy", "first.same"]
    assert normalized.symbols[1].modifiers == ("public", "async")
    assert [item.module for item in normalized.imports] == ["a.module", "z.module"]
    assert normalized.imports[1].modifiers == ("static",)
    assert [item.callee_text for item in normalized.calls] == ["build", "run"]
    assert [(item.kind, item.message) for item in normalized.issues] == [
        (ParseIssueKind.MISSING_NODE, "missing"),
        (ParseIssueKind.SYNTAX_ERROR, "first"),
        (ParseIssueKind.EXTRACTION_ERROR, "later"),
    ]


def test_modifier_order_is_fixed_and_ignores_unknown_values() -> None:
    assert normalize_modifiers(
        ("generator", "PRIVATE", "readonly", "async", "static", "private", "unknown")
    ) == ("private", "static", "async", "readonly", "generator")


def extract_python_fixture(data: bytes) -> ExtractionResult:
    source = SourceBuffer(data, data, 0)
    return get_extractor("python").extract(parse_python_fixture(data), source)


def test_python_extractor_captures_primary_structure_and_original_ranges() -> None:
    data = b'''import os
from app.services import AuthService as Service

class UserController(BaseController):
    def __init__(self, service: Service = Service()) -> None:
        self.service = service

    def login(self, email: str = "guest") -> bool:
        return self.service.authenticate(email)

def helper(value: int) -> str:
    return str(value)
'''

    result = extract_python_fixture(data)

    assert result == ExtractionResult(
        symbols=(
            SymbolInfo(
                "UserController",
                SymbolKind.CLASS,
                "UserController",
                None,
                SourceLocation(59, 289, 4, 0, 9, 47),
                (),
                None,
                (),
                ("BaseController",),
                (),
            ),
            SymbolInfo(
                "__init__",
                SymbolKind.CONSTRUCTOR,
                "UserController.__init__",
                "UserController",
                SourceLocation(101, 189, 5, 4, 6, 30),
                (
                    ParameterInfo("self", None, None),
                    ParameterInfo("service", "Service", "Service()"),
                ),
                "None",
                (),
                (),
                (),
            ),
            SymbolInfo(
                "login",
                SymbolKind.METHOD,
                "UserController.login",
                "UserController",
                SourceLocation(195, 289, 8, 4, 9, 47),
                (
                    ParameterInfo("self", None, None),
                    ParameterInfo("email", "str", '"guest"'),
                ),
                "bool",
                (),
                (),
                (),
            ),
            SymbolInfo(
                "helper",
                SymbolKind.FUNCTION,
                "helper",
                None,
                SourceLocation(291, 343, 11, 0, 12, 21),
                (ParameterInfo("value", "int", None),),
                "str",
                (),
                (),
                (),
            ),
        ),
        imports=(
            ImportInfo(
                "os",
                (ImportBinding("os", None),),
                False,
                (),
                SourceLocation(0, 9, 1, 0, 1, 9),
            ),
            ImportInfo(
                "app.services",
                (ImportBinding("AuthService", "Service"),),
                False,
                (),
                SourceLocation(10, 57, 2, 0, 2, 47),
            ),
        ),
        calls=(
            CallSite(
                "UserController.__init__",
                "Service",
                CallKind.CALL,
                SourceLocation(139, 148, 5, 42, 5, 51),
            ),
            CallSite(
                "UserController.login",
                "self.service.authenticate",
                CallKind.CALL,
                SourceLocation(257, 289, 9, 15, 9, 47),
            ),
            CallSite(
                "helper",
                "str",
                CallKind.CALL,
                SourceLocation(333, 343, 12, 11, 12, 21),
            ),
        ),
        issues=(),
    )
    assert [data[item.location.start_byte : item.location.end_byte] for item in result.symbols] == [
        data[59:289],
        data[101:189],
        data[195:289],
        data[291:343],
    ]


def test_python_extractor_keeps_async_declaration_and_decorator_call_ownership() -> None:
    data = b'''@register(factory())
async def fetch(value: Input = default()) -> Output:
    return await load(value)
'''

    result = extract_python_fixture(data)

    assert result.symbols == (
        SymbolInfo(
            "fetch",
            SymbolKind.FUNCTION,
            "fetch",
            None,
            SourceLocation(21, 102, 2, 0, 3, 28),
            (ParameterInfo("value", "Input", "default()"),),
            "Output",
            ("async",),
            (),
            (),
        ),
    )
    assert result.calls == (
        CallSite(None, "register", CallKind.CALL, SourceLocation(1, 20, 1, 1, 1, 20)),
        CallSite(None, "factory", CallKind.CALL, SourceLocation(10, 19, 1, 10, 1, 19)),
        CallSite("fetch", "default", CallKind.CALL, SourceLocation(52, 61, 2, 31, 2, 40)),
        CallSite("fetch", "load", CallKind.CALL, SourceLocation(91, 102, 3, 17, 3, 28)),
    )
    assert result.imports == ()
    assert result.issues == ()
    assert data[result.symbols[0].location.start_byte : result.symbols[0].location.end_byte].startswith(
        b"async def fetch"
    )


def test_python_extractor_qualifies_nested_functions_and_owns_inner_calls() -> None:
    data = b'''def outer():
    def inner(value):
        return transform(value)
    return inner(1)
'''

    result = extract_python_fixture(data)

    assert [(item.kind, item.qualified_name, item.parent_qualified_name) for item in result.symbols] == [
        (SymbolKind.FUNCTION, "outer", None),
        (SymbolKind.FUNCTION, "outer.inner", "outer"),
    ]
    assert result.calls == (
        CallSite(
            "outer.inner",
            "transform",
            CallKind.CALL,
            SourceLocation(50, 66, 3, 15, 3, 31),
        ),
        CallSite("outer", "inner", CallKind.CALL, SourceLocation(78, 86, 4, 11, 4, 19)),
    )


def test_python_extractor_classifies_only_class_init_as_constructor() -> None:
    data = b'''def __init__(self):
    pass

class Item:
    def __init__(self):
        build()

    def refresh(cls):
        reset()
'''

    result = extract_python_fixture(data)

    assert [(item.kind, item.qualified_name, item.parent_qualified_name) for item in result.symbols] == [
        (SymbolKind.FUNCTION, "__init__", None),
        (SymbolKind.CLASS, "Item", None),
        (SymbolKind.CONSTRUCTOR, "Item.__init__", "Item"),
        (SymbolKind.METHOD, "Item.refresh", "Item"),
    ]
    assert result.symbols[0].parameters == (ParameterInfo("self", None, None),)
    assert result.symbols[2].parameters == (ParameterInfo("self", None, None),)
    assert result.symbols[3].parameters == (ParameterInfo("cls", None, None),)
    assert [(item.caller_qualified_name, item.callee_text, item.kind) for item in result.calls] == [
        ("Item.__init__", "build", CallKind.CALL),
        ("Item.refresh", "reset", CallKind.CALL),
    ]


def test_python_extractor_retains_untyped_splat_parameter_identifiers() -> None:
    data = b'''def f(*args, **kwargs):
    pass
'''

    result = extract_python_fixture(data)

    assert result.symbols[0].parameters == (
        ParameterInfo("args", None, None),
        ParameterInfo("kwargs", None, None),
    )


def test_python_extractor_normalizes_annotated_splats_without_losing_parameter_order() -> None:
    data = b'''def annotated(prefix: str = "x", *args: int, flag: bool = True, **kwargs: object):
    pass
'''

    result = extract_python_fixture(data)

    assert result.symbols[0].parameters == (
        ParameterInfo("prefix", "str", '"x"'),
        ParameterInfo("args", "int", None),
        ParameterInfo("flag", "bool", "True"),
        ParameterInfo("kwargs", "object", None),
    )


def test_python_extractor_emits_only_module_and_class_uppercase_constants() -> None:
    data = b'''MODULE_VALUE = 1
lower = 2

class Settings:
    CLASS_VALUE: int = 3
    lower = 4

    def update(self):
        LOCAL_VALUE = 5
        self.ATTRIBUTE_VALUE = 6
'''

    result = extract_python_fixture(data)

    constants = [item for item in result.symbols if item.kind is SymbolKind.CONSTANT]
    assert constants == [
        SymbolInfo(
            "MODULE_VALUE",
            SymbolKind.CONSTANT,
            "MODULE_VALUE",
            None,
            SourceLocation(0, 16, 1, 0, 1, 16),
            (),
            None,
            (),
            (),
            (),
        ),
        SymbolInfo(
            "CLASS_VALUE",
            SymbolKind.CONSTANT,
            "Settings.CLASS_VALUE",
            "Settings",
            SourceLocation(48, 68, 5, 4, 5, 24),
            (),
            None,
            (),
            (),
            (),
        ),
    ]
    assert [item.qualified_name for item in result.symbols] == [
        "MODULE_VALUE",
        "Settings",
        "Settings.CLASS_VALUE",
        "Settings.update",
    ]


def test_python_extractor_normalizes_wildcard_relative_and_aliased_imports() -> None:
    data = b'''from . import local
from ..pkg import *
import os.path as osp
'''

    result = extract_python_fixture(data)

    assert result.imports == (
        ImportInfo(
            ".",
            (ImportBinding("local", None),),
            False,
            (),
            SourceLocation(0, 19, 1, 0, 1, 19),
        ),
        ImportInfo(
            "..pkg",
            (),
            True,
            (),
            SourceLocation(20, 39, 2, 0, 2, 19),
        ),
        ImportInfo(
            "os.path",
            (ImportBinding("os.path", "osp"),),
            False,
            (),
            SourceLocation(40, 61, 3, 0, 3, 21),
        ),
    )


def test_python_extractor_keeps_class_looking_invocations_as_plain_calls() -> None:
    data = b'''def make():
    return User()
'''

    result = extract_python_fixture(data)

    assert result.calls == (
        CallSite("make", "User", CallKind.CALL, SourceLocation(23, 29, 2, 11, 2, 17)),
    )


def test_python_extractor_bounds_declared_text_and_reports_one_issue() -> None:
    type_name = b"T" * 1001
    default_name = b"D" * 1001
    return_name = b"R" * 1001
    callee_name = b"C" * 1001
    data = (
        b"def bounded(value: "
        + type_name
        + b" = "
        + default_name
        + b") -> "
        + return_name
        + b":\n    return "
        + callee_name
        + b"()\n"
    )

    result = extract_python_fixture(data)

    assert len(result.symbols) == 1
    assert result.symbols[0].name == "bounded"
    assert result.symbols[0].parameters == (
        ParameterInfo("value", ("T" * 997) + "…", ("D" * 997) + "…"),
    )
    assert result.symbols[0].return_type == ("R" * 997) + "…"
    assert result.calls[0].callee_text == ("C" * 997) + "…"
    assert result.calls[0].kind is CallKind.CALL
    call_location = result.calls[0].location
    assert data[call_location.start_byte : call_location.end_byte] == callee_name + b"()"
    assert result.issues == (
        ParseIssue(
            ParseIssueKind.EXTRACTION_ERROR,
            "Extracted text exceeded the 1,000-byte limit.",
            None,
        ),
    )


def test_python_extractor_ignores_comments_and_docstrings_as_metadata() -> None:
    data = b'''"""module docs"""
# note
def documented():
    """function docs"""
    return None
'''

    result = extract_python_fixture(data)

    assert [(item.kind, item.qualified_name) for item in result.symbols] == [
        (SymbolKind.FUNCTION, "documented"),
    ]
    assert result.imports == ()
    assert result.calls == ()
    assert result.issues == ()
    assert not hasattr(result.symbols[0], "metadata")


def parse_java_fixture(data: bytes):
    parser = Parser(Language(tree_sitter_java.language()))
    return parser.parse(data)


def extract_java_fixture(data: bytes) -> ExtractionResult:
    source = SourceBuffer(data, data, 0)
    return get_extractor("java").extract(parse_java_fixture(data), source)


def test_java_extractor_captures_primary_structure_and_original_ranges() -> None:
    data = b'''package com.example.auth;

import java.util.Optional;

public final class AuthService implements AuthProvider {
    private UserRepository repository;

    public AuthService(UserRepository repository) {
        this.repository = repository;
    }

    public Optional<User> find(String email) {
        return repository.findByEmail(email);
    }
}
'''

    result = extract_java_fixture(data)

    assert result == ExtractionResult(
        symbols=(
            SymbolInfo(
                "AuthService",
                SymbolKind.CLASS,
                "com.example.auth.AuthService",
                None,
                SourceLocation(55, 349, 5, 0, 15, 1),
                (),
                None,
                ("public", "final"),
                (),
                ("AuthProvider",),
            ),
            SymbolInfo(
                "AuthService",
                SymbolKind.CONSTRUCTOR,
                "com.example.auth.AuthService.AuthService",
                "com.example.auth.AuthService",
                SourceLocation(156, 247, 8, 4, 10, 5),
                (ParameterInfo("repository", "UserRepository", None),),
                None,
                ("public",),
                (),
                (),
            ),
            SymbolInfo(
                "find",
                SymbolKind.METHOD,
                "com.example.auth.AuthService.find",
                "com.example.auth.AuthService",
                SourceLocation(253, 347, 12, 4, 14, 5),
                (ParameterInfo("email", "String", None),),
                "Optional<User>",
                ("public",),
                (),
                (),
            ),
        ),
        imports=(
            ImportInfo(
                "java.util.Optional",
                (),
                False,
                (),
                SourceLocation(27, 53, 3, 0, 3, 26),
            ),
        ),
        calls=(
            CallSite(
                "com.example.auth.AuthService.find",
                "repository.findByEmail",
                CallKind.CALL,
                SourceLocation(311, 340, 13, 15, 13, 44),
            ),
        ),
        issues=(),
    )
    assert all(symbol.name != "repository" for symbol in result.symbols)
    assert [data[item.location.start_byte : item.location.end_byte] for item in result.symbols] == [
        data[55:349],
        data[156:247],
        data[253:347],
    ]


def test_java_extractor_keeps_interface_signatures_and_extends_as_base_types() -> None:
    data = b'''package api;
interface Child extends Parent, Auditable {
    public abstract String find(int id);
}
'''

    result = extract_java_fixture(data)

    assert [(item.kind, item.qualified_name, item.parent_qualified_name) for item in result.symbols] == [
        (SymbolKind.INTERFACE, "api.Child", None),
        (SymbolKind.METHOD, "api.Child.find", "api.Child"),
    ]
    assert result.symbols[0].base_types == ("Parent", "Auditable")
    assert result.symbols[0].implemented_types == ()
    assert result.symbols[1].parameters == (ParameterInfo("id", "int", None),)
    assert result.symbols[1].return_type == "String"
    assert result.symbols[1].modifiers == ("public", "abstract")


def test_java_extractor_keeps_enum_without_emitting_enum_members() -> None:
    result = extract_java_fixture(b"enum Status { ACTIVE, INACTIVE }\n")

    assert [(item.kind, item.qualified_name) for item in result.symbols] == [
        (SymbolKind.ENUM, "Status"),
    ]


def test_java_extractor_splits_class_extends_and_multiple_implements() -> None:
    result = extract_java_fixture(
        b"class Child extends Base implements First, Second {}\n"
    )

    assert len(result.symbols) == 1
    assert result.symbols[0].base_types == ("Base",)
    assert result.symbols[0].implemented_types == ("First", "Second")


def test_java_extractor_keeps_overloads_at_distinct_locations() -> None:
    data = b'''class Search {
    void find(int id) {}
    void find(String id) {}
}
'''

    methods = [
        item for item in extract_java_fixture(data).symbols
        if item.kind is SymbolKind.METHOD
    ]

    assert [item.qualified_name for item in methods] == ["Search.find", "Search.find"]
    assert [item.location for item in methods] == [
        SourceLocation(19, 39, 2, 4, 2, 24),
        SourceLocation(44, 67, 3, 4, 3, 27),
    ]
    assert [item.parameters for item in methods] == [
        (ParameterInfo("id", "int", None),),
        (ParameterInfo("id", "String", None),),
    ]


def test_java_extractor_distinguishes_object_creation_from_method_invocation() -> None:
    data = b'''class Factory {
    User make() {
        audit();
        return new User();
    }
}
'''

    result = extract_java_fixture(data)

    assert [(item.caller_qualified_name, item.callee_text, item.kind) for item in result.calls] == [
        ("Factory.make", "audit", CallKind.CALL),
        ("Factory.make", "User", CallKind.CONSTRUCTOR),
    ]


def test_java_extractor_normalizes_static_wildcard_imports() -> None:
    data = b"import static java.util.Collections.*;\n"

    result = extract_java_fixture(data)

    assert result.imports == (
        ImportInfo(
            "java.util.Collections",
            (),
            True,
            ("static",),
            SourceLocation(0, 38, 1, 0, 1, 38),
        ),
    )


def test_java_extractor_emits_only_final_type_fields_as_constants() -> None:
    data = b'''interface Settings {
    int DEFAULT = 1;
}
class Constants {
    public static final int MAX = 3;
    final String NAME = "app";
    int ordinary = 4;
    void update() {
        final int local = 5;
    }
}
'''

    result = extract_java_fixture(data)
    constants = [item for item in result.symbols if item.kind is SymbolKind.CONSTANT]

    assert [(item.qualified_name, item.modifiers) for item in constants] == [
        ("Constants.MAX", ("public", "static", "final")),
        ("Constants.NAME", ("final",)),
    ]
    assert all(item.name not in {"DEFAULT", "ordinary", "local"} for item in result.symbols)


def test_java_extractor_qualifies_nested_types_and_their_methods() -> None:
    data = b'''package nested;
class Outer {
    interface Inner {
        void run();
    }
    enum State { ON }
    class Child {}
}
'''

    result = extract_java_fixture(data)

    assert [(item.kind, item.qualified_name, item.parent_qualified_name) for item in result.symbols] == [
        (SymbolKind.CLASS, "nested.Outer", None),
        (SymbolKind.INTERFACE, "nested.Outer.Inner", "nested.Outer"),
        (SymbolKind.METHOD, "nested.Outer.Inner.run", "nested.Outer.Inner"),
        (SymbolKind.ENUM, "nested.Outer.State", "nested.Outer"),
        (SymbolKind.CLASS, "nested.Outer.Child", "nested.Outer"),
    ]


def test_java_extractor_leaves_initializer_block_calls_unowned() -> None:
    data = b'''class Init {
    { boot(); }
    static { warm(); }
    void run() { go(); }
}
'''

    result = extract_java_fixture(data)

    assert [(item.caller_qualified_name, item.callee_text) for item in result.calls] == [
        (None, "boot"),
        (None, "warm"),
        ("Init.run", "go"),
    ]


def test_java_extractor_local_type_is_a_call_ownership_barrier() -> None:
    data = b'''class C {
    void outer() {
        class Local {
            { init(); }
            Object value = make();
            void inner() { run(); }
        }
    }
}
'''

    result = extract_java_fixture(data)

    assert [(item.caller_qualified_name, item.callee_text) for item in result.calls] == [
        (None, "init"),
        (None, "make"),
        ("C.outer.Local.inner", "run"),
    ]


def test_java_extractor_preserves_qualified_receiver_parameter_pattern() -> None:
    data = b'''class Outer {
    class Inner {
        Inner(Outer Outer.this) {}
    }
}
'''

    result = extract_java_fixture(data)
    constructor = next(
        item for item in result.symbols if item.kind is SymbolKind.CONSTRUCTOR
    )

    assert constructor.parameters == (
        ParameterInfo("Outer.this", "Outer", None),
    )


def test_java_extractor_filters_comments_and_package_annotations_from_names() -> None:
    data = b'''@Deprecated
package /* package note */ com.example;

import /* import note */ java.util.List;

class Child extends /* base note */ Base {}
'''

    result = extract_java_fixture(data)

    assert [(item.qualified_name, item.base_types) for item in result.symbols] == [
        ("com.example.Child", ("Base",)),
    ]
    assert [(item.module, item.bindings) for item in result.imports] == [
        ("java.util.List", ()),
    ]
    assert all(
        "note" not in value and "Deprecated" not in value
        for value in (
            result.symbols[0].qualified_name,
            *result.symbols[0].base_types,
            result.imports[0].module,
        )
    )


def parse_javascript_fixture(data: bytes):
    parser = Parser(Language(tree_sitter_javascript.language()))
    return parser.parse(data)


def extract_javascript_fixture(data: bytes) -> ExtractionResult:
    source = SourceBuffer(data, data, 0)
    return get_extractor("javascript").extract(parse_javascript_fixture(data), source)


def test_javascript_extractor_captures_primary_structure_and_original_ranges() -> None:
    data = b'''import api from "./api.js";

export default class AuthService {
    constructor(client) {
        this.client = client;
    }

    async login(user, password = "guest") {
        return api.login(user, password);
    }
}

export function logout(user) {
    return api.logout(user);
}
'''

    result = extract_javascript_fixture(data)

    assert result == ExtractionResult(
        symbols=(
            SymbolInfo(
                "AuthService",
                SymbolKind.CLASS,
                "AuthService",
                None,
                SourceLocation(44, 220, 3, 15, 11, 1),
                (),
                None,
                ("export", "default"),
                (),
                (),
            ),
            SymbolInfo(
                "constructor",
                SymbolKind.CONSTRUCTOR,
                "AuthService.constructor",
                "AuthService",
                SourceLocation(68, 125, 4, 4, 6, 5),
                (ParameterInfo("client", None, None),),
                None,
                (),
                (),
                (),
            ),
            SymbolInfo(
                "login",
                SymbolKind.METHOD,
                "AuthService.login",
                "AuthService",
                SourceLocation(131, 218, 8, 4, 10, 5),
                (
                    ParameterInfo("user", None, None),
                    ParameterInfo("password", None, '"guest"'),
                ),
                None,
                ("async",),
                (),
                (),
            ),
            SymbolInfo(
                "logout",
                SymbolKind.FUNCTION,
                "logout",
                None,
                SourceLocation(229, 283, 13, 7, 15, 1),
                (ParameterInfo("user", None, None),),
                None,
                ("export",),
                (),
                (),
            ),
        ),
        imports=(
            ImportInfo(
                "./api.js",
                (ImportBinding("default", "api"),),
                False,
                (),
                SourceLocation(0, 27, 1, 0, 1, 27),
            ),
        ),
        calls=(
            CallSite(
                "AuthService.login",
                "api.login",
                CallKind.CALL,
                SourceLocation(186, 211, 9, 15, 9, 40),
            ),
            CallSite(
                "logout",
                "api.logout",
                CallKind.CALL,
                SourceLocation(264, 280, 14, 11, 14, 27),
            ),
        ),
        issues=(),
    )
    assert all(symbol.return_type is None for symbol in result.symbols)
    assert [data[item.location.start_byte : item.location.end_byte] for item in result.symbols] == [
        data[44:220],
        data[68:125],
        data[131:218],
        data[229:283],
    ]


def test_javascript_extractor_names_only_direct_function_bindings_and_inherits_callback_owner() -> None:
    data = b'''function outer(items) {
    const arrow = (value = 1) => work(value);
    let expression = function internal(value) { return work2(value); };
    assigned = async function(value) { return work3(value); };
    items.map(item => transform(item));
    return arrow(1);
}
setTimeout(() => tick(), 0);
ignored ||= () => nope();
'''

    result = extract_javascript_fixture(data)

    assert [
        (item.qualified_name, item.parent_qualified_name, item.parameters, item.modifiers)
        for item in result.symbols
    ] == [
        ("outer", None, (ParameterInfo("items", None, None),), ()),
        ("outer.arrow", "outer", (ParameterInfo("value", None, "1"),), ()),
        ("outer.expression", "outer", (ParameterInfo("value", None, None),), ()),
        ("outer.assigned", "outer", (ParameterInfo("value", None, None),), ("async",)),
    ]
    assert [(item.caller_qualified_name, item.callee_text) for item in result.calls] == [
        ("outer.arrow", "work"),
        ("outer.expression", "work2"),
        ("outer.assigned", "work3"),
        ("outer", "items.map"),
        ("outer", "transform"),
        ("outer", "arrow"),
        (None, "setTimeout"),
        (None, "tick"),
        (None, "nope"),
    ]
    assert all(item.name not in {"ignored", "internal", "item"} for item in result.symbols)


def test_javascript_extractor_limits_constants_and_keeps_bounded_call_syntax() -> None:
    data = b'''const LIMIT = 3;
const { FIRST } = settings;
const helper = () => {
    const LOCAL = 1;
    new Client();
    registry[key](LOCAL);
};
class Child extends framework.Base {}
'''

    result = extract_javascript_fixture(data)

    assert [(item.kind, item.qualified_name) for item in result.symbols] == [
        (SymbolKind.CONSTANT, "LIMIT"),
        (SymbolKind.FUNCTION, "helper"),
        (SymbolKind.CLASS, "Child"),
    ]
    assert result.symbols[-1].base_types == ("framework.Base",)
    assert all(item.name not in {"FIRST", "LOCAL"} for item in result.symbols)
    assert [(item.caller_qualified_name, item.callee_text, item.kind) for item in result.calls] == [
        ("helper", "Client", CallKind.CONSTRUCTOR),
        ("helper", "registry[key]", CallKind.CALL),
    ]


def test_javascript_extractor_normalizes_es_and_commonjs_import_policies() -> None:
    data = b'''import "./setup.js";
import defaultApi, { readFile as read, writeFile } from "lib";
import * as path from "path";
const fs = require("fs");
const { join: combine, resolve } = require("path-tools");
require("side");
require(name);
import("lazy");
'''

    result = extract_javascript_fixture(data)

    assert result.imports == (
        ImportInfo("./setup.js", (), False, (), SourceLocation(0, 20, 1, 0, 1, 20)),
        ImportInfo(
            "lib",
            (
                ImportBinding("default", "defaultApi"),
                ImportBinding("readFile", "read"),
                ImportBinding("writeFile", None),
            ),
            False,
            (),
            SourceLocation(21, 83, 2, 0, 2, 62),
        ),
        ImportInfo(
            "path",
            (ImportBinding("*", "path"),),
            True,
            (),
            SourceLocation(84, 113, 3, 0, 3, 29),
        ),
        ImportInfo(
            "fs",
            (ImportBinding("*", "fs"),),
            True,
            (),
            SourceLocation(125, 138, 4, 11, 4, 24),
        ),
        ImportInfo(
            "path-tools",
            (
                ImportBinding("join", "combine"),
                ImportBinding("resolve", None),
            ),
            False,
            (),
            SourceLocation(175, 196, 5, 35, 5, 56),
        ),
        ImportInfo("side", (), False, (), SourceLocation(198, 213, 6, 0, 6, 15)),
    )
    assert [(item.callee_text, item.kind) for item in result.calls] == [
        ("require", CallKind.CALL),
        ("require", CallKind.CALL),
        ("require", CallKind.CALL),
        ("require", CallKind.CALL),
        ("import", CallKind.CALL),
    ]
    assert [item.module for item in result.imports] == [
        "./setup.js",
        "lib",
        "path",
        "fs",
        "path-tools",
        "side",
    ]


def test_jsx_extractor_ignores_elements_and_keeps_expression_container_calls() -> None:
    data = b'''export function LoginPanel({ user }) {
    return <Panel onClick={() => clicked()}>{format(user)}<Widget /></Panel>;
}
'''
    tree = parse_javascript_fixture(data)

    result = get_extractor("javascript").extract(tree, SourceBuffer(data, data, 0))

    assert not tree.root_node.has_error
    assert [(item.kind, item.qualified_name, item.parameters) for item in result.symbols] == [
        (
            SymbolKind.FUNCTION,
            "LoginPanel",
            (ParameterInfo("{ user }", None, None),),
        ),
    ]
    assert [(item.caller_qualified_name, item.callee_text) for item in result.calls] == [
        ("LoginPanel", "clicked"),
        ("LoginPanel", "format"),
    ]
    assert result.imports == ()
    assert result.issues == ()


@pytest.mark.parametrize(
    ("value", "field_order", "field_to_mutate", "replacement"),
    [
        (
            ParameterInfo("name", "str", "'value'"),
            ("name", "type_name", "default_value_text"),
            "name",
            "other",
        ),
        (
            ImportBinding("item", "renamed"),
            ("imported_name", "alias"),
            "alias",
            None,
        ),
        (
            ImportInfo(
                "package",
                (ImportBinding("item", None),),
                False,
                ("static",),
                SourceLocation(0, 1, 1, 0, 1, 1),
            ),
            ("module", "bindings", "is_wildcard", "modifiers", "location"),
            "module",
            "other.package",
        ),
        (
            SymbolInfo(
                "worker",
                SymbolKind.FUNCTION,
                "worker",
                None,
                SourceLocation(0, 1, 1, 0, 1, 1),
                (ParameterInfo("input", None, None),),
                None,
                ("async",),
                ("Base",),
                ("Protocol",),
            ),
            (
                "name", "kind", "qualified_name", "parent_qualified_name", "location",
                "parameters", "return_type", "modifiers", "base_types", "implemented_types",
            ),
            "qualified_name",
            "other",
        ),
        (
            CallSite(
                "worker",
                "service.run",
                CallKind.CALL,
                SourceLocation(0, 1, 1, 0, 1, 1),
            ),
            ("caller_qualified_name", "callee_text", "kind", "location"),
            "callee_text",
            "other.run",
        ),
        (
            ParseIssue(
                ParseIssueKind.SYNTAX_ERROR,
                "syntax error",
                SourceLocation(0, 1, 1, 0, 1, 1),
            ),
            ("kind", "message", "location"),
            "message",
            "other error",
        ),
        (
            ParsedFile(
                "src/module.py",
                ParsedLanguage.PYTHON,
                ParseStatus.SUCCESS,
                (),
                (),
                (),
                (),
            ),
            ("relative_path", "language", "status", "symbols", "imports", "calls", "issues"),
            "status",
            ParseStatus.FAILED,
        ),
        (
            SkippedParseFile("src/unknown.rs", ParseSkipReason.UNSUPPORTED_LANGUAGE),
            ("relative_path", "reason"),
            "reason",
            ParseSkipReason.UNSUPPORTED_LANGUAGE,
        ),
        (
            CodeParseInventory(
                "/repository",
                1,
                1,
                0,
                0,
                0,
                (
                    ParsedFile(
                        "src/module.py",
                        ParsedLanguage.PYTHON,
                        ParseStatus.SUCCESS,
                        (),
                        (),
                        (),
                        (),
                    ),
                ),
                (),
            ),
            (
                "repository_path", "total_files_requested", "success_files", "partial_files",
                "failed_files", "skipped_files", "files", "skipped",
            ),
            "success_files",
            2,
        ),
    ],
)
def test_models_are_frozen_slotted_and_keep_declared_field_order(
    value: object,
    field_order: tuple[str, ...],
    field_to_mutate: str,
    replacement: object,
) -> None:
    assert tuple(value.__dataclass_fields__) == field_order  # type: ignore[attr-defined]
    assert not hasattr(value, "__dict__")
    assert not any(
        field in value.__dataclass_fields__  # type: ignore[attr-defined]
        for field in ("source", "source_text", "tree", "syntax_tree")
    )
    with pytest.raises(FrozenInstanceError):
        setattr(value, field_to_mutate, replacement)


@pytest.mark.parametrize(
    "error_type",
    [InvalidParseInventory, ParserConfigurationError, RepositoryParseError],
)
def test_fatal_errors_share_code_parser_base(error_type: type[Exception]) -> None:
    assert issubclass(error_type, CodeParserError)


def scanned_file(
    relative_path: str,
    *,
    language: str | None,
    extension: str,
) -> ScannedFile:
    return ScannedFile(
        relative_path=relative_path,
        filename=relative_path.rsplit("/", maxsplit=1)[-1],
        extension=extension,
        language=language,
        category=FileCategory.SOURCE,
        size_bytes=0,
    )


def write_inventory_file(
    root: Path,
    relative_path: str,
    data: bytes,
    language: str | None,
) -> ScannedFile:
    path = root.joinpath(*relative_path.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return ScannedFile(
        relative_path=relative_path,
        filename=relative_path.rsplit("/", maxsplit=1)[-1],
        extension=Path(relative_path).suffix,
        language=language,
        category=FileCategory.SOURCE,
        size_bytes=len(data),
    )


def source_candidate(relative_path: str, size_bytes: int = 0) -> ScannedFile:
    return ScannedFile(
        relative_path=relative_path,
        filename=relative_path.rsplit("/", maxsplit=1)[-1],
        extension=".py",
        language="python",
        category=FileCategory.SOURCE,
        size_bytes=size_bytes,
    )


def platform_opener_name() -> str:
    return "_open_verified_windows" if os.name == "nt" else "_open_verified_posix"


def replace_platform_opener(
    monkeypatch: pytest.MonkeyPatch,
    replacement: object,
) -> None:
    monkeypatch.setattr(reader_module, platform_opener_name(), replacement)


class MutatingReadStream:
    def __init__(
        self,
        stream: object,
        *,
        before_read: object | None = None,
        after_read: object | None = None,
        after_close: object | None = None,
        trigger_call: int = 1,
    ) -> None:
        self._stream = stream
        self._before_read = before_read
        self._after_read = after_read
        self._after_close = after_close
        self._trigger_call = trigger_call
        self._read_calls = 0

    def fileno(self) -> int:
        return self._stream.fileno()  # type: ignore[no-any-return, union-attr]

    def read(self, size: int = -1) -> bytes:
        self._read_calls += 1
        if self._read_calls == self._trigger_call and self._before_read is not None:
            self._before_read()  # type: ignore[operator]
        data = self._stream.read(size)  # type: ignore[union-attr]
        if self._read_calls == self._trigger_call and self._after_read is not None:
            self._after_read()  # type: ignore[operator]
        return data

    def __enter__(self) -> "MutatingReadStream":
        return self

    def __exit__(self, *args: object) -> None:
        self._stream.close()  # type: ignore[union-attr]
        if self._after_close is not None:
            self._after_close()  # type: ignore[operator]


@pytest.mark.parametrize("root_kind", ["missing", "file"])
def test_repository_root_must_resolve_to_an_existing_directory(
    tmp_path: Path,
    root_kind: str,
) -> None:
    root = tmp_path / root_kind
    if root_kind == "file":
        root.write_bytes(b"not a directory")

    with pytest.raises(RepositoryParseError):
        SafeSourceReader(root)


@pytest.mark.parametrize(
    "relative_path",
    [
        "../../outside.py",
        "/absolute.py",
        "C:/outside.py",
        "src\\file.py",
        "src/../file.py",
        "./file.py",
        "src//file.py",
        "",
        "bad\x00.py",
    ],
)
def test_path_invalid_is_rejected_before_the_open_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_path: str,
) -> None:
    def forbidden_open(root: Path, parts: tuple[str, ...]):
        raise AssertionError(f"open boundary reached for {root!s} and {parts!r}")

    replace_platform_opener(monkeypatch, forbidden_open)

    with pytest.raises(SourceReadError) as raised:
        SafeSourceReader(tmp_path).read(source_candidate(relative_path))

    assert raised.value.kind is ParseIssueKind.PATH_INVALID


def test_reader_reconstructs_valid_nested_posix_path_under_resolved_repository_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"answer = 42\n"
    file = write_inventory_file(tmp_path, "src/nested/app.py", data, "python")
    calls: list[tuple[Path, tuple[str, ...]]] = []
    platform_opener = getattr(reader_module, platform_opener_name())

    def recording_open(root: Path, parts: tuple[str, ...]):
        calls.append((root, parts))
        assert platform_opener is not None
        return platform_opener(root, parts)

    replace_platform_opener(monkeypatch, recording_open)

    source = SafeSourceReader(tmp_path / ".").read(file)

    assert source.original_bytes == data
    expected_calls = 1 if os.name == "nt" else 2
    assert calls == [
        (tmp_path.resolve(), ("src", "nested", "app.py")),
    ] * expected_calls


def test_reader_reports_missing_file_as_read_error(tmp_path: Path) -> None:
    with pytest.raises(SourceReadError) as raised:
        SafeSourceReader(tmp_path).read(source_candidate("missing.py"))

    assert raised.value.kind is ParseIssueKind.READ_ERROR


def test_reader_reports_unreadable_open_as_read_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file = write_inventory_file(tmp_path, "blocked.py", b"pass\n", "python")

    def denied_open(root: Path, parts: tuple[str, ...]):
        raise PermissionError("denied")

    replace_platform_opener(monkeypatch, denied_open)

    with pytest.raises(SourceReadError) as raised:
        SafeSourceReader(tmp_path).read(file)

    assert raised.value.kind is ParseIssueKind.READ_ERROR


def test_file_changed_size_is_rejected_before_content_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file = source_candidate("changed.py", size_bytes=1)
    (tmp_path / file.relative_path).write_bytes(b"longer")
    platform_opener = getattr(reader_module, platform_opener_name())

    class ReadForbidden:
        def __init__(self, stream: object) -> None:
            self._stream = stream

        def fileno(self) -> int:
            return self._stream.fileno()  # type: ignore[no-any-return, union-attr]

        def read(self, size: int = -1) -> bytes:
            raise AssertionError("changed-size content must not be read")

        def __enter__(self) -> "ReadForbidden":
            return self

        def __exit__(self, *args: object) -> None:
            self._stream.close()  # type: ignore[union-attr]

    def guarded_open(root: Path, parts: tuple[str, ...]):
        assert platform_opener is not None
        return ReadForbidden(platform_opener(root, parts))

    replace_platform_opener(monkeypatch, guarded_open)

    with pytest.raises(SourceReadError) as raised:
        SafeSourceReader(tmp_path).read(file)

    assert raised.value.kind is ParseIssueKind.FILE_CHANGED


def test_file_changed_short_read_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "short.py"
    file = write_inventory_file(tmp_path, target.name, b"abcd", "python")
    platform_opener = getattr(reader_module, platform_opener_name())
    calls = 0

    def mutating_open(root: Path, parts: tuple[str, ...]):
        nonlocal calls
        assert platform_opener is not None
        stream = platform_opener(root, parts)
        calls += 1
        if calls == 1:
            return MutatingReadStream(stream, before_read=lambda: target.write_bytes(b"ab"))
        return stream

    replace_platform_opener(monkeypatch, mutating_open)

    with pytest.raises(SourceReadError) as raised:
        SafeSourceReader(tmp_path).read(file)

    assert raised.value.kind is ParseIssueKind.FILE_CHANGED


def test_file_changed_overflow_probe_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "overflow.py"
    file = write_inventory_file(tmp_path, target.name, b"abc", "python")
    platform_opener = getattr(reader_module, platform_opener_name())
    calls = 0

    def append_byte() -> None:
        with target.open("ab") as destination:
            destination.write(b"!")

    def mutating_open(root: Path, parts: tuple[str, ...]):
        nonlocal calls
        assert platform_opener is not None
        stream = platform_opener(root, parts)
        calls += 1
        if calls == 1:
            return MutatingReadStream(stream, before_read=append_byte)
        return stream

    replace_platform_opener(monkeypatch, mutating_open)

    with pytest.raises(SourceReadError) as raised:
        SafeSourceReader(tmp_path).read(file)

    assert raised.value.kind is ParseIssueKind.FILE_CHANGED


def test_file_changed_post_read_identity_mismatch_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "swapped.py"
    replacement = tmp_path / "replacement.py"
    file = write_inventory_file(tmp_path, target.name, b"old\n", "python")
    replacement.write_bytes(b"new\n")
    platform_opener = getattr(reader_module, platform_opener_name())
    calls = 0

    def replace_path() -> None:
        os.replace(replacement, target)

    def mutating_open(root: Path, parts: tuple[str, ...]):
        nonlocal calls
        assert platform_opener is not None
        stream = platform_opener(root, parts)
        calls += 1
        if calls == 1:
            return MutatingReadStream(stream, after_close=replace_path)
        return stream

    replace_platform_opener(monkeypatch, mutating_open)

    with pytest.raises(SourceReadError) as raised:
        SafeSourceReader(tmp_path).read(file)

    assert raised.value.kind is ParseIssueKind.FILE_CHANGED


def test_reader_rejects_non_regular_final_entry(tmp_path: Path) -> None:
    (tmp_path / "directory.py").mkdir()

    with pytest.raises(SourceReadError) as raised:
        SafeSourceReader(tmp_path).read(source_candidate("directory.py"))

    assert raised.value.kind in {ParseIssueKind.LINK_UNSAFE, ParseIssueKind.READ_ERROR}


def test_reader_returns_exact_regular_bytes_without_rescanning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"def f():\n    return 1\n"
    file = write_inventory_file(tmp_path, "app.py", data, "python")
    scandir_calls: list[object] = []
    original_scandir = os.scandir

    def recording_scandir(path: object):
        scandir_calls.append(path)
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", recording_scandir)

    source = SafeSourceReader(tmp_path).read(file)

    assert source == SourceBuffer(data, data, 0)
    assert scandir_calls == []


@pytest.mark.parametrize("data", [b"caf\xe9", "x".encode("utf-16"), "x".encode("utf-32")])
def test_reader_rejects_non_utf8_without_replacement(tmp_path: Path, data: bytes) -> None:
    file = write_inventory_file(tmp_path, "bad.py", data, "python")

    with pytest.raises(SourceReadError) as raised:
        SafeSourceReader(tmp_path).read(file)

    assert raised.value.kind is ParseIssueKind.DECODING_ERROR


def test_reader_preserves_original_utf8_bom_offsets(tmp_path: Path) -> None:
    data = codecs.BOM_UTF8 + b"def f():\n    pass\n"
    file = write_inventory_file(tmp_path, "bom.py", data, "python")

    source = SafeSourceReader(tmp_path).read(file)

    assert source.original_bytes == data
    assert source.parse_bytes == data[len(codecs.BOM_UTF8):]
    assert source.bom_prefix_bytes == 3


def test_reader_rejects_symlink_swap_without_reading_target(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"OUTSIDE_SECRET_SENTINEL")
    file = write_inventory_file(repository, "linked.py", b"safe", "python")
    target = repository / file.relative_path
    target.unlink()
    try:
        target.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(SourceReadError) as raised:
        SafeSourceReader(repository).read(file)

    assert raised.value.kind is ParseIssueKind.LINK_UNSAFE
    assert "OUTSIDE_SECRET_SENTINEL" not in str(raised.value)


def test_reparse_metadata_detects_windows_reparse_attribute() -> None:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

    class FakeStat:
        st_file_attributes = reparse_flag

    assert is_reparse_metadata(FakeStat())  # type: ignore[arg-type]


def test_reader_fails_closed_when_platform_nonfollowing_opener_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file = write_inventory_file(tmp_path, "app.py", b"pass\n", "python")

    def forbidden_following_open(self: Path, *args: object, **kwargs: object):
        raise AssertionError("Path.open fallback must never be used")

    replace_platform_opener(monkeypatch, None)
    monkeypatch.setattr(Path, "open", forbidden_following_open)

    with pytest.raises(SourceReadError) as raised:
        SafeSourceReader(tmp_path).read(file)

    assert raised.value.kind is ParseIssueKind.LINK_UNSAFE


class FakeWindowsBinaryStream:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def fileno(self) -> int:
        return 31

    def read(self, size: int = -1) -> bytes:
        return b"safe"[:size]


def windows_metadata(
    identity: int,
    *,
    directory: bool = False,
    reparse: bool = False,
    size: int = 0,
) -> reader_module._WindowsMetadata:
    attributes = 0x10 if directory else 0
    if reparse:
        attributes |= getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return reader_module._WindowsMetadata(attributes, 7, identity, size)


def install_windows_walk_fakes(
    monkeypatch: pytest.MonkeyPatch,
    metadata_by_handle: dict[int, reader_module._WindowsMetadata],
    *,
    root_handles: tuple[int, ...],
    relative_handles: tuple[int, ...],
) -> tuple[
    list[tuple[Path, int]],
    list[tuple[int, str, int, bool]],
    list[int],
    FakeWindowsBinaryStream,
]:
    root_results = iter(root_handles)
    relative_results = iter(relative_handles)
    root_calls: list[tuple[Path, int]] = []
    relative_calls: list[tuple[int, str, int, bool]] = []
    closed_handles: list[int] = []
    binary_stream = FakeWindowsBinaryStream()

    def open_root(path: Path, desired_access: int) -> int:
        root_calls.append((path, desired_access))
        return next(root_results)

    def open_relative(
        parent_handle: int,
        component: str,
        desired_access: int,
        *,
        directory: bool,
    ) -> int:
        relative_calls.append((parent_handle, component, desired_access, directory))
        return next(relative_results)

    monkeypatch.setattr(reader_module, "_IS_WINDOWS", True)
    monkeypatch.setattr(reader_module, "_open_windows_root_handle", open_root)
    monkeypatch.setattr(reader_module, "_open_windows_relative_handle", open_relative)
    monkeypatch.setattr(
        reader_module,
        "_windows_handle_metadata",
        lambda handle: metadata_by_handle[handle],
    )
    monkeypatch.setattr(reader_module, "_close_windows_handle", closed_handles.append)
    monkeypatch.setattr(
        reader_module,
        "_windows_handle_to_stream",
        lambda handle: binary_stream,
    )
    return root_calls, relative_calls, closed_handles, binary_stream


def test_windows_adapter_retains_root_and_parents_without_absolute_final_reopen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {
        10: windows_metadata(1, directory=True),
        11: windows_metadata(1, directory=True),
        20: windows_metadata(2, directory=True),
        21: windows_metadata(2, directory=True),
        30: windows_metadata(3, size=4),
        31: windows_metadata(3, size=4),
        32: windows_metadata(3, size=4),
    }
    root_calls, relative_calls, closed, binary_stream = install_windows_walk_fakes(
        monkeypatch,
        metadata,
        root_handles=(10, 11),
        relative_handles=(20, 21, 30, 31, 32),
    )

    source = reader_module._open_verified_windows(
        Path("C:/repository"),
        ("src", "app.py"),
    )

    assert [call[0] for call in root_calls] == [Path("C:/repository")] * 2
    assert [(call[0], call[1], call[3]) for call in relative_calls] == [
        (11, "src", True),
        (11, "src", True),
        (21, "app.py", False),
        (21, "app.py", False),
    ]
    assert closed == [10, 20, 30]
    assert 11 not in closed and 21 not in closed

    source.verify_path(4)  # type: ignore[attr-defined]

    assert [call[0] for call in root_calls] == [Path("C:/repository")] * 2
    assert relative_calls[-1][0:2] == (21, "app.py")
    assert closed == [10, 20, 30, 32]

    source.close()

    assert binary_stream.closed
    assert closed == [10, 20, 30, 32, 21, 11]


def test_windows_adapter_rejects_root_replacement_and_closes_both_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {
        10: windows_metadata(1, directory=True),
        11: windows_metadata(9, directory=True),
    }
    _, _, closed, _ = install_windows_walk_fakes(
        monkeypatch,
        metadata,
        root_handles=(10, 11),
        relative_handles=(),
    )

    with pytest.raises(SourceReadError) as raised:
        reader_module._open_verified_windows(Path("C:/repository"), ("app.py",))

    assert raised.value.kind is ParseIssueKind.FILE_CHANGED
    assert closed == [10, 11]


@pytest.mark.parametrize(
    ("opened_parent", "expected_kind"),
    [
        (windows_metadata(2, directory=True, reparse=True), ParseIssueKind.LINK_UNSAFE),
        (windows_metadata(9, directory=True), ParseIssueKind.FILE_CHANGED),
    ],
)
def test_windows_adapter_rejects_parent_junction_or_identity_race(
    monkeypatch: pytest.MonkeyPatch,
    opened_parent: reader_module._WindowsMetadata,
    expected_kind: ParseIssueKind,
) -> None:
    metadata = {
        10: windows_metadata(1, directory=True),
        11: windows_metadata(1, directory=True),
        20: windows_metadata(2, directory=True),
        21: opened_parent,
    }
    _, _, closed, _ = install_windows_walk_fakes(
        monkeypatch,
        metadata,
        root_handles=(10, 11),
        relative_handles=(20, 21),
    )

    with pytest.raises(SourceReadError) as raised:
        reader_module._open_verified_windows(
            Path("C:/repository"),
            ("src", "app.py"),
        )

    assert raised.value.kind is expected_kind
    assert closed == [10, 20, 21, 11]


def test_windows_missing_open_osfhandle_primitive_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []
    monkeypatch.setattr(
        reader_module,
        "_load_windows_api",
        lambda: SimpleNamespace(open_osfhandle=None),
    )
    monkeypatch.setattr(reader_module, "_close_windows_handle", closed.append)

    with pytest.raises(SourceReadError) as raised:
        reader_module._windows_handle_to_stream(31)

    assert raised.value.kind is ParseIssueKind.LINK_UNSAFE
    assert str(raised.value) == "Source path cannot be opened safely."
    assert closed == [31]


def test_posix_descriptor_is_closed_when_opened_identity_validation_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DirectoryMetadata:
        st_mode = stat.S_IFDIR | 0o755
        st_dev = 1
        st_ino = 2

    closed: list[int] = []
    monkeypatch.setattr(reader_module.os, "open", lambda *args, **kwargs: 91)
    monkeypatch.setattr(reader_module.os, "fstat", lambda descriptor: DirectoryMetadata())
    monkeypatch.setattr(reader_module.os, "close", closed.append)

    def identity_failure(first: object, second: object) -> bool:
        raise SourceReadError(ParseIssueKind.FILE_CHANGED, "changed")

    monkeypatch.setattr(reader_module, "_same_stat_identity", identity_failure)

    with pytest.raises(SourceReadError):
        reader_module._open_verified_posix_directory(
            7,
            "child",
            0,
            DirectoryMetadata(),  # type: ignore[arg-type]
        )

    assert closed == [91]


@pytest.mark.parametrize(
    ("language", "extension", "expected"),
    [
        ("python", ".py", ParsedLanguage.PYTHON),
        ("java", ".java", ParsedLanguage.JAVA),
        ("javascript", ".js", ParsedLanguage.JAVASCRIPT),
        ("javascript", ".jsx", ParsedLanguage.JAVASCRIPT),
        ("typescript", ".ts", ParsedLanguage.TYPESCRIPT),
        ("typescript", ".tsx", ParsedLanguage.TSX),
        ("typescript", ".py", None),
        ("python", ".tsx", None),
        ("go", ".go", None),
        (None, ".py", None),
    ],
)
def test_registry_selects_only_exact_language_extension_pairs(
    language: str | None,
    extension: str,
    expected: ParsedLanguage | None,
) -> None:
    file = scanned_file("src/file" + extension, language=language, extension=extension)

    spec = ParserRegistry().select(file)

    assert (None if spec is None else spec.language) is expected


def test_registry_defers_language_factory_until_the_matching_parser_is_requested() -> None:
    calls: list[str] = []

    def language_factory() -> Language:
        calls.append("python")
        return Language(tree_sitter_python.language())

    spec = ParserSpec("python", ParsedLanguage.PYTHON, ".py", "python", language_factory)

    ParserRegistry(specs=(spec,))

    assert calls == []


def test_registry_caches_the_parser_for_each_spec_after_first_initialization() -> None:
    calls: list[str] = []

    def language_factory() -> Language:
        calls.append("python")
        return Language(tree_sitter_python.language())

    spec = ParserSpec("python", ParsedLanguage.PYTHON, ".py", "python", language_factory)
    registry = ParserRegistry(specs=(spec,))

    first = registry.get_parser(spec)
    second = registry.get_parser(spec)

    assert isinstance(first, ParserHandle)
    assert first.parser is second.parser
    assert calls == ["python"]


def test_registry_keeps_a_failed_language_from_poisoning_another_language() -> None:
    calls: list[str] = []

    def unavailable_factory() -> Language:
        calls.append("unavailable")
        raise RuntimeError("repository-controlled failure")

    def working_factory() -> Language:
        calls.append("working")
        return Language(tree_sitter_python.language())

    unavailable = ParserSpec(
        "java", ParsedLanguage.JAVA, ".java", "java", unavailable_factory
    )
    working = ParserSpec("python", ParsedLanguage.PYTHON, ".py", "python", working_factory)
    registry = ParserRegistry(specs=(unavailable, working))

    with pytest.raises(ParserUnavailable) as error:
        registry.get_parser(unavailable)
    handle = registry.get_parser(working)

    assert str(error.value) == "Parser initialization is unavailable."
    assert handle.spec is working
    assert calls == ["unavailable", "working"]


def test_registry_instances_do_not_share_cached_parser_instances() -> None:
    def language_factory() -> Language:
        return Language(tree_sitter_python.language())

    spec = ParserSpec("python", ParsedLanguage.PYTHON, ".py", "python", language_factory)
    first_registry = ParserRegistry(specs=(spec,))
    second_registry = ParserRegistry(specs=(spec,))

    first = first_registry.get_parser(spec)
    second = second_registry.get_parser(spec)

    assert first.parser is not second.parser


@pytest.mark.parametrize(
    ("specs", "message"),
    [
        (
            (
                ParserSpec("python", ParsedLanguage.PYTHON, ".py", "python", lambda: None),
                ParserSpec("python", ParsedLanguage.JAVA, ".py", "java", lambda: None),
            ),
            "Invalid parser registry configuration.",
        ),
        (
            (
                ParserSpec("python", ParsedLanguage.PYTHON, ".py", "python", lambda: None),
                ParserSpec("other", ParsedLanguage.PYTHON, ".other", "python", lambda: None),
            ),
            "Invalid parser registry configuration.",
        ),
        (
            (ParserSpec("python", ParsedLanguage.PYTHON, ".py", "unknown", lambda: None),),
            "Invalid parser registry configuration.",
        ),
        (
            (ParserSpec("python", ParsedLanguage.PYTHON, "", "python", lambda: None),),
            "Invalid parser registry configuration.",
        ),
    ],
)
def test_registry_rejects_invalid_static_configuration_without_exposing_metadata(
    specs: tuple[ParserSpec, ...],
    message: str,
) -> None:
    with pytest.raises(ParserConfigurationError) as error:
        ParserRegistry(specs=specs)

    assert str(error.value) == message
