import ast
import time
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from backend.runtime_evidence import stack_parser
from backend.runtime_evidence.config import RuntimeEvidenceConfig
from backend.runtime_evidence.drafts import FrameDraft
from backend.runtime_evidence.exceptions import RuntimeEvidenceValidationError
from backend.runtime_evidence.models import (
    RuntimeEventKind,
    RuntimeFrameLanguage,
    RuntimeIssueCode,
)
from backend.runtime_evidence.stack_parser import (
    StackParseResult,
    parse_stack_source,
    parse_stack_trace_text,
)


def _parse(
    text,
    language,
    *,
    max_frames=10,
    max_function_bytes=100,
):
    return parse_stack_source(
        text,
        language,
        max_frames=max_frames,
        max_function_bytes=max_function_bytes,
    )


def test_python_grammar_and_messages():
    text = (
        "Traceback (most recent call last):\n"
        '  File "pkg/app.py", line 12, in run\n'
        "    helper()\n"
        "ValueError: bad\n"
        "During handling of the above exception, another exception "
        "occurred:\n"
        '  File "pkg/other.py", line 3, in helper\n'
    )
    result = _parse(text, RuntimeFrameLanguage.PYTHON)
    assert [
        (frame.source_text, frame.line, frame.column, frame.function_text)
        for frame in result.frames
    ] == [
        ("pkg/app.py", 12, None, "run"),
        ("pkg/other.py", 3, None, "helper"),
    ]
    assert result.message == (
        "Traceback (most recent call last):\n"
        "ValueError: bad\n"
        "During handling of the above exception, another exception "
        "occurred:"
    )
    assert result.recognized_frame_count == 2
    assert result.function_truncated is False
    assert result.frame_limit_hit is False
    assert all(
        frame.language is RuntimeFrameLanguage.PYTHON
        for frame in result.frames
    )


@pytest.mark.parametrize(
    "language",
    [
        RuntimeFrameLanguage.JAVA,
        RuntimeFrameLanguage.JAVASCRIPT,
        RuntimeFrameLanguage.TYPESCRIPT,
        RuntimeFrameLanguage.TSX,
    ],
)
def test_java_and_v8_grammars(language):
    if language is RuntimeFrameLanguage.JAVA:
        text = (
            "Caused by: java.lang.IllegalStateException: x\n"
            "\tat a.b.C.run(C.java:7)\n"
            "\tat a.b.C.other(C.java)\n"
            "\tat a.b.C.native(Native Method)\n"
            "\tat a.b.C.unknown(Unknown Source)\n"
            "\t... 12 more"
        )
    else:
        text = (
            "Error: x\n"
            "    at run (webpack:///src/app.js:4:9)\n"
            "    at src/other.js:8:2\n"
        )
    result = _parse(text, language)
    if language is RuntimeFrameLanguage.JAVA:
        assert len(result.frames) == 4
        assert result.frames[0].source_text == "C.java"
        assert result.frames[0].line == 7
        assert result.frames[0].column is None
        assert result.frames[1].source_text == "C.java"
        assert result.frames[1].line is None
        assert result.frames[2].source_text is None
        assert result.frames[2].line is None
        assert result.frames[3].source_text is None
        assert result.frames[3].line is None
        assert result.message == (
            "Caused by: java.lang.IllegalStateException: x"
        )
    else:
        assert [
            (frame.function_text, frame.source_text, frame.line, frame.column)
            for frame in result.frames
        ] == [
            ("run", "webpack:///src/app.js", 4, 9),
            (None, "src/other.js", 8, 2),
        ]
        assert result.message == "Error: x"
    assert result.recognized_frame_count == len(result.frames)
    assert all(frame.language is language for frame in result.frames)
    assert all(
        "... 12 more" not in (frame.function_text or "")
        for frame in result.frames
    )


def test_java_native_and_unknown_source_are_no_source_frames():
    result = _parse(
        "\tat a.b.C.native(Native Method)\n"
        "\tat a.b.C.unknown(Unknown Source)\n",
        RuntimeFrameLanguage.JAVA,
    )
    assert [
        (frame.function_text, frame.source_text, frame.line, frame.column)
        for frame in result.frames
    ] == [
        ("a.b.C.native", None, None, None),
        ("a.b.C.unknown", None, None, None),
    ]
    assert result.recognized_frame_count == 2
    assert result.message is None


def test_function_is_none_when_v8_omits_it():
    result = _parse(
        "    at src/other.js:8:2\n",
        RuntimeFrameLanguage.JAVASCRIPT,
    )
    assert result.frames[0].function_text is None
    assert result.frames[0].source_text == "src/other.js"
    assert (result.frames[0].line, result.frames[0].column) == (8, 2)


def test_v8_function_text_may_contain_parentheses():
    direct = _parse(
        "    at foo(bar) ([eval]:1:21)\n",
        RuntimeFrameLanguage.JAVASCRIPT,
    )
    assert direct.frames[0].function_text == "foo(bar)"
    assert direct.frames[0].source_text == "[eval]"
    assert (direct.frames[0].line, direct.frames[0].column) == (1, 21)

    spaced = _parse(
        "    at foo (bar) (src.js:2:7)\n",
        RuntimeFrameLanguage.JAVASCRIPT,
    )
    assert spaced.frames[0].function_text == "foo (bar)"
    assert spaced.frames[0].source_text == "src.js"
    assert (spaced.frames[0].line, spaced.frames[0].column) == (2, 7)


