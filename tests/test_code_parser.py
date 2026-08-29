import ast
import codecs
from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
import gc
import logging
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import weakref

import pytest
from tree_sitter import Language, Node, Parser, Tree
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript

import backend.code_parser as code_parser_package
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
import backend.code_parser.parser as parser_module
from backend.code_parser.parser import CodeParser
import backend.code_parser.reader as reader_module
from backend.code_parser.reader import (
    SafeSourceReader,
    SourceBuffer,
    SourceReadError,
    is_reparse_metadata,
)
from backend.file_scanner import FileScanner
from backend.file_scanner.models import FileCategory, FileInventory, ScannedFile
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


def test_normalize_extraction_deduplicates_first_values_and_sorts_syntax_issues() -> None:
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


def parse_typescript_fixture(data: bytes):
    parser = Parser(Language(tree_sitter_typescript.language_typescript()))
    return parser.parse(data)


def extract_typescript_fixture(data: bytes) -> ExtractionResult:
    source = SourceBuffer(data, data, 0)
    return get_extractor("typescript").extract(parse_typescript_fixture(data), source)


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


def test_javascript_extractor_bounds_unquoted_long_literal_modules_without_losing_imports() -> None:
    es_module = "e" * 1001
    commonjs_module = "c" * 1001
    data = (
        f'import "{es_module}";\nrequire("{commonjs_module}");\n'.encode("utf-8")
    )

    result = extract_javascript_fixture(data)

    bounded_es = ("e" * 997) + "…"
    bounded_commonjs = ("c" * 997) + "…"
    assert [(item.module, item.bindings) for item in result.imports] == [
        (bounded_es, ()),
        (bounded_commonjs, ()),
    ]
    assert result.calls == (
        CallSite(
            None,
            "require",
            CallKind.CALL,
            SourceLocation(1012, 2024, 2, 0, 2, 1012),
        ),
    )
    assert result.issues == (
        ParseIssue(
            ParseIssueKind.EXTRACTION_ERROR,
            "Extracted text exceeded the 1,000-byte limit.",
            None,
        ),
    )


def test_javascript_extractor_excludes_members_of_unextracted_class_expressions() -> None:
    data = b'''export default class {
    constructor() { build(); }
    method() { run(); }
}
const C = class {
    constructor() { initialize(); }
    method() { execute(); }
};
'''

    result = extract_javascript_fixture(data)

    assert [(item.kind, item.qualified_name) for item in result.symbols] == [
        (SymbolKind.CONSTANT, "C"),
    ]
    assert [(item.caller_qualified_name, item.callee_text) for item in result.calls] == [
        (None, "build"),
        (None, "run"),
        (None, "initialize"),
        (None, "execute"),
    ]


def test_javascript_extractor_uses_anonymous_class_call_ownership_barrier() -> None:
    data = b'''function outer() {
    const C = class {
        @decorate()
        method() { leaked(); }
    };
    return done();
}
'''

    result = extract_javascript_fixture(data)

    assert [(item.kind, item.qualified_name) for item in result.symbols] == [
        (SymbolKind.FUNCTION, "outer"),
    ]
    assert [(item.caller_qualified_name, item.callee_text) for item in result.calls] == [
        (None, "decorate"),
        (None, "leaked"),
        ("outer", "done"),
    ]


def test_javascript_extractor_keeps_decorator_calls_outside_method_ownership() -> None:
    data = b'''class C {
    @dec()
    method() { body(); }
}
'''

    result = extract_javascript_fixture(data)

    assert [(item.kind, item.qualified_name) for item in result.symbols] == [
        (SymbolKind.CLASS, "C"),
        (SymbolKind.METHOD, "C.method"),
    ]
    assert [(item.caller_qualified_name, item.callee_text) for item in result.calls] == [
        (None, "dec"),
        ("C.method", "body"),
    ]


def test_typescript_extractor_captures_interfaces_types_and_original_ranges() -> None:
    data = b'''import api, { User } from "./api";

interface AuthProvider {
    login(user: User, password?: string): Promise<boolean>;
}

export class AuthService implements AuthProvider {
    async login(user: User, password = "guest"): Promise<boolean> {
        return api.login(user, password);
    }
}
'''

    result = extract_typescript_fixture(data)

    assert result == ExtractionResult(
        symbols=(
            SymbolInfo(
                "AuthProvider",
                SymbolKind.INTERFACE,
                "AuthProvider",
                None,
                SourceLocation(36, 122, 3, 0, 5, 1),
                (),
                None,
                (),
                (),
                (),
            ),
            SymbolInfo(
                "login",
                SymbolKind.METHOD,
                "AuthProvider.login",
                "AuthProvider",
                SourceLocation(65, 119, 4, 4, 4, 58),
                (
                    ParameterInfo("user", "User", None),
                    ParameterInfo("password", "string", None),
                ),
                "Promise<boolean>",
                (),
                (),
                (),
            ),
            SymbolInfo(
                "AuthService",
                SymbolKind.CLASS,
                "AuthService",
                None,
                SourceLocation(131, 292, 7, 7, 11, 1),
                (),
                None,
                ("export",),
                (),
                ("AuthProvider",),
            ),
            SymbolInfo(
                "login",
                SymbolKind.METHOD,
                "AuthService.login",
                "AuthService",
                SourceLocation(179, 290, 8, 4, 10, 5),
                (
                    ParameterInfo("user", "User", None),
                    ParameterInfo("password", None, '"guest"'),
                ),
                "Promise<boolean>",
                ("async",),
                (),
                (),
            ),
        ),
        imports=(
            ImportInfo(
                "./api",
                (
                    ImportBinding("default", "api"),
                    ImportBinding("User", None),
                ),
                False,
                (),
                SourceLocation(0, 34, 1, 0, 1, 34),
            ),
        ),
        calls=(
            CallSite(
                "AuthService.login",
                "api.login",
                CallKind.CALL,
                SourceLocation(258, 283, 9, 15, 9, 40),
            ),
        ),
        issues=(),
    )
    assert [data[item.location.start_byte : item.location.end_byte] for item in result.symbols] == [
        data[36:122],
        data[65:119],
        data[131:292],
        data[179:290],
    ]


def test_typescript_extractor_applies_typed_declaration_and_exclusion_policies() -> None:
    data = b'''interface Auditable extends Serializable, Identifiable {
    format(value?: string): string;
}

class Service extends Base implements Auditable, Disposable {
    process(value: string): string;
    process(value: number): string;
    process(value: string | number): string { return String(value); }
    configure(optional?: number, ...labels: string[], limit: number = 3, { enabled }: Options = defaults): void {
        consume(optional);
    }
    ordinary: string;
    static readonly VERSION: string = "1";
}

type Alias = string;
const handler = (input: string): boolean => check(input);
const LIMIT: number = 3;
function outer() {
    const LOCAL = 1;
    [1].map((item: number) => use(item));
}
'''

    result = extract_typescript_fixture(data)

    assert [(item.kind, item.qualified_name) for item in result.symbols] == [
        (SymbolKind.INTERFACE, "Auditable"),
        (SymbolKind.METHOD, "Auditable.format"),
        (SymbolKind.CLASS, "Service"),
        (SymbolKind.METHOD, "Service.process"),
        (SymbolKind.METHOD, "Service.process"),
        (SymbolKind.METHOD, "Service.process"),
        (SymbolKind.METHOD, "Service.configure"),
        (SymbolKind.CONSTANT, "Service.VERSION"),
        (SymbolKind.FUNCTION, "handler"),
        (SymbolKind.CONSTANT, "LIMIT"),
        (SymbolKind.FUNCTION, "outer"),
    ]
    auditable, service = result.symbols[0], result.symbols[2]
    assert auditable.base_types == ("Serializable", "Identifiable")
    assert service.base_types == ("Base",)
    assert service.implemented_types == ("Auditable", "Disposable")
    overloads = [item for item in result.symbols if item.qualified_name == "Service.process"]
    assert [(item.location.start_byte, item.location.end_byte) for item in overloads] == [
        (162, 192),
        (198, 228),
        (234, 299),
    ]
    assert [item.parameters for item in overloads] == [
        (ParameterInfo("value", "string", None),),
        (ParameterInfo("value", "number", None),),
        (ParameterInfo("value", "string | number", None),),
    ]
    configure = next(item for item in result.symbols if item.qualified_name == "Service.configure")
    assert configure.parameters == (
        ParameterInfo("optional", "number", None),
        ParameterInfo("labels", "string[]", None),
        ParameterInfo("limit", "number", "3"),
        ParameterInfo("{ enabled }", "Options", "defaults"),
    )
    assert configure.return_type == "void"
    version = next(item for item in result.symbols if item.qualified_name == "Service.VERSION")
    assert (version.kind, version.modifiers) == (
        SymbolKind.CONSTANT,
        ("static", "readonly"),
    )
    handler = next(item for item in result.symbols if item.qualified_name == "handler")
    assert handler.parameters == (ParameterInfo("input", "string", None),)
    assert handler.return_type == "boolean"
    assert all(
        item.name not in {"Alias", "ordinary", "LOCAL", "item"}
        for item in result.symbols
    )
    assert [(item.caller_qualified_name, item.callee_text) for item in result.calls] == [
        ("Service.process", "String"),
        ("Service.configure", "consume"),
        ("handler", "check"),
        ("outer", "[1].map"),
        ("outer", "use"),
    ]
    assert result.issues == ()


