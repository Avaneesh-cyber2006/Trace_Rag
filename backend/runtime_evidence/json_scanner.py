"""Bounded structural JSON scanning for runtime evidence."""

from dataclasses import dataclass

_ERROR_MESSAGE = "invalid JSON structure"
_JSON_WHITESPACE = frozenset(" \t\r\n")
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_ESCAPE_CHARACTERS = frozenset('"\\/bfnrt')
_LITERALS = ("true", "false", "null")


class JsonScanError(ValueError):
    """Raised when JSON syntax is invalid."""


@dataclass(frozen=True, slots=True)
class JsonScanResult:
    node_count: int
    max_number_token_bytes: int
    top_level_kind: str


def _has_surrogate(text: str) -> bool:
    for character in text:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            return True
    return False


def _skip_whitespace(text: str, index: int) -> int:
    length = len(text)
    while index < length and text[index] in _JSON_WHITESPACE:
        index += 1
    return index


def _scan_string(text: str, index: int) -> int:
    length = len(text)
    index += 1
    while index < length:
        character = text[index]
        if character == '"':
            return index + 1
        if character == "\\":
            index += 1
            if index >= length:
                raise JsonScanError(_ERROR_MESSAGE)
            escape = text[index]
            if escape == "u":
                if index + 4 >= length:
                    raise JsonScanError(_ERROR_MESSAGE)
                for offset in range(1, 5):
                    if text[index + offset] not in _HEX_DIGITS:
                        raise JsonScanError(_ERROR_MESSAGE)
                index += 5
                continue
            if escape not in _ESCAPE_CHARACTERS:
                raise JsonScanError(_ERROR_MESSAGE)
            index += 1
            continue
        if ord(character) < 0x20:
            raise JsonScanError(_ERROR_MESSAGE)
        index += 1
    raise JsonScanError(_ERROR_MESSAGE)


def _is_digit(character: str) -> bool:
    return "0" <= character <= "9"


def _scan_number(text: str, index: int) -> tuple[int, int]:
    start = index
    length = len(text)
    if text[index] == "-":
        index += 1
        if index >= length:
            raise JsonScanError(_ERROR_MESSAGE)
    if text[index] == "0":
        index += 1
    elif "1" <= text[index] <= "9":
        index += 1
        while index < length and _is_digit(text[index]):
            index += 1
    else:
        raise JsonScanError(_ERROR_MESSAGE)
    if index < length and text[index] == ".":
        index += 1
        if index >= length or not _is_digit(text[index]):
            raise JsonScanError(_ERROR_MESSAGE)
        while index < length and _is_digit(text[index]):
            index += 1
    if index < length and text[index] in "eE":
        index += 1
        if index < length and text[index] in "+-":
            index += 1
        if index >= length or not _is_digit(text[index]):
            raise JsonScanError(_ERROR_MESSAGE)
        while index < length and _is_digit(text[index]):
            index += 1
    return index, index - start


def _scan_literal(text: str, index: int) -> int | None:
    for literal in _LITERALS:
        if text.startswith(literal, index):
            return index + len(literal)
    return None


def scan_json_structure(text: str) -> JsonScanResult:
    """Scan JSON syntax without materializing a value tree."""
    if _has_surrogate(text):
        raise JsonScanError(_ERROR_MESSAGE)

    length = len(text)
    index = 0
    node_count = 0
    max_number_token_bytes = 0
    top_level_kind = None
    stack: list[list[str]] = [["root", "value"]]

    def consume_value(position: int, after_state: str) -> int:
        nonlocal index, node_count, max_number_token_bytes, top_level_kind
        position = _skip_whitespace(text, position)
        if position >= length:
            raise JsonScanError(_ERROR_MESSAGE)
        character = text[position]
        if character == "{":
            node_count += 1
            if top_level_kind is None:
                top_level_kind = "object"
            stack[-1][1] = after_state
            stack.append(["object", "key_or_end"])
            return position + 1
        if character == "[":
            node_count += 1
            if top_level_kind is None:
                top_level_kind = "array"
            stack[-1][1] = after_state
            stack.append(["array", "value_or_end"])
            return position + 1
        if character == '"':
            end = _scan_string(text, position)
            node_count += 1
            if top_level_kind is None:
                top_level_kind = "scalar"
            stack[-1][1] = after_state
            return end
        if character == "-" or _is_digit(character):
            end, token_bytes = _scan_number(text, position)
            node_count += 1
            if token_bytes > max_number_token_bytes:
                max_number_token_bytes = token_bytes
            if top_level_kind is None:
                top_level_kind = "scalar"
            stack[-1][1] = after_state
            return end
        end = _scan_literal(text, position)
        if end is not None:
            node_count += 1
            if top_level_kind is None:
                top_level_kind = "scalar"
            stack[-1][1] = after_state
            return end
        raise JsonScanError(_ERROR_MESSAGE)

    while stack:
        frame = stack[-1]
        kind = frame[0]
        state = frame[1]

        if kind == "root":
            if state == "value":
                index = consume_value(index, "done")
            else:
                index = _skip_whitespace(text, index)
                if index != length:
                    raise JsonScanError(_ERROR_MESSAGE)
                stack.pop()
            continue

        if kind == "object":
            if state == "key_or_end":
                index = _skip_whitespace(text, index)
                if index >= length:
                    raise JsonScanError(_ERROR_MESSAGE)
                if text[index] == "}":
                    index += 1
                    stack.pop()
                    continue
                if text[index] != '"':
                    raise JsonScanError(_ERROR_MESSAGE)
                index = _scan_string(text, index)
                node_count += 1
                frame[1] = "colon"
                continue
            if state == "key":
                index = _skip_whitespace(text, index)
                if index >= length or text[index] != '"':
                    raise JsonScanError(_ERROR_MESSAGE)
                index = _scan_string(text, index)
                node_count += 1
                frame[1] = "colon"
                continue
            if state == "colon":
                index = _skip_whitespace(text, index)
                if index >= length or text[index] != ":":
                    raise JsonScanError(_ERROR_MESSAGE)
                index += 1
                frame[1] = "value"
                continue
            if state == "value":
                index = consume_value(index, "comma_or_end")
                continue
            if state == "comma_or_end":
                index = _skip_whitespace(text, index)
                if index >= length:
                    raise JsonScanError(_ERROR_MESSAGE)
                if text[index] == ",":
                    index += 1
                    frame[1] = "key"
                    continue
                if text[index] == "}":
                    index += 1
                    stack.pop()
                    continue
                raise JsonScanError(_ERROR_MESSAGE)
            raise JsonScanError(_ERROR_MESSAGE)

        if state == "value_or_end":
            index = _skip_whitespace(text, index)
            if index >= length:
                raise JsonScanError(_ERROR_MESSAGE)
            if text[index] == "]":
                index += 1
                stack.pop()
                continue
            index = consume_value(index, "comma_or_end")
            continue
        if state == "value":
            index = consume_value(index, "comma_or_end")
            continue
        if state == "comma_or_end":
            index = _skip_whitespace(text, index)
            if index >= length:
                raise JsonScanError(_ERROR_MESSAGE)
            if text[index] == ",":
                index += 1
                frame[1] = "value"
                continue
            if text[index] == "]":
                index += 1
                stack.pop()
                continue
            raise JsonScanError(_ERROR_MESSAGE)
        raise JsonScanError(_ERROR_MESSAGE)

    return JsonScanResult(
        node_count=node_count,
        max_number_token_bytes=max_number_token_bytes,
        top_level_kind=top_level_kind,
    )