def test_v8_eval_and_parenthesized_sources_are_not_split():
    eval_frame = _parse(
        "    at eval (eval at foo (src.js:1:2), <anonymous>:1:1)\n",
        RuntimeFrameLanguage.JAVASCRIPT,
    )
    assert eval_frame.frames[0].function_text == "eval"
    assert eval_frame.frames[0].source_text == (
        "eval at foo (src.js:1:2), <anonymous>"
    )
    assert (eval_frame.frames[0].line, eval_frame.frames[0].column) == (
        1,
        1,
    )

    named_eval = _parse(
        "    at foo (eval at <anonymous> (C:\\repo\\probe.js:1:1), "
        "<anonymous>:1:23)\n",
        RuntimeFrameLanguage.JAVASCRIPT,
    )
    assert named_eval.frames[0].function_text == "foo"
    assert named_eval.frames[0].source_text == (
        "eval at <anonymous> (C:\\repo\\probe.js:1:1), <anonymous>"
    )
    assert (
        named_eval.frames[0].line,
        named_eval.frames[0].column,
    ) == (1, 23)

    parenthesized_source = _parse(
        "    at run (foo (bar).js:1:2)\n",
        RuntimeFrameLanguage.JAVASCRIPT,
    )
    assert parenthesized_source.frames[0].function_text == "run"
    assert parenthesized_source.frames[0].source_text == "foo (bar).js"
    assert (
        parenthesized_source.frames[0].line,
        parenthesized_source.frames[0].column,
    ) == (1, 2)


def test_v8_sources_with_unbalanced_parentheses_are_retained_raw():
    unmatched_close = _parse(
        "    at probe (C:\\repo\\foo).js:1:26)\n",
        RuntimeFrameLanguage.JAVASCRIPT,
    )
    assert unmatched_close.frames[0].function_text == "probe"
    assert unmatched_close.frames[0].source_text == "C:\\repo\\foo).js"
    assert (
        unmatched_close.frames[0].line,
        unmatched_close.frames[0].column,
    ) == (1, 26)

    unmatched_open = _parse(
        "    at probe (C:\\repo\\foo (bar.js:1:26)\n",
        RuntimeFrameLanguage.JAVASCRIPT,
    )
    assert unmatched_open.frames[0].function_text == "probe"
    assert unmatched_open.frames[0].source_text == "C:\\repo\\foo (bar.js"
    assert (
        unmatched_open.frames[0].line,
        unmatched_open.frames[0].column,
    ) == (1, 26)


def test_v8_function_text_with_unbalanced_parentheses_is_retained():
    unmatched_close = _parse(
        "    at holder.foo)bar (C:\\repo\\weird.js:3:44)\n",
        RuntimeFrameLanguage.JAVASCRIPT,
    )
    assert unmatched_close.frames[0].function_text == "holder.foo)bar"
    assert unmatched_close.frames[0].source_text == "C:\\repo\\weird.js"
    assert (
        unmatched_close.frames[0].line,
        unmatched_close.frames[0].column,
    ) == (3, 44)

    unmatched_open = _parse(
        "    at holder.foo(bar (C:\\repo\\weird.js:4:44)\n",
        RuntimeFrameLanguage.JAVASCRIPT,
    )
    assert unmatched_open.frames[0].function_text == "holder.foo(bar"
    assert unmatched_open.frames[0].source_text == "C:\\repo\\weird.js"
    assert (
        unmatched_open.frames[0].line,
        unmatched_open.frames[0].column,
    ) == (4, 44)

    grouped = _parse(
        "    at holder.foo (bar) (baz (C:\\repo\\weird-2.js:2:49)\n",
        RuntimeFrameLanguage.JAVASCRIPT,
    )
    assert grouped.frames[0].function_text == "holder.foo (bar) (baz"
    assert grouped.frames[0].source_text == "C:\\repo\\weird-2.js"
    assert (grouped.frames[0].line, grouped.frames[0].column) == (2, 49)


def test_v8_grouped_function_and_unbalanced_source_keep_balanced_boundary():
    result = _parse(
        "    at foo (bar) (C:\\repo\\foo (bar.js:1:26)\n",
        RuntimeFrameLanguage.JAVASCRIPT,
    )
    assert result.frames[0].function_text == "foo (bar)"
    assert result.frames[0].source_text == "C:\\repo\\foo (bar.js"
    assert (result.frames[0].line, result.frames[0].column) == (1, 26)

    bare_source = _parse(
        "    at foo (bar) (foo (bar.js:1:26)\n",
        RuntimeFrameLanguage.JAVASCRIPT,
    )
    assert bare_source.frames[0].function_text == "foo (bar)"
    assert bare_source.frames[0].source_text == "foo (bar.js"
    assert (bare_source.frames[0].line, bare_source.frames[0].column) == (
        1,
        26,
    )


