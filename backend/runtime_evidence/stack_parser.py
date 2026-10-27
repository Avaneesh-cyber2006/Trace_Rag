"""Fixed-grammar stack trace parsing for runtime evidence."""

import re
from dataclasses import dataclass

from backend.runtime_evidence.config import RuntimeEvidenceConfig
from backend.runtime_evidence.drafts import (
    ArtifactParseResult,
    EventDraft,
    FrameDraft,
    IssueDraft,
)
from backend.runtime_evidence.exceptions import RuntimeEvidenceValidationError
from backend.runtime_evidence.lines import (
    PhysicalLine,
    decode_physical_line,
    split_physical_lines,
)
from backend.runtime_evidence.models import (
    RuntimeEventKind,
    RuntimeFrameLanguage,
    RuntimeIssueCode,
)
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


def _blocks(
    lines: tuple[PhysicalLine, ...],
) -> list[list[PhysicalLine]]:
    blocks: list[list[PhysicalLine]] = []
    current: list[PhysicalLine] = []
    for line in lines:
        if line.blank:
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _block_bytes(block: list[PhysicalLine]) -> int:
    return sum(len(line.raw) for line in block) + len(block) - 1


def _prefix_within(
    block: list[PhysicalLine], cap: int
) -> list[PhysicalLine]:
    selected: list[PhysicalLine] = []
    measured = 0
    for line in block:
        addition = len(line.raw) + (1 if selected else 0)
        if measured + addition > cap:
            break
        measured += addition
        selected.append(line)
    return selected


def _event_issue(
    code: RuntimeIssueCode,
    sequence: int,
    source_line: int | None,
) -> IssueDraft:
    return IssueDraft(
        code=code, event_sequence=sequence, source_line=source_line
    )


def _build_event(
    sequence: int,
    source_line: int,
    message: str | None,
    frames: tuple[FrameDraft, ...],
    text_truncated: bool,
    frames_truncated: bool,
) -> EventDraft:
    return EventDraft(
        sequence=sequence,
        source_line=source_line,
        kind=RuntimeEventKind.ERROR,
        timestamp_text=None,
        level=None,
        message=message,
        logger=None,
        operation=None,
        trace_id=None,
        span_id=None,
        parent_span_id=None,
        duration_ms_text=None,
        status=None,
        error_type=None,
        error_message=None,
        tags=(),
        frames=frames,
        text_truncated=text_truncated,
        frames_truncated=frames_truncated,
    )


def parse_stack_trace_text(
    content: bytes,
    config: RuntimeEvidenceConfig,
    declared_language: RuntimeFrameLanguage | None,
    max_new_events: int,
) -> ArtifactParseResult:
    """Parse one STACK_TRACE_TEXT artifact into events and issues."""
    if (
        type(declared_language) is not RuntimeFrameLanguage
        or declared_language is RuntimeFrameLanguage.UNKNOWN
    ):
        raise RuntimeEvidenceValidationError(
            "stack trace text requires a known declared language"
        )

    lines = split_physical_lines(content)
    blocks = _blocks(lines)
    if not blocks:
        return ArtifactParseResult(
            events=(),
            issues=(),
            text_truncated=False,
            frames_truncated=False,
            empty_input=True,
            event_limit_hit=False,
        )

    events: list[EventDraft] = []
    issues: list[IssueDraft] = []
    text_truncated = False
    frames_truncated = False
    event_limit_hit = False

    for block in blocks:
        if len(events) >= max_new_events:
            event_limit_hit = True
            break
        sequence = len(events)
        source_line = block[0].number
        over_cap = _block_bytes(block) > config.max_stack_bytes
        selected = (
            _prefix_within(block, config.max_stack_bytes)
            if over_cap
            else block
        )

        usable_lines: list[str] = []
        block_issues: list[IssueDraft] = []
        for line in selected:
            decoded = decode_physical_line(line, config.max_line_bytes)
            if decoded.issue is not None:
                block_issues.append(
                    IssueDraft(
                        code=decoded.issue,
                        event_sequence=None,
                        source_line=line.number,
                    )
                )
            else:
                usable_lines.append(decoded.text)

        if not over_cap and not usable_lines:
            issues.extend(block_issues)
            continue

        parsed = parse_stack_source(
            "\n".join(usable_lines),
            declared_language,
            max_frames=config.max_frames_per_event,
            max_function_bytes=config.max_frame_function_bytes,
        )
        if parsed.message is None:
            message = None
            message_truncated = False
        else:
            message, message_truncated = truncate_utf8_text(
                parsed.message, config.max_message_bytes
            )

        event_text_truncated = (
            message_truncated or parsed.function_truncated or over_cap
        )
        event_frames_truncated = parsed.frame_limit_hit or over_cap

        issues.extend(block_issues)
        if over_cap:
            issues.append(
                _event_issue(
                    RuntimeIssueCode.STACK_TOO_LARGE,
                    sequence,
                    source_line,
                )
            )
        elif parsed.recognized_frame_count == 0:
            issues.append(
                _event_issue(
                    RuntimeIssueCode.STACK_UNPARSEABLE,
                    sequence,
                    source_line,
                )
            )
        if parsed.frame_limit_hit:
            issues.append(
                _event_issue(
                    RuntimeIssueCode.FRAME_LIMIT_REACHED,
                    sequence,
                    source_line,
                )
            )
        if event_text_truncated:
            issues.append(
                _event_issue(
                    RuntimeIssueCode.TEXT_TRUNCATED,
                    sequence,
                    source_line,
                )
            )

        events.append(
            _build_event(
                sequence,
                source_line,
                message,
                parsed.frames,
                event_text_truncated,
                event_frames_truncated,
            )
        )
        text_truncated = text_truncated or event_text_truncated
        frames_truncated = frames_truncated or event_frames_truncated

    return ArtifactParseResult(
        events=tuple(events),
        issues=tuple(issues),
        text_truncated=text_truncated,
        frames_truncated=frames_truncated,
        empty_input=False,
        event_limit_hit=event_limit_hit,
    )
