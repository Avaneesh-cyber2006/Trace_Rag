"""Fixed-grammar stack trace parsing for runtime evidence."""

import re
from dataclasses import dataclass

from backend.runtime_evidence.drafts import FrameDraft
from backend.runtime_evidence.models import RuntimeFrameLanguage
from backend.runtime_evidence.validation import truncate_utf8_text

_PYTHON_FRAME_PATTERN = re.compile(
    r'^[ \t]*File "(?P<source>.*)", line (?P<line>[0-9]+), '
    r"in (?P<function>.+)$"
)
_JAVA_FRAME_PREFIX_PATTERN = re.compile(
    r"^[ \t]*at (?P<function>[^\s(]+)\("
)
_V8_AT_PREFIX_PATTERN = re.compile(r"^[ \t]*at ")
_CONTINUATION_PATTERN = re.compile(r"^[ \t]*\.\.\. [0-9]+ more$")

_V8_LANGUAGES = frozenset(
    (
        RuntimeFrameLanguage.JAVASCRIPT,
        RuntimeFrameLanguage.TYPESCRIPT,
        RuntimeFrameLanguage.TSX,
    )
)
_BLANK_CHARACTERS = frozenset(" \t")
_MAX_POSITION_DIGITS = 19


@dataclass(frozen=True, slots=True)
class StackParseResult:
    frames: tuple[FrameDraft, ...]
    message: str | None
    recognized_frame_count: int
    function_truncated: bool
    frame_limit_hit: bool


def _is_blank(line: str) -> bool:
    return all(character in _BLANK_CHARACTERS for character in line)


def _join_message(lines: list[str]) -> str | None:
    if not lines:
        return None
    return "\n".join(lines)


def _is_ascii_digits(text: str) -> bool:
    return bool(text) and all("0" <= character <= "9" for character in text)


def _parse_position(text: str) -> int | None:
    if len(text) > _MAX_POSITION_DIGITS:
        return None
    return int(text)


def _build_frame(
    language: RuntimeFrameLanguage,
    function_text: str | None,
    source_text: str | None,
    line: int | None,
    column: int | None,
    max_function_bytes: int,
) -> tuple[FrameDraft, bool]:
    truncated = False
    if function_text is not None:
        function_text, truncated = truncate_utf8_text(
            function_text, max_function_bytes
        )
    return (
        FrameDraft(
            language=language,
            function_text=function_text,
            source_text=source_text,
            line=line,
            column=column,
        ),
        truncated,
    )


def _split_java_source_and_line(
    inside: str,
) -> tuple[str, int | None]:
    separator = inside.rfind(":")
    if separator > 0:
        line_text = inside[separator + 1:]
        if _is_ascii_digits(line_text):
            return (
                inside[:separator],
                _parse_position(line_text),
            )
    return (inside, None)


def _split_v8_suffix(
    text: str,
) -> tuple[str, int | None, int | None] | None:
    column_separator = text.rfind(":")
    if column_separator <= 0:
        return None
    line_separator = text.rfind(":", 0, column_separator)
    if line_separator <= 0:
        return None
    prefix = text[:line_separator]
    line_text = text[line_separator + 1:column_separator]
    column_text = text[column_separator + 1:]
    if not (
        prefix
        and _is_ascii_digits(line_text)
        and _is_ascii_digits(column_text)
    ):
        return None
    line = _parse_position(line_text)
    column = _parse_position(column_text)
    if line is None or column is None:
        return (prefix, None, None)
    return (prefix, line, column)


def _split_v8_source_and_positions(
    inside: str,
) -> tuple[str, int | None, int | None] | None:
    return _split_v8_suffix(inside)


def _parenthesis_state(text: str) -> tuple[bool, bool, bool]:
    openings: list[int] = []
    balanced = True
    has_group = False
    for index, character in enumerate(text):
        if character == "(":
            openings.append(index)
        elif character == ")":
            if openings:
                openings.pop()
                if not openings:
                    has_group = True
            else:
                balanced = False
    if openings:
        balanced = False
    unmatched_source_like = False
    if openings:
        first_unmatched = openings[0] + 1
        unmatched_source_like = (
            text.find("/", first_unmatched) >= 0
            or text.find("\\", first_unmatched) >= 0
        )
    return balanced, has_group, unmatched_source_like


def _find_v8_wrapper_opening(inside: str) -> int | None:
    depth = 1
    for index in range(len(inside) - 1, -1, -1):
        character = inside[index]
        if character == ")":
            depth += 1
        elif character == "(":
            if depth == 1:
                return index
            depth -= 1
    return None


