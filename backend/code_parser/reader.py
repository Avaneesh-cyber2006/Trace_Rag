"""Fail-closed source-byte reader for parser inventory candidates."""

from __future__ import annotations

import codecs
from dataclasses import dataclass
import errno
import functools
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from typing import BinaryIO, Callable

from backend.file_scanner.models import ScannedFile

from .exceptions import RepositoryParseError
from .models import ParseIssueKind


_PATH_INVALID_MESSAGE = "Invalid source path."
_LINK_UNSAFE_MESSAGE = "Source path cannot be opened safely."
_READ_ERROR_MESSAGE = "Unable to read source file."
_FILE_CHANGED_MESSAGE = "Source file changed after scanning."
_DECODING_ERROR_MESSAGE = "Source file is not valid UTF-8."
_WINDOWS_REPARSE_POINT = 0x00000400
_IS_WINDOWS = os.name == "nt"


@dataclass(frozen=True, slots=True)
class SourceBuffer:
    original_bytes: bytes
    parse_bytes: bytes
    bom_prefix_bytes: int


class SourceReadError(Exception):
    """A sanitized per-file source read failure."""

    def __init__(self, kind: ParseIssueKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _source_error(kind: ParseIssueKind) -> SourceReadError:
    messages = {
        ParseIssueKind.PATH_INVALID: _PATH_INVALID_MESSAGE,
        ParseIssueKind.LINK_UNSAFE: _LINK_UNSAFE_MESSAGE,
        ParseIssueKind.READ_ERROR: _READ_ERROR_MESSAGE,
        ParseIssueKind.FILE_CHANGED: _FILE_CHANGED_MESSAGE,
        ParseIssueKind.DECODING_ERROR: _DECODING_ERROR_MESSAGE,
    }
    return SourceReadError(kind, messages[kind])


def is_reparse_metadata(metadata: os.stat_result) -> bool:
    """Return whether non-following metadata identifies a Windows reparse point."""

    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", _WINDOWS_REPARSE_POINT)
    return bool(attributes & reparse_flag)


def _stat_identity(metadata: os.stat_result) -> tuple[int, int]:
    device = getattr(metadata, "st_dev", None)
    inode = getattr(metadata, "st_ino", None)
    if not isinstance(device, int) or not isinstance(inode, int):
        raise _source_error(ParseIssueKind.LINK_UNSAFE)
    if _IS_WINDOWS and inode == 0:
        raise _source_error(ParseIssueKind.LINK_UNSAFE)
    return device, inode


def _same_stat_identity(first: os.stat_result, second: os.stat_result) -> bool:
    return _stat_identity(first) == _stat_identity(second)


def _require_regular(metadata: os.stat_result) -> None:
    if is_reparse_metadata(metadata) or stat.S_ISLNK(metadata.st_mode):
        raise _source_error(ParseIssueKind.LINK_UNSAFE)
    if not stat.S_ISREG(metadata.st_mode):
        raise _source_error(ParseIssueKind.LINK_UNSAFE)


def _raise_posix_open_error(error: OSError) -> None:
    unsafe_errors = {errno.ELOOP, errno.ENOTDIR}
    if error.errno in unsafe_errors:
        raise _source_error(ParseIssueKind.LINK_UNSAFE) from error
    raise _source_error(ParseIssueKind.READ_ERROR) from error


def _require_posix_primitives() -> None:
    required_flags = ("O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(os, name) for name in required_flags):
        raise _source_error(ParseIssueKind.LINK_UNSAFE)
    if os.open not in os.supports_dir_fd or os.stat not in os.supports_dir_fd:
        raise _source_error(ParseIssueKind.LINK_UNSAFE)
    if os.stat not in os.supports_follow_symlinks:
        raise _source_error(ParseIssueKind.LINK_UNSAFE)


def _open_verified_posix(root: Path, parts: tuple[str, ...]) -> BinaryIO:
    """Open a regular child using root-relative, non-following POSIX descriptors."""

    _require_posix_primitives()
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    file_flags |= getattr(os, "O_NONBLOCK", 0)
    directory_fd: int | None = None
    file_fd: int | None = None

    try:
        root_before = os.stat(root, follow_symlinks=False)
        if not stat.S_ISDIR(root_before.st_mode) or is_reparse_metadata(root_before):
            raise _source_error(ParseIssueKind.LINK_UNSAFE)
        directory_fd = os.open(root, directory_flags)
        root_opened = os.fstat(directory_fd)
        if not stat.S_ISDIR(root_opened.st_mode) or not _same_stat_identity(
            root_before, root_opened
        ):
            raise _source_error(ParseIssueKind.LINK_UNSAFE)

        for component in parts[:-1]:
            before = os.stat(component, dir_fd=directory_fd, follow_symlinks=False)
            if (
                stat.S_ISLNK(before.st_mode)
                or is_reparse_metadata(before)
                or not stat.S_ISDIR(before.st_mode)
            ):
                raise _source_error(ParseIssueKind.LINK_UNSAFE)
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            opened = os.fstat(next_fd)
            if not stat.S_ISDIR(opened.st_mode) or not _same_stat_identity(before, opened):
                os.close(next_fd)
                raise _source_error(ParseIssueKind.FILE_CHANGED)
            os.close(directory_fd)
            directory_fd = next_fd

        before = os.stat(parts[-1], dir_fd=directory_fd, follow_symlinks=False)
        _require_regular(before)
        file_fd = os.open(parts[-1], file_flags, dir_fd=directory_fd)
        opened = os.fstat(file_fd)
        _require_regular(opened)
        if not _same_stat_identity(before, opened):
            raise _source_error(ParseIssueKind.FILE_CHANGED)

        stream = os.fdopen(file_fd, "rb", buffering=0)
        file_fd = None
        return stream
    except SourceReadError:
        raise
    except OSError as error:
        _raise_posix_open_error(error)
        raise AssertionError("unreachable")
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)


@dataclass(frozen=True, slots=True)
class _WindowsMetadata:
    st_file_attributes: int
    volume_serial: int
    file_index: int
    size: int


@functools.lru_cache(maxsize=1)
def _load_windows_api() -> SimpleNamespace:
    try:
        import ctypes
        from ctypes import wintypes
        import msvcrt
    except (ImportError, OSError) as error:
        raise _source_error(ParseIssueKind.LINK_UNSAFE) from error

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        get_information = kernel32.GetFileInformationByHandle
        get_information.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ByHandleFileInformation),
        ]
        get_information.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
    except (AttributeError, OSError) as error:
        raise _source_error(ParseIssueKind.LINK_UNSAFE) from error

    return SimpleNamespace(
        ctypes=ctypes,
        wintypes=wintypes,
        msvcrt=msvcrt,
        information_type=ByHandleFileInformation,
        create_file=create_file,
        get_information=get_information,
        close_handle=close_handle,
        invalid_handle=ctypes.c_void_p(-1).value,
    )


