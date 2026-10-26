import ast
import json
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from backend.runtime_evidence import json_adapter, json_scanner
from backend.runtime_evidence.config import RuntimeEvidenceConfig
from backend.runtime_evidence.json_adapter import (
    NormalizedEvent,
    normalize_event_object,
)
from backend.runtime_evidence.json_scanner import (
    JsonScanError,
    JsonScanResult,
    scan_json_structure,
)
from backend.runtime_evidence.models import (
    RuntimeEventKind,
    RuntimeFrameLanguage,
    RuntimeIssueCode,
    issue_message,
)


@pytest.mark.parametrize(
    ("text", "nodes", "kind", "number_bytes"),
    [
        ("{}", 1, "object", 0),
        ('{"a":1}', 3, "object", 1),
        ("[{}]", 2, "array", 0),
        ('{"a":{}}', 3, "object", 0),
        ('[ "x" ]', 2, "array", 0),
        ('{"a":true}', 3, "object", 0),
        ('{"a":false}', 3, "object", 0),
        ("[null]", 2, "array", 0),
        ('{"\\u0061":-1.2e+3}', 3, "object", 7),
        ('{"a":1,"a":2}', 5, "object", 1),
        ("7", 1, "scalar", 1),
    ],
)
def test_exact_node_and_number_token_counts(
    text, nodes, kind, number_bytes
):
    result = scan_json_structure(text)
    assert (
        result.node_count,
        result.top_level_kind,
        result.max_number_token_bytes,
    ) == (nodes, kind, number_bytes)


def test_braces_quotes_and_backslashes_inside_strings_are_not_structure():
    result = scan_json_structure('{"a":"} ] \\\\ \\" {", "b":[1]}')
    assert result.top_level_kind == "object"
    assert result.node_count == 6


@pytest.mark.parametrize(
    "bad",
    [
        '{"a":}',
        '{"a":1,}',
        '{"a" 1}',
        '"\\x"',
        '"\\u12"',
        "[1,]",
        '{"a":1} trailing',
        "NaN",
        "Infinity",
        '{"a":"\x01"}',
    ],
)
def test_invalid_syntax_is_rejected_without_artifact_text(bad):
    with pytest.raises(JsonScanError) as caught:
        scan_json_structure(bad)
    assert "\\x01" not in str(caught.value)


def test_scanner_does_not_materialize_json(monkeypatch):
    monkeypatch.setattr(
        json, "loads", lambda *args, **kwargs: pytest.fail("materialized")
    )
    monkeypatch.setattr(
        json,
        "JSONDecoder",
        lambda *args, **kwargs: pytest.fail("decoder constructed"),
    )
    assert scan_json_structure('{"a":[1,2,3]}').node_count == 6


def test_deep_nesting_is_iterative_and_unpaired_escape_is_syntax_only():
    deep = "[" * 5000 + "0" + "]" * 5000
    assert scan_json_structure(deep).node_count == 5001
    assert scan_json_structure('"\\ud800"').node_count == 1


def test_json_whitespace_only_around_value():
    result = scan_json_structure(' \t\r\n{"a"\t:\r\n[ 1 , 2 ]\n}\t ')
    assert (
        result.node_count,
        result.top_level_kind,
        result.max_number_token_bytes,
    ) == (5, "object", 1)


@pytest.mark.parametrize(
    "text",
    ["", "   ", "\t\r\n", "\v{}", "\f{}", "{}\u00a0", "\u00a0{}"],
)
def test_empty_or_non_json_whitespace_is_rejected(text):
    with pytest.raises(JsonScanError):
        scan_json_structure(text)


def test_top_level_trailing_whitespace_is_allowed():
    assert scan_json_structure('{"a":1} \n\t\r').node_count == 3


def test_duplicate_and_escaped_keys_are_counted_once_each():
    assert scan_json_structure('{"a":1,"a":2}').node_count == 5
    assert scan_json_structure('{"a":{},"a":[]}').node_count == 5
    assert scan_json_structure('{"\\u0061":1}').node_count == 3
    assert scan_json_structure('{"a\\"b":1}').node_count == 3


@pytest.mark.parametrize(
    "text",
    [
        "01",
        "00",
        "-01",
        "1.",
        "1e",
        "1e+",
        "1e-",
        ".1",
        "+1",
        "-",
        "--1",
        "1..2",
        "1e+-2",
        "1 2",
        "0x10",
        "1_0",
    ],
)
def test_malformed_numbers_are_rejected(text):
    with pytest.raises(JsonScanError):
        scan_json_structure(text)