def test_no_recognized_frames_returns_empty_and_unparseable_input():
    result = _parse(
        "ValueError: bad\n"
        "not a frame\n",
        RuntimeFrameLanguage.PYTHON,
    )
    assert result.frames == ()
    assert result.recognized_frame_count == 0
    assert result.message == "ValueError: bad\nnot a frame"
    assert result.function_truncated is False
    assert result.frame_limit_hit is False


@pytest.mark.parametrize(
    "language",
    [
        RuntimeFrameLanguage.JAVASCRIPT,
        RuntimeFrameLanguage.TYPESCRIPT,
        RuntimeFrameLanguage.TSX,
    ],
)
def test_v8_columns_are_exact_and_function_may_be_omitted(language):
    text = (
        "    at run (src/app.ts:104:27)\n"
        "    at src/later.tsx:9:3\n"
    )
    result = _parse(text, language)
    assert [
        (frame.function_text, frame.source_text, frame.line, frame.column)
        for frame in result.frames
    ] == [
        ("run", "src/app.ts", 104, 27),
        (None, "src/later.tsx", 9, 3),
    ]
    assert result.message is None
    assert result.recognized_frame_count == 2


def test_python_and_java_frames_have_no_columns():
    python_frame = _parse(
        '  File "pkg/app.py", line 12, in run\n',
        RuntimeFrameLanguage.PYTHON,
    ).frames[0]
    java_frame = _parse(
        "\tat a.b.C.run(C.java:7)\n",
        RuntimeFrameLanguage.JAVA,
    ).frames[0]
    assert python_frame.column is None
    assert java_frame.column is None


def test_frames_preserve_reported_order():
    text = (
        '  File "first.py", line 1, in one\n'
        '  File "second.py", line 2, in two\n'
        '  File "third.py", line 3, in three\n'
    )
    result = _parse(text, RuntimeFrameLanguage.PYTHON)
    assert [frame.source_text for frame in result.frames] == [
        "first.py",
        "second.py",
        "third.py",
    ]


def test_suppressed_and_during_handling_headers_stay_in_same_message():
    text = (
        "Traceback (most recent call last):\n"
        '  File "a.py", line 1, in outer\n'
        "ValueError: outer\n"
        "Suppressed: Traceback (most recent call last):\n"
        '  File "b.py", line 2, in inner\n'
        "RuntimeError: inner\n"
        "During handling of the above exception, another exception "
        "occurred:\n"
        '  File "c.py", line 3, in again\n'
        "Caused by: KeyError: final"
    )
    result = _parse(text, RuntimeFrameLanguage.PYTHON)
    assert result.message == (
        "Traceback (most recent call last):\n"
        "ValueError: outer\n"
        "Suppressed: Traceback (most recent call last):\n"
        "RuntimeError: inner\n"
        "During handling of the above exception, another exception "
        "occurred:\n"
        "Caused by: KeyError: final"
    )
    assert [frame.source_text for frame in result.frames] == [
        "a.py",
        "b.py",
        "c.py",
    ]


def test_non_frame_message_lines_and_ignored_continuations():
    text = (
        'Exception in thread "main" java.lang.RuntimeException: bad\n'
        "\tat a.b.C.run(C.java:7)\n"
        "\t... 12 more\n"
        "\tat not-a-frame\n"
        "Caused by: java.io.IOException\n"
    )
    result = _parse(text, RuntimeFrameLanguage.JAVA)
    assert [frame.function_text for frame in result.frames] == ["a.b.C.run"]
    assert result.message == (
        'Exception in thread "main" java.lang.RuntimeException: bad\n'
        "\tat not-a-frame\n"
        "Caused by: java.io.IOException"
    )


def test_python_indented_snippets_are_ignored_even_when_they_say_file():
    text = (
        "Traceback (most recent call last):\n"
        '  File "pkg/app.py", line 12, in run\n'
        '      with open("File \\"nested\\"") as handle:\n'
        "  File is mentioned in this snippet too\n"
        "ValueError: bad\n"
    )
    result = _parse(text, RuntimeFrameLanguage.PYTHON)
    assert result.message == (
        "Traceback (most recent call last):\nValueError: bad"
    )
    assert [frame.source_text for frame in result.frames] == [
        "pkg/app.py"
    ]


@pytest.mark.parametrize(
    ("language", "text"),
    [
        (
            RuntimeFrameLanguage.PYTHON,
            '  File "a.py", line five, in f\n',
        ),
        (
            RuntimeFrameLanguage.PYTHON,
            '  File "a.py", line 5\n',
        ),
        (
            RuntimeFrameLanguage.PYTHON,
            "  File a.py, line 5, in f\n",
        ),
        (
            RuntimeFrameLanguage.JAVA,
            "\tat a.b.C.run(C.java:7) trailing\n",
        ),
        (
            RuntimeFrameLanguage.JAVA,
            "\tat a.b.C.run(C.java:7\n",
        ),
        (
            RuntimeFrameLanguage.JAVA,
            "\tat a.b.C.run()\n",
        ),
        (
            RuntimeFrameLanguage.JAVASCRIPT,
            "    at run (app.js:4)\n",
        ),
        (
            RuntimeFrameLanguage.JAVASCRIPT,
            "    at app.js\n",
        ),
        (
            RuntimeFrameLanguage.JAVASCRIPT,
            "    at run app.js:4\n",
        ),
        (
            RuntimeFrameLanguage.JAVASCRIPT,
            "    at (app.js:4:9)\n",
        ),
    ],
)
def test_malformed_or_incomplete_frames_are_not_recognized(
    language, text
):
    result = _parse(text, language)
    assert result.frames == ()
    assert result.recognized_frame_count == 0
    assert result.function_truncated is False
    assert result.frame_limit_hit is False


