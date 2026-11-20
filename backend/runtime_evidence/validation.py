"""Scalar validation helpers for runtime evidence."""

_LOWERCASE_HEX_DIGITS = frozenset("0123456789abcdef")
_ASCII_LETTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
)


def utf8_size(value: str) -> int:
    """Return the UTF-8 encoded byte length of ``value``."""
    return len(value.encode("utf-8"))


def require_scalar_text(
    value: object,
    field_name: str,
    error_type: type[Exception],
    *,
    allow_empty: bool = True,
    max_bytes: int | None = None,
    reject_nul: bool = True,
) -> str:
    """Require exact string text made only of Unicode scalar values."""
    if type(value) is not str:
        raise error_type(f"{field_name} must be a string")
    if not allow_empty and not value:
        raise error_type(f"{field_name} must not be empty")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise error_type(f"{field_name} must contain only Unicode scalar values")
    if reject_nul and "\x00" in value:
        raise error_type(f"{field_name} must not contain NUL")
    if max_bytes is not None and utf8_size(value) > max_bytes:
        raise error_type(f"{field_name} exceeds its UTF-8 byte limit")
    return value


def require_exact_bool(
    value: object,
    field_name: str,
    error_type: type[Exception],
) -> bool:
    """Require an exact ``bool`` value."""
    if type(value) is not bool:
        raise error_type(f"{field_name} must be an exact bool")
    return value


def require_positive_int(
    value: object,
    field_name: str,
    error_type: type[Exception],
) -> int:
    """Require an exact positive ``int`` value, rejecting ``bool``."""
    if type(value) is bool or type(value) is not int:
        raise error_type(f"{field_name} must be an exact int")
    if value <= 0:
        raise error_type(f"{field_name} must be positive")
    return value


def require_nonnegative_int(
    value: object,
    field_name: str,
    error_type: type[Exception],
) -> int:
    """Require an exact nonnegative ``int`` value, rejecting ``bool``."""
    if type(value) is bool or type(value) is not int:
        raise error_type(f"{field_name} must be an exact int")
    if value < 0:
        raise error_type(f"{field_name} must be nonnegative")
    return value


def require_sha256_digest(
    value: object,
    field_name: str,
    error_type: type[Exception],
) -> str:
    """Require a lowercase 64-character hexadecimal SHA-256 digest."""
    text = require_scalar_text(
        value, field_name, error_type, allow_empty=False
    )
    if len(text) != 64 or any(
        character not in _LOWERCASE_HEX_DIGITS for character in text
    ):
        raise error_type(f"{field_name} must be a lowercase SHA-256 digest")
    return text


def require_relative_posix_path(
    value: object,
    field_name: str,
    error_type: type[Exception],
) -> str:
    """Require a nonempty, canonical, repository-relative POSIX path."""
    text = require_scalar_text(
        value, field_name, error_type, allow_empty=False
    )
    if text.startswith("/"):
        raise error_type(f"{field_name} must be a relative path")
    if (
        len(text) >= 2
        and text[0] in _ASCII_LETTERS
        and text[1] == ":"
    ):
        raise error_type(f"{field_name} must not contain a drive prefix")
    if "\\" in text:
        raise error_type(f"{field_name} must use POSIX separators")
    if any(ord(character) < 0x20 for character in text):
        raise error_type(f"{field_name} must not contain control characters")
    if any(
        component in ("", ".", "..") for component in text.split("/")
    ):
        raise error_type(f"{field_name} must contain canonical components")
    return text


def truncate_utf8_text(value: str, max_bytes: int) -> tuple[str, bool]:
    """Truncate text at a code-point boundary, reserving an ellipsis."""
    if utf8_size(value) <= max_bytes:
        return (value, False)
    prefix_limit = max_bytes - 3
    if prefix_limit < 1:
        return ("", True)
    used_bytes = 0
    end_index = 0
    for index, character in enumerate(value):
        character_bytes = len(character.encode("utf-8"))
        if used_bytes + character_bytes > prefix_limit:
            break
        used_bytes += character_bytes
        end_index = index + 1
    if end_index == 0:
        return ("", True)
    return (value[:end_index] + "\u2026", True)