def test_typescript_extractor_normalizes_abstract_and_accessibility_modifiers() -> None:
    data = b'''abstract class Worker {
    private static readonly TOKEN = "x";
    abstract run(input: string): number;
}
'''

    result = extract_typescript_fixture(data)

    assert [
        (item.kind, item.qualified_name, item.modifiers)
        for item in result.symbols
    ] == [
        (SymbolKind.CLASS, "Worker", ("abstract",)),
        (
            SymbolKind.CONSTANT,
            "Worker.TOKEN",
            ("private", "static", "readonly"),
        ),
        (SymbolKind.METHOD, "Worker.run", ("abstract",)),
    ]
    assert result.symbols[-1].parameters == (
        ParameterInfo("input", "string", None),
    )
    assert result.symbols[-1].return_type == "number"


def test_typescript_extractor_excludes_comments_from_types_and_heritage() -> None:
    data = b'''interface Child extends /* interface note */ Parent {
    read(x: /* parameter note */ string): /* return note */ number;
}
class Service extends /* base note */ Base implements /* first note */ Child, /* second note */ Other {}
'''

    result = extract_typescript_fixture(data)

    child, read, service = result.symbols
    assert (child.base_types, service.base_types, service.implemented_types) == (
        ("Parent",),
        ("Base",),
        ("Child", "Other"),
    )
    assert read.parameters == (ParameterInfo("x", "string", None),)
    assert read.return_type == "number"
    assert all(
        "note" not in text
        for text in (
            *child.base_types,
            *service.base_types,
            *service.implemented_types,
            read.parameters[0].type_name or "",
            read.return_type or "",
        )
    )


def test_typescript_extractor_preserves_top_level_function_overloads() -> None:
    data = b'''function parse(value: string): string;
function parse(value: number): number;
function parse(value: string | number): string | number { return value; }
'''

    result = extract_typescript_fixture(data)

    assert [
        (
            item.kind,
            item.qualified_name,
            item.location,
            item.parameters,
            item.return_type,
        )
        for item in result.symbols
    ] == [
        (
            SymbolKind.FUNCTION,
            "parse",
            SourceLocation(0, 38, 1, 0, 1, 38),
            (ParameterInfo("value", "string", None),),
            "string",
        ),
        (
            SymbolKind.FUNCTION,
            "parse",
            SourceLocation(39, 77, 2, 0, 2, 38),
            (ParameterInfo("value", "number", None),),
            "number",
        ),
        (
            SymbolKind.FUNCTION,
            "parse",
            SourceLocation(78, 151, 3, 0, 3, 73),
            (ParameterInfo("value", "string | number", None),),
            "string | number",
        ),
    ]


def test_typescript_extractor_keeps_module_ambient_constants_only() -> None:
    data = b'''declare const LIMIT: number;
export declare const EXPORTED: string;
function outer() {
    declare const LOCAL: number;
    declare let MUTABLE: number;
}
'''

    result = extract_typescript_fixture(data)

    assert [
        (item.kind, item.qualified_name, item.location, item.modifiers)
        for item in result.symbols
    ] == [
        (
            SymbolKind.CONSTANT,
            "LIMIT",
            SourceLocation(14, 27, 1, 14, 1, 27),
            ("declare",),
        ),
        (
            SymbolKind.CONSTANT,
            "EXPORTED",
            SourceLocation(50, 66, 2, 21, 2, 37),
            ("export", "declare"),
        ),
        (
            SymbolKind.FUNCTION,
            "outer",
            SourceLocation(68, 154, 3, 0, 6, 1),
            (),
        ),
    ]
    assert all(item.name not in {"LOCAL", "MUTABLE"} for item in result.symbols)


def test_tsx_extractor_uses_registry_grammar_without_jsx_symbol_noise() -> None:
    data = b'''type Props = { onLogin: () => void };

export function LoginButton({ onLogin }: Props): JSX.Element {
    return <button onClick={() => onLogin()}>Login</button>;
}
'''
    file = scanned_file(
        "src/LoginButton.tsx",
        language="typescript",
        extension=".tsx",
    )
    registry = ParserRegistry()
    spec = registry.select(file)

    assert spec is not None
    assert (spec.language, spec.extractor_key) == (ParsedLanguage.TSX, "tsx")
    handle = registry.get_parser(spec)
    source = SourceBuffer(data, data, 0)
    result = get_extractor(spec.extractor_key).extract(handle.parser.parse(data), source)

    assert result == ExtractionResult(
        symbols=(
            SymbolInfo(
                "LoginButton",
                SymbolKind.FUNCTION,
                "LoginButton",
                None,
                SourceLocation(46, 164, 3, 7, 5, 1),
                (ParameterInfo("{ onLogin }", "Props", None),),
                "JSX.Element",
                ("export",),
                (),
                (),
            ),
        ),
        imports=(),
        calls=(
            CallSite(
                "LoginButton",
                "onLogin",
                CallKind.CALL,
                SourceLocation(136, 145, 4, 34, 4, 43),
            ),
        ),
        issues=(),
    )
    assert all(item.name not in {"Props", "button", "onClick"} for item in result.symbols)


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
    ("extractor_key", "data", "retained_name", "invalid_range"),
    [
        (
            "python",
            b"def kept():\n    return ok()\n\ndef broken(\n",
            "kept",
            (29, 40),
        ),
        (
            "java",
            b"class Kept { void ok() { run(); } }\nclass Broken { void nope( {\n",
            "Kept",
            (36, 63),
        ),
        (
            "javascript",
            b"function kept() { ok(); }\nfunction broken( {\n",
            "kept",
            (26, 44),
        ),
        (
            "typescript",
            b"interface Kept {}\nfunction broken( {\n",
            "Kept",
            (18, 36),
        ),
        (
            "tsx",
            b"function Kept() { return <div />; }\nfunction broken( {\n",
            "Kept",
            (36, 54),
        ),
    ],
)
def test_malformed_supported_source_retains_complete_metadata_as_partial_parse(
    extractor_key: str,
    data: bytes,
    retained_name: str,
    invalid_range: tuple[int, int],
) -> None:
    if extractor_key == "python":
        tree = parse_python_fixture(data)
    elif extractor_key == "java":
        tree = parse_java_fixture(data)
    elif extractor_key == "javascript":
        tree = parse_javascript_fixture(data)
    elif extractor_key == "typescript":
        tree = parse_typescript_fixture(data)
    else:
        tree = Parser(Language(tree_sitter_typescript.language_tsx())).parse(data)

    result = get_extractor(extractor_key).extract(tree, SourceBuffer(data, data, 0))

    assert tree.root_node.has_error
    assert retained_name in {symbol.name for symbol in result.symbols}
    assert extractor_base.classify_parse_status(result) is ParseStatus.PARTIAL
    assert result.issues
    assert {issue.kind for issue in result.issues} <= {
        ParseIssueKind.SYNTAX_ERROR,
        ParseIssueKind.MISSING_NODE,
    }
    assert ParseIssueKind.SYNTAX_ERROR in {issue.kind for issue in result.issues}
    assert all(issue.location is not None for issue in result.issues)
    syntax_issue = next(
        issue for issue in result.issues if issue.kind is ParseIssueKind.SYNTAX_ERROR
    )
    assert syntax_issue.location is not None
    assert (
        syntax_issue.location.start_byte,
        syntax_issue.location.end_byte,
    ) == invalid_range
    assert data[slice(*invalid_range)].startswith(
        b"def broken" if extractor_key == "python" else b"function broken"
        if extractor_key in {"javascript", "typescript", "tsx"}
        else b"class Broken"
    )
    assert all(
        issue.message in {
            "Syntax error in source file.",
            "Required syntax is missing.",
        }
        for issue in result.issues
    )
    assert all("broken" not in issue.message.lower() for issue in result.issues)


@pytest.mark.parametrize(
    ("extractor_key", "data", "kept_name", "rejected_names"),
    [
        (
            "python",
            b"def good():\n    pass\ndef broken(x=):\n    pass\n",
            "good",
            {"broken"},
        ),
        (
            "java",
            b"class Good {}\nclass Broken { void nope( { } }\n",
            "Good",
            {"Broken", "nope"},
        ),
        (
            "javascript",
            b"function good() {}\nfunction broken(x = ) {}\n",
            "good",
            {"broken"},
        ),
        (
            "typescript",
            b"interface Good {}\nfunction broken(x: ) {}\n",
            "Good",
            {"broken"},
        ),
        (
            "tsx",
            b"function Good() { return <div />; }\n"
            b"function Broken(x: ) { return <span />; }\n",
            "Good",
            {"Broken"},
        ),
    ],
)
def test_malformed_declaration_is_absent_while_known_good_symbol_survives(
    extractor_key: str,
    data: bytes,
    kept_name: str,
    rejected_names: set[str],
) -> None:
    if extractor_key == "python":
        tree = parse_python_fixture(data)
    elif extractor_key == "java":
        tree = parse_java_fixture(data)
    elif extractor_key == "javascript":
        tree = parse_javascript_fixture(data)
    elif extractor_key == "typescript":
        tree = parse_typescript_fixture(data)
    else:
        tree = Parser(Language(tree_sitter_typescript.language_tsx())).parse(data)

    result = get_extractor(extractor_key).extract(tree, SourceBuffer(data, data, 0))
    extracted_names = {symbol.name for symbol in result.symbols}

    assert tree.root_node.has_error
    assert kept_name in extracted_names
    assert extracted_names.isdisjoint(rejected_names)
    assert result.issues
    assert extractor_base.classify_parse_status(result) is ParseStatus.PARTIAL