@pytest.mark.parametrize(
    ("text", "number_bytes"),
    [
        ("0", 1),
        ("-0", 2),
        ("1.0", 3),
        ("1e10", 4),
        ("1E-10", 5),
        ("-1.2e+3", 7),
    ],
)
def test_valid_number_forms_and_token_bytes(text, number_bytes):
    result = scan_json_structure(text)
    assert result.top_level_kind == "scalar"
    assert result.node_count == 1
    assert result.max_number_token_bytes == number_bytes


def test_max_number_token_includes_sign_dot_and_exponent():
    result = scan_json_structure("[-1.25e+10, 3]")
    assert result.node_count == 3
    assert result.top_level_kind == "array"
    assert result.max_number_token_bytes == 9


@pytest.mark.parametrize(
    "bad",
    ["True", "FALSE", "Null", "nul", "tru", "falsey", "truefalse"],
)
def test_literals_are_lowercase_only_and_complete(bad):
    with pytest.raises(JsonScanError):
        scan_json_structure(bad)


@pytest.mark.parametrize("text", ["true", "false", "null"])
def test_valid_literals_are_scalars(text):
    result = scan_json_structure(text)
    assert result.node_count == 1
    assert result.top_level_kind == "scalar"


@pytest.mark.parametrize("text", ['"x"', "-1.2e3"])
def test_valid_non_literal_scalars(text):
    result = scan_json_structure(text)
    assert result.node_count == 1
    assert result.top_level_kind == "scalar"


def test_array_and_object_punctuation_counts():
    result = scan_json_structure('{"a":[1,{"b":null}]}')
    assert result.node_count == 7
    assert result.top_level_kind == "object"


@pytest.mark.parametrize(
    "bad",
    [
        '{"a":1 "b":2}',
        "[1 2]",
        '{"a"::1}',
        '{"a":,}',
        '{,"a":1}',
        "[,1]",
        "[1,,2]",
        '{"a":1]',
        "[1}",
        '{"a":1}}',
        "[]extra",
        "{}{}",
        '"unterminated',
        '{"a":1',
        "[1,2",
        '"\\',
    ],
)
def test_malformed_delimiters_and_strings_are_rejected(bad):
    with pytest.raises(JsonScanError):
        scan_json_structure(bad)


def test_actual_surrogate_code_points_are_rejected():
    surrogate = chr(0xD800)
    for text in (
        surrogate,
        '"' + surrogate + '"',
        '{"a":"' + surrogate + '"}',
    ):
        with pytest.raises(JsonScanError):
            scan_json_structure(text)


def test_escaped_surrogates_are_syntax_only():
    assert scan_json_structure('"\\ud800"').node_count == 1
    assert scan_json_structure('{"\\ud800":1}').node_count == 3
    assert scan_json_structure('"\\ud83d\\ude00"').node_count == 1


@pytest.mark.parametrize(
    "character", ["\x00", "\x01", "\x1f", "\n", "\t", "\r"]
)
def test_raw_control_characters_in_strings_are_rejected(character):
    with pytest.raises(JsonScanError):
        scan_json_structure('"' + character + '"')


@pytest.mark.parametrize(
    "text",
    [
        r'"\n"',
        r'"\t"',
        r'"\r"',
        r'"\b"',
        r'"\f"',
        r'"\/"',
        r'"\\"',
    ],
)
def test_escaped_controls_and_solidus_are_accepted(text):
    assert scan_json_structure(text).node_count == 1


def test_scanner_reports_without_enforcing_budgets():
    huge_number = "1" * 2000
    result = scan_json_structure(huge_number)
    assert result.node_count == 1
    assert result.max_number_token_bytes == 2000

    many = "[" + ",".join(["0"] * 2000) + "]"
    assert scan_json_structure(many).node_count == 2001


def test_error_message_is_fixed_and_sanitized():
    sentinel = '{"secret":"' + chr(1) + '"}'
    with pytest.raises(JsonScanError) as caught:
        scan_json_structure(sentinel)
    assert str(caught.value) == "invalid JSON structure"


def test_json_scan_result_is_frozen_slotted_and_typed():
    assert [field.name for field in fields(JsonScanResult)] == [
        "node_count",
        "max_number_token_bytes",
        "top_level_kind",
    ]
    assert JsonScanResult.__slots__ == (
        "node_count",
        "max_number_token_bytes",
        "top_level_kind",
    )
    result = scan_json_structure("{}")
    assert type(result.node_count) is int
    assert type(result.max_number_token_bytes) is int
    assert type(result.top_level_kind) is str
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.node_count = 2


