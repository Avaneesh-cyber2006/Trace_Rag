"""Real Module 2 -> Module 3 -> Module 6 integration over a temporary repository.

Source is read by the scanner and the parser only. The graph build runs under a
guard that rejects every path-based opener, so a source reread during graph
construction fails the test rather than passing silently.
"""

import pytest

from backend.code_graph import build_code_graph
from backend.code_graph.models import (
    CodeGraphInventory,
    GraphEdgeKind,
    GraphNodeKind,
    ReferenceKind,
    ResolutionFailureReason,
)
from backend.code_parser import CodeParser
from backend.code_parser.models import ParsedLanguage, ParseStatus, SymbolKind
from backend.file_scanner import FileScanner

FORBIDDEN_MESSAGE = "Graph builder reread source"


def _forbid(*args, **kwargs):
    raise AssertionError(FORBIDDEN_MESSAGE)


def _guard_source_reads(monkeypatch):
    from pathlib import Path

    for owner, name in (
        (Path, "open"),
        (Path, "read_text"),
        (Path, "read_bytes"),
    ):
        monkeypatch.setattr(owner, name, _forbid)


def _pipeline(tmp_path, monkeypatch, files, namespace="integration-repo"):
    for relative, content in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    file_inventory = FileScanner().scan(tmp_path, repository_namespace=namespace)
    parse_inventory = CodeParser().parse_inventory(file_inventory)

    with monkeypatch.context() as guard:
        guard.setattr("builtins.open", _forbid)
        guard.setattr("io.open", _forbid)
        import os

        guard.setattr(os, "open", _forbid)
        guard.setattr(os, "scandir", _forbid)
        from pathlib import Path

        _guard_source_reads(guard)
        graph = build_code_graph(file_inventory, parse_inventory)

    return file_inventory, parse_inventory, graph