def test_syntax_issue_locations_use_original_bom_bytes_and_missing_nodes() -> None:
    parse_bytes = b"class A {"
    original_bytes = codecs.BOM_UTF8 + parse_bytes
    tree = parse_java_fixture(parse_bytes)

    issues = extractor_base.collect_syntax_issues(
        tree,
        SourceBuffer(original_bytes, parse_bytes, len(codecs.BOM_UTF8)),
    )

    assert issues == (
        ParseIssue(
            ParseIssueKind.MISSING_NODE,
            "Required syntax is missing.",
            SourceLocation(12, 12, 1, 12, 1, 12),
        ),
    )


def test_syntax_issue_collection_deduplicates_identical_error_captures() -> None:
    point = SimpleNamespace(row=0, column=1)
    duplicate_errors = [
        SimpleNamespace(
            type="ERROR",
            start_byte=1,
            end_byte=4,
            start_point=point,
            end_point=SimpleNamespace(row=0, column=4),
            is_error=True,
            is_missing=False,
            children=[],
        )
        for _ in range(2)
    ]
    root = SimpleNamespace(
        type="module",
        start_byte=0,
        end_byte=5,
        start_point=SimpleNamespace(row=0, column=0),
        end_point=SimpleNamespace(row=0, column=5),
        is_error=False,
        is_missing=False,
        children=duplicate_errors,
    )
    data = b"xbad!"

    issues = extractor_base.collect_syntax_issues(
        SimpleNamespace(root_node=root),
        SourceBuffer(data, data, 0),
    )

    assert issues == (
        ParseIssue(
            ParseIssueKind.SYNTAX_ERROR,
            "Syntax error in source file.",
            SourceLocation(1, 4, 1, 1, 1, 4),
        ),
    )
    assert b"bad" not in issues[0].message.encode("utf-8")


@pytest.mark.parametrize(
    ("result", "expected_status"),
    [
        (ExtractionResult((), (), (), ()), ParseStatus.SUCCESS),
        (
            ExtractionResult(
                (
                    SymbolInfo(
                        "kept",
                        SymbolKind.FUNCTION,
                        "kept",
                        None,
                        SourceLocation(0, 4, 1, 0, 1, 4),
                        (),
                        None,
                        (),
                        (),
                        (),
                    ),
                ),
                (),
                (),
                (),
            ),
            ParseStatus.SUCCESS,
        ),
        (
            ExtractionResult(
                (),
                (),
                (),
                (
                    ParseIssue(
                        ParseIssueKind.SYNTAX_ERROR,
                        "Syntax error in source file.",
                        SourceLocation(0, 1, 1, 0, 1, 1),
                    ),
                ),
            ),
            ParseStatus.FAILED,
        ),
        (
            ExtractionResult(
                (),
                (),
                (CallSite(None, "kept", CallKind.CALL, SourceLocation(0, 6, 1, 0, 1, 6)),),
                (
                    ParseIssue(
                        ParseIssueKind.EXTRACTION_ERROR,
                        "Extracted text exceeded the 1,000-byte limit.",
                        None,
                    ),
                ),
            ),
            ParseStatus.PARTIAL,
        ),
        (
            ExtractionResult(
                (),
                (),
                (),
                (
                    ParseIssue(
                        ParseIssueKind.EXTRACTION_ERROR,
                        "Extraction failed.",
                        None,
                    ),
                ),
            ),
            ParseStatus.FAILED,
        ),
    ],
)
def test_parse_status_depends_only_on_issues_and_trustworthy_structure(
    result: ExtractionResult,
    expected_status: ParseStatus,
) -> None:
    assert extractor_base.classify_parse_status(result) is expected_status
    if expected_status is ParseStatus.FAILED:
        assert result.symbols == ()
        assert result.imports == ()
        assert result.calls == ()


def test_parse_status_keeps_clean_supported_trees_successful() -> None:
    empty = extract_python_fixture(b"# no structure\n")
    structural = extract_python_fixture(b"def kept():\n    pass\n")

    assert empty == ExtractionResult((), (), (), ())
    assert extractor_base.classify_parse_status(empty) is ParseStatus.SUCCESS
    assert [symbol.name for symbol in structural.symbols] == ["kept"]
    assert structural.issues == ()
    assert extractor_base.classify_parse_status(structural) is ParseStatus.SUCCESS


def test_syntax_issue_invalid_span_retains_only_a_complete_nested_call() -> None:
    complete_data = b"@@ broken();"
    complete_tree = parse_javascript_fixture(complete_data)
    complete = extract_javascript_fixture(complete_data)

    incomplete_data = b"@@ broken("
    incomplete_tree = parse_javascript_fixture(incomplete_data)
    incomplete = extract_javascript_fixture(incomplete_data)

    assert complete_tree.root_node.has_error
    assert complete.calls == (
        CallSite(None, "broken", CallKind.CALL, SourceLocation(3, 11, 1, 3, 1, 11)),
    )
    assert extractor_base.classify_parse_status(complete) is ParseStatus.PARTIAL
    assert incomplete_tree.root_node.has_error
    assert incomplete.symbols == ()
    assert incomplete.imports == ()
    assert incomplete.calls == ()
    assert extractor_base.classify_parse_status(incomplete) is ParseStatus.FAILED


def test_malformed_tsx_call_with_internal_syntax_is_absent_and_failed() -> None:
    data = b"ok(foo = );"
    tree = Parser(Language(tree_sitter_typescript.language_tsx())).parse(data)

    result = get_extractor("tsx").extract(tree, SourceBuffer(data, data, 0))

    assert tree.root_node.has_error
    assert result.symbols == ()
    assert result.imports == ()
    assert result.calls == ()
    assert result.issues == (
        ParseIssue(
            ParseIssueKind.SYNTAX_ERROR,
            "Syntax error in source file.",
            SourceLocation(7, 8, 1, 7, 1, 8),
        ),
    )
    assert extractor_base.classify_parse_status(result) is ParseStatus.FAILED


def test_ecmascript_call_ownership_lookup_is_constant_per_call(monkeypatch) -> None:
    """A parent-walk implementation makes this counter grow calls x depth."""

    from backend.code_parser.extractors import ecmascript as ecmascript_module

    parent_reads = 0

    class CountingNode:
        def __init__(
            self,
            node_type: str,
            start_byte: int,
            *,
            parent: object | None = None,
            function: object | None = None,
        ) -> None:
            self.type = node_type
            self.start_byte = start_byte
            self.end_byte = start_byte + 1
            self._parent = parent
            self._function = function

        @property
        def parent(self) -> object | None:
            nonlocal parent_reads
            parent_reads += 1
            return self._parent

        def child_by_field_name(self, name: str) -> object | None:
            return self._function if name == "function" else None

    ancestor: object | None = None
    for depth in range(64):
        ancestor = CountingNode("parent", depth, parent=ancestor)

    calls = tuple(
        CountingNode(
            "call_expression",
            index,
            parent=ancestor,
            function=CountingNode("member_expression", index),
        )
        for index in range(64)
    )
    events = tuple(SimpleNamespace(kind=ENTER, node=node) for node in calls)

    monkeypatch.setattr(
        ecmascript_module,
        "collect_syntax_issues",
        lambda tree, source: (),
    )
    monkeypatch.setattr(
        ecmascript_module,
        "iter_events",
        lambda root: iter(events),
    )
    monkeypatch.setattr(
        ecmascript_module,
        "is_trustworthy_capture",
        lambda *args: True,
    )
    monkeypatch.setattr(
        ecmascript_module,
        "bounded_node_text",
        lambda node, source: extractor_base.BoundedText("target", False),
    )
    monkeypatch.setattr(
        ecmascript_module,
        "source_location",
        lambda node, source: extraction_location(node.start_byte, node.end_byte),
    )

    result = ecmascript_module.EcmaScriptExtractor().extract(
        SimpleNamespace(root_node=object()),
        SourceBuffer(b"", b"", 0),
    )

    assert len(result.calls) == len(calls)
    assert parent_reads <= len(calls)