def test_numeric_locations_keep_multi_digit_values_exactly():
    python = _parse(
        '  File "a.py", line 98765, in run\n',
        RuntimeFrameLanguage.PYTHON,
    ).frames[0]
    java = _parse(
        "\tat a.b.C.run(Big.java:100200)\n",
        RuntimeFrameLanguage.JAVA,
    ).frames[0]
    v8 = _parse(
        "    at run (src/app.js:123456:7890)\n",
        RuntimeFrameLanguage.JAVASCRIPT,
    ).frames[0]
    assert (python.line, python.column) == (98765, None)
    assert (java.line, java.column) == (100200, None)
    assert (v8.line, v8.column) == (123456, 7890)


def test_function_truncation_is_reported_and_codepoint_safe():
    text = (
        "Unicode: caf\u00e9\n"
        "    at run_\u00e9\u00e9\u00e9 (src/app.js:1:2)\n"
        "    at short (src/short.js:3:4)\n"
    )
    result = _parse(
        text,
        RuntimeFrameLanguage.JAVASCRIPT,
        max_function_bytes=8,
    )
    assert result.frames[0].function_text == "run_\u2026"
    assert result.frames[1].function_text == "short"
    assert result.function_truncated is True
    assert result.message == "Unicode: caf\u00e9"
    encoded = result.frames[0].function_text.encode("utf-8")
    assert len(encoded) <= 8
    assert encoded.decode("utf-8") == "run_\u2026"


def test_function_at_exact_limit_is_not_truncated():
    result = _parse(
        "    at run (src/app.js:1:2)\n",
        RuntimeFrameLanguage.JAVASCRIPT,
        max_function_bytes=3,
    )
    assert result.frames[0].function_text == "run"
    assert result.function_truncated is False


def test_frame_limit_omits_extra_frames_and_counts_all_recognized():
    text = "".join(
        f'  File "file{index}.py", line {index + 1}, in f{index}\n'
        for index in range(4)
    )
    capped = _parse(
        text,
        RuntimeFrameLanguage.PYTHON,
        max_frames=2,
    )
    assert [frame.source_text for frame in capped.frames] == [
        "file0.py",
        "file1.py",
    ]
    assert capped.recognized_frame_count == 4
    assert capped.frame_limit_hit is True

    exact = _parse(
        text,
        RuntimeFrameLanguage.PYTHON,
        max_frames=4,
    )
    assert len(exact.frames) == 4
    assert exact.recognized_frame_count == 4
    assert exact.frame_limit_hit is False

    zero = _parse(
        text,
        RuntimeFrameLanguage.PYTHON,
        max_frames=0,
    )
    assert zero.frames == ()
    assert zero.recognized_frame_count == 4
    assert zero.frame_limit_hit is True


def test_raw_source_is_retained_without_normalization_or_rejection():
    over_long_source = "x" * 2000
    text = (
        '  File "C:\\repo\\a.py", line 1, in windows\n'
        '  File "percent%ZZ.js", line 2, in percent\n'
        '  File "nul\x00name.py", line 3, in nul\n'
        f'  File "{over_long_source}.py", line 4, in long\n'
        'ValueError: at fake.py:1:2\n'
    )
    result = _parse(text, RuntimeFrameLanguage.PYTHON)
    assert [frame.source_text for frame in result.frames] == [
        "C:\\repo\\a.py",
        "percent%ZZ.js",
        "nul\x00name.py",
        f"{over_long_source}.py",
    ]
    assert result.message == "ValueError: at fake.py:1:2"
    assert result.recognized_frame_count == 4
    assert result.function_truncated is False


def test_hostile_punctuation_in_java_source_is_retained_raw():
    text = (
        "\tat a.b.C.run(C:\\repo(foo){bar}\\\"quoted\\\".java:7)\n"
    )
    result = _parse(text, RuntimeFrameLanguage.JAVA)
    assert result.frames[0].source_text == (
        'C:\\repo(foo){bar}\\"quoted\\".java'
    )
    assert result.frames[0].line == 7


def test_hostile_punctuation_in_v8_source_is_retained_raw():
    text = (
        "    at run (C:\\repo (foo) {bar} \\\"quoted\\\".js:17:3)\n"
    )
    result = _parse(text, RuntimeFrameLanguage.JAVASCRIPT)
    assert result.frames[0].source_text == (
        'C:\\repo (foo) {bar} \\"quoted\\".js'
    )
    assert (result.frames[0].line, result.frames[0].column) == (17, 3)


