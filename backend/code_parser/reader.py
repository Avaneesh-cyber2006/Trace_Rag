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


def _open_verified_posix_directory(
    directory_fd: int,
    component: str,
    directory_flags: int,
    before: os.stat_result,
) -> int:
    next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
    try:
        opened = os.fstat(next_fd)
        if not stat.S_ISDIR(opened.st_mode) or not _same_stat_identity(before, opened):
            raise _source_error(ParseIssueKind.FILE_CHANGED)
        return next_fd
    except BaseException:
        os.close(next_fd)
        raise


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
            next_fd = _open_verified_posix_directory(
                directory_fd,
                component,
                directory_flags,
                before,
            )
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


_WINDOWS_DIRECTORY_ATTRIBUTE = 0x00000010
_WINDOWS_FILE_READ_DATA = 0x00000001
_WINDOWS_FILE_TRAVERSE = 0x00000020
_WINDOWS_FILE_READ_ATTRIBUTES = 0x00000080
_WINDOWS_SYNCHRONIZE = 0x00100000
_WINDOWS_ROOT_ACCESS = (
    _WINDOWS_FILE_TRAVERSE | _WINDOWS_FILE_READ_ATTRIBUTES | _WINDOWS_SYNCHRONIZE
)
_WINDOWS_FINAL_ACCESS = (
    _WINDOWS_FILE_READ_DATA | _WINDOWS_FILE_READ_ATTRIBUTES | _WINDOWS_SYNCHRONIZE
)


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

    class UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(UnicodeString)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        ]

    class IoStatusUnion(ctypes.Union):
        _fields_ = [
            ("Status", ctypes.c_long),
            ("Pointer", wintypes.LPVOID),
        ]

    class IoStatusBlock(ctypes.Structure):
        _anonymous_ = ("result",)
        _fields_ = [
            ("result", IoStatusUnion),
            ("Information", ctypes.c_size_t),
        ]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
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
        nt_open_file = ntdll.NtOpenFile
        nt_open_file.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            ctypes.POINTER(ObjectAttributes),
            ctypes.POINTER(IoStatusBlock),
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        nt_open_file.restype = ctypes.c_long
        status_to_dos_error = ntdll.RtlNtStatusToDosError
        status_to_dos_error.argtypes = [wintypes.ULONG]
        status_to_dos_error.restype = wintypes.ULONG
        open_osfhandle = msvcrt.open_osfhandle
        if not callable(open_osfhandle):
            raise AttributeError("open_osfhandle")
    except (AttributeError, OSError) as error:
        raise _source_error(ParseIssueKind.LINK_UNSAFE) from error

    return SimpleNamespace(
        ctypes=ctypes,
        wintypes=wintypes,
        msvcrt=msvcrt,
        information_type=ByHandleFileInformation,
        unicode_string_type=UnicodeString,
        object_attributes_type=ObjectAttributes,
        io_status_block_type=IoStatusBlock,
        create_file=create_file,
        get_information=get_information,
        close_handle=close_handle,
        nt_open_file=nt_open_file,
        status_to_dos_error=status_to_dos_error,
        open_osfhandle=open_osfhandle,
        invalid_handle=ctypes.c_void_p(-1).value,
    )


def _windows_extended_path(path: Path) -> str:
    text = str(path)
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def _raise_windows_open_error(error: OSError) -> None:
    unsafe_codes = {681, 1920, 4390, 4392, 4393, 4394}
    if getattr(error, "winerror", None) in unsafe_codes:
        raise _source_error(ParseIssueKind.LINK_UNSAFE) from error
    raise _source_error(ParseIssueKind.READ_ERROR) from error


def _open_windows_root_handle(path: Path, desired_access: int) -> int:
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
        _raise_windows_open_error(error)
        raise AssertionError("unreachable")
    return int(handle)


