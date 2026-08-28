from dataclasses import FrozenInstanceError

import pytest
from tree_sitter import Language
import tree_sitter_python

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