def _split_v8_function_and_source(
    inside: str,
) -> tuple[str, str, int | None, int | None] | None:
    suffix = _split_v8_suffix(inside)
    if suffix is None:
        return None
    prefix, line, column = suffix
    suffix_start = len(prefix)

    depth = 0
    balanced = True
    last_balanced_separator = None
    for index, character in enumerate(inside):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                balanced = False
        if (
            character == " "
            and index > 0
            and index + 2 < suffix_start
            and inside[index + 1] == "("
            and balanced
            and depth == 0
        ):
            last_balanced_separator = index

    # V8 does not escape parentheses in either function or source text.
    # Prefer the wrapper that ends at the final close. An unbalanced prefix
    # with a complete group is trusted only when its unmatched group is not
    # source-like and the wrapper source has a location separator; otherwise
    # use the last balanced prefix to avoid swallowing an unbalanced source.
    separator = None
    opening = _find_v8_wrapper_opening(inside)
    if (
        opening is not None
        and opening > 1
        and inside[opening - 1] == " "
        and opening + 1 < suffix_start
    ):
        function_balanced, has_group, unmatched_source_like = (
            _parenthesis_state(inside[:opening - 1])
        )
        wrapper_source_like = (
            inside.find("/", opening + 1, suffix_start) >= 0
            or inside.find("\\", opening + 1, suffix_start) >= 0
        )
        if (
            function_balanced
            or (
                has_group
                and not unmatched_source_like
                and wrapper_source_like
            )
            or last_balanced_separator is None
        ):
            separator = opening - 1
    if separator is None:
        separator = last_balanced_separator
    if separator is None:
        return None
    source_text = inside[separator + 2:suffix_start]
    if not source_text:
        return None
    return (
        inside[:separator],
        source_text,
        line,
        column,
    )


def _classify_python_line(
    line: str,
    language: RuntimeFrameLanguage,
    max_function_bytes: int,
) -> tuple[FrameDraft | None, str | None, bool]:
    match = _PYTHON_FRAME_PATTERN.fullmatch(line)
    if match is not None:
        frame, truncated = _build_frame(
            language,
            match.group("function"),
            match.group("source"),
            _parse_position(match.group("line")),
            None,
            max_function_bytes,
        )
        return frame, None, truncated
    if line[:1] in _BLANK_CHARACTERS:
        return None, None, False
    return None, line, False


def _classify_java_line(
    line: str,
    language: RuntimeFrameLanguage,
    max_function_bytes: int,
) -> tuple[FrameDraft | None, str | None, bool]:
    if _CONTINUATION_PATTERN.fullmatch(line) is not None:
        return None, None, False
    prefix = _JAVA_FRAME_PREFIX_PATTERN.match(line)
    if prefix is None:
        return None, line, False
    remainder = line[prefix.end():]
    if not remainder.endswith(")"):
        return None, line, False
    inside = remainder[:-1]
    if not inside:
        return None, line, False
    function_text = prefix.group("function")
    if inside in ("Native Method", "Unknown Source"):
        frame, truncated = _build_frame(
            language,
            function_text,
            None,
            None,
            None,
            max_function_bytes,
        )
        return frame, None, truncated
    source_text, line_number = _split_java_source_and_line(inside)
    frame, truncated = _build_frame(
        language,
        function_text,
        source_text,
        line_number,
        None,
        max_function_bytes,
    )
    return frame, None, truncated


def _classify_v8_line(
    line: str,
    language: RuntimeFrameLanguage,
    max_function_bytes: int,
) -> tuple[FrameDraft | None, str | None, bool]:
    if _CONTINUATION_PATTERN.fullmatch(line) is not None:
        return None, None, False
    prefix = _V8_AT_PREFIX_PATTERN.match(line)
    if prefix is None:
        return None, line, False
    body = line[prefix.end():]
    if body.endswith(")"):
        parsed_function = _split_v8_function_and_source(body[:-1])
        if parsed_function is None:
            return None, line, False
        function_text, source_text, line_number, column = (
            parsed_function
        )
        frame, truncated = _build_frame(
            language,
            function_text,
            source_text,
            line_number,
            column,
            max_function_bytes,
        )
        return frame, None, truncated
    parsed = _split_v8_source_and_positions(body)
    if parsed is None:
        return None, line, False
    source_text, line_number, column = parsed
    frame, truncated = _build_frame(
        language,
        None,
        source_text,
        line_number,
        column,
        max_function_bytes,
    )
    return frame, None, truncated


def _classify_line(
    line: str,
    language: RuntimeFrameLanguage,
    max_function_bytes: int,
) -> tuple[FrameDraft | None, str | None, bool]:
    if language is RuntimeFrameLanguage.PYTHON:
        return _classify_python_line(
            line, language, max_function_bytes
        )
    if language is RuntimeFrameLanguage.JAVA:
        return _classify_java_line(line, language, max_function_bytes)
    if language in _V8_LANGUAGES:
        return _classify_v8_line(line, language, max_function_bytes)
    return None, line, False


def parse_stack_source(
    text: str,
    language: RuntimeFrameLanguage,
    *,
    max_frames: int,
    max_function_bytes: int,
) -> StackParseResult:
    """Parse one stack-trace block using its declared language grammar."""
    frames: list[FrameDraft] = []
    message_lines: list[str] = []
    recognized_frame_count = 0
    function_truncated = False
    frame_limit_hit = False

    for line in text.split("\n"):
        if _is_blank(line):
            continue
        frame, message_line, truncated = _classify_line(
            line, language, max_function_bytes
        )
        if frame is not None:
            recognized_frame_count += 1
            if len(frames) >= max_frames:
                frame_limit_hit = True
                continue
            frames.append(frame)
            if truncated:
                function_truncated = True
        elif message_line is not None:
            message_lines.append(message_line)

    return StackParseResult(
        frames=tuple(frames),
        message=_join_message(message_lines),
        recognized_frame_count=recognized_frame_count,
        function_truncated=function_truncated,
        frame_limit_hit=frame_limit_hit,
    )