def _open_windows_relative_handle(
    parent_handle: int,
    component: str,
    desired_access: int,
    *,
    directory: bool,
) -> int:
    api = _load_windows_api()
    name_buffer = api.ctypes.create_unicode_buffer(component)
    name_length = len(component.encode("utf-16-le"))
    object_name = api.unicode_string_type(
        name_length,
        name_length + 2,
        api.ctypes.cast(name_buffer, api.wintypes.LPWSTR),
    )
    object_attributes = api.object_attributes_type(
        api.ctypes.sizeof(api.object_attributes_type),
        api.wintypes.HANDLE(parent_handle),
        api.ctypes.pointer(object_name),
        0x00000040,
        None,
        None,
    )
    io_status = api.io_status_block_type()
    handle = api.wintypes.HANDLE()
    share_read_write_delete = 0x00000001 | 0x00000002 | 0x00000004
    open_options = 0x00200000 | 0x00000020
    open_options |= 0x00000001 if directory else 0x00000040
    status = api.nt_open_file(
        api.ctypes.byref(handle),
        desired_access,
        api.ctypes.byref(object_attributes),
        api.ctypes.byref(io_status),
        share_read_write_delete,
        open_options,
    )
    if status < 0:
        error_code = api.status_to_dos_error(api.wintypes.ULONG(status).value)
        error = api.ctypes.WinError(error_code)
        _raise_windows_open_error(error)
        raise AssertionError("unreachable")
    if handle.value is None:
        raise _source_error(ParseIssueKind.LINK_UNSAFE)
    return int(handle.value)


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


def _require_windows_directory(metadata: _WindowsMetadata) -> None:
    if is_reparse_metadata(metadata) or not (
        metadata.st_file_attributes & _WINDOWS_DIRECTORY_ATTRIBUTE
    ):
        raise _source_error(ParseIssueKind.LINK_UNSAFE)


def _require_windows_regular(metadata: _WindowsMetadata) -> None:
    if is_reparse_metadata(metadata) or (
        metadata.st_file_attributes & _WINDOWS_DIRECTORY_ATTRIBUTE
    ):
        raise _source_error(ParseIssueKind.LINK_UNSAFE)


def _open_checked_windows_root(root: Path) -> int:
    before_handle = _open_windows_root_handle(root, _WINDOWS_ROOT_ACCESS)
    try:
        before = _windows_handle_metadata(before_handle)
        _require_windows_directory(before)
        _windows_identity(before)
    finally:
        _close_windows_handle(before_handle)

    opened_handle = _open_windows_root_handle(root, _WINDOWS_ROOT_ACCESS)
    try:
        opened = _windows_handle_metadata(opened_handle)
        _require_windows_directory(opened)
        if _windows_identity(before) != _windows_identity(opened):
            raise _source_error(ParseIssueKind.FILE_CHANGED)
        return opened_handle
    except BaseException:
        _close_windows_handle(opened_handle)
        raise


def _open_checked_windows_relative(
    parent_handle: int,
    component: str,
    desired_access: int,
    *,
    directory: bool,
) -> tuple[int, _WindowsMetadata]:
    before_handle = _open_windows_relative_handle(
        parent_handle,
        component,
        _WINDOWS_FILE_READ_ATTRIBUTES | _WINDOWS_SYNCHRONIZE,
        directory=directory,
    )
    try:
        before = _windows_handle_metadata(before_handle)
        if directory:
            _require_windows_directory(before)
        else:
            _require_windows_regular(before)
        _windows_identity(before)
    finally:
        _close_windows_handle(before_handle)

    opened_handle = _open_windows_relative_handle(
        parent_handle,
        component,
        desired_access,
        directory=directory,
    )
    try:
        opened = _windows_handle_metadata(opened_handle)
        if directory:
            _require_windows_directory(opened)
        else:
            _require_windows_regular(opened)
        if _windows_identity(before) != _windows_identity(opened):
            raise _source_error(ParseIssueKind.FILE_CHANGED)
        return opened_handle, opened
    except BaseException:
        _close_windows_handle(opened_handle)
        raise