def test_module_has_no_materialization_regex_or_execution():
    source = Path(json_scanner.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    banned_modules = {
        "json",
        "re",
        "importlib",
        "os",
        "pathlib",
        "socket",
        "subprocess",
    }
    banned_calls = {"compile", "eval", "exec", "open"}
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
                assert node.func.id not in banned_calls


def _config(**kw):
    values = dict(max_tags=4, max_tag_key_bytes=8, max_tag_value_bytes=8,
                  max_message_bytes=16, max_error_message_bytes=16,
                  max_stack_bytes=128, max_frames_per_event=4,
                  max_frame_function_bytes=12, retain_event_text=True)
    values.update(kw)
    return RuntimeEvidenceConfig(**values)


def _norm(value, **kw):
    values = dict(sequence=0, source_line=None, config=_config(),
                  declared_language=None)
    values.update(kw)
    return normalize_event_object(value, **values)


def _codes(result):
    return [issue.code for issue in result.issues]


def test_log_event_is_normalized_not_empty():
    result = _norm({"kind": "log", "message": "hello"})
    assert result.draft is not None
    assert result.draft.kind is RuntimeEventKind.LOG
    assert result.issues == ()


def test_log_event_and_duration_are_exact_strings():
    result = _norm({"kind": "log", "message": "hello", "duration_ms": "001"},
                   source_line=7)
    assert result.draft is None
    result = _norm({"kind": "log", "message": "hello", "duration_ms": "1.20"},
                   source_line=7)
    assert result.draft.message == "hello"
    assert result.draft.duration_ms_text == "1.20"
    assert result.draft.source_line == 7


def test_required_fields_and_error_only_on_error():
    for value in ({"kind": "log"},
                  {"kind": "span"},
                  {"kind": "error"},
                  {"kind": "log", "message": "x", "error": {}},
                  {"kind": "span", "operation": "op", "error": {}}):
        assert _norm(value).draft is None
    assert _norm({"kind": "error"}).draft is None
    assert _norm({"kind": "error", "error": {}}).draft is not None


def test_numeric_strings_do_not_satisfy_int_fields_and_bool_is_not_int():
    for line in ("1", True, 1.0):
        assert _norm({"kind": "error", "message": "x",
                      "error": {"frames": [{"line": line}]}}).draft is None


def test_numeric_metadata_never_becomes_duration_text():
    result = _norm({"kind": "log", "message": "x", "duration_ms": 1})
    assert result.draft is None


def test_duration_bound_is_exact_utf8_bytes():
    assert _norm({"kind": "log", "message": "x",
                  "duration_ms": "1" * 64}).draft.duration_ms_text == "1" * 64
    assert _norm({"kind": "log", "message": "x",
                  "duration_ms": "1" * 65}).draft is None


def test_tags_are_sorted_and_metadata_only_omits_them():
    result = _norm({"kind": "log", "message": "x",
                    "tags": {"b": "2", "a": "1"}})
    assert [t.key for t in result.draft.tags] == ["a", "b"]
    projected = _norm({"kind": "log", "message": "x",
                       "tags": {"a": "1"}},
                      config=_config(retain_event_text=False)).draft
    assert projected.message is None and projected.tags == ()
    assert projected.text_truncated is False
    assert all(i.code is not RuntimeIssueCode.TEXT_TRUNCATED
               for i in _norm({"kind": "log", "message": "x" * 100},
                              config=_config(retain_event_text=False)).issues)


def test_explicit_frames_precede_stack_and_retain_invalid_locations():
    value = {"kind": "error", "error": {
        "frames": [{"function": "f", "source": "a.py", "line": 0,
                    "column": -2}],
        "stack": "this would be unparseable"}}
    result = _norm(value)
    assert len(result.draft.frames) == 1
    assert result.draft.frames[0].line is None
    assert result.draft.frames[0].column is None
    assert [i.code for i in result.issues] == [
        RuntimeIssueCode.INVALID_SOURCE_LOCATION]


def test_stack_language_precedes_size_and_parse():
    big = "x" * 200
    result = _norm({"kind": "error", "error": {"stack": big}},
                   declared_language=None)
    assert result.draft is not None and result.draft.frames == ()
    assert [i.code for i in result.issues] == [
        RuntimeIssueCode.STACK_LANGUAGE_MISSING]


def test_stack_size_truncation_sets_both_flags_and_issues():
    result = _norm({"kind": "error",
                    "error": {"stack": '  File "a.py", line 1, in f\n' + "x" * 200}},
                   declared_language=RuntimeFrameLanguage.PYTHON)
    assert result.draft.frames == ()
    assert result.draft.text_truncated is True
    assert result.draft.frames_truncated is True
    assert [i.code for i in result.issues] == [
        RuntimeIssueCode.STACK_TOO_LARGE, RuntimeIssueCode.TEXT_TRUNCATED]


def test_unknown_keys_are_not_inspected_for_nul_or_surrogates():
    value = {"kind": "log", "message": "x", "unknown": "\x00\ud800"}
    assert _norm(value).draft is not None


def test_normalized_event_interface_is_frozen_and_slotted():
    assert [field.name for field in fields(NormalizedEvent)] == [
        "draft", "issues"]
    assert NormalizedEvent.__slots__ == ("draft", "issues")
    result = _norm({"kind": "log", "message": "x"})
    assert isinstance(result.issues, tuple)
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.draft = None


def test_invalid_event_issue_has_no_sequence_and_keeps_source_line():
    result = _norm({"kind": "bogus"}, sequence=5, source_line=9)
    assert result.draft is None
    (issue,) = result.issues
    assert issue.code is RuntimeIssueCode.INVALID_EVENT_SCHEMA
    assert issue.event_sequence is None
    assert issue.source_line == 9


def test_valid_event_issues_use_event_sequence_and_source_line():
    result = _norm({"kind": "log", "message": "x" * 20},
                   sequence=3, source_line=11)
    (issue,) = result.issues
    assert issue.code is RuntimeIssueCode.TEXT_TRUNCATED
    assert issue.event_sequence == 3
    assert issue.source_line == 11


@pytest.mark.parametrize(
    "value",
    [None, [], "log", 1, True, {"kind": None}, {"kind": 1}, {"kind": "LOG"}],
)
def test_event_must_be_object_with_known_kind(value):
    assert _norm(value).draft is None


def test_all_optional_fields_map_exactly():
    result = _norm({
        "kind": "span", "operation": "op", "timestamp": "ts", "level": "INFO",
        "logger": "lg", "trace_id": "t", "span_id": "s",
        "parent_span_id": "p", "duration_ms": "0.5", "status": "ok",
        "message": "m",
    })
    draft = result.draft
    assert draft.timestamp_text == "ts"
    assert draft.level == "INFO"
    assert draft.logger == "lg"
    assert draft.operation == "op"
    assert draft.trace_id == "t"
    assert draft.span_id == "s"
    assert draft.parent_span_id == "p"
    assert draft.duration_ms_text == "0.5"
    assert draft.status == "ok"
    assert draft.message == "m"
    assert draft.error_type is None
    assert draft.error_message is None
    assert draft.tags == ()
    assert draft.frames == ()
    assert draft.text_truncated is False
    assert draft.frames_truncated is False
    assert result.issues == ()


@pytest.mark.parametrize(
    ("field", "max_bytes"),
    [
        ("timestamp", 128), ("level", 32), ("logger", 256),
        ("operation", 256), ("trace_id", 256), ("span_id", 256),
        ("parent_span_id", 256), ("status", 32),
    ],
)
def test_non_truncatable_event_text_bounds(field, max_bytes):
    base = {"kind": "log", "message": "x"}
    exact = dict(base)
    exact[field] = "a" * max_bytes
    assert _norm(exact).draft is not None
    over = dict(base)
    over[field] = "a" * (max_bytes + 1)
    assert _norm(over).draft is None


def test_non_truncatable_bound_is_utf8_bytes():
    assert _norm({"kind": "log", "message": "x",
                  "level": "\u00e9" * 16}).draft is not None
    assert _norm({"kind": "log", "message": "x",
                  "level": "\u00e9" * 17}).draft is None


@pytest.mark.parametrize(
    "text", ["0", "10", "1.20", "0.0", "1234567890.000"],
)
def test_duration_decimal_forms_are_accepted(text):
    result = _norm({"kind": "log", "message": "x", "duration_ms": text})
    assert result.draft.duration_ms_text == text


@pytest.mark.parametrize(
    "text",
    ["", "00", "01", "-1", "1.", ".5", "1e3", " 1", "1 ", "1..2", "abc"],
)
def test_duration_decimal_forms_are_rejected(text):
    assert _norm({"kind": "log", "message": "x",
                  "duration_ms": text}).draft is None


def test_message_truncation_sets_flag_and_one_issue():
    result = _norm({"kind": "log", "message": "\u00e9" * 20},
                   config=_config(max_message_bytes=17))
    assert result.draft.message == "\u00e9" * 7 + "\u2026"
    assert result.draft.text_truncated is True
    assert [i.code for i in result.issues] == [
        RuntimeIssueCode.TEXT_TRUNCATED]

    exact = _norm({"kind": "log", "message": "a" * 16})
    assert exact.draft.message == "a" * 16
    assert exact.draft.text_truncated is False
    assert exact.issues == ()


def test_error_message_truncation_sets_flag_and_one_issue():
    result = _norm({"kind": "error", "message": "m",
                    "error": {"message": "x" * 20}})
    assert result.draft.error_message == "x" * 13 + "\u2026"
    assert result.draft.text_truncated is True
    assert [i.code for i in result.issues] == [
        RuntimeIssueCode.TEXT_TRUNCATED]


def test_multiple_text_truncations_report_one_issue():
    result = _norm({"kind": "error", "message": "x" * 20,
                    "error": {"message": "y" * 20}})
    assert result.draft.text_truncated is True
    assert [i.code for i in result.issues] == [
        RuntimeIssueCode.TEXT_TRUNCATED]


def test_tag_count_and_value_types():
    four = {f"k{i}": "v" for i in range(4)}
    assert len(_norm({"kind": "log", "message": "x",
                      "tags": four}).draft.tags) == 4
    five = {f"k{i}": "v" for i in range(5)}
    assert _norm({"kind": "log", "message": "x",
                  "tags": five}).draft is None
    for bad in ({"a": 1}, {"a": True}, {"a": None}, {"a": {"b": "c"}},
                {"a": ["b"]}):
        assert _norm({"kind": "log", "message": "x",
                      "tags": bad}).draft is None
    assert _norm({"kind": "log", "message": "x", "tags": []}).draft is None


def test_tag_key_and_value_bounds_are_exact_utf8_bytes():
    result = _norm({"kind": "log", "message": "x",
                    "tags": {"a" * 8: "b" * 8}})
    assert result.draft.tags[0].key == "a" * 8
    assert result.draft.tags[0].value == "b" * 8
    assert _norm({"kind": "log", "message": "x",
                  "tags": {"a" * 9: "b"}}).draft is None
    assert _norm({"kind": "log", "message": "x",
                  "tags": {"a": "b" * 9}}).draft is None
    assert _norm({"kind": "log", "message": "x",
                  "tags": {"\u00e9" * 5: "b"}}).draft is None


def test_tag_keys_and_values_reject_nul_and_unpaired_surrogates():
    for tags in ({"a": "\x00"}, {"\x00": "b"}, {"a": "\ud800"},
                 {"\udc00": "b"}):
        assert _norm({"kind": "log", "message": "x",
                      "tags": tags}).draft is None
    assert _norm({"kind": "log", "message": "x",
                  "tags": {"a": "\ud83d\ude00"}}).draft is not None


def test_tags_are_sorted_by_key():
    result = _norm({"kind": "log", "message": "x",
                    "tags": {"z": "1", "a": "2", "m": "3"}})
    assert [tag.key for tag in result.draft.tags] == ["a", "m", "z"]


@pytest.mark.parametrize(
    "field",
    [
        "timestamp", "level", "logger", "operation", "trace_id", "span_id",
        "parent_span_id", "duration_ms", "status", "message",
    ],
)
def test_nul_in_retained_event_string_invalidates(field):
    value = {"kind": "log", "message": "x"}
    if field == "duration_ms":
        value[field] = "0\x00"
    else:
        value[field] = "a\x00"
    assert _norm(value).draft is None


@pytest.mark.parametrize(
    "field",
    [
        "timestamp", "level", "logger", "operation", "trace_id", "span_id",
        "parent_span_id", "duration_ms", "status", "message",
    ],
)
def test_unpaired_surrogate_in_retained_event_string_invalidates(field):
    value = {"kind": "log", "message": "x"}
    if field == "duration_ms":
        value[field] = "0\ud800"
    else:
        value[field] = "a\ud800"
    assert _norm(value).draft is None


def test_error_fields_reject_nul_and_surrogates():
    for error in ({"type": "a\x00"}, {"type": "a\ud800"},
                  {"message": "a\x00"}, {"message": "a\ud800"}):
        assert _norm({"kind": "error", "message": "x",
                      "error": error}).draft is None


def test_valid_surrogate_pairs_are_accepted_and_combined():
    pair = "\ud83d\ude00"
    result = _norm({"kind": "log", "message": pair})
    assert result.draft is not None
    assert result.draft.message == "\U0001f600"


def test_explicit_frame_source_is_retained_raw():
    for source in ("a\x00.py", "a\ud800.py", "x" * 2000):
        result = _norm({"kind": "error", "message": "x",
                        "error": {"frames": [{"source": source}]}})
        assert result.draft is not None
        assert result.draft.frames[0].source_text == source
        assert result.issues == ()


def test_explicit_frame_elements_and_fields_are_typed():
    for frames in (["x"], [1], [None], [{"function": 1}],
                   [{"source": 1}], [{"line": "1"}], [{"line": True}],
                   [{"line": 1.0}], [{"column": False}],
                   [{"column": "0"}]):
        assert _norm({"kind": "error", "message": "x",
                      "error": {"frames": frames}}).draft is None
    assert _norm({"kind": "error", "message": "x",
                  "error": {"frames": {}}}).draft is None


def test_frame_positions_are_exact_and_invalid_values_become_none_once():
    result = _norm({"kind": "error", "message": "x", "error": {"frames": [
        {"line": 5, "column": 0},
        {"line": 0, "column": -1},
    ]}})
    assert [(frame.line, frame.column) for frame in result.draft.frames] == [
        (5, 0), (None, None)]
    assert [i.code for i in result.issues] == [
        RuntimeIssueCode.INVALID_SOURCE_LOCATION]


def test_frame_cap_omits_extras_and_reports_once():
    frames = [{"function": f"f{i}"} for i in range(6)]
    result = _norm({"kind": "error", "message": "x",
                    "error": {"frames": frames}},
                   config=_config(max_frames_per_event=4))
    assert len(result.draft.frames) == 4
    assert [frame.function_text for frame in result.draft.frames] == [
        "f0", "f1", "f2", "f3"]
    assert result.draft.frames_truncated is True
    assert result.draft.text_truncated is False
    assert [i.code for i in result.issues] == [
        RuntimeIssueCode.FRAME_LIMIT_REACHED]


def test_frame_function_truncation_sets_text_flag():
    result = _norm({"kind": "error", "message": "x", "error": {"frames": [
        {"function": "abcdefghijklmnop"}]}})
    assert result.draft.frames[0].function_text == "abcdefghi\u2026"
    assert result.draft.text_truncated is True
    assert [i.code for i in result.issues] == [
        RuntimeIssueCode.TEXT_TRUNCATED]


def test_frame_function_nul_and_surrogate_invalidate_event():
    for function in ("a\x00", "a\ud800"):
        assert _norm({"kind": "error", "message": "x", "error": {"frames": [
            {"function": function}]}}).draft is None


def test_explicit_frames_use_declared_language_or_unknown():
    value = {"kind": "error", "message": "x",
             "error": {"frames": [{"function": "f"}]}}
    assert _norm(
        value, declared_language=RuntimeFrameLanguage.PYTHON
    ).draft.frames[0].language is RuntimeFrameLanguage.PYTHON
    assert _norm(value).draft.frames[0].language is RuntimeFrameLanguage.UNKNOWN
    assert _norm(
        value, declared_language=RuntimeFrameLanguage.UNKNOWN
    ).draft.frames[0].language is RuntimeFrameLanguage.UNKNOWN


def test_empty_explicit_frames_suppress_stack():
    result = _norm({"kind": "error",
                    "error": {"frames": [], "stack": "not a stack"}},
                   declared_language=RuntimeFrameLanguage.PYTHON)
    assert result.draft.frames == ()
    assert result.issues == ()
    assert result.draft.frames_truncated is False


def test_stack_is_ignored_when_frames_key_is_present():
    result = _norm({"kind": "error", "message": "x", "error": {
        "frames": [{"function": "f"}],
        "stack": "\x00\ud800" + "x" * 200}},
        declared_language=RuntimeFrameLanguage.PYTHON)
    assert result.draft is not None
    assert len(result.draft.frames) == 1
    assert result.issues == ()


def test_valid_stack_parses_frames_in_reported_order():
    stack = '  File "a.py", line 3, in f\n  File "b.py", line 4, in g\n'
    result = _norm({"kind": "error", "message": "x",
                    "error": {"stack": stack}},
                   declared_language=RuntimeFrameLanguage.PYTHON)
    assert [(frame.function_text, frame.source_text, frame.line,
             frame.column, frame.language)
            for frame in result.draft.frames] == [
        ("f", "a.py", 3, None, RuntimeFrameLanguage.PYTHON),
        ("g", "b.py", 4, None, RuntimeFrameLanguage.PYTHON)]
    assert result.issues == ()
    assert result.draft.text_truncated is False
    assert result.draft.frames_truncated is False


@pytest.mark.parametrize(
    "stack",
    [
        "not a stack",
        "\x00",
        '  File "a.py", line 1, in f\n\x00',
        '  File "a.py", line 1, in f\n\ud800',
    ],
)
def test_stack_without_recognized_frame_or_with_nul_or_surrogate_is_unparseable(
    stack,
):
    result = _norm({"kind": "error", "message": "x",
                    "error": {"stack": stack}},
                   declared_language=RuntimeFrameLanguage.PYTHON)
    assert result.draft is not None
    assert result.draft.frames == ()
    assert result.draft.frames_truncated is False
    assert result.draft.text_truncated is False
    assert [i.code for i in result.issues] == [
        RuntimeIssueCode.STACK_UNPARSEABLE]


def test_over_cap_unpaired_surrogate_stack_is_unparseable_not_truncated():
    stack = "\ud800" * 200
    result = _norm({"kind": "error", "message": "x",
                    "error": {"stack": stack}},
                   declared_language=RuntimeFrameLanguage.PYTHON)
    assert result.draft is not None
    assert result.draft.frames == ()
    assert result.draft.frames_truncated is False
    assert result.draft.text_truncated is False
    assert [i.code for i in result.issues] == [
        RuntimeIssueCode.STACK_UNPARSEABLE]


def test_json_adapter_does_not_use_surrogatepass():
    source = Path(json_adapter.__file__).read_text(encoding="utf-8")
    assert "surrogatepass" not in source


def test_stack_type_is_validated_even_when_frames_are_present():
    result = _norm({"kind": "error",
                    "error": {"frames": [], "stack": 42}},
                   declared_language=RuntimeFrameLanguage.PYTHON)
    assert result.draft is None
    assert [i.code for i in result.issues] == [
        RuntimeIssueCode.INVALID_EVENT_SCHEMA]


def test_stack_function_truncation_sets_text_flag():
    stack = '  File "a.py", line 1, in abcdefghijklmnop\n'
    result = _norm({"kind": "error", "message": "x",
                    "error": {"stack": stack}},
                   declared_language=RuntimeFrameLanguage.PYTHON)
    assert result.draft.frames[0].function_text == "abcdefghi\u2026"
    assert result.draft.text_truncated is True
    assert [i.code for i in result.issues] == [
        RuntimeIssueCode.TEXT_TRUNCATED]


def test_stack_frame_limit_sets_frames_truncated_and_issue():
    stack = "".join(
        f'  File "a.py", line {index + 1}, in f\n' for index in range(3))
    result = _norm({"kind": "error", "message": "x",
                    "error": {"stack": stack}},
                   declared_language=RuntimeFrameLanguage.PYTHON,
                   config=_config(max_frames_per_event=2))
    assert len(result.draft.frames) == 2
    assert result.draft.frames_truncated is True
    assert result.draft.text_truncated is False
    assert [i.code for i in result.issues] == [
        RuntimeIssueCode.FRAME_LIMIT_REACHED]


def test_stack_size_precedes_nul_and_parse():
    stack = '  File "a.py", line 1, in f\n\x00' + "x" * 200
    result = _norm({"kind": "error", "message": "x",
                    "error": {"stack": stack}},
                   declared_language=RuntimeFrameLanguage.PYTHON)
    assert result.draft.frames == ()
    assert result.draft.text_truncated is True
    assert result.draft.frames_truncated is True
    assert [i.code for i in result.issues] == [
        RuntimeIssueCode.STACK_TOO_LARGE, RuntimeIssueCode.TEXT_TRUNCATED]


def test_stack_size_is_exact_utf8_bytes():
    exact = _norm({"kind": "error", "message": "x",
                   "error": {"stack": "\u00e9" * 64}},
                  declared_language=RuntimeFrameLanguage.PYTHON)
    assert [i.code for i in exact.issues] == [
        RuntimeIssueCode.STACK_UNPARSEABLE]
    over = _norm({"kind": "error", "message": "x",
                  "error": {"stack": "\u00e9" * 65}},
                 declared_language=RuntimeFrameLanguage.PYTHON)
    assert [i.code for i in over.issues] == [
        RuntimeIssueCode.STACK_TOO_LARGE, RuntimeIssueCode.TEXT_TRUNCATED]


def test_valid_surrogate_pair_in_stack_is_parseable():
    stack = '  File "a.py", line 1, in \ud83d\ude00\n'
    result = _norm({"kind": "error", "message": "x",
                    "error": {"stack": stack}},
                   declared_language=RuntimeFrameLanguage.PYTHON)
    assert result.draft.frames[0].function_text == "\U0001f600"
    assert result.issues == ()


@pytest.mark.parametrize("error", [None, [], "x", 1, True])
def test_non_object_error_invalidates(error):
    assert _norm({"kind": "error", "message": "x",
                  "error": error}).draft is None


def test_error_type_and_message_map_with_bounds():
    result = _norm({"kind": "error", "error": {"type": "T",
                                               "message": "m"}})
    assert result.draft.error_type == "T"
    assert result.draft.error_message == "m"
    assert result.draft.message is None
    for text in ("a" * 257, "a\x00"):
        assert _norm({"kind": "error", "message": "x",
                      "error": {"type": text}}).draft is None


def test_metadata_only_projection_retains_structure_and_omits_text():
    result = _norm({
        "kind": "error",
        "timestamp": "ts", "level": "ERROR", "logger": "lg",
        "operation": "op", "trace_id": "t", "span_id": "s",
        "parent_span_id": "p", "duration_ms": "1.5", "status": "failed",
        "message": "secret message",
        "tags": {"a": "b"},
        "error": {
            "type": "Boom", "message": "secret error",
            "frames": [{"function": "f", "source": "src/a.py",
                        "line": 3, "column": 4}],
        },
    }, config=_config(retain_event_text=False))
    draft = result.draft
    assert draft is not None
    assert draft.kind is RuntimeEventKind.ERROR
    assert draft.timestamp_text == "ts"
    assert draft.level == "ERROR"
    assert draft.trace_id == "t"
    assert draft.span_id == "s"
    assert draft.parent_span_id == "p"
    assert draft.duration_ms_text == "1.5"
    assert draft.status == "failed"
    assert draft.message is None
    assert draft.logger is None
    assert draft.operation is None
    assert draft.error_type is None
    assert draft.error_message is None
    assert draft.tags == ()
    assert [(frame.function_text, frame.source_text, frame.line,
             frame.column) for frame in draft.frames] == [
        (None, "src/a.py", 3, 4)]
    assert draft.text_truncated is False
    assert draft.frames_truncated is False
    assert result.issues == ()


def test_metadata_only_omits_over_limit_truncatable_text_without_issues():
    result = _norm({
        "kind": "error",
        "message": "m" * 100,
        "tags": {"a": "b"},
        "error": {"message": "e" * 100,
                  "frames": [{"function": "f" * 100}]},
    }, config=_config(retain_event_text=False))
    draft = result.draft
    assert draft.message is None
    assert draft.error_message is None
    assert draft.tags == ()
    assert draft.frames[0].function_text is None
    assert draft.text_truncated is False
    assert result.issues == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("logger", "a" * 257), ("operation", "a" * 257),
        ("level", "a" * 33), ("timestamp", "a" * 129),
        ("status", "a" * 33),
    ],
)
def test_metadata_only_still_validates_non_truncatable_bounds(field, value):
    result = _norm({"kind": "log", "message": "x", field: value},
                   config=_config(retain_event_text=False))
    assert result.draft is None


