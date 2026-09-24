"""Metadata-only validation for Module 6 graph inputs."""

from dataclasses import replace

import pytest

from backend.code_graph.exceptions import CodeGraphValidationError
from backend.code_graph.validation import ValidatedInputs, validate_inputs
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
from backend.file_scanner.models import (
    FileCategory,
    FileInventory,
    IgnoredFile,
    IgnoreReason,
    ScannedFile,
    SkippedDirectory,
    SkippedDirectoryReason,
)


def _assert_invalid(file_inventory: object, parse_inventory: object) -> None:
    with pytest.raises(CodeGraphValidationError):
        validate_inputs(file_inventory, parse_inventory)


def _scanned(
    path: str,
    *,
    filename: str | None = None,
    extension: str = ".py",
    language: str | None = "python",
    category: FileCategory = FileCategory.SOURCE,
    size_bytes: int = 4096,
) -> ScannedFile:
    return ScannedFile(
        path,
        filename if filename is not None else path.rsplit("/", 1)[-1],
        extension,
        language,
        category,
        size_bytes,
    )


def _file_inventory(
    files: tuple[ScannedFile, ...],
    *,
    ignored: tuple[IgnoredFile, ...] = (),
    skipped_directories: tuple[SkippedDirectory, ...] = (),
    namespace: object = "repo",
    repository_path: object = "/scanner/root",
) -> FileInventory:
    return FileInventory(
        repository_path,
        len(files) + len(ignored),
        len(files),
        len(ignored),
        files,
        ignored,
        skipped_directories,
        namespace,
    )


def _parse_inventory(
    files: tuple[ParsedFile, ...],
    *,
    skipped: tuple[SkippedParseFile, ...] = (),
    namespace: object = "repo",
    repository_path: object = "/parser/root",
) -> CodeParseInventory:
    return CodeParseInventory(
        repository_path,
        len(files) + len(skipped),
        sum(file.status is ParseStatus.SUCCESS for file in files),
        sum(file.status is ParseStatus.PARTIAL for file in files),
        sum(file.status is ParseStatus.FAILED for file in files),
        len(skipped),
        files,
        skipped,
        namespace,
    )


@pytest.mark.parametrize("namespace", [None, "", 1, True])
def test_namespace_is_required(cg, namespace):
    fi, pi = cg.pair(cg.parsed())
    _assert_invalid(replace(fi, repository_namespace=namespace), pi)


def test_namespaces_must_match(cg):
    fi, pi = cg.pair(cg.parsed())
    _assert_invalid(fi, replace(pi, repository_namespace="other"))


@pytest.mark.parametrize("repository_path", [None, "", 1, True])
def test_inventory_repository_paths_are_nonempty_strings(cg, repository_path):
    fi, pi = cg.pair(cg.parsed())
    _assert_invalid(replace(fi, repository_path=repository_path), pi)
    _assert_invalid(fi, replace(pi, repository_path=repository_path))


def test_different_absolute_repository_paths_do_not_break_namespace_association(cg):
    fi, pi = cg.pair(cg.parsed())
    result = validate_inputs(
        replace(fi, repository_path="C:/scanner/snapshot"),
        replace(pi, repository_path="/parser/snapshot"),
    )
    assert result.repository_namespace == "repo"


@pytest.mark.parametrize(
    "field",
    ["total_files_seen", "included_files", "ignored_files"],
)
@pytest.mark.parametrize("value", [-1, True, 1.5, "1"])
def test_file_inventory_counters_require_exact_nonnegative_integers(cg, field, value):
    fi, pi = cg.pair(cg.parsed())
    _assert_invalid(replace(fi, **{field: value}), pi)


@pytest.mark.parametrize(
    "field",
    [
        "total_files_requested",
        "success_files",
        "partial_files",
        "failed_files",
        "skipped_files",
    ],
)
@pytest.mark.parametrize("value", [-1, True, 1.5, "1"])
def test_parse_inventory_counters_require_exact_nonnegative_integers(cg, field, value):
    fi, pi = cg.pair(cg.parsed())
    _assert_invalid(fi, replace(pi, **{field: value}))