def _windows_extended_path(path: Path) -> str:
    text = str(path)
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def _open_windows_handle(path: Path, desired_access: int) -> int:
    api = _load_windows_api()
    share_read_write_delete = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    open_reparse_point = 0x00200000
    backup_semantics = 0x02000000
    handle = api.create_file(
        _windows_extended_path(path),
        desired_access,
        share_read_write_delete,
        None,
        open_existing,
        open_reparse_point | backup_semantics,
        None,
    )
    if handle is None or handle == api.invalid_handle:
        error = api.ctypes.WinError(api.ctypes.get_last_error())
        unsafe_codes = {681, 1920, 4390, 4392, 4393, 4394}
        if getattr(error, "winerror", None) in unsafe_codes:
            raise _source_error(ParseIssueKind.LINK_UNSAFE) from error
        raise _source_error(ParseIssueKind.READ_ERROR) from error
    return int(handle)


def _close_windows_handle(handle: int) -> None:
    api = _load_windows_api()
    api.close_handle(api.wintypes.HANDLE(handle))


def _windows_handle_metadata(handle: int) -> _WindowsMetadata:
    api = _load_windows_api()
    information = api.information_type()
    if not api.get_information(api.wintypes.HANDLE(handle), api.ctypes.byref(information)):
        error = api.ctypes.WinError(api.ctypes.get_last_error())
        raise _source_error(ParseIssueKind.LINK_UNSAFE) from error
    return _WindowsMetadata(
        st_file_attributes=int(information.dwFileAttributes),
        volume_serial=int(information.dwVolumeSerialNumber),
        file_index=(int(information.nFileIndexHigh) << 32) | int(information.nFileIndexLow),
        size=(int(information.nFileSizeHigh) << 32) | int(information.nFileSizeLow),
    )