def test_metadata_only_still_validates_tags_and_nul():
    for value in ({"kind": "log", "message": "x", "tags": {"a": 1}},
                  {"kind": "log", "message": "x", "tags": {"a": "b" * 9}},
                  {"kind": "log", "message": "a\x00"},
                  {"kind": "log", "message": "a\ud800"}):
        assert _norm(value, config=_config(
            retain_event_text=False)).draft is None


def test_metadata_only_stack_parsing_keeps_structural_frames():
    stack = '  File "a.py", line 3, in abcdefghijklmnop\n'
    result = _norm({"kind": "error", "message": "x",
                    "error": {"stack": stack}},
                   declared_language=RuntimeFrameLanguage.PYTHON,
                   config=_config(retain_event_text=False))
    assert len(result.draft.frames) == 1
    assert result.draft.frames[0].function_text is None
    assert result.draft.frames[0].source_text == "a.py"
    assert result.draft.frames[0].line == 3
    assert result.draft.text_truncated is False
    assert result.issues == ()


def test_metadata_only_stack_size_truncation_still_reports():
    result = _norm({"kind": "error", "message": "x",
                    "error": {"stack": "x" * 200}},
                   declared_language=RuntimeFrameLanguage.PYTHON,
                   config=_config(retain_event_text=False))
    assert result.draft.text_truncated is True
    assert result.draft.frames_truncated is True
    assert [i.code for i in result.issues] == [
        RuntimeIssueCode.STACK_TOO_LARGE, RuntimeIssueCode.TEXT_TRUNCATED]