def test_inventory_counters_must_match_collections_and_statuses(cg):
    success = cg.parsed("success.py")
    partial = cg.parsed("partial.py", status=ParseStatus.PARTIAL)
    failed = cg.parsed("failed.py", status=ParseStatus.FAILED)
    fi, pi = cg.pair(success, partial, failed)
    _assert_invalid(replace(fi, total_files_seen=4), pi)
    _assert_invalid(replace(fi, included_files=2), pi)
    _assert_invalid(replace(fi, ignored_files=1, total_files_seen=4), pi)
    _assert_invalid(fi, replace(pi, total_files_requested=4))
    _assert_invalid(fi, replace(pi, success_files=2))
    _assert_invalid(fi, replace(pi, partial_files=0))
    _assert_invalid(fi, replace(pi, failed_files=0))
    _assert_invalid(fi, replace(pi, skipped_files=1))


def test_source_and_test_paths_are_covered_using_sets(cg):
    source = cg.parsed("src/z.py")
    test = cg.parsed("tests/a.py")
    fi, pi = cg.pair(source, test)
    fi = replace(
        fi,
        files=(
            replace(fi.files[1], category=FileCategory.TEST),
            fi.files[0],
            _scanned(
                "README.md",
                extension=".md",
                language=None,
                category=FileCategory.DOCUMENTATION,
            ),
        ),
        total_files_seen=3,
        included_files=3,
    )
    pi = replace(pi, files=(test, source))
    result = validate_inputs(fi, pi)
    assert tuple(file.relative_path for file, _ in result.pairs) == (
        "src/z.py",
        "tests/a.py",
    )


def test_unsupported_source_is_covered_by_one_skipped_record(cg):
    parsed = cg.parsed("src/a.py")
    fi, pi = cg.pair(parsed)
    unsupported = _scanned("src/native.rs", extension=".rs", language="rust")
    fi = replace(
        fi,
        files=fi.files + (unsupported,),
        total_files_seen=2,
        included_files=2,
    )
    pi = replace(
        pi,
        total_files_requested=2,
        skipped_files=1,
        skipped=(
            SkippedParseFile(
                "src/native.rs",
                ParseSkipReason.UNSUPPORTED_LANGUAGE,
            ),
        ),
    )
    assert validate_inputs(fi, pi).pairs == ((fi.files[0], parsed),)


def test_coverage_rejects_missing_or_orphan_parse_paths(cg):
    parsed = cg.parsed("src/a.py")
    fi, pi = cg.pair(parsed)
    missing = _scanned("tests/missing.py", category=FileCategory.TEST)
    missing_fi = replace(
        fi,
        files=fi.files + (missing,),
        total_files_seen=2,
        included_files=2,
    )
    _assert_invalid(missing_fi, pi)
    _assert_invalid(fi, replace(pi, files=(replace(parsed, relative_path="orphan.py"),)))


def test_unsupported_skip_rejects_a_supported_selector(cg):
    parsed = cg.parsed("src/a.py")
    fi, _ = cg.pair(parsed)
    pi = _parse_inventory(
        (),
        skipped=(SkippedParseFile("src/a.py", ParseSkipReason.UNSUPPORTED_LANGUAGE),),
    )
    _assert_invalid(fi, pi)


@pytest.mark.parametrize(
    ("inventory_language", "extension", "parsed_language"),
    [
        ("python", ".py", ParsedLanguage.PYTHON),
        ("java", ".java", ParsedLanguage.JAVA),
        ("javascript", ".js", ParsedLanguage.JAVASCRIPT),
        ("javascript", ".jsx", ParsedLanguage.JAVASCRIPT),
        ("typescript", ".ts", ParsedLanguage.TYPESCRIPT),
        ("typescript", ".tsx", ParsedLanguage.TSX),
    ],
)
def test_supported_scanner_selectors_match_parser_languages(
    cg, inventory_language, extension, parsed_language
):
    path = f"src/a{extension}"
    parsed = cg.parsed(path, language=parsed_language)
    fi = _file_inventory(
        (_scanned(path, extension=extension, language=inventory_language),)
    )
    pi = _parse_inventory((parsed,))
    assert validate_inputs(fi, pi).pairs == ((fi.files[0], parsed),)