def test_ecmascript_modifier_lookup_is_constant_per_declaration(monkeypatch) -> None:
    """Contextual modifiers must come from traversal state, never parent walks."""

    from backend.code_parser.extractors import ecmascript as ecmascript_module

    parent_reads = 0

    class CountingNode:
        def __init__(
            self,
            node_type: str,
            start_byte: int,
            *,
            parent: object | None = None,
            name: object | None = None,
        ) -> None:
            self.type = node_type
            self.start_byte = start_byte
            self.end_byte = start_byte + 1
            self.children = ()
            self._parent = parent
            self._name = name

        @property
        def parent(self) -> object | None:
            nonlocal parent_reads
            parent_reads += 1
            return self._parent

        def child_by_field_name(self, field: str) -> object | None:
            return self._name if field == "name" else None

    ancestor: object = CountingNode("program", 10_000)
    for depth in range(64):
        ancestor = CountingNode("wrapper", 9_000 + depth, parent=ancestor)

    declarations = tuple(
        CountingNode(
            "function_declaration",
            index * 2,
            parent=ancestor,
            name=CountingNode("identifier", index * 2),
        )
        for index in range(64)
    )
    events = tuple(
        event
        for declaration in declarations
        for event in (
            SimpleNamespace(kind=ENTER, node=declaration),
            SimpleNamespace(kind=EXIT, node=declaration),
        )
    )

    monkeypatch.setattr(
        ecmascript_module,
        "collect_syntax_issues",
        lambda tree, source: (),
    )
    monkeypatch.setattr(
        ecmascript_module,
        "iter_events",
        lambda root: iter(events),
    )
    monkeypatch.setattr(
        ecmascript_module,
        "is_trustworthy_capture",
        lambda *args: True,
    )
    monkeypatch.setattr(
        ecmascript_module,
        "bounded_node_text",
        lambda node, source: extractor_base.BoundedText("fn", False),
    )
    monkeypatch.setattr(
        ecmascript_module,
        "source_location",
        lambda node, source: extraction_location(node.start_byte, node.end_byte),
    )

    result = ecmascript_module.EcmaScriptExtractor().extract(
        SimpleNamespace(root_node=object()),
        SourceBuffer(b"", b"", 0),
    )

    assert len(result.symbols) == len(declarations)
    assert all(symbol.modifiers == () for symbol in result.symbols)
    assert parent_reads <= len(declarations)


class _ParentCountingEcmaNode:
    def __init__(
        self,
        node_type: str,
        start_byte: int,
        parent_reads: list[int],
        *,
        parent: object | None = None,
        text: str | None = None,
        children: tuple[object, ...] = (),
        named_children: tuple[object, ...] = (),
        fields: dict[str, object] | None = None,
    ) -> None:
        self.type = node_type
        self.start_byte = start_byte
        self.end_byte = start_byte + 1
        self.text = node_type if text is None else text
        self.children = children
        self.named_children = named_children
        self._fields = {} if fields is None else fields
        self._parent = parent
        self._parent_reads = parent_reads

    @property
    def parent(self) -> object | None:
        self._parent_reads[0] += 1
        return self._parent

    def child_by_field_name(self, field: str) -> object | None:
        return self._fields.get(field)


def _extract_fake_typescript_events(monkeypatch, events: tuple[object, ...]):
    from backend.code_parser.extractors import ecmascript as ecmascript_module

    monkeypatch.setattr(
        ecmascript_module,
        "collect_syntax_issues",
        lambda tree, source: (),
    )
    monkeypatch.setattr(
        ecmascript_module,
        "iter_events",
        lambda root: iter(events),
    )
    monkeypatch.setattr(
        ecmascript_module,
        "is_trustworthy_capture",
        lambda *args: True,
    )
    monkeypatch.setattr(
        ecmascript_module,
        "bounded_node_text",
        lambda node, source: extractor_base.BoundedText(node.text, False),
    )
    monkeypatch.setattr(
        ecmascript_module,
        "source_location",
        lambda node, source: extraction_location(node.start_byte, node.end_byte),
    )
    monkeypatch.setattr(
        ecmascript_module,
        "_string_value",
        lambda node, source: None if node is None else (node.text, False),
    )
    return ecmascript_module.EcmaScriptExtractor(
        mode=ecmascript_module.TYPESCRIPT
    ).extract(
        SimpleNamespace(root_node=object()),
        SourceBuffer(b"", b"", 0),
    )


def test_deep_ecmascript_methods_and_fields_never_read_node_parent(monkeypatch) -> None:
    parent_reads = [0]
    offset = 0

    def node(node_type: str, *, parent=None, text=None, children=(), fields=None):
        nonlocal offset
        offset += 2
        return _ParentCountingEcmaNode(
            node_type,
            offset,
            parent_reads,
            parent=parent,
            text=text,
            children=children,
            fields=fields,
        )

    program = node("program")
    events: list[object] = [SimpleNamespace(kind=ENTER, node=program)]
    enclosing: object = program
    nested_functions: list[tuple[object, object]] = []
    for depth in range(32):
        name = node("identifier", text=f"f{depth}")
        declaration = node(
            "function_declaration",
            parent=enclosing,
            fields={"name": name},
        )
        body = node("statement_block", parent=declaration)
        events.extend(
            (
                SimpleNamespace(kind=ENTER, node=declaration),
                SimpleNamespace(kind=ENTER, node=body),
            )
        )
        nested_functions.append((declaration, body))
        enclosing = body

    class_name = node("type_identifier", text="Deep")
    class_node = node(
        "class_declaration",
        parent=enclosing,
        fields={"name": class_name},
    )
    class_body = node("class_body", parent=class_node)
    class_node._fields["body"] = class_body
    method_name = node("property_identifier", text="run")
    method = node(
        "method_definition",
        parent=class_body,
        fields={"name": method_name},
    )
    static = node("static", text="static")
    readonly = node("readonly", text="readonly")
    field_name = node("property_identifier", text="TOKEN")
    field = node(
        "public_field_definition",
        parent=class_body,
        children=(static, readonly),
        fields={"name": field_name},
    )
    events.extend(
        (
            SimpleNamespace(kind=ENTER, node=class_node),
            SimpleNamespace(kind=ENTER, node=class_body),
            SimpleNamespace(kind=ENTER, node=method),
            SimpleNamespace(kind=EXIT, node=method),
            SimpleNamespace(kind=ENTER, node=field),
            SimpleNamespace(kind=EXIT, node=field),
            SimpleNamespace(kind=EXIT, node=class_body),
            SimpleNamespace(kind=EXIT, node=class_node),
        )
    )
    for declaration, body in reversed(nested_functions):
        events.extend(
            (
                SimpleNamespace(kind=EXIT, node=body),
                SimpleNamespace(kind=EXIT, node=declaration),
            )
        )
    events.append(SimpleNamespace(kind=EXIT, node=program))

    result = _extract_fake_typescript_events(monkeypatch, tuple(events))
    deep_class, deep_method, deep_field = result.symbols[-3:]

    assert (deep_class.kind, deep_class.name) == (SymbolKind.CLASS, "Deep")
    assert (deep_method.kind, deep_method.name) == (SymbolKind.METHOD, "run")
    assert (deep_field.kind, deep_field.name, deep_field.modifiers) == (
        SymbolKind.CONSTANT,
        "TOKEN",
        ("static", "readonly"),
    )
    assert deep_method.parent_qualified_name == deep_class.qualified_name
    assert deep_field.parent_qualified_name == deep_class.qualified_name
    assert parent_reads == [0]


def test_local_and_module_lexical_classification_never_reads_node_parent(
    monkeypatch,
) -> None:
    parent_reads = [0]

    def lexical(start: int, parent: object, name_text: str):
        name = _ParentCountingEcmaNode(
            "identifier", start + 1, parent_reads, text=name_text
        )
        declarator = _ParentCountingEcmaNode(
            "variable_declarator",
            start + 2,
            parent_reads,
            fields={"name": name},
        )
        kind = _ParentCountingEcmaNode("const", start + 3, parent_reads)
        declaration = _ParentCountingEcmaNode(
            "lexical_declaration",
            start,
            parent_reads,
            parent=parent,
            named_children=(declarator,),
            fields={"kind": kind},
        )
        return declaration

    program = _ParentCountingEcmaNode("program", 0, parent_reads)
    function_name = _ParentCountingEcmaNode(
        "identifier", 2, parent_reads, text="outer"
    )
    function = _ParentCountingEcmaNode(
        "function_declaration",
        1,
        parent_reads,
        parent=program,
        fields={"name": function_name},
    )
    body = _ParentCountingEcmaNode(
        "statement_block", 3, parent_reads, parent=function
    )
    local = lexical(10, body, "LOCAL")
    module = lexical(20, program, "GLOBAL")
    events = (
        SimpleNamespace(kind=ENTER, node=program),
        SimpleNamespace(kind=ENTER, node=function),
        SimpleNamespace(kind=ENTER, node=body),
        SimpleNamespace(kind=ENTER, node=local),
        SimpleNamespace(kind=EXIT, node=local),
        SimpleNamespace(kind=EXIT, node=body),
        SimpleNamespace(kind=EXIT, node=function),
        SimpleNamespace(kind=ENTER, node=module),
        SimpleNamespace(kind=EXIT, node=module),
        SimpleNamespace(kind=EXIT, node=program),
    )

    result = _extract_fake_typescript_events(monkeypatch, events)

    assert [(symbol.kind, symbol.name) for symbol in result.symbols] == [
        (SymbolKind.FUNCTION, "outer"),
        (SymbolKind.CONSTANT, "GLOBAL"),
    ]
    assert parent_reads == [0]