def test_metadata_only_stack_frame_limit_still_reports():
    stack = "".join(
        f'  File "a.py", line {index + 1}, in f\n' for index in range(3))
    result = _norm({"kind": "error", "message": "x",
                    "error": {"stack": stack}},
                   declared_language=RuntimeFrameLanguage.PYTHON,
                   config=_config(retain_event_text=False,
                                  max_frames_per_event=2))
    assert result.draft.frames_truncated is True
    assert result.draft.text_truncated is False
    assert [i.code for i in result.issues] == [
        RuntimeIssueCode.FRAME_LIMIT_REACHED]


def test_structural_budget_messages_are_fixed():
    secret = "s3cr3t-" + "x" * 300
    result = _norm({"kind": "log", "message": "x", "logger": secret})
    (issue,) = result.issues
    assert issue.code is RuntimeIssueCode.INVALID_EVENT_SCHEMA
    assert issue_message(issue.code) == (
        "event does not match the supported schema")
    assert secret not in issue_message(issue.code)

    oversized = _norm({"kind": "error", "message": "x",
                       "error": {"stack": "x" * 200}},
                      declared_language=RuntimeFrameLanguage.PYTHON)
    assert issue_message(RuntimeIssueCode.STACK_TOO_LARGE) == (
        "stack exceeds the configured byte limit")
    assert "200" not in issue_message(
        RuntimeIssueCode.STACK_TOO_LARGE)
