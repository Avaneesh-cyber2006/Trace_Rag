import itertools
import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from types import MappingProxyType

import pytest

from backend.code_graph import CodeGraphInventory, GraphNode, GraphNodeKind
from backend.runtime_evidence import (
    FrameAssociation, GraphBinding, PathMatchBasis, RuntimeArtifact,
    RuntimeArtifactFormat, RuntimeArtifactStatus, RuntimeEvidenceConfig,
    RuntimeEvidenceIngestor, RuntimeEvidenceRequest, RuntimeFrameLanguage,
    RuntimeIncompleteReason, RuntimeIssueCode,
)
from backend.runtime_evidence.models import issue_message


def _artifact(
    name,
    content,
    fmt=RuntimeArtifactFormat.TEXT_LOG,
    language=None,
):
    return RuntimeArtifact(name, fmt, content, language)


def _request(*artifacts, namespace="repo"):
    return RuntimeEvidenceRequest(namespace, tuple(artifacts))


def _snapshot(value):
    if is_dataclass(value):
        return {
            field.name: _snapshot(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {key: _snapshot(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_snapshot(item) for item in value]
    if (
        value is None
        or type(value) is bool
        or isinstance(value, (int, str))
    ):
        return value
    raise AssertionError(f"unexpected public value type: {type(value)!r}")


def _canonical(inventory):
    return json.dumps(
        _snapshot(inventory),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _leaf_values(value):
    if is_dataclass(value):
        for field in fields(value):
            yield from _leaf_values(getattr(value, field.name))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield key
            yield from _leaf_values(item)
    elif isinstance(value, tuple):
        for item in value:
            yield from _leaf_values(item)
    else:
        yield value


def _file_node(node_id, relative_path, namespace="repo"):
    return GraphNode(
        node_id=node_id,
        node_kind=GraphNodeKind.FILE,
        repository_namespace=namespace,
        relative_path=relative_path,
        language="python",
        parse_status="success",
        symbol_kind=None,
        name=None,
        qualified_name=None,
        location=None,
    )


def _graph(*nodes, namespace="repo"):
    return CodeGraphInventory(
        namespace,
        tuple(nodes),
        (),
        (),
        MappingProxyType({}),
        MappingProxyType({}),
        MappingProxyType({}),
        MappingProxyType({}),
    )


def _status_values(inventory):
    return [artifact.status.value for artifact in inventory.artifacts]


def _reason_values(inventory):
    return [reason.value for reason in inventory.incomplete_reasons]


def _assert_canonical_issue_order(inventory):
    positions = {
        artifact.artifact_id: position
        for position, artifact in enumerate(inventory.artifacts)
    }

    def key(issue):
        return (
            positions[issue.artifact_id],
            0 if issue.event_sequence is not None else 1,
            issue.event_sequence if issue.event_sequence is not None else 0,
            0 if issue.source_line is not None else 1,
            issue.source_line if issue.source_line is not None else 0,
            issue.code.value,
            issue.message,
        )

    keys = [key(issue) for issue in inventory.issues]
    assert keys == sorted(keys)
    assert all(left < right for left, right in zip(keys, keys[1:]))


def test_shuffled_artifact_order_is_observation_identical():
    artifacts = (
        _artifact("a_bad.log", b"\xff\n"),
        _artifact("b_long.log", b"abcdef\n"),
        _artifact(
            "c_events.jsonl",
            b'{"kind":"log","message":"one"}\n'
            b'{"kind":"log","message":"two"}\n',
            RuntimeArtifactFormat.JSON_EVENTS,
        ),
        _artifact("d_later.log", b"x\n"),
    )
    config = RuntimeEvidenceConfig(
        max_events=2, max_message_bytes=4, max_issues=8
    )
    ingestor = RuntimeEvidenceIngestor(config)
    snapshots = {
        _canonical(ingestor.ingest(_request(*permutation)))
        for permutation in itertools.permutations(artifacts)
    }
    assert len(snapshots) == 1

    inventory = ingestor.ingest(_request(*artifacts))
    assert [
        artifact.source_names for artifact in inventory.artifacts
    ] == [
        ("a_bad.log",),
        ("b_long.log",),
        ("c_events.jsonl",),
        ("d_later.log",),
    ]
    assert _status_values(inventory) == [
        "failed", "partial", "partial", "skipped"
    ]
    assert [artifact.event_count for artifact in inventory.artifacts] == [
        0, 1, 1, 0
    ]
    assert inventory.total_events == 2
    assert inventory.duplicate_artifacts == 0
    assert inventory.events_truncated is True
    assert inventory.text_truncated is True
    assert inventory.frames_truncated is False
    assert inventory.issues_truncated is False
    assert _reason_values(inventory) == [
        "artifact_failed",
        "artifact_partial",
        "artifact_skipped",
        "event_limit_reached",
        "text_truncated",
    ]


def test_source_name_casefold_ties_are_deterministic():
    artifacts = (
        _artifact("b.log", b"lower\n"),
        _artifact("B.log", b"upper\n"),
        _artifact("A.log", b"first\n"),
    )
    ingestor = RuntimeEvidenceIngestor()
    inventory = ingestor.ingest(_request(*artifacts))
    assert [
        artifact.source_names for artifact in inventory.artifacts
    ] == [("A.log",), ("B.log",), ("b.log",)]
    assert [event.message for event in inventory.events] == [
        "first", "upper", "lower"
    ]
    snapshots = {
        _canonical(ingestor.ingest(_request(*permutation)))
        for permutation in itertools.permutations(artifacts)
    }
    assert len(snapshots) == 1


def test_unicode_source_name_casefold_tie_is_deterministic():
    artifacts = (
        _artifact("ss.log", b"ss\n"),
        _artifact("\u00df.log", b"eszett\n"),
    )
    ingestor = RuntimeEvidenceIngestor()
    inventory = ingestor.ingest(_request(*artifacts))
    assert [
        artifact.source_names for artifact in inventory.artifacts
    ] == [("ss.log",), ("\u00df.log",)]
    assert [event.message for event in inventory.events] == [
        "ss", "eszett"
    ]
    snapshots = {
        _canonical(ingestor.ingest(_request(*permutation)))
        for permutation in itertools.permutations(artifacts)
    }
    assert len(snapshots) == 1


def test_duplicate_artifacts_collapse_with_sorted_source_names():
    artifacts = (
        _artifact("zeta.log", b"same\n"),
        _artifact("Alpha.log", b"same\n"),
        _artifact("alpha.log", b"same\n"),
        _artifact("BETA.log", b"same\n"),
    )
    ingestor = RuntimeEvidenceIngestor()
    inventories = [
        ingestor.ingest(_request(*permutation))
        for permutation in itertools.permutations(artifacts)
    ]
    assert len({_canonical(inventory) for inventory in inventories}) == 1
    assert len({
        inventory.artifacts[0].artifact_id for inventory in inventories
    }) == 1

    inventory = inventories[0]
    assert len(inventory.artifacts) == 1
    record = inventory.artifacts[0]
    assert record.source_names == (
        "Alpha.log", "alpha.log", "BETA.log", "zeta.log"
    )
    assert record.duplicate_count == 3
    assert inventory.submitted_artifacts == 4
    assert inventory.duplicate_artifacts == 3
    assert inventory.submitted_artifacts == (
        len(inventory.artifacts) + inventory.duplicate_artifacts
    )
    assert inventory.total_input_bytes == len(b"same\n")
    assert record.status is RuntimeArtifactStatus.COMPLETE
    assert record.event_count == 1


def test_artifact_ids_are_independent_of_capture_label():
    content = b"one\n"
    first = RuntimeEvidenceIngestor().ingest(
        _request(_artifact("a.log", content))
    )
    second = RuntimeEvidenceIngestor().ingest(
        _request(_artifact("renamed.log", content))
    )
    first_artifact = first.artifacts[0]
    second_artifact = second.artifacts[0]
    assert first_artifact.artifact_id == second_artifact.artifact_id
    assert first.events[0].event_id == second.events[0].event_id
    assert first_artifact.source_names == ("a.log",)
    assert second_artifact.source_names == ("renamed.log",)
    assert first.submitted_artifacts == second.submitted_artifacts
    assert first.duplicate_artifacts == second.duplicate_artifacts
    assert first.total_input_bytes == second.total_input_bytes
    assert first.total_events == second.total_events
    assert first.total_frames == second.total_frames
    assert first_artifact.status is second_artifact.status
    assert first_artifact.event_count == second_artifact.event_count
    assert first_artifact.frame_count == second_artifact.frame_count
    assert first_artifact.issue_count == second_artifact.issue_count


def test_events_follow_canonical_artifact_order_then_local_sequence():
    inventory = RuntimeEvidenceIngestor().ingest(_request(
        _artifact("b.log", b"b0\nb1\n"),
        _artifact("a.log", b"a0\na1\n"),
    ))
    assert [
        artifact.source_names for artifact in inventory.artifacts
    ] == [("a.log",), ("b.log",)]
    assert [event.message for event in inventory.events] == [
        "a0", "a1", "b0", "b1"
    ]
    assert [event.sequence for event in inventory.events] == [0, 1, 0, 1]
    for artifact in inventory.artifacts:
        assert inventory.events_by_artifact[artifact.artifact_id] == tuple(
            event for event in inventory.events
            if event.artifact_id == artifact.artifact_id
        )


def test_frames_follow_event_order_then_reported_ordinal():
    inventory = RuntimeEvidenceIngestor().ingest(_request(
        _artifact(
            "trace.txt",
            b'  File "pkg/z.py", line 1, in f\n'
            b'  File "pkg/a.py", line 2, in g\n'
            b"\n"
            b'  File "pkg/m.py", line 3, in h\n',
            RuntimeArtifactFormat.STACK_TRACE_TEXT,
            RuntimeFrameLanguage.PYTHON,
        )
    ))
    assert [
        (event.sequence, frame.ordinal, frame.relative_path)
        for event in inventory.events
        for frame in event.frames
    ] == [
        (0, 0, "pkg/z.py"),
        (0, 1, "pkg/a.py"),
        (1, 0, "pkg/m.py"),
    ]
    assert inventory.frames == tuple(
        frame for event in inventory.events for frame in event.frames
    )


def test_json_document_array_order_and_timestamps_are_preserved():
    document = (
        b'[{"kind":"log","message":"late",'
        b'"timestamp":"2026-01-01T00:00:00Z"},'
        b'{"kind":"log","message":"early",'
        b'"timestamp":"1999-01-01T00:00:00Z"}]'
    )
    inventory = RuntimeEvidenceIngestor().ingest(_request(
        _artifact(
            "events.json", document, RuntimeArtifactFormat.JSON_EVENTS
        )
    ))
    assert [event.message for event in inventory.events] == [
        "late", "early"
    ]
    assert [event.sequence for event in inventory.events] == [0, 1]
    assert [event.source_line for event in inventory.events] == [None, None]
    assert [event.timestamp_text for event in inventory.events] == [
        "2026-01-01T00:00:00Z",
        "1999-01-01T00:00:00Z",
    ]


def test_jsonl_physical_line_order_and_one_based_provenance():
    content = (
        b'{"kind":"log","message":"one"}\n'
        b"\n"
        b'{"kind":"log","message":"two"}\n'
    )
    inventory = RuntimeEvidenceIngestor().ingest(_request(
        _artifact(
            "events.jsonl", content, RuntimeArtifactFormat.JSON_EVENTS
        )
    ))
    assert [
        (event.message, event.source_line)
        for event in inventory.events
    ] == [("one", 1), ("two", 3)]
    assert [event.sequence for event in inventory.events] == [0, 1]


def test_skipped_malformed_lines_do_not_create_sequence_gaps():
    content = (
        b'{"kind":"log","message":"one"}\n'
        b"not json\n"
        b'{"kind":"log","message":"two"}\n'
    )
    inventory = RuntimeEvidenceIngestor().ingest(_request(
        _artifact(
            "events.jsonl", content, RuntimeArtifactFormat.JSON_EVENTS
        )
    ))
    assert [event.sequence for event in inventory.events] == [0, 1]
    assert [event.source_line for event in inventory.events] == [1, 3]
    assert len(inventory.issues) == 1
    issue = inventory.issues[0]
    assert issue.code is RuntimeIssueCode.MALFORMED_JSON
    assert issue.event_sequence is None
    assert issue.source_line == 2
    assert issue.message == issue_message(RuntimeIssueCode.MALFORMED_JSON)
    assert _status_values(inventory) == ["partial"]
    assert _reason_values(inventory) == ["artifact_partial"]
    assert inventory.events_truncated is False


def test_timestamps_never_create_cross_artifact_chronological_order():
    artifacts = (
        _artifact(
            "b_then.jsonl",
            b'{"kind":"log","message":"later","timestamp":"2026"}\n',
            RuntimeArtifactFormat.JSON_EVENTS,
        ),
        _artifact(
            "a_first.jsonl",
            b'{"kind":"log","message":"earlier","timestamp":"1999"}\n',
            RuntimeArtifactFormat.JSON_EVENTS,
        ),
    )
    ingestor = RuntimeEvidenceIngestor()
    inventory = ingestor.ingest(_request(*artifacts))
    assert [
        artifact.source_names for artifact in inventory.artifacts
    ] == [("a_first.jsonl",), ("b_then.jsonl",)]
    assert [event.message for event in inventory.events] == [
        "earlier", "later"
    ]
    assert [event.sequence for event in inventory.events] == [0, 0]
    snapshots = {
        _canonical(ingestor.ingest(_request(*permutation)))
        for permutation in itertools.permutations(artifacts)
    }
    assert len(snapshots) == 1


def test_frame_ordinals_remain_contiguous_after_truncation():
    config = RuntimeEvidenceConfig(
        max_frames_per_event=2, max_total_frames=3
    )
    inventory = RuntimeEvidenceIngestor(config).ingest(_request(
        _artifact(
            "trace.txt",
            b'  File "pkg/a.py", line 1, in f\n'
            b'  File "pkg/b.py", line 2, in g\n'
            b'  File "pkg/c.py", line 3, in h\n'
            b"\n"
            b'  File "pkg/d.py", line 4, in i\n'
            b'  File "pkg/e.py", line 5, in j\n',
            RuntimeArtifactFormat.STACK_TRACE_TEXT,
            RuntimeFrameLanguage.PYTHON,
        )
    ))
    first, second = inventory.events
    assert [
        (frame.ordinal, frame.relative_path) for frame in first.frames
    ] == [(0, "pkg/a.py"), (1, "pkg/b.py")]
    assert [
        (frame.ordinal, frame.relative_path) for frame in second.frames
    ] == [(0, "pkg/d.py")]
    assert inventory.total_frames == 3
    assert first.frames_truncated is True
    assert second.frames_truncated is True
    assert _status_values(inventory) == ["partial"]
    assert [
        (issue.code, issue.event_sequence, issue.source_line)
        for issue in inventory.issues
    ] == [
        (RuntimeIssueCode.FRAME_LIMIT_REACHED, 0, 1),
        (RuntimeIssueCode.FRAME_LIMIT_REACHED, 1, 5),
    ]
    assert _reason_values(inventory) == [
        "artifact_partial", "frame_limit_reached"
    ]
    assert inventory.frames == tuple(
        frame for event in inventory.events for frame in event.frames
    )
    assert inventory.frames_by_file["pkg/a.py"] == (inventory.frames[0],)
    assert inventory.frames_by_file["pkg/b.py"] == (inventory.frames[1],)
    assert inventory.frames_by_file["pkg/d.py"] == (inventory.frames[2],)


def test_issue_order_follows_artifact_event_line_code_and_message():
    config = RuntimeEvidenceConfig(max_events=1)
    inventory = RuntimeEvidenceIngestor(config).ingest(_request(
        _artifact("a.log", b"\xff\n"),
        _artifact(
            "b.jsonl",
            b'{"kind":"error","message":"ok","error":{"frames":'
            b'[{"source":"bad\\u0001.py","line":1}]}}\n'
            b'{"kind":"log"}\n'
            b'{"kind":"error","message":"two"}\n',
            RuntimeArtifactFormat.JSON_EVENTS,
        ),
    ))
    artifacts = {
        artifact.source_names[0]: artifact
        for artifact in inventory.artifacts
    }
    a_id = artifacts["a.log"].artifact_id
    b_id = artifacts["b.jsonl"].artifact_id
    assert [
        (issue.code, issue.artifact_id, issue.event_sequence,
         issue.source_line)
        for issue in inventory.issues
    ] == [
        (RuntimeIssueCode.INVALID_UTF8, a_id, None, 1),
        (RuntimeIssueCode.INVALID_SOURCE_LOCATION, b_id, 0, 1),
        (RuntimeIssueCode.INVALID_EVENT_SCHEMA, b_id, None, 2),
        (RuntimeIssueCode.EVENT_LIMIT_REACHED, b_id, None, None),
    ]
    assert [
        issue.message for issue in inventory.issues
    ] == [
        issue_message(RuntimeIssueCode.INVALID_UTF8),
        issue_message(RuntimeIssueCode.INVALID_SOURCE_LOCATION),
        issue_message(RuntimeIssueCode.INVALID_EVENT_SCHEMA),
        issue_message(RuntimeIssueCode.EVENT_LIMIT_REACHED),
    ]
    assert [
        artifact.issue_count for artifact in inventory.artifacts
    ] == [1, 3]
    assert sum(
        artifact.issue_count for artifact in inventory.artifacts
    ) == len(inventory.issues)
    assert _status_values(inventory) == ["failed", "partial"]
    assert _reason_values(inventory) == [
        "artifact_failed", "artifact_partial", "event_limit_reached"
    ]
    _assert_canonical_issue_order(inventory)


def test_issue_deduplication_precedes_the_issue_cap():
    content = (
        b'{"kind":"error","message":"ok","error":{"frames":['
        b'{"source":"bad\\u0001.py","line":1},'
        b'{"source":"bad\\u0001.py","line":2},'
        b'{"source":"bad\\u0001.py","line":3}]}}\n'
    )
    inventory = RuntimeEvidenceIngestor(
        RuntimeEvidenceConfig(max_issues=1)
    ).ingest(_request(
        _artifact(
            "events.jsonl", content, RuntimeArtifactFormat.JSON_EVENTS
        )
    ))
    assert len(inventory.frames) == 3
    assert len(inventory.issues) == 1
    issue = inventory.issues[0]
    assert issue.code is RuntimeIssueCode.INVALID_SOURCE_LOCATION
    assert issue.event_sequence == 0
    assert issue.source_line is None
    assert inventory.issues_truncated is False
    assert inventory.artifacts[0].issue_count == 1
    assert _status_values(inventory) == ["partial"]
    assert _reason_values(inventory) == ["artifact_partial"]


def test_issue_sentinel_copies_first_dropped_issue_and_is_canonically_placed():
    config = RuntimeEvidenceConfig(max_issues=1, max_message_bytes=4)
    inventory = RuntimeEvidenceIngestor(config).ingest(_request(
        _artifact("a_bad.log", b"\xff\n"),
        _artifact("b_long.log", b"abcdef\n"),
    ))
    assert [issue.code for issue in inventory.issues] == [
        RuntimeIssueCode.INVALID_UTF8,
        RuntimeIssueCode.ISSUE_LIMIT_REACHED,
    ]
    sentinel = inventory.issues[-1]
    b_long = inventory.artifacts[1]
    assert b_long.source_names == ("b_long.log",)
    assert sentinel.artifact_id == b_long.artifact_id
    assert sentinel.event_sequence == 0
    assert sentinel.source_line == 1
    assert sentinel.message == "additional issues were omitted"
    assert inventory.issues_truncated is True
    assert _status_values(inventory) == ["failed", "partial"]
    assert [
        artifact.issue_count for artifact in inventory.artifacts
    ] == [1, 1]
    assert sum(
        artifact.issue_count for artifact in inventory.artifacts
    ) == 2
    ordinary_issues = len(inventory.issues) - 1
    assert ordinary_issues == config.max_issues
    assert len(inventory.issues) <= config.max_issues + 1
    assert _reason_values(inventory) == [
        "artifact_failed",
        "artifact_partial",
        "issue_limit_reached",
        "text_truncated",
    ]
    _assert_canonical_issue_order(inventory)


def test_incomplete_reasons_are_unique_and_sorted_across_scaled_artifacts():
    config = RuntimeEvidenceConfig(
        max_events=3,
        max_frames_per_event=1,
        max_total_frames=1,
        max_message_bytes=4,
        max_issues=8,
        max_stack_bytes=256,
        max_line_bytes=256,
    )
    inventory = RuntimeEvidenceIngestor(config).ingest(_request(
        _artifact("aa0_bad.log", b"\xff\n"),
        _artifact("aa1_bad.log", b"\xfe\n"),
        _artifact(
            "ab_stack.txt",
            b'  File "a.py", line 1, in f\n'
            b"\n"
            b'  File "b.py", line 2, in g\n',
            RuntimeArtifactFormat.STACK_TRACE_TEXT,
            RuntimeFrameLanguage.PYTHON,
        ),
        _artifact("ac_long.log", b"abcdef\n"),
        _artifact("ad_skip.log", b"x\n"),
    ))
    assert _status_values(inventory) == [
        "failed", "failed", "partial", "partial", "skipped"
    ]
    assert inventory.total_events == 3
    assert inventory.total_frames == 1
    assert inventory.events_truncated is True
    assert inventory.frames_truncated is True
    assert inventory.text_truncated is True
    assert inventory.issues_truncated is False
    reasons = inventory.incomplete_reasons
    assert reasons == (
        RuntimeIncompleteReason.ARTIFACT_FAILED,
        RuntimeIncompleteReason.ARTIFACT_PARTIAL,
        RuntimeIncompleteReason.ARTIFACT_SKIPPED,
        RuntimeIncompleteReason.EVENT_LIMIT_REACHED,
        RuntimeIncompleteReason.FRAME_LIMIT_REACHED,
        RuntimeIncompleteReason.TEXT_TRUNCATED,
    )
    assert len(reasons) == len(set(reasons))
    assert reasons == tuple(
        sorted(set(reasons), key=lambda reason: reason.value)
    )
    assert inventory.ingestion_complete is False


def test_index_keys_and_values_follow_the_authoritative_order():
    inventory = RuntimeEvidenceIngestor().ingest(_request(
        _artifact("empty.log", b""),
        _artifact(
            "events.jsonl",
            b'{"kind":"span","operation":"op","trace_id":"z"}\n'
            b'{"kind":"span","operation":"op","trace_id":"a"}\n',
            RuntimeArtifactFormat.JSON_EVENTS,
        ),
        _artifact(
            "trace.txt",
            b'  File "pkg/b.py", line 1, in f\n'
            b"\n"
            b'  File "pkg/a.py", line 2, in g\n',
            RuntimeArtifactFormat.STACK_TRACE_TEXT,
            RuntimeFrameLanguage.PYTHON,
        ),
    ))
    mappings = (
        inventory.events_by_artifact,
        inventory.events_by_trace_id,
        inventory.events_by_fingerprint,
        inventory.frames_by_file,
        inventory.frames_by_node,
    )
    for mapping in mappings:
        assert list(mapping) == sorted(mapping)
    assert list(inventory.events_by_trace_id) == ["a", "z"]
    assert inventory.events_by_trace_id["a"] == (inventory.events[1],)
    assert inventory.events_by_trace_id["z"] == (inventory.events[0],)
    assert list(inventory.frames_by_file) == ["pkg/a.py", "pkg/b.py"]
    assert inventory.frames_by_file["pkg/a.py"] == (inventory.frames[1],)
    assert inventory.frames_by_file["pkg/b.py"] == (inventory.frames[0],)
    assert dict(inventory.frames_by_node) == {}
    for artifact in inventory.artifacts:
        assert inventory.events_by_artifact[artifact.artifact_id] == tuple(
            event for event in inventory.events
            if event.artifact_id == artifact.artifact_id
        )
    for mapping in mappings:
        with pytest.raises(TypeError):
            mapping["x"] = ()


def test_basename_only_reference_without_graph_retains_no_path():
    inventory = RuntimeEvidenceIngestor().ingest(_request(
        _artifact(
            "trace.txt",
            b'  File "a.py", line 1, in f\n'
            b"\n"
            b'  File "b.py", line 2, in g\n',
            RuntimeArtifactFormat.STACK_TRACE_TEXT,
            RuntimeFrameLanguage.PYTHON,
        )
    ))
    assert len(inventory.frames) == 2
    for frame in inventory.frames:
        assert frame.relative_path is None
        assert frame.source_reference_sha256 is None
        assert frame.path_match_basis is PathMatchBasis.UNMATCHED
        assert frame.association is FrameAssociation.NOT_EVALUATED
        assert frame.primary_node_id is None
        assert frame.candidate_node_ids == ()
    assert dict(inventory.frames_by_file) == {}
    assert dict(inventory.frames_by_node) == {}


def test_graph_indexes_preserve_authoritative_frame_order():
    graph = _graph(
        _file_node("node-c", "pkg/c.py"),
        _file_node("node-a", "pkg/a.py"),
        _file_node("node-b", "pkg/b.py"),
    )
    inventory = RuntimeEvidenceIngestor().ingest(
        _request(
            _artifact(
                "trace.txt",
                b'  File "pkg/c.py", line 1, in f\n'
                b"\n"
                b'  File "pkg/a.py", line 2, in g\n'
                b"\n"
                b'  File "pkg/c.py", line 3, in h\n',
                RuntimeArtifactFormat.STACK_TRACE_TEXT,
                RuntimeFrameLanguage.PYTHON,
            )
        ),
        graph,
    )
    expected_nodes = ["node-c", "node-a", "node-c"]
    for frame, node_id in zip(inventory.frames, expected_nodes):
        assert frame.path_match_basis is PathMatchBasis.EXACT_RELATIVE_PATH
        assert frame.association is FrameAssociation.FILE
        assert frame.primary_node_id == node_id
        assert frame.candidate_node_ids == (node_id,)
    assert list(inventory.frames_by_file) == ["pkg/a.py", "pkg/c.py"]
    assert inventory.frames_by_file["pkg/c.py"] == (
        inventory.frames[0], inventory.frames[2]
    )
    assert list(inventory.frames_by_node) == ["node-a", "node-c"]
    assert inventory.frames_by_node["node-c"] == (
        inventory.frames[0], inventory.frames[2]
    )
    for key, value in inventory.frames_by_file.items():
        assert value == tuple(
            frame for frame in inventory.frames
            if frame.relative_path == key
        )
    for key, value in inventory.frames_by_node.items():
        assert value == tuple(
            frame for frame in inventory.frames
            if frame.primary_node_id == key
        )
    for mapping in (
        inventory.events_by_artifact,
        inventory.events_by_trace_id,
        inventory.events_by_fingerprint,
        inventory.frames_by_file,
        inventory.frames_by_node,
    ):
        with pytest.raises(TypeError):
            mapping["x"] = ()


def _rich_request():
    return _request(
        _artifact("a.log", b"one\n"),
        _artifact(
            "b.jsonl",
            b'{"kind":"log","message":"two"}\n',
            RuntimeArtifactFormat.JSON_EVENTS,
        ),
        _artifact(
            "c.txt",
            b'  File "pkg/app.py", line 1, in f\n',
            RuntimeArtifactFormat.STACK_TRACE_TEXT,
            RuntimeFrameLanguage.PYTHON,
        ),
    )


def _rich_graph():
    return _graph(_file_node("node-app", "pkg/app.py"))


def _request_state(request):
    return (
        request.repository_namespace,
        request.artifacts,
        tuple(artifact.content for artifact in request.artifacts),
    )


def _index_snapshots(inventory):
    return (
        inventory.events_by_artifact,
        inventory.events_by_trace_id,
        inventory.events_by_fingerprint,
        inventory.frames_by_file,
        inventory.frames_by_node,
    )


def _assert_all_distinct(left, right, label):
    for name in (
        "events_by_artifact",
        "events_by_trace_id",
        "events_by_fingerprint",
        "frames_by_file",
        "frames_by_node",
    ):
        assert getattr(left, name) is not getattr(
            right, name
        ), (label, name)


def test_repeated_calls_on_one_instance_are_byte_identical_and_isolated():
    request = _rich_request()
    state_before = _request_state(request)
    ingestor = RuntimeEvidenceIngestor()
    first = ingestor.ingest(request)
    second = ingestor.ingest(request)
    assert first == second
    assert first is not second
    _assert_all_distinct(first, second, "inventory")
    artifact_id = first.artifacts[0].artifact_id
    assert first.events_by_artifact[artifact_id] == (
        second.events_by_artifact[artifact_id]
    )
    assert first.events_by_artifact[artifact_id] is not (
        second.events_by_artifact[artifact_id]
    )
    assert _canonical(first) == _canonical(second)
    assert _request_state(request) == state_before


def test_separate_instances_with_equal_configurations_are_byte_identical():
    first_config = RuntimeEvidenceConfig(max_events=10)
    second_config = RuntimeEvidenceConfig(max_events=10)
    assert first_config == second_config
    assert first_config is not second_config
    request = _rich_request()
    first = RuntimeEvidenceIngestor(first_config).ingest(request)
    second = RuntimeEvidenceIngestor(second_config).ingest(request)
    assert first == second
    assert first is not second
    _assert_all_distinct(first, second, "inventory")
    assert _canonical(first) == _canonical(second)


def _identity_tuples(inventory):
    return (
        tuple(artifact.artifact_id for artifact in inventory.artifacts),
        tuple(event.event_id for event in inventory.events),
        tuple(frame.frame_id for frame in inventory.frames),
    )


def test_repeated_calls_with_and_without_graph_are_stable_and_keep_ids():
    request = _rich_request()
    graph = _rich_graph()
    state_before = _request_state(request)
    no_graph = [RuntimeEvidenceIngestor().ingest(request) for _ in range(2)]
    with_graph = [
        RuntimeEvidenceIngestor().ingest(request, graph) for _ in range(2)
    ]
    for pair in (no_graph, with_graph):
        assert pair[0] == pair[1]
        assert _canonical(pair[0]) == _canonical(pair[1])
        _assert_all_distinct(pair[0], pair[1], "inventory")
    assert no_graph[0] != with_graph[0]
    assert no_graph[0].graph_binding is GraphBinding.NOT_PROVIDED
    assert with_graph[0].graph_binding is (
        GraphBinding.NAMESPACE_MATCHED_UNVERSIONED
    )
    assert no_graph[0].frames[0].path_match_basis is (
        PathMatchBasis.UNVERIFIED_RELATIVE_PATH
    )
    assert no_graph[0].frames[0].association is (
        FrameAssociation.NOT_EVALUATED
    )
    assert with_graph[0].frames[0].path_match_basis is (
        PathMatchBasis.EXACT_RELATIVE_PATH
    )
    assert with_graph[0].frames[0].association is FrameAssociation.FILE
    assert len({
        _identity_tuples(inventory)
        for inventory in (*no_graph, *with_graph)
    }) == 1
    assert _request_state(request) == state_before


def test_snapshot_rejects_unsupported_public_value_types():
    inventory = RuntimeEvidenceIngestor().ingest(_request(
        _artifact("secret.log", b"\x00SECRET\x00")
    ))
    canonical = _canonical(inventory)
    assert b"SECRET" not in canonical
    assert b"\x00SECRET\x00" not in canonical
    assert not any(
        isinstance(value, (bytes, bytearray, memoryview))
        for value in _leaf_values(inventory)
    )
    for value in (b"raw", bytearray(b"raw"), [1], {1, 2}):
        with pytest.raises(AssertionError) as exc_info:
            _snapshot(value)
        assert "unexpected public value type" in str(exc_info.value)
    with pytest.raises(AssertionError) as exc_info:
        _snapshot(_artifact("raw.log", b"raw"))
    assert "unexpected public value type" in str(exc_info.value)