@pytest.mark.parametrize(
    ("inventory_language", "extension", "parsed_language"),
    [
        ("python", ".py", ParsedLanguage.JAVA),
        ("javascript", ".jsx", ParsedLanguage.TYPESCRIPT),
        ("typescript", ".tsx", ParsedLanguage.TYPESCRIPT),
        ("typescript", ".ts", ParsedLanguage.TSX),
        ("javascript", ".ts", ParsedLanguage.JAVASCRIPT),
    ],
)
def test_selector_mismatches_are_rejected(
    cg, inventory_language, extension, parsed_language
):
    path = f"src/a{extension}"
    parsed = cg.parsed(path, language=parsed_language)
    fi = _file_inventory(
        (_scanned(path, extension=extension, language=inventory_language),)
    )
    _assert_invalid(fi, _parse_inventory((parsed,)))


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/src/a.py",
        "C:/src/a.py",
        "C:src/a.py",
        "//server/share/a.py",
        "\\\\server\\share\\a.py",
        "src\\a.py",
        "src/\x00a.py",
        "src//a.py",
        "./src/a.py",
        "src/./a.py",
        "../src/a.py",
        "src/../a.py",
    ],
)
def test_hostile_paths_are_rejected_without_normalization(cg, path):
    parsed = cg.parsed()
    fi, pi = cg.pair(parsed)
    hostile_fi = replace(
        fi,
        files=(replace(fi.files[0], relative_path=path),),
    )
    hostile_pi = replace(
        pi,
        files=(replace(parsed, relative_path=path),),
    )
    _assert_invalid(hostile_fi, hostile_pi)


def test_all_path_bearing_record_types_use_canonical_paths(cg):
    parsed = cg.parsed()
    fi, pi = cg.pair(parsed)
    ignored = IgnoredFile("../secret", IgnoreReason.SENSITIVE_FILE)
    bad_ignored = _file_inventory(fi.files, ignored=(ignored,))
    _assert_invalid(bad_ignored, pi)
    skipped_directory = SkippedDirectory(
        "vendor\\cache",
        SkippedDirectoryReason.IGNORED_DIRECTORY,
    )
    _assert_invalid(replace(fi, skipped_directories=(skipped_directory,)), pi)
    skipped = SkippedParseFile("src//a.rs", ParseSkipReason.UNSUPPORTED_LANGUAGE)
    skipped_fi = _file_inventory(
        (
            fi.files[0],
            _scanned("src//a.rs", extension=".rs", language="rust"),
        )
    )
    skipped_pi = replace(
        pi,
        total_files_requested=2,
        skipped_files=1,
        skipped=(skipped,),
    )
    _assert_invalid(skipped_fi, skipped_pi)


def test_duplicate_paths_across_scanner_and_parser_categories_are_rejected(cg):
    parsed = cg.parsed("a.py")
    fi, pi = cg.pair(parsed)
    duplicate_file = replace(
        fi,
        files=fi.files + (fi.files[0],),
        total_files_seen=2,
        included_files=2,
    )
    _assert_invalid(duplicate_file, pi)
    duplicate_ignored = _file_inventory(
        fi.files,
        ignored=(IgnoredFile("a.py", IgnoreReason.BINARY),),
    )
    _assert_invalid(duplicate_ignored, pi)
    duplicate_directory = replace(
        fi,
        skipped_directories=(
            SkippedDirectory("a.py", SkippedDirectoryReason.SYMLINK),
        ),
    )
    _assert_invalid(duplicate_directory, pi)
    duplicate_parsed = _parse_inventory((parsed, parsed))
    _assert_invalid(fi, duplicate_parsed)
    duplicate_skipped = _parse_inventory(
        (parsed,),
        skipped=(SkippedParseFile("a.py", ParseSkipReason.UNSUPPORTED_LANGUAGE),),
    )
    _assert_invalid(fi, duplicate_skipped)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("relative_path", 1),
        ("filename", None),
        ("filename", 1),
        ("extension", None),
        ("extension", 1),
        ("language", 1),
        ("language", True),
        ("category", "source"),
        ("size_bytes", -1),
        ("size_bytes", True),
    ],
)
def test_every_scanned_file_field_is_validated(cg, field, value):
    parsed = cg.parsed()
    fi, pi = cg.pair(parsed)
    _assert_invalid(fi.__class__(
        fi.repository_path,
        fi.total_files_seen,
        fi.included_files,
        fi.ignored_files,
        (replace(fi.files[0], **{field: value}),),
        fi.ignored,
        fi.skipped_directories,
        fi.repository_namespace,
    ), pi)


