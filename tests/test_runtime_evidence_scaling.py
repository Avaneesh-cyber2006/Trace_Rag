"""Deterministic scaling and resource-operation gates for runtime evidence."""

import json
import sys
from collections import Counter
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import MappingProxyType

import pytest

import backend.runtime_evidence.json_adapter as json_adapter
from backend.runtime_evidence import (
    FrameAssociation,
    PathMatchBasis,
    RuntimeArtifact,
    RuntimeArtifactFormat,
    RuntimeArtifactStatus,
    RuntimeEvidenceConfig,
    RuntimeEvidenceIngestor,
    RuntimeEvidenceRequest,
    RuntimeFrameLanguage,
    RuntimeIssueCode,
)
from backend.runtime_evidence.association import (
    AssociationIndex,
    GraphFileEntry,
    GraphSymbolEntry,
    associate_frames,
    match_symbols_by_line,
)
from backend.runtime_evidence.drafts import FrameDraft
from backend.runtime_evidence.json_adapter import parse_json_events
from backend.runtime_evidence.json_scanner import scan_json_structure


class CountingMapping(Mapping):
    def __init__(self, source):
        self.source = source
        self.counts = Counter()

    def __getitem__(self, key):
        self.counts["get"] += 1
        return self.source[key]

    def get(self, key, default=None):
        self.counts["get"] += 1
        return self.source.get(key, default)

    def __iter__(self):
        self.counts["iter"] += 1
        for key in self.source:
            self.counts["yield"] += 1
            yield key

    def __len__(self):
        return len(self.source)


class CountingTuple(tuple):
    def __new__(cls, source):
        instance = super().__new__(cls, source)
        instance.counts = Counter()
        return instance

    def __iter__(self):
        self.counts["iter"] += 1
        for item in super().__iter__():
            self.counts["yield"] += 1
            yield item


def _config(**overrides):
    values = dict(
        max_events=1000,
        max_json_nodes=10000,
        max_line_bytes=4096,
        max_issues=100,
    )
    values.update(overrides)
    return RuntimeEvidenceConfig(**values)


def _artifact(
    name="a.log",
    content=b"",
    fmt=RuntimeArtifactFormat.TEXT_LOG,
    language=None,
):
    return RuntimeArtifact(name, fmt, content, language)


def _jsonl(count, *, text="x"):
    line = json.dumps({"kind": "log", "message": text}).encode()
    return b"\n".join([line] * count) + b"\n"


def _leaf_values(value):
    from dataclasses import fields, is_dataclass

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


def _flat_object(fields):
    return "{" + ",".join(f'"k{i}":{i}' for i in range(fields)) + "}"


def _scan_line_events(text):
    count = 0
    previous = sys.gettrace()

    def tracer(frame, event, arg):
        nonlocal count
        if event == "line" and Path(frame.f_code.co_filename).name == (
            "json_scanner.py"
        ):
            count += 1
        return tracer

    sys.settrace(tracer)
    try:
        result = scan_json_structure(text)
    finally:
        sys.settrace(previous)
    return count, result


def test_json_structural_scan_work_is_linear_in_input_fields(monkeypatch):
    monkeypatch.setattr(
        json,
        "loads",
        lambda *args, **kwargs: pytest.fail("scanner materialized JSON"),
    )
    counts = {}
    for fields in (32, 64, 128):
        count, result = _scan_line_events(_flat_object(fields))
        assert result.node_count == 2 * fields + 1
        counts[fields] = count
    assert counts[32] > 0
    assert counts[64] <= 3 * counts[32]
    assert counts[128] <= 3 * counts[64]