def test_bound_commonjs_require_never_reads_node_parent(monkeypatch) -> None:
    parent_reads = [0]
    program = _ParentCountingEcmaNode("program", 0, parent_reads)
    alias = _ParentCountingEcmaNode("identifier", 3, parent_reads, text="fs")
    function = _ParentCountingEcmaNode("identifier", 5, parent_reads, text="require")
    module = _ParentCountingEcmaNode("string", 7, parent_reads, text="node:fs")
    arguments = _ParentCountingEcmaNode(
        "arguments", 6, parent_reads, named_children=(module,)
    )
    call = _ParentCountingEcmaNode(
        "call_expression",
        4,
        parent_reads,
        fields={"function": function, "arguments": arguments},
    )
    declarator = _ParentCountingEcmaNode(
        "variable_declarator",
        2,
        parent_reads,
        fields={"name": alias, "value": call},
    )
    call._parent = declarator
    kind = _ParentCountingEcmaNode("const", 8, parent_reads)
    lexical = _ParentCountingEcmaNode(
        "lexical_declaration",
        1,
        parent_reads,
        parent=program,
        named_children=(declarator,),
        fields={"kind": kind},
    )
    declarator._parent = lexical
    events = (
        SimpleNamespace(kind=ENTER, node=program),
        SimpleNamespace(kind=ENTER, node=lexical),
        SimpleNamespace(kind=ENTER, node=declarator),
        SimpleNamespace(kind=ENTER, node=call),
        SimpleNamespace(kind=EXIT, node=call),
        SimpleNamespace(kind=EXIT, node=declarator),
        SimpleNamespace(kind=EXIT, node=lexical),
        SimpleNamespace(kind=EXIT, node=program),
    )

    result = _extract_fake_typescript_events(monkeypatch, events)

    assert [(item.module, item.bindings, item.is_wildcard) for item in result.imports] == [
        ("node:fs", (ImportBinding("*", "fs"),), True),
    ]
    assert [(call.caller_qualified_name, call.callee_text) for call in result.calls] == [
        (None, "require"),
    ]
    assert parent_reads == [0]


def test_capture_trust_does_not_rescan_syntax_issue_locations() -> None:
    class NonIterableIssues(tuple):
        def __iter__(self):
            raise AssertionError("capture trust rescanned syntax issues")

    data = b"ok();"
    call = parse_javascript_fixture(data).root_node.named_children[0].named_children[0]
    function = call.child_by_field_name("function")

    assert extractor_base.is_trustworthy_capture(
        call,
        SourceBuffer(data, data, 0),
        NonIterableIssues(),
        function,
    )


@pytest.mark.parametrize(
    ("extract_fixture", "data", "symbol_count"),
    [
        (
            extract_python_fixture,
            b"".join(
                (b"    " * depth) + b"def abcdefghij():\n"
                for depth in range(110)
            )
            + (b"    " * 110)
            + b"pass\n",
            110,
        ),
        (
            extract_java_fixture,
            (b"class Abcdefghij {" * 110) + (b"}" * 110),
            110,
        ),
        (
            extract_javascript_fixture,
            (b"function abcdefghij(){" * 110) + (b"}" * 110),
            110,
        ),
    ],
    ids=("python", "java", "javascript"),
)
def test_qualified_name_metadata_stays_within_the_utf8_byte_bound(
    extract_fixture,
    data: bytes,
    symbol_count: int,
) -> None:
    result = extract_fixture(data)
    qualified_name_bytes = [
        len(value.encode("utf-8"))
        for symbol in result.symbols
        for value in (symbol.qualified_name, symbol.parent_qualified_name)
        if value is not None
    ]

    assert len(result.symbols) == symbol_count
    assert all(size <= 1000 for size in qualified_name_bytes)
    assert sum(qualified_name_bytes) <= symbol_count * 2 * 1000
    assert any(issue.kind is ParseIssueKind.EXTRACTION_ERROR for issue in result.issues)


def test_rejected_python_class_is_a_symbol_and_call_ownership_barrier() -> None:
    data = b"class Broken(:\n    def good(self):\n        ok()\n"

    result = extract_python_fixture(data)

    assert result.symbols == ()
    assert [(call.caller_qualified_name, call.callee_text) for call in result.calls] == [
        (None, "ok"),
    ]


def test_java_anonymous_class_is_a_symbol_and_call_ownership_barrier() -> None:
    data = (
        b"class A { void outer(){ Object x = new Object(){ "
        b"void run(){ go(); } }; } }"
    )

    result = extract_java_fixture(data)

    assert [(symbol.kind, symbol.qualified_name) for symbol in result.symbols] == [
        (SymbolKind.CLASS, "A"),
        (SymbolKind.METHOD, "A.outer"),
    ]
    assert [(call.caller_qualified_name, call.callee_text) for call in result.calls] == [
        ("A.outer", "Object"),
        (None, "go"),
    ]


def test_java_unsupported_record_is_a_symbol_and_call_ownership_barrier() -> None:
    data = b"record R(int x) { void m(){ go(); } }"

    result = extract_java_fixture(data)

    assert result.symbols == ()
    assert [(call.caller_qualified_name, call.callee_text) for call in result.calls] == [
        (None, "go"),
    ]


def test_typescript_namespace_is_a_symbol_and_call_ownership_barrier() -> None:
    data = b"namespace N { export function f(){ go(); } }"

    result = extract_typescript_fixture(data)

    assert result.symbols == ()
    assert [(call.caller_qualified_name, call.callee_text) for call in result.calls] == [
        (None, "go"),
    ]


def test_python_local_class_bases_and_initializers_are_unowned() -> None:
    data = b'''def outer():
    class Local(factory()):
        VALUE = make()
        def method(self):
            inside()
    return done()
'''

    result = extract_python_fixture(data)

    assert [(call.caller_qualified_name, call.callee_text) for call in result.calls] == [
        (None, "factory"),
        (None, "make"),
        ("outer.Local.method", "inside"),
        ("outer", "done"),
    ]


def test_typescript_parameter_decorator_call_is_outside_method_ownership() -> None:
    data = b"class C { method(@dec() x: string) { body(); } }"

    result = extract_typescript_fixture(data)

    assert [(call.caller_qualified_name, call.callee_text) for call in result.calls] == [
        (None, "dec"),
        ("C.method", "body"),
    ]


def test_python_nested_decorator_calls_are_universally_unowned() -> None:
    data = b'''def outer():
    @register(factory())
    def inner():
        body()
    return after()
'''

    result = extract_python_fixture(data)

    assert [(call.caller_qualified_name, call.callee_text) for call in result.calls] == [
        (None, "register"),
        (None, "factory"),
        ("outer.inner", "body"),
        ("outer", "after"),
    ]


def test_typescript_nested_class_decorator_calls_are_universally_unowned() -> None:
    data = b'''function outer() {
    @dec(factory())
    class Inner {
        method() { body(); }
    }
    return after();
}
'''

    result = extract_typescript_fixture(data)

    assert [(call.caller_qualified_name, call.callee_text) for call in result.calls] == [
        (None, "dec"),
        (None, "factory"),
        ("outer.Inner.method", "body"),
        ("outer", "after"),
    ]


def test_java_vararg_type_stays_within_the_utf8_byte_bound() -> None:
    data = b"class C { void run(" + (b"A" * 1000) + b"... values) {} }"

    result = extract_java_fixture(data)
    parameter = result.symbols[1].parameters[0]

    assert parameter.type_name is not None
    assert len(parameter.type_name.encode("utf-8")) <= 1000
    assert parameter.type_name.endswith("…")
    assert any(issue.kind is ParseIssueKind.EXTRACTION_ERROR for issue in result.issues)


class _ParentCountingJavaNode:
    def __init__(self, node: Node, parent_reads: list[int]) -> None:
        self._node = node
        self._parent_reads = parent_reads

    @property
    def parent(self) -> Node | None:
        self._parent_reads[0] += 1
        return self._node.parent

    def __getattr__(self, name: str):
        return getattr(self._node, name)


def _extract_java_with_parent_read_counter(
    monkeypatch,
    data: bytes,
) -> tuple[ExtractionResult, list[int]]:
    from backend.code_parser.extractors import java as java_module

    tree = parse_java_fixture(data)
    parent_reads = [0]
    events = tuple(
        SimpleNamespace(
            kind=event.kind,
            node=_ParentCountingJavaNode(event.node, parent_reads),
        )
        for event in iter_events(tree.root_node)
    )
    monkeypatch.setattr(java_module, "iter_events", lambda root: iter(events))

    result = java_module.JavaExtractor().extract(
        tree,
        SourceBuffer(data, data, 0),
    )
    return result, parent_reads


def test_deep_java_declarations_never_read_node_parent(monkeypatch) -> None:
    depth = 64
    data = (
        b"package perf;\n"
        + (b"class Layer {\n" * depth)
        + b"static final int LIMIT = 1;\n"
        + b"Layer() { construct(); }\n"
        + b"void run() { execute(); new Worker(); }\n"
        + (b"}\n" * depth)
    )

    result, parent_reads = _extract_java_with_parent_read_counter(
        monkeypatch,
        data,
    )

    deepest_type = "perf." + ".".join(("Layer",) * depth)
    assert len(
        [symbol for symbol in result.symbols if symbol.kind is SymbolKind.CLASS]
    ) == depth
    assert [
        (symbol.kind, symbol.qualified_name, symbol.parent_qualified_name)
        for symbol in result.symbols[-3:]
    ] == [
        (SymbolKind.CONSTANT, f"{deepest_type}.LIMIT", deepest_type),
        (SymbolKind.CONSTRUCTOR, f"{deepest_type}.Layer", deepest_type),
        (SymbolKind.METHOD, f"{deepest_type}.run", deepest_type),
    ]
    assert [
        (call.caller_qualified_name, call.callee_text, call.kind)
        for call in result.calls
    ] == [
        (f"{deepest_type}.Layer", "construct", CallKind.CALL),
        (f"{deepest_type}.run", "execute", CallKind.CALL),
        (f"{deepest_type}.run", "Worker", CallKind.CONSTRUCTOR),
    ]
    assert parent_reads == [0]