def test_ignored_and_skipped_reason_types_are_exact(cg):
    parsed = cg.parsed()
    fi, pi = cg.pair(parsed)
    bad_ignored = _file_inventory(
        fi.files,
        ignored=(IgnoredFile("ignored.bin", "binary"),),
    )
    _assert_invalid(bad_ignored, pi)
    bad_directory = replace(
        fi,
        skipped_directories=(SkippedDirectory("vendor", "symlink"),),
    )
    _assert_invalid(bad_directory, pi)
    unsupported = _scanned("x.rs", extension=".rs", language="rust")
    bad_skip_fi = _file_inventory(fi.files + (unsupported,))
    bad_skip_pi = _parse_inventory(
        (parsed,),
        skipped=(SkippedParseFile("x.rs", "unsupported_language"),),
    )
    _assert_invalid(bad_skip_fi, bad_skip_pi)


def test_exact_inventory_records_and_tuple_collections_are_required(cg):
    parsed = cg.parsed()
    fi, pi = cg.pair(parsed)
    _assert_invalid(object(), pi)
    _assert_invalid(fi, object())
    for field in ("files", "ignored", "skipped_directories"):
        _assert_invalid(replace(fi, **{field: list(getattr(fi, field))}), pi)
    for field in ("files", "skipped"):
        _assert_invalid(fi, replace(pi, **{field: list(getattr(pi, field))}))
    _assert_invalid(replace(fi, files=({},)), pi)
    _assert_invalid(fi, replace(pi, files=({},)))


@pytest.mark.parametrize(
    "location",
    [
        SourceLocation(-1, 1, 1, 0, 1, 1),
        SourceLocation(2, 1, 1, 0, 1, 1),
        SourceLocation(0, 11, 1, 0, 1, 1),
        SourceLocation(True, 1, 1, 0, 1, 1),
        SourceLocation(0, 1, 0, 0, 1, 1),
        SourceLocation(0, 1, 2, 0, 1, 1),
        SourceLocation(0, 1, 1, -1, 1, 1),
        SourceLocation(0, 1, 1, 2, 1, 1),
    ],
)
def test_locations_require_exact_ordered_coordinates_within_file(cg, location):
    symbol = cg.symbol("f")
    parsed = cg.parsed(symbols=(replace(symbol, location=location),))
    fi, pi = cg.pair(parsed)
    fi = replace(fi, files=(replace(fi.files[0], size_bytes=10),))
    _assert_invalid(fi, pi)


def test_bom_adjusted_bytes_and_multiline_columns_are_preserved(cg):
    location = SourceLocation(3, 8, 1, 5, 2, 0)
    symbol = replace(cg.symbol("f"), location=location)
    parsed = cg.parsed(symbols=(symbol,))
    fi, pi = cg.pair(parsed)
    fi = replace(fi, files=(replace(fi.files[0], size_bytes=8),))
    result = validate_inputs(fi, pi)
    assert result.pairs[0][1].symbols[0].location is location


def test_issue_locations_are_nullable_and_bounded(cg):
    issue_without_location = ParseIssue(ParseIssueKind.READ_ERROR, "read", None)
    parsed = cg.parsed(status=ParseStatus.PARTIAL, issues=(issue_without_location,))
    fi, pi = cg.pair(parsed)
    assert validate_inputs(fi, pi).pairs[0][1].issues == (issue_without_location,)
    bad_issue = replace(issue_without_location, location=SourceLocation(0, 5000, 1, 0, 1, 1))
    _assert_invalid(fi, replace(pi, files=(replace(parsed, issues=(bad_issue,)),)))


def test_all_six_location_fields_exclude_bool(cg):
    symbol = cg.symbol("f")
    parsed = cg.parsed(symbols=(symbol,))
    fi, pi = cg.pair(parsed)
    for field in (
        "start_byte",
        "end_byte",
        "start_line",
        "start_column",
        "end_line",
        "end_column",
    ):
        bad = replace(symbol.location, **{field: True})
        bad_symbol = replace(symbol, location=bad)
        _assert_invalid(fi, replace(pi, files=(replace(parsed, symbols=(bad_symbol,)),)))