def _counted_loads(monkeypatch):
    calls = []
    real = json_adapter.json.loads

    def counted(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(json_adapter.json, "loads", counted)
    return calls


def test_document_materializes_once_and_over_budget_materializes_zero(
    monkeypatch,
):
    document = (
        b"["
        + b",".join([b'{"kind":"log","message":"x"}'] * 20)
        + b"]"
    )
    calls = _counted_loads(monkeypatch)
    result = parse_json_events(document, _config(), None, 100)
    assert len(result.events) == 20
    assert calls == [1]

    calls.clear()
    over = parse_json_events(document, _config(max_json_nodes=5), None, 100)
    assert over.events == ()
    assert [(issue.code, issue.source_line) for issue in over.issues] == [
        (RuntimeIssueCode.MALFORMED_JSON, None)
    ]
    assert calls == []


def test_jsonl_materializes_each_line_once_and_never_sums_nodes(monkeypatch):
    content = _jsonl(10)
    calls = _counted_loads(monkeypatch)
    result = parse_json_events(content, _config(max_json_nodes=5), None, 100)
    assert len(result.events) == 10
    assert len(calls) == 10


def test_basename_buckets_avoid_a_global_scan_per_frame():
    target = GraphFileEntry("target", "pkg/app.py", ("pkg", "app.py"))
    unrelated = tuple(
        GraphFileEntry(f"f{i}", f"pkg{i}/app{i}.py", (f"pkg{i}", f"app{i}.py"))
        for i in range(500)
    )
    by_path = CountingMapping({target.relative_path: target})
    by_base = CountingMapping(
        {entry.components[-1]: (entry,) for entry in unrelated + (target,)}
    )
    by_file = CountingMapping({})
    index = AssociationIndex(by_path, by_base, by_file)
    drafts = tuple(
        FrameDraft(RuntimeFrameLanguage.PYTHON, "f", "app.py", 1, None)
        for _ in range(20)
    )

    result = associate_frames(
        drafts,
        event_id="e" * 64,
        event_sequence=0,
        source_line=1,
        repository_namespace="repo",
        config=RuntimeEvidenceConfig(),
        graph_index=index,
    )

    assert all(
        frame.path_match_basis is PathMatchBasis.UNIQUE_BASENAME
        for frame in result.frames
    )
    assert all(frame.relative_path == "pkg/app.py" for frame in result.frames)
    assert all(
        frame.association is FrameAssociation.FILE for frame in result.frames
    )
    assert by_path.counts["iter"] == 0
    assert by_base.counts["iter"] == 0
    assert by_base.counts["get"] >= 1


def test_line_sweep_never_compares_all_symbols_with_all_frames():
    symbols = CountingTuple(
        GraphSymbolEntry(f"s{i}", i * 10 + 1, i * 10 + 5) for i in range(1000)
    )
    lines = tuple(range(1, 1001, 10))

    matches = match_symbols_by_line(lines, symbols)

    assert len(matches) == len(lines)
    assert all(len(match) == 1 for match in matches)
    assert symbols.counts["yield"] <= 2 * len(symbols) + 10


def test_record_outputs_scale_linearly_without_content_deduplication():
    for count in (20, 40):
        inventory = RuntimeEvidenceIngestor(_config()).ingest(
            RuntimeEvidenceRequest(
                "repo",
                (
                    _artifact(
                        "a.log",
                        b"".join(f"line{i}\n".encode() for i in range(count)),
                    ),
                ),
            )
        )
        assert inventory.total_events == count
        assert len(inventory.events) == count
        assert len(inventory.artifacts) == 1


def test_event_budget_at_scale_sets_one_issue_and_exact_counters():
    content = b"".join(f"line{i}\n".encode() for i in range(100))
    inventory = RuntimeEvidenceIngestor(_config(max_events=10)).ingest(
        RuntimeEvidenceRequest("repo", (_artifact("a.log", content),))
    )
    assert inventory.total_events == 10
    assert len(inventory.events) == 10
    assert inventory.events_truncated is True
    assert [issue.code for issue in inventory.issues] == [
        RuntimeIssueCode.EVENT_LIMIT_REACHED
    ]
    assert inventory.artifacts[0].status is RuntimeArtifactStatus.PARTIAL


def test_frame_budget_at_scale_keeps_exact_prefix_and_one_issue():
    event = {
        "kind": "error",
        "message": "x",
        "error": {
            "frames": [
                {"function": f"f{i}", "source": "pkg/app.py", "line": i + 1}
                for i in range(40)
            ]
        },
    }
    inventory = RuntimeEvidenceIngestor(
        _config(
            max_frames_per_event=5,
            max_total_frames=100,
            max_json_nodes=10000,
        )
    ).ingest(
        RuntimeEvidenceRequest(
            "repo",
            (
                _artifact(
                    "a.jsonl",
                    json.dumps(event).encode() + b"\n",
                    RuntimeArtifactFormat.JSON_EVENTS,
                ),
            ),
        )
    )
    assert len(inventory.frames) == 5
    assert len(inventory.events) == 1
    assert inventory.events[0].frames_truncated is True
    assert inventory.frames_truncated is True
    assert inventory.artifacts[0].frames_truncated is True
    assert inventory.artifacts[0].status is RuntimeArtifactStatus.PARTIAL
    assert [issue.code for issue in inventory.issues] == [
        RuntimeIssueCode.FRAME_LIMIT_REACHED
    ]
    assert [frame.ordinal for frame in inventory.frames] == [0, 1, 2, 3, 4]


def test_issue_budget_at_scale_keeps_exact_ordinary_count_and_sentinel():
    content = b"\xff\n" * 40
    inventory = RuntimeEvidenceIngestor(_config(max_issues=5)).ingest(
        RuntimeEvidenceRequest("repo", (_artifact("a.log", content),))
    )
    assert len(inventory.issues) == 6
    assert inventory.issues_truncated is True
    assert inventory.issues[-1].code is RuntimeIssueCode.ISSUE_LIMIT_REACHED
    assert [issue.source_line for issue in inventory.issues] == [
        1,
        2,
        3,
        4,
        5,
        6,
    ]
    assert inventory.issues[-1].artifact_id == inventory.artifacts[0].artifact_id
    assert inventory.issues[-1].event_sequence is None
    assert (
        sum(
            issue.code is not RuntimeIssueCode.ISSUE_LIMIT_REACHED
            for issue in inventory.issues
        )
        == 5
    )
    assert sum(artifact.issue_count for artifact in inventory.artifacts) == 6


def test_indexes_and_tuples_are_immutable_at_scale():
    inventory = RuntimeEvidenceIngestor(_config()).ingest(
        RuntimeEvidenceRequest("repo", (_artifact("a.log", _jsonl(20)),))
    )
    for mapping in (
        inventory.events_by_artifact,
        inventory.events_by_trace_id,
        inventory.events_by_fingerprint,
        inventory.frames_by_file,
        inventory.frames_by_node,
    ):
        assert isinstance(mapping, MappingProxyType)
        with pytest.raises(TypeError):
            mapping["injected"] = ()
    for record in (
        inventory.artifacts,
        inventory.events,
        inventory.frames,
        inventory.issues,
    ):
        assert type(record) is tuple
        with pytest.raises(AttributeError):
            record.append(None)
    with pytest.raises(FrozenInstanceError):
        inventory.events = ()
    assert not hasattr(inventory, "__dict__")


def test_no_raw_bytes_remain_in_the_returned_inventory():
    inventory = RuntimeEvidenceIngestor(_config()).ingest(
        RuntimeEvidenceRequest(
            "repo",
            (_artifact("a.log", b"SENTINEL-RAW-BYTES\n"),),
        )
    )
    assert not any(
        isinstance(value, (bytes, bytearray, memoryview))
        for value in _leaf_values(inventory)
    )