def test_java_nested_context_preserves_members_and_ownership() -> None:
    data = b'''package p;
class Outer {
    static final int TOP = initTop();
    Outer() { start(); }
    class Inner {
        final int VALUE = make();
        Inner() { construct(); }
        void run() { work(); new Worker(); }
    }
    void outer() {
        class Local {
            final int LOCAL = localMake();
            Local() { localCtor(); }
            void go() { localCall(); }
        }
        after();
    }
}
'''

    result = extract_java_fixture(data)

    assert [
        (symbol.kind, symbol.qualified_name, symbol.parent_qualified_name)
        for symbol in result.symbols
    ] == [
        (SymbolKind.CLASS, "p.Outer", None),
        (SymbolKind.CONSTANT, "p.Outer.TOP", "p.Outer"),
        (SymbolKind.CONSTRUCTOR, "p.Outer.Outer", "p.Outer"),
        (SymbolKind.CLASS, "p.Outer.Inner", "p.Outer"),
        (SymbolKind.CONSTANT, "p.Outer.Inner.VALUE", "p.Outer.Inner"),
        (SymbolKind.CONSTRUCTOR, "p.Outer.Inner.Inner", "p.Outer.Inner"),
        (SymbolKind.METHOD, "p.Outer.Inner.run", "p.Outer.Inner"),
        (SymbolKind.METHOD, "p.Outer.outer", "p.Outer"),
        (SymbolKind.CLASS, "p.Outer.outer.Local", "p.Outer.outer"),
        (SymbolKind.CONSTANT, "p.Outer.outer.Local.LOCAL", "p.Outer.outer.Local"),
        (
            SymbolKind.CONSTRUCTOR,
            "p.Outer.outer.Local.Local",
            "p.Outer.outer.Local",
        ),
        (SymbolKind.METHOD, "p.Outer.outer.Local.go", "p.Outer.outer.Local"),
    ]
    assert [
        (call.caller_qualified_name, call.callee_text, call.kind)
        for call in result.calls
    ] == [
        (None, "initTop", CallKind.CALL),
        ("p.Outer.Outer", "start", CallKind.CALL),
        (None, "make", CallKind.CALL),
        ("p.Outer.Inner.Inner", "construct", CallKind.CALL),
        ("p.Outer.Inner.run", "work", CallKind.CALL),
        ("p.Outer.Inner.run", "Worker", CallKind.CONSTRUCTOR),
        (None, "localMake", CallKind.CALL),
        ("p.Outer.outer.Local.Local", "localCtor", CallKind.CALL),
        ("p.Outer.outer.Local.go", "localCall", CallKind.CALL),
        ("p.Outer.outer", "after", CallKind.CALL),
    ]
    assert result.issues == ()


def test_extraction_result_is_frozen_slotted_and_retains_only_metadata() -> None:
    result = ExtractionResult((), (), (), ())

    assert tuple(result.__dataclass_fields__) == ("symbols", "imports", "calls", "issues")
    assert not hasattr(result, "__dict__")
    assert not any(
        name in result.__dataclass_fields__
        for name in ("source", "source_bytes", "source_text", "tree", "syntax_tree")
    )
    with pytest.raises(FrozenInstanceError):
        result.symbols = ()  # type: ignore[misc]


def test_get_extractor_rejects_keys_outside_the_closed_registry() -> None:
    with pytest.raises(ValueError, match="Unknown extractor key"):
        get_extractor("../../repository-controlled")


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
            (
                "relative_path", "language", "status", "symbols", "imports", "calls",
                "issues", "source_sha256",
            ),
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
                "repository_namespace",
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


def test_public_exports_expose_the_complete_code_parser_contract() -> None:
    expected = {
        "CallKind",
        "CallSite",
        "CodeParseInventory",
        "CodeParser",
        "CodeParserError",
        "ImportBinding",
        "ImportInfo",
        "InvalidParseInventory",
        "ParameterInfo",
        "ParsedFile",
        "ParsedLanguage",
        "ParseIssue",
        "ParseIssueKind",
        "ParserConfigurationError",
        "ParseSkipReason",
        "ParseStatus",
        "RepositoryParseError",
        "SkippedParseFile",
        "SourceLocation",
        "SymbolInfo",
        "SymbolKind",
        "parse_code_inventory",
    }

    assert set(code_parser_package.__all__) == expected
    assert {name for name in expected if hasattr(code_parser_package, name)} == expected


def test_convenience_api_delegates_the_given_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = orchestration_inventory(tmp_path, ())
    result = object()
    received: list[FileInventory] = []

    def parse_inventory(self: CodeParser, value: FileInventory) -> object:
        received.append(value)
        return result

    monkeypatch.setattr(CodeParser, "parse_inventory", parse_inventory)

    actual = code_parser_package.parse_code_inventory(inventory)

    assert actual is result
    assert received == [inventory]


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


def orchestration_inventory(
    root: Path,
    files: tuple[ScannedFile, ...],
) -> FileInventory:
    return FileInventory(
        repository_path=str(root),
        total_files_seen=len(files),
        included_files=len(files),
        ignored_files=0,
        files=files,
        ignored=(),
        skipped_directories=(),
    )


def orchestration_file(
    relative_path: str,
    *,
    language: str | None,
    extension: str,
    category: FileCategory = FileCategory.SOURCE,
    size_bytes: int = 0,
) -> ScannedFile:
    return ScannedFile(
        relative_path=relative_path,
        filename=relative_path.rsplit("/", maxsplit=1)[-1],
        extension=extension,
        language=language,
        category=category,
        size_bytes=size_bytes,
    )


def test_code_parse_inventory_namespace_defaults_to_none() -> None:
    result = CodeParseInventory("/repo", 0, 0, 0, 0, 0, (), ())
    assert tuple(result.__dataclass_fields__)[-1] == "repository_namespace"
    assert result.repository_namespace is None


def test_parsed_file_source_sha256_defaults_to_none() -> None:
    result = ParsedFile(
        "app.py", ParsedLanguage.PYTHON, ParseStatus.SUCCESS, (), (), (), ()
    )
    assert result.source_sha256 is None
    assert not hasattr(result, "source_bytes")
    assert not hasattr(result, "source_text")


@pytest.mark.parametrize("namespace", ("opaque:A", "opaque:a", ""))
def test_parser_copies_repository_namespace_exactly(
    tmp_path: Path, namespace: str
) -> None:
    (tmp_path / "app.py").write_bytes(b"value = 1\n")
    scanned = FileScanner().scan(tmp_path, repository_namespace=namespace)
    parsed = CodeParser().parse_inventory(scanned)
    assert parsed.repository_namespace == namespace


def test_parser_rejects_non_string_repository_namespace(tmp_path: Path) -> None:
    inventory = orchestration_inventory(tmp_path, ())
    malformed = replace(inventory, repository_namespace=object())
    with pytest.raises(InvalidParseInventory, match="Invalid file inventory"):
        CodeParser().parse_inventory(malformed)


def test_module_2_integration_parses_scanner_inventory_without_rescanning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixtures = {
        "app.py": b"def run():\n    pass\n",
        "Example.java": b"class Example {}\n",
        "browser.js": b"function render() {}\n",
        "types.ts": b"interface Service {}\n",
        "view.tsx": b"const view = <div />;\n",
        "unsupported.go": b"package main\n",
        "settings.toml": b"enabled = true\n",
        "README.md": b"# Fixture\n",
    }
    for relative_path, data in fixtures.items():
        tmp_path.joinpath(relative_path).write_bytes(data)

    inventory = FileScanner().scan(tmp_path)

    def forbidden_scan(self: FileScanner, repository_path: Path | str) -> FileInventory:
        raise AssertionError(f"Module 3 must not rescan {repository_path!r}")

    monkeypatch.setattr(FileScanner, "scan", forbidden_scan)

    result = CodeParser().parse_inventory(inventory)

    assert result.total_files_requested == 6
    assert (result.success_files, result.partial_files, result.failed_files) == (5, 0, 0)
    assert result.skipped == (
        SkippedParseFile(
            "unsupported.go",
            ParseSkipReason.UNSUPPORTED_LANGUAGE,
        ),
    )
    assert {file.relative_path: file.language for file in result.files} == {
        "app.py": ParsedLanguage.PYTHON,
        "browser.js": ParsedLanguage.JAVASCRIPT,
        "Example.java": ParsedLanguage.JAVA,
        "types.ts": ParsedLanguage.TYPESCRIPT,
        "view.tsx": ParsedLanguage.TSX,
    }
    assert not ({"settings.toml", "README.md"} & {
        file.relative_path for file in result.files
    })


