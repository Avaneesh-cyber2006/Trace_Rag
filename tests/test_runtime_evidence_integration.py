import builtins
import copy
import importlib
import io
import os
import socket
import subprocess
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from pathlib import Path
from types import MappingProxyType

import pytest

from backend.code_graph import CodeGraphInventory, GraphNode, GraphNodeKind, build_code_graph
from backend.code_parser import CodeParser
from backend.code_parser.models import ParseStatus
from backend.file_scanner import FileScanner
from backend.runtime_evidence import (
    FrameAssociation, GraphBinding, PathMatchBasis, RuntimeArtifact,
    RuntimeArtifactFormat, RuntimeEvidenceGraphError, RuntimeEvidenceIngestor,
    RuntimeEvidenceRequest, RuntimeFrameLanguage,
)

NAMESPACE = "integration-repo"
STACK = (
    b"Traceback (most recent call last):\n"
    b'  File "pkg/app.py", line 5, in run\n'
    b"ValueError: boom\n"
)
APP = (
    "def helper():\n"
    "    return 1\n"
    "\n"
    "\n"
    "def run():\n"
    "    helper()\n"
)
UTIL = "value = 1\n"


def _artifact(name, content, fmt=RuntimeArtifactFormat.STACK_TRACE_TEXT,
              language=RuntimeFrameLanguage.PYTHON):
    return RuntimeArtifact(name, fmt, content, language)


def _request(content=STACK, *, namespace=NAMESPACE, name="run.log"):
    return RuntimeEvidenceRequest(namespace, (_artifact(name, content),))


def _write_repository(tmp_path, files):
    for relative, content in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _real_graph(tmp_path, files):
    _write_repository(tmp_path, files)
    files_inventory = FileScanner().scan(
        tmp_path, repository_namespace=NAMESPACE)
    parsed = CodeParser().parse_inventory(files_inventory)
    graph = build_code_graph(files_inventory, parsed)
    return graph, parsed


def _file_node(graph, path):
    return next(node for node in graph.nodes
                if node.node_kind is GraphNodeKind.FILE
                and node.relative_path == path)


def _symbol_node(graph, qualified_name):
    return next(node for node in graph.nodes
                if node.node_kind is GraphNodeKind.SYMBOL
                and node.qualified_name == qualified_name)


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


def _graph_snapshot(graph):
    return (
        graph.repository_namespace,
        graph.nodes,
        graph.edges,
        graph.unresolved_references,
        dict(graph.outgoing_edges),
        dict(graph.incoming_edges),
        dict(graph.nodes_by_file),
        dict(graph.nodes_by_qualified_name),
    )


def _forbid(*args, **kwargs):
    raise AssertionError("Module 8 reread source or crossed the boundary")


def _guard_source_reads(guard):
    for owner, name in (
        (builtins, "open"), (io, "open"), (os, "open"),
        (os, "scandir"), (os, "listdir"), (os, "remove"), (os, "unlink"),
        (os, "rename"), (os, "replace"), (os, "mkdir"), (os, "makedirs"),
        (os, "rmdir"), (os, "chmod"), (os, "symlink"), (os, "link"),
        (os, "truncate"), (os, "fdopen"),
        (Path, "open"), (Path, "read_text"), (Path, "read_bytes"),
        (Path, "write_text"), (Path, "write_bytes"), (Path, "stat"),
        (Path, "resolve"), (Path, "iterdir"), (Path, "unlink"),
        (Path, "rename"), (Path, "replace"), (Path, "mkdir"),
        (Path, "rmdir"), (Path, "touch"), (Path, "chmod"),
        (Path, "symlink_to"), (Path, "hardlink_to"),
        (subprocess, "run"), (subprocess, "Popen"), (subprocess, "call"),
        (subprocess, "check_call"), (subprocess, "check_output"),
        (socket, "socket"), (socket, "create_connection"),
        (importlib, "import_module"),
    ):
        guard.setattr(owner, name, _forbid)


def test_real_pipeline_associates_exact_relative_symbol(tmp_path):
    graph, parsed = _real_graph(tmp_path, {"pkg/app.py": APP, "pkg/util.py": UTIL})
    assert isinstance(graph, CodeGraphInventory)
    assert parsed.success_files == 2
    assert _symbol_node(graph, "run").parse_status is ParseStatus.SUCCESS

    inventory = RuntimeEvidenceIngestor().ingest(_request(), graph)

    assert len(inventory.events) == 1
    assert len(inventory.frames) == 1
    frame = inventory.frames[0]
    run = _symbol_node(graph, "run")
    file_node = _file_node(graph, "pkg/app.py")
    assert frame.ordinal == 0
    assert frame.line == 5
    assert frame.relative_path == "pkg/app.py"
    assert frame.path_match_basis is PathMatchBasis.EXACT_RELATIVE_PATH
    assert frame.association is FrameAssociation.SYMBOL
    assert frame.primary_node_id == run.node_id
    assert frame.candidate_node_ids == tuple(
        sorted((file_node.node_id, run.node_id)))
    assert inventory.frames_by_node[run.node_id] == (frame,)
    assert file_node.node_id not in inventory.frames_by_node
    assert inventory.graph_binding is GraphBinding.NAMESPACE_MATCHED_UNVERSIONED
    assert inventory.declared_revision is None
    assert inventory.declared_captured_at is None


