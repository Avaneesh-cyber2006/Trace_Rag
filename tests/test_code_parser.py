import codecs
from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import stat
from types import SimpleNamespace

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