def test_logging_reports_sanitized_lifecycle_metadata_without_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    safe_data = b'''# SOURCE_SENTINEL\ndef safe(\n    value: ANNOTATION_SENTINEL = "DEFAULT_SENTINEL",\n):\n    return CALLEE_SENTINEL(value)\n'''
    invalid_data = b"\xffRAW_BYTES_SENTINEL"
    exploding_data = b"class Exploding {}\n"
    (tmp_path / "safe.py").write_bytes(safe_data)
    (tmp_path / "invalid.py").write_bytes(invalid_data)
    (tmp_path / "Exploding.java").write_bytes(exploding_data)
    original_get_extractor = parser_module.get_extractor

    class ExplodingExtractor:
        def extract(self, tree: Tree, source: SourceBuffer) -> ExtractionResult:
            raise RuntimeError("EXCEPTION_SENTINEL")

    def extractor_for(key: str):
        if key == "java":
            return ExplodingExtractor()
        return original_get_extractor(key)

    monkeypatch.setattr(parser_module, "get_extractor", extractor_for)
    files = (
        orchestration_file(
            "safe.py", language="python", extension=".py", size_bytes=len(safe_data)
        ),
        orchestration_file(
            "invalid.py", language="python", extension=".py",
            size_bytes=len(invalid_data),
        ),
        orchestration_file(
            "Exploding.java", language="java", extension=".java",
            size_bytes=len(exploding_data),
        ),
    )

    with caplog.at_level(logging.DEBUG, logger="backend.code_parser.parser"):
        result = CodeParser().parse_inventory(orchestration_inventory(tmp_path, files))

    messages = [record.getMessage() for record in caplog.records]
    combined = "\n".join(messages)
    assert result.success_files == 1
    assert result.failed_files == 2
    assert "Code parse inventory start: 3 files requested" in messages
    assert "Code parse inventory complete: 1 success, 0 partial, 2 failed, 0 skipped" in messages
    assert any(
        "safe.py" in message
        and "status=success" in message
        and "symbols=1" in message
        and "calls=1" in message
        for message in messages
    )
    assert any(
        "invalid.py" in message and "decoding_error" in message
        for message in messages
    )
    assert any(
        "Exploding.java" in message and "extraction_error" in message
        for message in messages
    )
    assert {record.levelno for record in caplog.records} >= {
        logging.INFO,
        logging.DEBUG,
        logging.WARNING,
    }
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)
    for sentinel in (
        "SOURCE_SENTINEL",
        "ANNOTATION_SENTINEL",
        "DEFAULT_SENTINEL",
        "CALLEE_SENTINEL",
        "RAW_BYTES_SENTINEL",
        "EXCEPTION_SENTINEL",
    ):
        assert sentinel not in combined


def test_logging_uses_error_only_for_fatal_root_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    inventory = orchestration_inventory(tmp_path / "missing", ())

    with caplog.at_level(logging.DEBUG, logger="backend.code_parser.parser"):
        with pytest.raises(RepositoryParseError):
            CodeParser().parse_inventory(inventory)

    errors = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.ERROR
    ]
    assert errors == ["Code parse inventory failed: repository root unavailable"]
    assert str(tmp_path) not in "\n".join(errors)


def test_logging_sanitizes_control_characters_in_relative_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingReader:
        def __init__(self, repository_path: str) -> None:
            assert repository_path == str(tmp_path)

        def read(self, file: ScannedFile) -> SourceBuffer:
            raise SourceReadError(ParseIssueKind.READ_ERROR, "untrusted detail")

    monkeypatch.setattr(parser_module, "SafeSourceReader", FailingReader)
    candidate = orchestration_file(
        "line\nbreak.py",
        language="python",
        extension=".py",
    )

    with caplog.at_level(logging.DEBUG, logger="backend.code_parser.parser"):
        CodeParser().parse_inventory(orchestration_inventory(tmp_path, (candidate,)))

    combined = "\n".join(record.getMessage() for record in caplog.records)
    assert "line?break.py" in combined
    assert "line\nbreak.py" not in combined
    assert "untrusted detail" not in combined


def test_logging_reports_skipped_status_and_zero_extraction_counts(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    candidate = orchestration_file(
        "unsupported.go",
        language="go",
        extension=".go",
    )

    with caplog.at_level(logging.DEBUG, logger="backend.code_parser.parser"):
        CodeParser().parse_inventory(orchestration_inventory(tmp_path, (candidate,)))

    assert (
        "Code parse file complete: unsupported.go status=skipped "
        "symbols=0 imports=0 calls=0 issues=0"
        in [record.getMessage() for record in caplog.records]
    )


def test_inventory_validation_rejects_non_inventory_and_inconsistent_counters(
    tmp_path: Path,
) -> None:
    valid = orchestration_inventory(tmp_path, ())
    invalid_values = (
        None,
        replace(valid, included_files=1),
        replace(valid, ignored_files=1),
        replace(valid, total_files_seen=1),
    )

    for invalid in invalid_values:
        with pytest.raises(InvalidParseInventory) as raised:
            CodeParser().parse_inventory(invalid)  # type: ignore[arg-type]
        assert str(raised.value) == "Invalid file inventory."


def test_inventory_validation_rejects_missing_repository_root(tmp_path: Path) -> None:
    inventory = orchestration_inventory(tmp_path / "missing", ())

    with pytest.raises(RepositoryParseError):
        CodeParser().parse_inventory(inventory)


def test_category_selection_unsupported_and_contradictory_candidates(
    tmp_path: Path,
) -> None:
    source_data = b"def source():\n    pass\n"
    test_data = b"def test_source():\n    pass\n"
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "app.py").write_bytes(source_data)
    (tmp_path / "tests" / "test_app.py").write_bytes(test_data)
    files = (
        orchestration_file(
            "notes/readme.py", language="python", extension=".py",
            category=FileCategory.DOCUMENTATION,
        ),
        orchestration_file(
            "settings.py", language="python", extension=".py",
            category=FileCategory.CONFIG,
        ),
        orchestration_file(
            "build.py", language="python", extension=".py",
            category=FileCategory.BUILD,
        ),
        orchestration_file(
            "tests/wrong.ts", language="python", extension=".ts",
            category=FileCategory.TEST,
        ),
        orchestration_file("src/unknown.go", language="go", extension=".go"),
        orchestration_file(
            "tests/test_app.py", language="python", extension=".py",
            category=FileCategory.TEST, size_bytes=len(test_data),
        ),
        orchestration_file(
            "src/app.py", language="python", extension=".py",
            size_bytes=len(source_data),
        ),
    )

    result = CodeParser().parse_inventory(orchestration_inventory(tmp_path, files))

    assert result.total_files_requested == 4
    assert [(file.relative_path, file.status) for file in result.files] == [
        ("src/app.py", ParseStatus.SUCCESS),
        ("tests/test_app.py", ParseStatus.SUCCESS),
    ]
    assert result.skipped == (
        SkippedParseFile("src/unknown.go", ParseSkipReason.UNSUPPORTED_LANGUAGE),
        SkippedParseFile("tests/wrong.ts", ParseSkipReason.UNSUPPORTED_LANGUAGE),
    )
    assert (result.success_files, result.partial_files, result.failed_files) == (2, 0, 0)
    assert result.skipped_files == 2


def test_per_file_failure_keeps_malformed_scanned_path_nonfatal(tmp_path: Path) -> None:
    invalid = orchestration_file(
        "../escape.py", language="python", extension=".py", size_bytes=0
    )

    result = CodeParser().parse_inventory(orchestration_inventory(tmp_path, (invalid,)))

    assert result.files == (
        ParsedFile(
            "../escape.py",
            ParsedLanguage.PYTHON,
            ParseStatus.FAILED,
            (),
            (),
            (),
            (ParseIssue(ParseIssueKind.PATH_INVALID, "Invalid source path.", None),),
        ),
    )
    assert result.failed_files == 1


@pytest.mark.parametrize(
    ("kind", "expected_message"),
    [
        (ParseIssueKind.READ_ERROR, "Unable to read source file."),
        (ParseIssueKind.FILE_CHANGED, "Source file changed after scanning."),
        (ParseIssueKind.PATH_INVALID, "Invalid source path."),
        (ParseIssueKind.LINK_UNSAFE, "Source path cannot be opened safely."),
        (ParseIssueKind.DECODING_ERROR, "Source file is not valid UTF-8."),
    ],
)
def test_per_file_failure_translates_reader_errors_to_fixed_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: ParseIssueKind,
    expected_message: str,
) -> None:
    class FailingReader:
        def __init__(self, repository_path: str) -> None:
            assert repository_path == str(tmp_path)

        def read(self, file: ScannedFile) -> SourceBuffer:
            raise SourceReadError(kind, "repository-controlled secret")

    monkeypatch.setattr(parser_module, "SafeSourceReader", FailingReader)
    candidate = orchestration_file("app.py", language="python", extension=".py")

    result = CodeParser().parse_inventory(orchestration_inventory(tmp_path, (candidate,)))

    assert result.files == (
        ParsedFile(
            "app.py",
            ParsedLanguage.PYTHON,
            ParseStatus.FAILED,
            (),
            (),
            (),
            (ParseIssue(kind, expected_message, None),),
        ),
    )
    assert "secret" not in result.files[0].issues[0].message


