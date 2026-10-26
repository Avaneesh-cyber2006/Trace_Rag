import ast
import json
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from backend.runtime_evidence import json_scanner
from backend.runtime_evidence.json_scanner import (
    JsonScanError,
    JsonScanResult,
    scan_json_structure,
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