def test_real_pipeline_blocks_source_after_parse(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text(
        "def helper():\n    pass\n\ndef run():\n    helper()\n", encoding="utf-8"
    )
    file_inventory = FileScanner().scan(
        tmp_path, repository_namespace="integration-repo"
    )
    parse_inventory = CodeParser().parse_inventory(file_inventory)
    with monkeypatch.context() as guard:
        from pathlib import Path

        guard.setattr(Path, "open", _forbid)
        guard.setattr(Path, "read_text", _forbid)
        guard.setattr(Path, "read_bytes", _forbid)
        graph = build_code_graph(file_inventory, parse_inventory)
    assert sum(e.edge_kind is GraphEdgeKind.CALLS for e in graph.edges) == 1


def test_python_pipeline_produces_expected_nodes_and_call_edge(tmp_path, monkeypatch):
    _inventory, parsed, graph = _pipeline(
        tmp_path,
        monkeypatch,
        {
            "pkg/util.py": "def helper():\n    return 1\n",
            "main.py": (
                "from pkg.util import helper\n"
                "\n"
                "def run():\n"
                "    helper()\n"
            ),
        },
    )

    assert isinstance(graph, CodeGraphInventory)
    by_name = {
        node.qualified_name: node
        for node in graph.nodes
        if node.node_kind is GraphNodeKind.SYMBOL
    }
    assert {"helper", "run"} <= set(by_name)

    calls = [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.CALLS]
    assert len(calls) == 1
    assert by_name["run"].node_id == calls[0].source_node_id
    assert by_name["helper"].node_id == calls[0].target_node_id

    imports = [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.IMPORTS]
    assert len(imports) == 1
    files_by_path = {
        node.relative_path: node.node_id
        for node in graph.nodes
        if node.node_kind is GraphNodeKind.FILE
    }
    assert files_by_path["main.py"] == imports[0].source_node_id
    assert files_by_path["pkg/util.py"] == imports[0].target_node_id
    assert {node.repository_namespace for node in graph.nodes} == {"integration-repo"}


def test_java_pipeline_produces_inherits_and_implements_edges(tmp_path, monkeypatch):
    _inventory, _parsed, graph = _pipeline(
        tmp_path,
        monkeypatch,
        {
            "pkg/Base.java": "package pkg;\npublic class Base {}\n",
            "pkg/Port.java": "package pkg;\npublic interface Port {}\n",
            "pkg/Child.java": (
                "package pkg;\n"
                "public class Child extends Base implements Port {}\n"
            ),
        },
    )

    kinds = {edge.edge_kind for edge in graph.edges}
    assert GraphEdgeKind.INHERITS in kinds
    assert GraphEdgeKind.IMPLEMENTS in kinds

    targets = {
        node.qualified_name
        for node in graph.nodes
        if node.node_kind is GraphNodeKind.SYMBOL
        and any(
            edge.edge_kind is GraphEdgeKind.INHERITS
            and edge.target_node_id == node.node_id
            for edge in graph.edges
        )
    }
    assert "pkg.Base" in targets
    implemented = {
        node.qualified_name
        for node in graph.nodes
        if node.node_kind is GraphNodeKind.SYMBOL
        and any(
            edge.edge_kind is GraphEdgeKind.IMPLEMENTS
            and edge.target_node_id == node.node_id
            for edge in graph.edges
        )
    }
    assert "pkg.Port" in implemented


def test_ecmascript_pipeline_produces_import_and_call_edges(tmp_path, monkeypatch):
    _inventory, _parsed, graph = _pipeline(
        tmp_path,
        monkeypatch,
        {
            "util.js": "export function helper() {}\n",
            "main.js": 'import { helper } from "./util.js";\nfunction run() { helper(); }\n',
        },
    )

    by_name = {
        node.qualified_name: node
        for node in graph.nodes
        if node.node_kind is GraphNodeKind.SYMBOL
    }
    assert {"helper", "run"} <= set(by_name)

    calls = [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.CALLS]
    assert len(calls) == 1
    assert by_name["run"].node_id == calls[0].source_node_id
    assert by_name["helper"].node_id == calls[0].target_node_id

    imports = [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.IMPORTS]
    assert len(imports) == 1


def test_typescript_and_tsx_pipeline_records_declared_types(tmp_path, monkeypatch):
    _inventory, parsed, graph = _pipeline(
        tmp_path,
        monkeypatch,
        {
            "types.ts": (
                "export interface Port {}\n"
                "export class Adapter implements Port {}\n"
            ),
            "view.tsx": "export function View() { return null; }\n",
        },
    )

    languages = {node.language for node in graph.nodes}
    assert ParsedLanguage.TYPESCRIPT in languages
    assert ParsedLanguage.TSX in languages
    by_name = {
        node.qualified_name: node
        for node in graph.nodes
        if node.node_kind is GraphNodeKind.SYMBOL
    }
    assert {"Port", "Adapter", "View"} <= set(by_name)
    implements = [
        edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.IMPLEMENTS
    ]
    assert len(implements) == 1
    assert by_name["Adapter"].node_id == implements[0].source_node_id
    assert by_name["Port"].node_id == implements[0].target_node_id
    assert parsed.failed_files == 0


def test_external_and_unknown_references_keep_structured_evidence(
    tmp_path, monkeypatch
):
    _inventory, _parsed, graph = _pipeline(
        tmp_path,
        monkeypatch,
        {
            "main.py": (
                "import requests\n"
                "\n"
                "def run():\n"
                "    requests.get('http://example.invalid')\n"
                "    nowhere()\n"
            ),
        },
    )

    reasons = {
        (item.reference_kind, item.raw_target): item.reason
        for item in graph.unresolved_references
    }
    assert reasons[ReferenceKind.IMPORT, "requests"] is ResolutionFailureReason.EXTERNAL
    assert reasons[ReferenceKind.CALL, "nowhere"] is ResolutionFailureReason.NOT_FOUND
    assert all(
        item.candidate_node_ids == ()
        for item in graph.unresolved_references
        if item.reason is not ResolutionFailureReason.AMBIGUOUS
    )


def test_partial_parse_keeps_file_and_trusted_symbols(tmp_path, monkeypatch):
    _inventory, parsed, graph = _pipeline(
        tmp_path,
        monkeypatch,
        {
            "broken.py": "def kept():\n    return 1\n\ndef also_kept(:\n",
            "fine.py": "def fine():\n    return 2\n",
        },
    )

    partial_file = [
        node
        for node in graph.nodes
        if node.relative_path == "broken.py"
        and node.node_kind is GraphNodeKind.FILE
    ]
    assert len(partial_file) == 1
    assert partial_file[0].parse_status is ParseStatus.PARTIAL
    # Trusted evidence extracted before the syntax error survives.
    partial_symbols = {
        node.qualified_name
        for node in graph.nodes
        if node.relative_path == "broken.py"
        and node.node_kind is GraphNodeKind.SYMBOL
    }
    assert "kept" in partial_symbols
    assert parsed.partial_files == 1


def test_failed_parse_retains_only_its_file(tmp_path, monkeypatch):
    _inventory, parsed, graph = _pipeline(
        tmp_path,
        monkeypatch,
        {
            "good.py": "def good():\n    return 1\n",
            "bad.py": "def (((:\n    ???\n",
        },
    )

    failed = [node for node in graph.nodes if node.parse_status is ParseStatus.FAILED]
    if failed:
        assert all(node.node_kind is GraphNodeKind.FILE for node in failed)
        assert all(node.qualified_name is None for node in failed)
        assert all(
            item.relative_path != "bad.py"
            for item in graph.unresolved_references
        )
    assert parsed.failed_files == sum(
        1 for file in parsed.files if file.status is ParseStatus.FAILED
    )


def test_unicode_and_bom_paths_are_preserved_verbatim(tmp_path, monkeypatch):
    _inventory, _parsed, graph = _pipeline(
        tmp_path,
        monkeypatch,
            {
                "caf\u00e9/na\u00efve.py": "def helper():\n    return '\u2713'\n",
            },
    )

    paths = {node.relative_path for node in graph.nodes}
    assert "caf\u00e9/na\u00efve.py" in paths
    names = {
        node.name for node in graph.nodes if node.node_kind is GraphNodeKind.SYMBOL
    }
    assert "helper" in names


def test_repeated_builds_are_deterministic_and_isolated(tmp_path, monkeypatch):
    _inventory, _parsed, first = _pipeline(
        tmp_path,
        monkeypatch,
        {"a.py": "def run():\n    helper()\n\ndef helper():\n    return 1\n"},
    )
    original_nodes = first.nodes

    _inventory_two, _parsed_two, second = _pipeline(
        tmp_path,
        monkeypatch,
        {"b.py": "def other():\n    return 2\n"},
    )

    assert first.nodes == original_nodes
    assert first != second
    # A later build of a different repository must not alter the first graph.
    assert first.nodes_by_qualified_name.get("other") is None
    assert "a.py" in first.nodes_by_file
    assert "b.py" not in first.nodes_by_file
    assert first.nodes_by_file is not second.nodes_by_file
    with pytest.raises(TypeError):
        first.nodes_by_file["injected.py"] = ()


def test_nested_declarations_produce_direct_containment_only(tmp_path, monkeypatch):
    _inventory, _parsed, graph = _pipeline(
        tmp_path,
        monkeypatch,
        {
            "nested.py": (
                "class Outer:\n"
                "    def method(self):\n"
                "        return 1\n"
                "\n"
                "def top_level():\n"
                "    return 2\n"
            ),
        },
    )

    contains = [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.CONTAINS]
    defines = [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.DEFINES]
    by_name = {
        node.qualified_name: node
        for node in graph.nodes
        if node.node_kind is GraphNodeKind.SYMBOL
    }
    assert len(contains) == 1
    assert contains[0].source_node_id == by_name["Outer"].node_id
    assert contains[0].target_node_id == by_name["Outer.method"].node_id
    # FILE DEFINES the class and the top-level function, never the nested method.
    id_to_name = {node.node_id: node.qualified_name for node in graph.nodes}
    defined = {
        id_to_name[edge.target_node_id]
        for edge in defines
        if id_to_name.get(edge.target_node_id)
    }
    assert defined == {"Outer", "top_level"}


def test_published_graph_indexes_are_read_only_after_real_pipeline(
    tmp_path, monkeypatch
):
    _inventory, _parsed, graph = _pipeline(
        tmp_path, monkeypatch, {"a.py": "def run():\n    return 1\n"}
    )

    for name in (
        "outgoing_edges",
        "incoming_edges",
        "nodes_by_file",
        "nodes_by_qualified_name",
    ):
        index = getattr(graph, name)
        with pytest.raises(TypeError):
            index["injected"] = ()

    with pytest.raises(Exception):
        graph.nodes = ()


def test_graph_build_makes_no_network_call(tmp_path, monkeypatch):
    import socket

    calls = []

    def record(*args, **kwargs):
        calls.append(args)
        raise AssertionError("network access during graph build")

    with monkeypatch.context() as guard:
        guard.setattr(socket, "socket", record)
        guard.setattr(socket, "create_connection", record)
        guard.setattr(socket, "getaddrinfo", record)
        _inventory, _parsed, graph = _pipeline(
            tmp_path, monkeypatch, {"a.py": "def run():\n    return 1\n"}
        )

    assert calls == []
    assert graph.nodes