def _windows_handle_to_stream(handle: int) -> BinaryIO:
    api = _load_windows_api()
    open_osfhandle = getattr(api, "open_osfhandle", None)
    if not callable(open_osfhandle):
        _close_windows_handle(handle)
        raise _source_error(ParseIssueKind.LINK_UNSAFE)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    try:
        file_descriptor = open_osfhandle(handle, flags)
    except (AttributeError, OSError, ValueError) as error:
        _close_windows_handle(handle)
        raise _source_error(ParseIssueKind.LINK_UNSAFE) from error
    try:
        return os.fdopen(file_descriptor, "rb", buffering=0)
    except (OSError, ValueError):
        os.close(file_descriptor)
        raise


class _WindowsVerifiedStream:
    def __init__(
        self,
        stream: BinaryIO,
        retained_directories: list[int],
        final_component: str,
        final_identity: tuple[int, int],
    ) -> None:
        self._stream = stream
        self._retained_directories = retained_directories
        self._final_component = final_component
        self._final_identity = final_identity
        self._closed = False

    def fileno(self) -> int:
        return self._stream.fileno()

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def verify_path(self, expected_size: int) -> None:
        if self._closed or not self._retained_directories:
            raise _source_error(ParseIssueKind.LINK_UNSAFE)
        parent_handle = self._retained_directories[-1]
        handle = _open_windows_relative_handle(
            parent_handle,
            self._final_component,
            _WINDOWS_FILE_READ_ATTRIBUTES | _WINDOWS_SYNCHRONIZE,
            directory=False,
        )
        try:
            metadata = _windows_handle_metadata(handle)
            _require_windows_regular(metadata)
            if metadata.size != expected_size or _windows_identity(metadata) != self._final_identity:
                raise _source_error(ParseIssueKind.FILE_CHANGED)
        finally:
            _close_windows_handle(handle)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._stream.close()
        finally:
            while self._retained_directories:
                _close_windows_handle(self._retained_directories.pop())

    def __enter__(self) -> "_WindowsVerifiedStream":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _open_verified_windows(root: Path, parts: tuple[str, ...]) -> BinaryIO:
    """Open a Windows child with non-following reparse and same-handle checks."""

    if not _IS_WINDOWS:
        raise _source_error(ParseIssueKind.LINK_UNSAFE)

    retained_directories: list[int] = []
    final_handle: int | None = None
    try:
        root_handle = _open_checked_windows_root(root)
        retained_directories.append(root_handle)
        for component in parts[:-1]:
            directory_handle, _ = _open_checked_windows_relative(
                retained_directories[-1],
                component,
                _WINDOWS_ROOT_ACCESS,
                directory=True,
            )
            retained_directories.append(directory_handle)

        final_handle, final_metadata = _open_checked_windows_relative(
            retained_directories[-1],
            parts[-1],
            _WINDOWS_FINAL_ACCESS,
            directory=False,
        )
        stream_handle = final_handle
        final_handle = None
        stream = _windows_handle_to_stream(stream_handle)
        return _WindowsVerifiedStream(
            stream,
            retained_directories,
            parts[-1],
            _windows_identity(final_metadata),
        )
    except SourceReadError:
        raise
    except (AttributeError, OSError, ValueError) as error:
        raise _source_error(ParseIssueKind.READ_ERROR) from error
    finally:
        if final_handle is not None:
            _close_windows_handle(final_handle)
        if final_handle is not None or "stream" not in locals():
            while retained_directories:
                _close_windows_handle(retained_directories.pop())


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
            verified_with_retained_parent = False
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

                verify_path = getattr(source, "verify_path", None)
                if callable(verify_path):
                    try:
                        verify_path(file.size_bytes)
                    except SourceReadError as error:
                        if error.kind in {
                            ParseIssueKind.READ_ERROR,
                            ParseIssueKind.FILE_CHANGED,
                        }:
                            raise _source_error(ParseIssueKind.FILE_CHANGED) from error
                        raise
                    verified_with_retained_parent = True

            if not verified_with_retained_parent:
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