def test_large_but_bounded_stack_is_processed_with_frame_cap():
    frame_lines = "".join(
        f'  File "generated/file{index}.py", line {index + 1}, in f\n'
        for index in range(500)
    )
    text = (
        "Traceback (most recent call last):\n"
        + frame_lines
        + "ValueError: final\n"
    )
    result = _parse(
        text,
        RuntimeFrameLanguage.PYTHON,
        max_frames=10,
    )
    assert result.recognized_frame_count == 500
    assert len(result.frames) == 10
    assert result.frames[0].source_text == "generated/file0.py"
    assert result.frames[-1].source_text == "generated/file9.py"
    assert result.frame_limit_hit is True
    assert result.message == (
        "Traceback (most recent call last):\nValueError: final"
    )


def test_v8_frames_without_message_return_none():
    result = _parse(
        "    at run (src/app.js:1:2)\n",
        RuntimeFrameLanguage.JAVASCRIPT,
    )
    assert result.message is None


def test_stack_parse_result_is_frozen_slotted_and_holds_tuple_frames():
    assert [field.name for field in fields(StackParseResult)] == [
        "frames",
        "message",
        "recognized_frame_count",
        "function_truncated",
        "frame_limit_hit",
    ]
    assert StackParseResult.__slots__ == (
        "frames",
        "message",
        "recognized_frame_count",
        "function_truncated",
        "frame_limit_hit",
    )
    result = _parse(
        '  File "a.py", line 1, in run\n',
        RuntimeFrameLanguage.PYTHON,
    )
    assert type(result.frames) is tuple
    assert type(result.frames[0]) is FrameDraft
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.message = "changed"
    with pytest.raises(FrozenInstanceError):
        result.frames[0].line = 2


def test_v8_continuation_is_ignored():
    result = _parse(
        "Error: x\n"
        "    at run (src/app.js:1:2)\n"
        "    ... 4 more\n",
        RuntimeFrameLanguage.JAVASCRIPT,
    )
    assert [frame.function_text for frame in result.frames] == ["run"]
    assert result.message == "Error: x"


def test_oversized_position_tokens_do_not_raise():
    huge = "9" * 4301
    python = _parse(
        f'  File "a.py", line {huge}, in run\n',
        RuntimeFrameLanguage.PYTHON,
    )
    assert python.recognized_frame_count == 1
    assert python.frames[0].line is None

    java = _parse(
        f"\tat a.b.C.run(C.java:{huge})\n",
        RuntimeFrameLanguage.JAVA,
    )
    assert java.recognized_frame_count == 1
    assert java.frames[0].line is None

    v8_line = _parse(
        f"    at run (src/app.js:{huge}:2)\n",
        RuntimeFrameLanguage.JAVASCRIPT,
    )
    assert v8_line.recognized_frame_count == 1
    assert (v8_line.frames[0].line, v8_line.frames[0].column) == (
        None,
        None,
    )

    v8_column = _parse(
        f"    at run (src/app.js:1:{huge})\n",
        RuntimeFrameLanguage.JAVASCRIPT,
    )
    assert v8_column.recognized_frame_count == 1
    assert (
        v8_column.frames[0].line,
        v8_column.frames[0].column,
    ) == (None, None)

    v8_bare_line = _parse(
        f"    at src/app.js:{huge}:2\n",
        RuntimeFrameLanguage.JAVASCRIPT,
    )
    assert v8_bare_line.recognized_frame_count == 1
    assert (
        v8_bare_line.frames[0].line,
        v8_bare_line.frames[0].column,
    ) == (None, None)

    v8_bare_column = _parse(
        f"    at src/app.js:1:{huge}\n",
        RuntimeFrameLanguage.JAVASCRIPT,
    )
    assert v8_bare_column.recognized_frame_count == 1
    assert (
        v8_bare_column.frames[0].line,
        v8_bare_column.frames[0].column,
    ) == (None, None)


def test_pathological_long_unmatched_lines_do_not_backtrack_catastrophically():
    java_line = "\tat " + "a(" * 32750 + "Z\n"
    v8_line = "    at " + "a (" * 21830 + "Z\n"
    started = time.perf_counter()
    java = _parse(java_line, RuntimeFrameLanguage.JAVA)
    v8 = _parse(v8_line, RuntimeFrameLanguage.JAVASCRIPT)
    elapsed = time.perf_counter() - started
    assert java.frames == ()
    assert v8.frames == ()
    assert elapsed < 2.0


def test_pathological_v8_function_frames_are_linear():
    text = "    at " + "a (" * 21830 + "a:1:2)\n"
    started = time.perf_counter()
    result = _parse(text, RuntimeFrameLanguage.JAVASCRIPT)
    elapsed = time.perf_counter() - started
    assert len(result.frames) == 1
    assert result.frames[0].function_text == "a"
    assert result.frames[0].source_text == ("a (" * 21829 + "a")
    assert (result.frames[0].line, result.frames[0].column) == (1, 2)
    assert elapsed < 0.5