def _windows_identity(metadata: _WindowsMetadata) -> tuple[int, int]:
    if metadata.file_index == 0:
        raise _source_error(ParseIssueKind.LINK_UNSAFE)
    return metadata.volume_serial, metadata.file_index


def _verify_windows_parents(root: Path, parts: tuple[str, ...]) -> None:
    file_read_attributes = 0x00000080
    directory_attribute = 0x00000010
    for count in range(1, len(parts)):
        handle = _open_windows_handle(root.joinpath(*parts[:count]), file_read_attributes)
        try:
            metadata = _windows_handle_metadata(handle)
            if is_reparse_metadata(metadata) or not (
                metadata.st_file_attributes & directory_attribute
            ):
                raise _source_error(ParseIssueKind.LINK_UNSAFE)
            _windows_identity(metadata)
        finally:
            _close_windows_handle(handle)


def _open_verified_windows(root: Path, parts: tuple[str, ...]) -> BinaryIO:
    """Open a Windows child with non-following reparse and same-handle checks."""

    if not _IS_WINDOWS:
        raise _source_error(ParseIssueKind.LINK_UNSAFE)

    file_read_attributes = 0x00000080
    generic_read = 0x80000000
    directory_attribute = 0x00000010
    path = root.joinpath(*parts)
    _verify_windows_parents(root, parts)

    before_handle = _open_windows_handle(path, file_read_attributes)
    try:
        before = _windows_handle_metadata(before_handle)
    finally:
        _close_windows_handle(before_handle)
    if is_reparse_metadata(before) or before.st_file_attributes & directory_attribute:
        raise _source_error(ParseIssueKind.LINK_UNSAFE)

    opened_handle = _open_windows_handle(path, generic_read)
    try:
        opened = _windows_handle_metadata(opened_handle)
        if is_reparse_metadata(opened) or opened.st_file_attributes & directory_attribute:
            raise _source_error(ParseIssueKind.LINK_UNSAFE)
        if _windows_identity(before) != _windows_identity(opened):
            raise _source_error(ParseIssueKind.FILE_CHANGED)
        _verify_windows_parents(root, parts)

        api = _load_windows_api()
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        file_descriptor = api.msvcrt.open_osfhandle(opened_handle, flags)
        opened_handle = 0
        try:
            return os.fdopen(file_descriptor, "rb", buffering=0)
        except (OSError, ValueError):
            os.close(file_descriptor)
            raise
    except SourceReadError:
        raise
    except (OSError, ValueError) as error:
        raise _source_error(ParseIssueKind.READ_ERROR) from error
    finally:
        if opened_handle:
            _close_windows_handle(opened_handle)


_PlatformOpener = Callable[[Path, tuple[str, ...]], BinaryIO]


def _platform_opener() -> _PlatformOpener | None:
    if os.name == "posix":
        return _open_verified_posix
    if os.name == "nt":
        return _open_verified_windows
    return None