def test_symbol_free_file_associates_at_file_level(tmp_path):
    stack = (b"Traceback (most recent call last):\n"
             b'  File "pkg/util.py", line 1, in module\n'
             b"ValueError: boom\n")
    graph, _parsed = _real_graph(tmp_path, {"pkg/util.py": UTIL})
    file_node = _file_node(graph, "pkg/util.py")
    assert not [node for node in graph.nodes
                if node.node_kind is GraphNodeKind.SYMBOL
                and node.relative_path == "pkg/util.py"]

    inventory = RuntimeEvidenceIngestor().ingest(
        _request(stack, name="util.log"), graph)

    frame = inventory.frames[0]
    assert len(inventory.frames) == 1
    assert frame.line == 1
    assert frame.relative_path == "pkg/util.py"
    assert frame.path_match_basis is PathMatchBasis.EXACT_RELATIVE_PATH
    assert frame.association is FrameAssociation.FILE
    assert frame.primary_node_id == file_node.node_id
    assert frame.candidate_node_ids == (file_node.node_id,)
    assert inventory.frames_by_node == {file_node.node_id: (frame,)}
    assert inventory.graph_binding is GraphBinding.NAMESPACE_MATCHED_UNVERSIONED


def test_namespace_mismatch_fails_before_association_and_keeps_graph(
        tmp_path, monkeypatch):
    graph, _parsed = _real_graph(tmp_path, {"pkg/app.py": APP})
    before = _graph_snapshot(graph)
    with monkeypatch.context() as guard:
        _guard_source_reads(guard)
        with pytest.raises(RuntimeEvidenceGraphError) as caught:
            RuntimeEvidenceIngestor().ingest(
                _request(namespace="SENTINEL-OTHER-NAMESPACE"), graph)
    assert str(caught.value) == "runtime evidence graph is invalid"
    assert "SENTINEL" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert _graph_snapshot(graph) == before


def test_ingestion_does_not_reread_source_or_rebuild_the_graph(
        tmp_path, monkeypatch):
    graph, _parsed = _real_graph(tmp_path, {"pkg/app.py": APP})
    request = _request()
    before = _graph_snapshot(graph)
    with monkeypatch.context() as guard:
        _guard_source_reads(guard)
        inventory = RuntimeEvidenceIngestor().ingest(request, graph)
    assert inventory.frames[0].primary_node_id == _symbol_node(graph, "run").node_id
    assert inventory.frames[0].association is FrameAssociation.SYMBOL
    assert _graph_snapshot(graph) == before
    leaves = tuple(_leaf_values(inventory))
    assert not [value for value in leaves if isinstance(value, GraphNode)]
    assert not [value for value in leaves if isinstance(value, CodeGraphInventory)]
    assert not [value for value in leaves
                if isinstance(value, (bytes, bytearray, memoryview))]


def test_ids_are_graph_independent_and_graph_only_changes_association(tmp_path):
    graph, _parsed = _real_graph(tmp_path, {"pkg/app.py": APP})
    request = _request()
    ingestor = RuntimeEvidenceIngestor()
    without_graph = ingestor.ingest(request)
    with_graph = ingestor.ingest(request, graph)

    assert [a.artifact_id for a in without_graph.artifacts] == [
        a.artifact_id for a in with_graph.artifacts]
    assert [a.content_sha256 for a in without_graph.artifacts] == [
        a.content_sha256 for a in with_graph.artifacts]
    assert [e.event_id for e in without_graph.events] == [
        e.event_id for e in with_graph.events]
    assert [e.sequence for e in without_graph.events] == [
        e.sequence for e in with_graph.events]
    assert [f.frame_id for f in without_graph.frames] == [
        f.frame_id for f in with_graph.frames]
    assert [f.ordinal for f in without_graph.frames] == [
        f.ordinal for f in with_graph.frames]
    assert without_graph.frames[0].source_reference_sha256 == (
        with_graph.frames[0].source_reference_sha256)
    assert without_graph.frames[0].relative_path == "pkg/app.py"
    assert without_graph.frames[0].association is FrameAssociation.NOT_EVALUATED
    assert without_graph.frames[0].path_match_basis is (
        PathMatchBasis.UNVERIFIED_RELATIVE_PATH)
    assert with_graph.frames[0].association is FrameAssociation.SYMBOL
    assert with_graph.frames[0].path_match_basis is (
        PathMatchBasis.EXACT_RELATIVE_PATH)
    assert without_graph.graph_binding is GraphBinding.NOT_PROVIDED
    assert without_graph.frames_by_node == {}
    assert with_graph.frames_by_node


def test_consumer_reads_without_conversion_or_mutation(tmp_path):
    graph, _parsed = _real_graph(tmp_path, {"pkg/app.py": APP})
    inventory = RuntimeEvidenceIngestor().ingest(_request(), graph)
    run = _symbol_node(graph, "run")
    before_graph = _graph_snapshot(graph)
    before_leaves = tuple(_leaf_values(inventory))

    def consume(value):
        return (
            value.total_events,
            value.total_frames,
            tuple(event.event_id for event in value.events),
            tuple(frame.frame_id for frame in value.frames),
            tuple(sorted(value.frames_by_node)),
            {key: tuple(frame.frame_id for frame in frames)
             for key, frames in value.frames_by_node.items()},
            {key: tuple(event.event_id for event in events)
             for key, events in value.events_by_fingerprint.items()},
        )

    first = consume(inventory)
    assert consume(inventory) == first
    assert inventory.frames_by_node[run.node_id] == tuple(
        frame for frame in inventory.frames
        if frame.primary_node_id == run.node_id)
    assert type(inventory.frames_by_node) is MappingProxyType
    assert type(inventory.events_by_fingerprint) is MappingProxyType
    for mapping in (inventory.frames_by_node, inventory.events_by_fingerprint):
        with pytest.raises(TypeError):
            mapping["injected"] = ()
    assert _graph_snapshot(graph) == before_graph
    assert tuple(_leaf_values(inventory)) == before_leaves
