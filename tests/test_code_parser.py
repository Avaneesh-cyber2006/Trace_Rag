from dataclasses import FrozenInstanceError

import pytest

from backend.code_parser.exceptions import (
    CodeParserError,
    InvalidParseInventory,
    ParserConfigurationError,
    RepositoryParseError,
)
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