def test_valid_nested_symbol_import_call_and_issue_metadata_is_preserved(cg):
    location = SourceLocation(0, 10, 1, 0, 1, 10)
    parameter = ParameterInfo("value", "int", "0")
    symbol = SymbolInfo(
        "run",
        SymbolKind.METHOD,
        "Service.run",
        "Service",
        location,
        (parameter,),
        "str",
        ("public",),
        ("Base",),
        ("Runnable",),
    )
    imported = ImportInfo(
        "pkg.mod",
        (ImportBinding("item", "alias"),),
        False,
        ("static",),
        location,
    )
    call = CallSite("Service.run", "alias", CallKind.CALL, location)
    issue = ParseIssue(ParseIssueKind.MISSING_NODE, "missing", location)
    parsed = cg.parsed(
        status=ParseStatus.PARTIAL,
        symbols=(symbol,),
        imports=(imported,),
        calls=(call,),
        issues=(issue,),
    )
    fi, pi = cg.pair(parsed)
    assert validate_inputs(fi, pi).pairs == ((fi.files[0], parsed),)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: replace(item, name=""),
        lambda item: replace(item, name=1),
        lambda item: replace(item, kind="method"),
        lambda item: replace(item, qualified_name=""),
        lambda item: replace(item, qualified_name=1),
        lambda item: replace(item, parent_qualified_name=1),
        lambda item: replace(item, location={}),
        lambda item: replace(item, parameters=[]),
        lambda item: replace(item, parameters=({},)),
        lambda item: replace(item, return_type=1),
        lambda item: replace(item, modifiers=[]),
        lambda item: replace(item, modifiers=(1,)),
        lambda item: replace(item, base_types=[]),
        lambda item: replace(item, base_types=(1,)),
        lambda item: replace(item, implemented_types=[]),
        lambda item: replace(item, implemented_types=(1,)),
    ],
)
def test_symbol_metadata_rejects_malformed_fields(cg, mutate):
    symbol = SymbolInfo(
        "run", SymbolKind.METHOD, "Service.run", None, cg.loc(), (), None, (), (), ()
    )
    parsed = cg.parsed(symbols=(symbol,))
    fi, pi = cg.pair(parsed)
    bad = replace(parsed, symbols=(mutate(symbol),))
    _assert_invalid(fi, replace(pi, files=(bad,)))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: replace(item, name=1),
        lambda item: replace(item, type_name=1),
        lambda item: replace(item, default_value_text=1),
    ],
)
def test_parameter_metadata_rejects_malformed_fields(cg, mutate):
    parameter = mutate(ParameterInfo("value", None, None))
    parsed = cg.parsed(symbols=(replace(cg.symbol("f"), parameters=(parameter,)),))
    fi, pi = cg.pair(parsed)
    _assert_invalid(fi, pi)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: replace(item, module=1),
        lambda item: replace(item, bindings=[]),
        lambda item: replace(item, bindings=({},)),
        lambda item: replace(item, is_wildcard=1),
        lambda item: replace(item, modifiers=[]),
        lambda item: replace(item, modifiers=(1,)),
        lambda item: replace(item, location={}),
    ],
)
def test_import_metadata_rejects_malformed_fields(cg, mutate):
    item = ImportInfo("pkg", (), False, (), cg.loc())
    parsed = cg.parsed(imports=(item,))
    fi, pi = cg.pair(parsed)
    bad = replace(parsed, imports=(mutate(item),))
    _assert_invalid(fi, replace(pi, files=(bad,)))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: replace(item, imported_name=1),
        lambda item: replace(item, alias=1),
    ],
)
def test_import_binding_metadata_rejects_malformed_fields(cg, mutate):
    binding = mutate(ImportBinding("item", None))
    parsed = cg.parsed(imports=(ImportInfo("pkg", (binding,), False, (), cg.loc()),))
    fi, pi = cg.pair(parsed)
    _assert_invalid(fi, pi)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: replace(item, caller_qualified_name=1),
        lambda item: replace(item, callee_text=1),
        lambda item: replace(item, kind="call"),
        lambda item: replace(item, location={}),
    ],
)
def test_call_metadata_rejects_malformed_fields(cg, mutate):
    call = CallSite(None, "f", CallKind.CALL, cg.loc())
    parsed = cg.parsed(calls=(call,))
    fi, pi = cg.pair(parsed)
    bad = replace(parsed, calls=(mutate(call),))
    _assert_invalid(fi, replace(pi, files=(bad,)))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: replace(item, kind="read_error"),
        lambda item: replace(item, message=1),
        lambda item: replace(item, location={}),
    ],
)
def test_issue_metadata_rejects_malformed_fields(cg, mutate):
    issue = ParseIssue(ParseIssueKind.READ_ERROR, "read", None)
    parsed = cg.parsed(status=ParseStatus.PARTIAL, issues=(mutate(issue),))
    fi, pi = cg.pair(parsed)
    _assert_invalid(fi, pi)