def _validate_relative_path(relative_path: object) -> tuple[str, ...]:
    if not isinstance(relative_path, str) or not relative_path:
        raise _source_error(ParseIssueKind.PATH_INVALID)
    if "\x00" in relative_path or "\\" in relative_path or relative_path.startswith("/"):
        raise _source_error(ParseIssueKind.PATH_INVALID)
    if len(relative_path) >= 2 and relative_path[0].isalpha() and relative_path[1] == ":":
        raise _source_error(ParseIssueKind.PATH_INVALID)

    parts = tuple(relative_path.split("/"))
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise _source_error(ParseIssueKind.PATH_INVALID)

    if _IS_WINDOWS:
        reserved = {"CON", "PRN", "AUX", "NUL"}
        reserved.update(f"COM{number}" for number in range(1, 10))
        reserved.update(f"LPT{number}" for number in range(1, 10))
        for part in parts:
            stem = part.rstrip(" .").split(".", maxsplit=1)[0].upper()
            if (
                stem in reserved
                or part.endswith((" ", "."))
                or any(character in part for character in ':<>"|?*')
            ):
                raise _source_error(ParseIssueKind.PATH_INVALID)
    return parts


class SafeSourceReader:
    def __init__(self, repository_path: Path | str) -> None:
        if isinstance(repository_path, str) and not repository_path:
            raise RepositoryParseError("Unable to establish the repository root.")
        try:
            root = Path(repository_path).resolve(strict=True)
            metadata = os.stat(root, follow_symlinks=False)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise RepositoryParseError("Unable to establish the repository root.") from error
        if not stat.S_ISDIR(metadata.st_mode):
            raise RepositoryParseError("The repository root is not a directory.")
        self.repository_path = root

    def read(self, file: ScannedFile) -> SourceBuffer:
        parts = _validate_relative_path(file.relative_path)
        candidate = self.repository_path.joinpath(*parts)
        try:
            candidate.relative_to(self.repository_path)
        except ValueError as error:
            raise _source_error(ParseIssueKind.PATH_INVALID) from error

        opener = _platform_opener()
        if opener is None:
            raise _source_error(ParseIssueKind.LINK_UNSAFE)

        try:
            with opener(self.repository_path, parts) as source:
                before = os.fstat(source.fileno())
                _require_regular(before)
                _stat_identity(before)
                if before.st_size != file.size_bytes:
                    raise _source_error(ParseIssueKind.FILE_CHANGED)

                original_bytes = source.read(file.size_bytes)
                overflow = source.read(1)
                if len(original_bytes) != file.size_bytes or overflow:
                    raise _source_error(ParseIssueKind.FILE_CHANGED)

                after = os.fstat(source.fileno())
                _require_regular(after)
                if after.st_size != file.size_bytes or not _same_stat_identity(before, after):
                    raise _source_error(ParseIssueKind.FILE_CHANGED)

            try:
                with opener(self.repository_path, parts) as current_source:
                    current = os.fstat(current_source.fileno())
                    _require_regular(current)
                    if current.st_size != file.size_bytes or not _same_stat_identity(
                        before, current
                    ):
                        raise _source_error(ParseIssueKind.FILE_CHANGED)
            except SourceReadError as error:
                if error.kind in {ParseIssueKind.READ_ERROR, ParseIssueKind.FILE_CHANGED}:
                    raise _source_error(ParseIssueKind.FILE_CHANGED) from error
                raise
        except SourceReadError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise _source_error(ParseIssueKind.READ_ERROR) from error

        bom_prefix_bytes = len(codecs.BOM_UTF8) if original_bytes.startswith(codecs.BOM_UTF8) else 0
        parse_bytes = original_bytes[bom_prefix_bytes:]
        if bom_prefix_bytes and parse_bytes.startswith(codecs.BOM_UTF8):
            raise _source_error(ParseIssueKind.DECODING_ERROR)
        try:
            parse_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise _source_error(ParseIssueKind.DECODING_ERROR) from error
        return SourceBuffer(original_bytes, parse_bytes, bom_prefix_bytes)