def test_production_module_has_no_io_execution_or_dynamic_regexes():
    source = Path(stack_parser.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    banned_modules = {
        "importlib",
        "os",
        "pathlib",
        "socket",
        "subprocess",
    }
    banned_builtin_calls = {"compile", "eval", "exec", "open"}
    module_compile_calls = set()
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            for node in ast.walk(statement.value):
                if _is_regex_compile_call(node):
                    module_compile_calls.add(id(node))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(
                alias.name.split(".")[0] not in banned_modules
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            assert (
                node.module or ""
            ).split(".")[0] not in banned_modules
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                assert node.func.id not in banned_builtin_calls
            if _is_regex_compile_call(node):
                assert id(node) in module_compile_calls
                assert len(node.args) == 1
                pattern = node.args[0]
                assert isinstance(pattern, ast.Constant)
                assert type(pattern.value) is str


def _is_regex_compile_call(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "compile"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "re"
    )


def _config(**kw):
    values = dict(
        max_stack_bytes=256,
        max_frames_per_event=8,
        max_message_bytes=64,
        max_frame_function_bytes=64,
        max_line_bytes=256,
    )
    values.update(kw)
    return RuntimeEvidenceConfig(**values)


@pytest.mark.parametrize(
    "language", [None, RuntimeFrameLanguage.UNKNOWN]
)
def test_stack_trace_text_requires_known_declared_language(language):
    with pytest.raises(
        RuntimeEvidenceValidationError,
        match="^stack trace text requires a known declared language$",
    ):
        parse_stack_trace_text(b"", _config(), language, 10)


def test_one_block_is_one_error_event_with_source_line():
    content = (
        b"Traceback (most recent call last):\n"
        b'  File "a.py", line 3, in f\n'
        b"ValueError: x\n"
    )
    result = parse_stack_trace_text(
        content, _config(), RuntimeFrameLanguage.PYTHON, 10
    )
    assert len(result.events) == 1
    event = result.events[0]
    assert event.kind is RuntimeEventKind.ERROR
    assert event.source_line == 1
    assert len(event.frames) == 1
    assert event.message == "Traceback (most recent call last):\nValueError: x"


def test_blank_line_separates_independent_blocks():
    content = (
        b'  File "a.py", line 1, in f\n'
        b"\n"
        b'  File "b.py", line 2, in g\n'
    )
    result = parse_stack_trace_text(
        content, _config(), RuntimeFrameLanguage.PYTHON, 10
    )
    assert [
        (event.source_line, len(event.frames))
        for event in result.events
    ] == [(1, 1), (3, 1)]


def test_over_cap_block_retains_one_event_even_with_empty_prefix():
    content = b"x" * 40 + b"\n" + b"second\n"
    result = parse_stack_trace_text(
        content,
        _config(max_stack_bytes=10),
        RuntimeFrameLanguage.PYTHON,
        10,
    )
    assert len(result.events) == 1
    event = result.events[0]
    assert event.message is None and event.frames == ()
    assert event.text_truncated and event.frames_truncated
    assert [(issue.code, issue.source_line) for issue in result.issues] == [
        (RuntimeIssueCode.STACK_TOO_LARGE, 1),
        (RuntimeIssueCode.TEXT_TRUNCATED, 1),
    ]


def test_over_cap_prefix_processes_only_complete_lines():
    content = (
        b'  File "a.py", line 1, in f\n'
        b'  File "b.py", line 2, in g\n'
        b'  File "c.py", line 3, in h\n'
    )
    cap = 30  # first frame line plus LF, second does not fit
    result = parse_stack_trace_text(
        content,
        _config(max_stack_bytes=cap),
        RuntimeFrameLanguage.PYTHON,
        10,
    )
    assert len(result.events) == 1
    assert [frame.source_text for frame in result.events[0].frames] == [
        "a.py"
    ]
    assert result.events[0].frames_truncated is True


def test_unusable_block_produces_no_event_but_mixed_blocks_recover():
    content = b"\xff\n\n" + b'  File "a.py", line 1, in f\n'
    result = parse_stack_trace_text(
        content, _config(), RuntimeFrameLanguage.PYTHON, 10
    )
    assert len(result.events) == 1
    assert [(issue.code, issue.source_line) for issue in result.issues] == [
        (RuntimeIssueCode.INVALID_UTF8, 1)
    ]
    empty = parse_stack_trace_text(
        b"\xff\n", _config(), RuntimeFrameLanguage.PYTHON, 10
    )
    assert empty.events == () and empty.empty_input is False


def test_usable_block_without_frames_retains_event_and_reports_unparseable():
    result = parse_stack_trace_text(
        b"Error: no frames\n",
        _config(),
        RuntimeFrameLanguage.JAVASCRIPT,
        10,
    )
    assert len(result.events) == 1
    assert result.events[0].frames == ()
    assert [
        (issue.code, issue.source_line, issue.event_sequence)
        for issue in result.issues
    ] == [(RuntimeIssueCode.STACK_UNPARSEABLE, 1, 0)]


def test_empty_and_blank_only_artifacts_are_empty_input():
    for content in (b"", b" \t\n\n\t \n"):
        result = parse_stack_trace_text(
            content, _config(), RuntimeFrameLanguage.PYTHON, 10
        )
        assert result.events == ()
        assert result.issues == ()
        assert result.empty_input is True
        assert result.text_truncated is False
        assert result.frames_truncated is False
        assert result.event_limit_hit is False


def test_crlf_and_lf_blocks_measure_the_same_normalized_bytes():
    line_a = b'  File "a.py", line 1, in f'
    line_b = b'  File "b.py", line 2, in g'
    block_bytes = len(line_a) + 1 + len(line_b)
    lf_content = line_a + b"\n" + line_b + b"\n"
    crlf_content = line_a + b"\r\n" + line_b + b"\r\n"
    for content in (lf_content, crlf_content):
        exact = parse_stack_trace_text(
            content,
            _config(max_stack_bytes=block_bytes),
            RuntimeFrameLanguage.PYTHON,
            10,
        )
        assert [
            frame.source_text for frame in exact.events[0].frames
        ] == ["a.py", "b.py"]
        assert exact.events[0].frames_truncated is False
        assert exact.issues == ()

        capped = parse_stack_trace_text(
            content,
            _config(max_stack_bytes=block_bytes - 1),
            RuntimeFrameLanguage.PYTHON,
            10,
        )
        assert [
            frame.source_text for frame in capped.events[0].frames
        ] == ["a.py"]
        assert capped.events[0].frames_truncated is True


def test_overlong_line_is_skipped_and_reported():
    frame = b'  File "a.py", line 1, in f'
    content = frame + b"\n" + b"x" * 40 + b"\n"
    result = parse_stack_trace_text(
        content,
        _config(max_line_bytes=30),
        RuntimeFrameLanguage.PYTHON,
        10,
    )
    assert len(result.events) == 1
    assert [frame.source_text for frame in result.events[0].frames] == [
        "a.py"
    ]
    assert result.events[0].source_line == 1
    assert [
        (issue.code, issue.event_sequence, issue.source_line)
        for issue in result.issues
    ] == [(RuntimeIssueCode.LINE_TOO_LONG, None, 2)]


def test_nul_line_is_skipped_and_reported():
    content = b'  File "a.py", line 1, in f\n' + b"bad\x00line\n"
    result = parse_stack_trace_text(
        content, _config(), RuntimeFrameLanguage.PYTHON, 10
    )
    assert len(result.events) == 1
    assert result.events[0].source_line == 1
    assert [
        (issue.code, issue.event_sequence, issue.source_line)
        for issue in result.issues
    ] == [(RuntimeIssueCode.NUL_BYTE, None, 2)]


def test_block_with_no_usable_lines_produces_no_event():
    content = b"\xff\n" + b"x" * 300 + b"\n" + b"bad\x00\n"
    result = parse_stack_trace_text(
        content,
        _config(max_stack_bytes=1024),
        RuntimeFrameLanguage.PYTHON,
        10,
    )
    assert result.events == ()
    assert result.empty_input is False
    assert [
        (issue.code, issue.event_sequence, issue.source_line)
        for issue in result.issues
    ] == [
        (RuntimeIssueCode.INVALID_UTF8, None, 1),
        (RuntimeIssueCode.LINE_TOO_LONG, None, 2),
        (RuntimeIssueCode.NUL_BYTE, None, 3),
    ]


def test_frame_limit_and_function_truncation_report_once_per_event():
    content = (
        b'  File "a.py", line 1, in function_one\n'
        b'  File "b.py", line 2, in function_two\n'
    )
    result = parse_stack_trace_text(
        content,
        _config(max_frames_per_event=1, max_frame_function_bytes=4),
        RuntimeFrameLanguage.PYTHON,
        10,
    )
    event = result.events[0]
    assert [frame.source_text for frame in event.frames] == ["a.py"]
    assert event.frames[0].function_text == "f\u2026"
    assert event.frames_truncated is True
    assert event.text_truncated is True
    assert result.frames_truncated is True
    assert result.text_truncated is True
    assert [
        (issue.code, issue.event_sequence, issue.source_line)
        for issue in result.issues
    ] == [
        (RuntimeIssueCode.FRAME_LIMIT_REACHED, 0, 1),
        (RuntimeIssueCode.TEXT_TRUNCATED, 0, 1),
    ]


def test_message_truncation_reports_one_issue():
    content = b"E" * 40 + b"\n" + b'  File "a.py", line 1, in f\n'
    result = parse_stack_trace_text(
        content,
        _config(max_message_bytes=10),
        RuntimeFrameLanguage.PYTHON,
        10,
    )
    event = result.events[0]
    assert event.message == "E" * 7 + "\u2026"
    assert event.text_truncated is True
    assert event.frames_truncated is False
    assert result.text_truncated is True
    assert result.frames_truncated is False
    assert [
        (issue.code, issue.event_sequence, issue.source_line)
        for issue in result.issues
    ] == [(RuntimeIssueCode.TEXT_TRUNCATED, 0, 1)]


def test_multiple_blocks_preserve_order_and_sequences():
    content = (
        b'  File "a.py", line 1, in f\n'
        b"\n"
        b'  File "b.py", line 2, in g\n'
        b"\n"
        b'  File "c.py", line 3, in h\n'
    )
    result = parse_stack_trace_text(
        content, _config(), RuntimeFrameLanguage.PYTHON, 10
    )
    assert [
        (event.sequence, event.source_line, event.frames[0].source_text)
        for event in result.events
    ] == [(0, 1, "a.py"), (1, 3, "b.py"), (2, 5, "c.py")]


def test_event_capacity_stops_before_later_blocks():
    content = b'  File "a.py", line 1, in f\n\n' + b"\xff\n"
    result = parse_stack_trace_text(
        content, _config(), RuntimeFrameLanguage.PYTHON, 1
    )
    assert len(result.events) == 1
    assert result.event_limit_hit is True
    assert result.empty_input is False
    assert result.issues == ()


def test_chained_python_sections_remain_one_event():
    content = (
        b"Traceback (most recent call last):\n"
        b'  File "a.py", line 1, in outer\n'
        b"ValueError: outer\n"
        b"Caused by: KeyError: final\n"
        b'  File "b.py", line 2, in inner\n'
    )
    result = parse_stack_trace_text(
        content,
        _config(max_message_bytes=200),
        RuntimeFrameLanguage.PYTHON,
        10,
    )
    assert len(result.events) == 1
    assert [
        frame.source_text for frame in result.events[0].frames
    ] == ["a.py", "b.py"]
    assert result.events[0].message == (
        "Traceback (most recent call last):\n"
        "ValueError: outer\n"
        "Caused by: KeyError: final"
    )
    assert result.issues == ()


def test_invalid_utf8_surrogate_encoding_is_skipped():
    content = b'  File "a.py", line 1, in f\n' + b"\xed\xa0\x80\n"
    result = parse_stack_trace_text(
        content, _config(), RuntimeFrameLanguage.PYTHON, 10
    )
    assert result.events[0].source_line == 1
    assert [
        (issue.code, issue.event_sequence, issue.source_line)
        for issue in result.issues
    ] == [(RuntimeIssueCode.INVALID_UTF8, None, 2)]


def test_source_line_after_skipped_invalid_lines():
    content = b"\xff\n\x00bad\n\n" + b'  File "a.py", line 1, in f\n'
    result = parse_stack_trace_text(
        content, _config(), RuntimeFrameLanguage.PYTHON, 10
    )
    assert len(result.events) == 1
    assert result.events[0].source_line == 4
    assert [
        (issue.code, issue.source_line) for issue in result.issues
    ] == [
        (RuntimeIssueCode.INVALID_UTF8, 1),
        (RuntimeIssueCode.NUL_BYTE, 2),
    ]


def test_prefix_ending_exactly_at_cap_is_accepted():
    line_a = b'  File "a.py", line 1, in f'
    line_b = b'  File "b.py", line 2, in g'
    content = line_a + b"\n" + line_b + b"\n"
    cap = len(line_a) + 1
    result = parse_stack_trace_text(
        content,
        _config(max_stack_bytes=cap),
        RuntimeFrameLanguage.PYTHON,
        10,
    )
    assert [
        frame.source_text for frame in result.events[0].frames
    ] == ["a.py"]
    assert result.events[0].frames_truncated is True
    assert result.issues[0].code is RuntimeIssueCode.STACK_TOO_LARGE


def test_over_cap_message_only_prefix_is_not_unparseable():
    content = b"Error: x\n" + b"y" * 40 + b"\n"
    result = parse_stack_trace_text(
        content,
        _config(max_stack_bytes=20),
        RuntimeFrameLanguage.JAVASCRIPT,
        10,
    )
    assert len(result.events) == 1
    event = result.events[0]
    assert event.message == "Error: x"
    assert event.frames == ()
    assert [issue.code for issue in result.issues] == [
        RuntimeIssueCode.STACK_TOO_LARGE,
        RuntimeIssueCode.TEXT_TRUNCATED,
    ]


def test_v8_ambiguous_function_source_without_trailing_close_is_retained():
    result = parse_stack_trace_text(
        b"at foo(bar (src).js:1:2\n",
        _config(),
        RuntimeFrameLanguage.JAVASCRIPT,
        10,
    )
    assert len(result.events) == 1
    event = result.events[0]
    assert len(event.frames) == 1
    frame = event.frames[0]
    assert frame.function_text is None
    assert frame.source_text == "foo(bar (src).js"
    assert (frame.line, frame.column) == (1, 2)
    assert event.message is None
    assert result.issues == ()
    assert event.frames_truncated is False
    assert event.text_truncated is False


def test_v8_ambiguous_function_source_with_trailing_close_is_unparseable():
    result = parse_stack_trace_text(
        b"at foo(bar (src).js:1:2)\n",
        _config(),
        RuntimeFrameLanguage.JAVASCRIPT,
        10,
    )
    assert len(result.events) == 1
    event = result.events[0]
    assert event.frames == ()
    assert event.message == "at foo(bar (src).js:1:2)"
    assert [
        (issue.code, issue.source_line, issue.event_sequence)
        for issue in result.issues
    ] == [(RuntimeIssueCode.STACK_UNPARSEABLE, 1, 0)]


def test_v8_ambiguous_line_beside_valid_frame_stays_message_text():
    content = (
        b"at foo(bar (src).js:1:2)\n"
        b"    at good (ok.js:2:3)\n"
    )
    result = parse_stack_trace_text(
        content, _config(), RuntimeFrameLanguage.JAVASCRIPT, 10
    )
    assert len(result.events) == 1
    event = result.events[0]
    assert [frame.function_text for frame in event.frames] == ["good"]
    assert [frame.source_text for frame in event.frames] == ["ok.js"]
    assert event.message == "at foo(bar (src).js:1:2)"
    assert result.issues == ()