@pytest.mark.parametrize("digest", ["", "A" * 64, "0" * 63, "g" * 64, 1, True])
def test_present_source_digest_must_be_lowercase_sha256(cg, digest):
    parsed = replace(cg.parsed(), source_sha256=digest)
    fi, pi = cg.pair(parsed)
    _assert_invalid(fi, pi)


def test_none_digest_is_accepted_without_source_access(cg, monkeypatch):
    parsed = replace(cg.parsed(), source_sha256=None)
    fi, pi = cg.pair(parsed)

    def fail_open(*args, **kwargs):
        raise AssertionError("validation attempted source access")

    monkeypatch.setattr("builtins.open", fail_open)
    assert validate_inputs(fi, pi).pairs == ((fi.files[0], parsed),)


@pytest.mark.parametrize("field", ["symbols", "imports", "calls", "issues"])
def test_parsed_evidence_collections_are_exact_tuples(cg, field):
    parsed = cg.parsed()
    fi, pi = cg.pair(parsed)
    bad = replace(parsed, **{field: []})
    _assert_invalid(fi, replace(pi, files=(bad,)))


def test_parsed_file_language_and_status_require_exact_enums(cg):
    parsed = cg.parsed()
    fi, pi = cg.pair(parsed)
    _assert_invalid(fi, replace(pi, files=(replace(parsed, language="python"),)))
    _assert_invalid(fi, replace(pi, files=(replace(parsed, status="success"),)))


def test_failed_files_require_empty_extractions(cg):
    failed = cg.parsed("failed.py", status=ParseStatus.FAILED)
    fi, pi = cg.pair(failed)
    assert validate_inputs(fi, pi).pairs == ((fi.files[0], failed),)
    for field, evidence in (
        ("symbols", (cg.symbol("f"),)),
        ("imports", (ImportInfo("pkg", (), False, (), cg.loc()),)),
        ("calls", (CallSite(None, "f", CallKind.CALL, cg.loc()),)),
    ):
        bad = replace(failed, **{field: evidence})
        _assert_invalid(fi, replace(pi, files=(bad,)))


def test_success_and_partial_evidence_may_be_empty_or_nonempty(cg):
    success = cg.parsed("empty.py")
    partial = cg.parsed(
        "partial.py",
        status=ParseStatus.PARTIAL,
        symbols=(cg.symbol("f"),),
        imports=(ImportInfo("pkg", (), False, (), cg.loc()),),
        calls=(CallSite(None, "f", CallKind.CALL, cg.loc()),),
        issues=(ParseIssue(ParseIssueKind.MISSING_NODE, "missing", None),),
    )
    fi, pi = cg.pair(partial, success)
    result = validate_inputs(fi, pi)
    assert tuple(parsed.relative_path for _, parsed in result.pairs) == (
        "empty.py",
        "partial.py",
    )


def test_duplicate_evidence_is_preserved_for_later_collision_checks(cg):
    symbol = cg.symbol("same")
    imported = ImportInfo("pkg", (), False, (), cg.loc())
    call = CallSite(None, "same", CallKind.CALL, cg.loc())
    parsed = cg.parsed(
        symbols=(symbol, symbol),
        imports=(imported, imported),
        calls=(call, call),
    )
    fi, pi = cg.pair(parsed)
    validated = validate_inputs(fi, pi).pairs[0][1]
    assert validated.symbols == (symbol, symbol)
    assert validated.imports == (imported, imported)
    assert validated.calls == (call, call)


def test_validated_inputs_is_frozen_slotted_and_sorted_by_exact_path(cg):
    upper = cg.parsed("Z.py")
    lower = cg.parsed("a.py")
    fi, pi = cg.pair(lower, upper)
    result = validate_inputs(fi, replace(pi, files=(upper, lower)))
    assert isinstance(result, ValidatedInputs)
    assert result.pairs == ((fi.files[1], upper), (fi.files[0], lower))
    assert getattr(ValidatedInputs, "__slots__") == ("repository_namespace", "pairs")
    assert getattr(ValidatedInputs, "__dataclass_params__").frozen is True
    with pytest.raises(AttributeError):
        result.repository_namespace = "changed"


def test_case_distinct_unicode_paths_are_validated_as_distinct(cg):
    first = cg.parsed("Módül.py")
    second = cg.parsed("módül.py")
    file_inventory, parse_inventory = cg.pair(second, first)

    result = validate_inputs(file_inventory, parse_inventory)

    assert tuple(parsed.relative_path for _, parsed in result.pairs) == (
        "Módül.py",
        "módül.py",
    )