def test_per_file_failure_isolates_unavailable_grammar_from_sibling_language(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    java_data = b"class Broken {}\n"
    python_data = b"pass\n"
    (tmp_path / "Broken.java").write_bytes(java_data)
    (tmp_path / "good.py").write_bytes(python_data)

    def unavailable_language() -> Language:
        raise RuntimeError("grammar loader detail")

    registry = ParserRegistry(
        specs=(
            ParserSpec(
                "java", ParsedLanguage.JAVA, ".java", "java", unavailable_language
            ),
            ParserSpec(
                "python", ParsedLanguage.PYTHON, ".py", "python",
                lambda: Language(tree_sitter_python.language()),
            ),
        )
    )
    monkeypatch.setattr(parser_module, "ParserRegistry", lambda: registry)
    files = (
        orchestration_file(
            "good.py", language="python", extension=".py", size_bytes=len(python_data)
        ),
        orchestration_file(
            "Broken.java", language="java", extension=".java", size_bytes=len(java_data)
        ),
    )

    result = CodeParser().parse_inventory(orchestration_inventory(tmp_path, files))

    assert [(file.relative_path, file.status) for file in result.files] == [
        ("Broken.java", ParseStatus.FAILED),
        ("good.py", ParseStatus.SUCCESS),
    ]
    assert result.files[0].issues == (
        ParseIssue(
            ParseIssueKind.PARSER_UNAVAILABLE,
            "Parser initialization is unavailable.",
            None,
        ),
    )
    assert (result.success_files, result.failed_files) == (1, 1)


def test_per_file_failure_isolates_unexpected_extractor_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    java_data = b"class Broken {}\n"
    python_data = b"pass\n"
    (tmp_path / "Broken.java").write_bytes(java_data)
    (tmp_path / "good.py").write_bytes(python_data)
    original_get_extractor = parser_module.get_extractor

    class ExplodingExtractor:
        def extract(self, tree: Tree, source: SourceBuffer) -> ExtractionResult:
            raise RuntimeError("source text must not escape")

    def extractor_for(key: str):
        return ExplodingExtractor() if key == "java" else original_get_extractor(key)

    monkeypatch.setattr(parser_module, "get_extractor", extractor_for)
    files = (
        orchestration_file(
            "good.py", language="python", extension=".py", size_bytes=len(python_data)
        ),
        orchestration_file(
            "Broken.java", language="java", extension=".java", size_bytes=len(java_data)
        ),
    )

    result = CodeParser().parse_inventory(orchestration_inventory(tmp_path, files))

    assert [(file.relative_path, file.status) for file in result.files] == [
        ("Broken.java", ParseStatus.FAILED),
        ("good.py", ParseStatus.SUCCESS),
    ]
    assert result.files[0].issues == (
        ParseIssue(ParseIssueKind.EXTRACTION_ERROR, "Extraction failed.", None),
    )
    assert "source text" not in result.files[0].issues[0].message


def test_deterministic_inventory_order_normalization_and_counters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, data in (("a.py", b"P"), ("B.py", b"F"), ("c.py", b"S")):
        (tmp_path / name).write_bytes(data)
    early = SymbolInfo(
        "early", SymbolKind.CLASS, "early", None, extraction_location(1, 2), (), None,
        (), (), (),
    )
    first = SymbolInfo(
        "same", SymbolKind.FUNCTION, "first.same", None, extraction_location(10, 12),
        (), None, ("PUBLIC",), (), (),
    )
    duplicate = replace(first, qualified_name="discarded.same", modifiers=("private",))
    syntax_issue = ParseIssue(
        ParseIssueKind.SYNTAX_ERROR, "Syntax error in source file.",
        extraction_location(20, 21),
    )
    extraction_issue = ParseIssue(
        ParseIssueKind.EXTRACTION_ERROR, "Extraction failed.", None
    )

    class OrderedExtractor:
        def extract(self, tree: Tree, source: SourceBuffer) -> ExtractionResult:
            if source.original_bytes == b"P":
                return ExtractionResult((duplicate, early, first), (), (), (syntax_issue,))
            if source.original_bytes == b"F":
                return ExtractionResult((), (), (), (extraction_issue,))
            return ExtractionResult((first, duplicate, early), (), (), ())

    monkeypatch.setattr(parser_module, "get_extractor", lambda key: OrderedExtractor())
    files = (
        orchestration_file("C.go", language="go", extension=".go", size_bytes=0),
        orchestration_file("c.py", language="python", extension=".py", size_bytes=1),
        orchestration_file("B.py", language="python", extension=".py", size_bytes=1),
        orchestration_file(
            "a.py", language="python", extension=".py", category=FileCategory.TEST,
            size_bytes=1,
        ),
    )

    result = CodeParser().parse_inventory(orchestration_inventory(tmp_path, files))

    assert [file.relative_path for file in result.files] == ["a.py", "B.py", "c.py"]
    assert [file.relative_path for file in result.skipped] == ["C.go"]
    assert [symbol.qualified_name for symbol in result.files[0].symbols] == [
        "early", "discarded.same"
    ]
    assert result.files[0].symbols[1].modifiers == ("private",)
    assert [symbol.qualified_name for symbol in result.files[2].symbols] == [
        "early", "first.same"
    ]
    assert result.files[2].symbols[1].modifiers == ("public",)
    assert [file.status for file in result.files] == [
        ParseStatus.PARTIAL, ParseStatus.FAILED, ParseStatus.SUCCESS
    ]
    assert (
        result.total_files_requested,
        result.success_files,
        result.partial_files,
        result.failed_files,
        result.skipped_files,
    ) == (4, 1, 1, 1, 1)
    assert result.total_files_requested == (
        result.success_files
        + result.partial_files
        + result.failed_files
        + result.skipped_files
    )
    assert len(result.files) == result.success_files + result.partial_files + result.failed_files
    assert len(result.skipped) == result.skipped_files


def test_one_file_at_a_time_parse_once_and_no_retention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str]] = []
    source_references: list[weakref.ReferenceType[object]] = []
    tree_references: list[weakref.ReferenceType[object]] = []

    class TransientSource:
        def __init__(self, relative_path: str) -> None:
            self.original_bytes = ("SOURCE_PAYLOAD:" + relative_path).encode()
            self.parse_bytes = self.original_bytes
            self.bom_prefix_bytes = 0

    class TransientTree:
        pass

    class RecordingReader:
        def __init__(self, repository_path: str) -> None:
            assert repository_path == str(tmp_path)

        def read(self, file: ScannedFile) -> TransientSource:
            gc.collect()
            assert all(reference() is None for reference in source_references)
            assert all(reference() is None for reference in tree_references)
            source = TransientSource(file.relative_path)
            source_references.append(weakref.ref(source))
            events.append(("read", file.relative_path))
            return source

    class RecordingParser:
        def parse(self, data: bytes) -> TransientTree:
            relative_path = data.decode().removeprefix("SOURCE_PAYLOAD:")
            events.append(("parse", relative_path))
            tree = TransientTree()
            tree_references.append(weakref.ref(tree))
            return tree

    spec = SimpleNamespace(
        language=ParsedLanguage.PYTHON,
        extractor_key="python",
    )
    recording_parser = RecordingParser()

    class RecordingRegistry:
        def select(self, file: ScannedFile):
            return spec

        def get_parser(self, selected: object):
            assert selected is spec
            return SimpleNamespace(parser=recording_parser)

    class RecordingExtractor:
        def extract(
            self,
            tree: TransientTree,
            source: TransientSource,
        ) -> ExtractionResult:
            relative_path = source.original_bytes.decode().removeprefix("SOURCE_PAYLOAD:")
            events.append(("extract", relative_path))
            return ExtractionResult((), (), (), ())

    def forbidden_scandir(path: object):
        raise AssertionError(f"orchestration must not scan {path!r}")

    monkeypatch.setattr(parser_module, "SafeSourceReader", RecordingReader)
    monkeypatch.setattr(parser_module, "ParserRegistry", RecordingRegistry)
    monkeypatch.setattr(parser_module, "get_extractor", lambda key: RecordingExtractor())
    monkeypatch.setattr(os, "scandir", forbidden_scandir)
    files = (
        orchestration_file("b.py", language="python", extension=".py"),
        orchestration_file("a.py", language="python", extension=".py"),
    )

    result = CodeParser().parse_inventory(orchestration_inventory(tmp_path, files))
    gc.collect()

    assert events == [
        ("read", "a.py"),
        ("parse", "a.py"),
        ("extract", "a.py"),
        ("read", "b.py"),
        ("parse", "b.py"),
        ("extract", "b.py"),
    ]
    assert all(reference() is None for reference in source_references)
    assert all(reference() is None for reference in tree_references)

    def retained_values(value: object):
        yield value
        if is_dataclass(value):
            for field in fields(value):
                yield from retained_values(getattr(value, field.name))
        elif isinstance(value, tuple):
            for item in value:
                yield from retained_values(item)

    retained = tuple(retained_values(result))
    assert not any(isinstance(value, (bytes, Node, Tree, TransientTree)) for value in retained)
    assert not any(
        isinstance(value, str) and value.startswith("SOURCE_PAYLOAD:")
        for value in retained
    )


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


def test_readme_documents_module_3() -> None:
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(
        encoding="utf-8"
    )

    required_fragments = (
        "Module 3 — Code Parser",
        "CodeParser",
        "parse_inventory",
        "FileInventory",
        "CodeParseInventory",
        "Python",
        "Java",
        "JavaScript/JSX",
        "TypeScript",
        "TSX",
        "[start_byte, end_byte)",
        "PARTIAL",
        "unsupported",
        "tree-sitter==0.25.2",
        "tree-sitter-python==0.25.0",
        "tree-sitter-java==0.23.5",
        "tree-sitter-javascript==0.25.0",
        "tree-sitter-typescript==0.23.2",
        "offline",
        "does not execute repository code",
        "no cross-file resolution",
        "no chunking",
        "no RAG",
    )

    assert all(fragment in readme for fragment in required_fragments)
