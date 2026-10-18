"""Real Module 2 -> Module 3 -> Module 6 integration over a temporary repository.

Source is read by the scanner and the parser only. The graph build runs under a
guard that rejects every path-based opener, so a source reread during graph
construction fails the test rather than passing silently.
"""

import copy
from dataclasses import FrozenInstanceError

import pytest

import backend.code_graph as code_graph
from backend.code_graph import build_code_graph
from backend.code_graph.models import (
    CodeGraphInventory,
    GraphEdge,
    GraphEdgeKind,
    GraphNode,
    GraphNodeKind,
    ReferenceKind,
    ResolutionFailureReason,
    UnresolvedReference,
)
from backend.code_parser import CodeParser
from backend.code_parser.models import ParsedLanguage, ParseStatus
from backend.file_scanner import FileScanner

FORBIDDEN_MESSAGE = "Graph builder reread source"


def _forbid(*args, **kwargs):
    raise AssertionError(FORBIDDEN_MESSAGE)


def _guard_source_reads(guard):
    """Block every filesystem/process/import/network vector after scan/parse.

    Scanner and parser have already finished by the time this is applied, so the
    guard cannot interfere with upstream source reading. It mirrors Task 15's
    complete forbidden-API list so a source reread or side effect during graph
    construction fails the test instead of passing silently.
    """
    import builtins
    import importlib
    import io
    import os
    import socket
    import subprocess
    from pathlib import Path

    for owner, name in (
        (builtins, "open"),
        (io, "open"),
        (os, "open"),
        (os, "scandir"),
        (os, "remove"),
        (os, "unlink"),
        (os, "rename"),
        (os, "replace"),
        (os, "mkdir"),
        (os, "makedirs"),
        (os, "rmdir"),
        (os, "removedirs"),
        (os, "chmod"),
        (os, "symlink"),
        (os, "link"),
        (os, "truncate"),
        (os, "fdopen"),
        (os, "listdir"),
        (Path, "open"),
        (Path, "read_text"),
        (Path, "read_bytes"),
        (Path, "write_text"),
        (Path, "write_bytes"),
        (Path, "stat"),
        (Path, "resolve"),
        (Path, "iterdir"),
        (Path, "unlink"),
        (Path, "rename"),
        (Path, "replace"),
        (Path, "mkdir"),
        (Path, "rmdir"),
        (Path, "touch"),
        (Path, "chmod"),
        (Path, "symlink_to"),
        (Path, "hardlink_to"),
        (subprocess, "run"),
        (subprocess, "Popen"),
        (importlib, "import_module"),
        (socket, "socket"),
        (socket, "create_connection"),
    ):
        guard.setattr(owner, name, _forbid)


def _pipeline(tmp_path, monkeypatch, files, namespace="integration-repo"):
    for relative, content in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    file_inventory = FileScanner().scan(tmp_path, repository_namespace=namespace)
    parse_inventory = CodeParser().parse_inventory(file_inventory)

    with monkeypatch.context() as guard:
        _guard_source_reads(guard)
        graph = build_code_graph(file_inventory, parse_inventory)

    return file_inventory, parse_inventory, graph


def test_real_pipeline_blocks_source_after_parse(tmp_path, monkeypatch):
    _inventory, _parsed, graph = _pipeline(
        tmp_path,
        monkeypatch,
        {"a.py": "def helper():\n    pass\n\ndef run():\n    helper()\n"},
    )

    calls = [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.CALLS]
    assert len(calls) == 1


def _file_of(parse_inventory, path):
    return next(file for file in parse_inventory.files if file.relative_path == path)


def _qualified_names(parse_inventory, path):
    return [symbol.qualified_name for symbol in _file_of(parse_inventory, path).symbols]


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

    # Assert the real upstream metadata first, so a parser regression cannot be
    # mistaken for a graph regression.
    main = _file_of(parsed, "main.py")
    assert main.language is ParsedLanguage.PYTHON
    assert main.status is ParseStatus.SUCCESS
    assert _qualified_names(parsed, "main.py") == ["run"]
    assert _qualified_names(parsed, "pkg/util.py") == ["helper"]
    assert [(item.module, tuple(b.imported_name for b in item.bindings)) for item in main.imports] == [
        ("pkg.util", ("helper",))
    ]
    assert [(item.caller_qualified_name, item.callee_text) for item in main.calls] == [
        ("run", "helper")
    ]

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
    _inventory, parsed, graph = _pipeline(
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

    child = _file_of(parsed, "pkg/Child.java")
    assert child.language is ParsedLanguage.JAVA
    assert _qualified_names(parsed, "pkg/Child.java") == ["pkg.Child"]
    assert _qualified_names(parsed, "pkg/Base.java") == ["pkg.Base"]
    assert _qualified_names(parsed, "pkg/Port.java") == ["pkg.Port"]
    declared = child.symbols[0]
    # The parser records the exact source spelling; Module 6 resolves it against
    # the same-package qualified symbol without altering the metadata.
    assert set(declared.base_types) == {"Base"}
    assert set(declared.implemented_types) == {"Port"}
    # The declaring symbol's own location is the type relationship's location.
    by_name = {
        node.qualified_name: node.node_id
        for node in graph.nodes
        if node.node_kind is GraphNodeKind.SYMBOL
    }
    inherits = [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.INHERITS]
    implements = [
        edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.IMPLEMENTS
    ]
    assert len(inherits) == 1
    assert len(implements) == 1
    assert inherits[0].source_node_id == by_name["pkg.Child"]
    assert inherits[0].target_node_id == by_name["pkg.Base"]
    assert implements[0].source_node_id == by_name["pkg.Child"]
    assert implements[0].target_node_id == by_name["pkg.Port"]
    assert inherits[0].location == declared.location
    assert implements[0].location == declared.location
    assert not graph.unresolved_references


def test_ecmascript_pipeline_produces_import_and_call_edges(tmp_path, monkeypatch):
    _inventory, parsed, graph = _pipeline(
        tmp_path,
        monkeypatch,
        {
            "util.js": "export function helper() {}\n",
            "main.js": 'import { helper } from "./util.js";\nfunction run() { helper(); }\n',
        },
    )

    main = _file_of(parsed, "main.js")
    assert main.language is ParsedLanguage.JAVASCRIPT
    assert _qualified_names(parsed, "main.js") == ["run"]
    assert _qualified_names(parsed, "util.js") == ["helper"]
    assert [(item.module, tuple(b.imported_name for b in item.bindings)) for item in main.imports] == [
        ("./util.js", ("helper",))
    ]
    assert [(item.caller_qualified_name, item.callee_text) for item in main.calls] == [
        ("run", "helper")
    ]
    assert _file_of(parsed, "util.js").symbols[0].modifiers == ("export",)

    by_name = {
        node.qualified_name: node.node_id
        for node in graph.nodes
        if node.node_kind is GraphNodeKind.SYMBOL
    }
    files_by_path = {
        node.relative_path: node.node_id
        for node in graph.nodes
        if node.node_kind is GraphNodeKind.FILE
    }
    calls = [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.CALLS]
    assert len(calls) == 1
    assert calls[0].source_node_id == by_name["run"]
    assert calls[0].target_node_id == by_name["helper"]

    imports = [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.IMPORTS]
    assert len(imports) == 1
    assert imports[0].source_node_id == files_by_path["main.js"]
    assert imports[0].target_node_id == files_by_path["util.js"]
    assert not graph.unresolved_references


def test_jsx_pipeline_is_recorded_as_javascript(tmp_path, monkeypatch):
    """JSX maps to ParsedLanguage.JAVASCRIPT per the approved language mapping."""
    _inventory, parsed, graph = _pipeline(
        tmp_path,
        monkeypatch,
        {
            "widget.jsx": "export function helper() {}\n",
            "view.jsx": (
                'import { helper } from "./widget.jsx";\n'
                "export function View() { return helper(); }\n"
            ),
        },
    )

    view = _file_of(parsed, "view.jsx")
    assert view.language is ParsedLanguage.JAVASCRIPT
    assert _qualified_names(parsed, "view.jsx") == ["View"]
    assert _qualified_names(parsed, "widget.jsx") == ["helper"]
    assert [(item.module, tuple(b.imported_name for b in item.bindings)) for item in view.imports] == [
        ("./widget.jsx", ("helper",))
    ]
    assert [(item.caller_qualified_name, item.callee_text) for item in view.calls] == [
        ("View", "helper")
    ]
    assert ParsedLanguage.JAVASCRIPT in {node.language for node in graph.nodes}

    by_name = {
        node.qualified_name: node.node_id
        for node in graph.nodes
        if node.node_kind is GraphNodeKind.SYMBOL
    }
    files_by_path = {
        node.relative_path: node.node_id
        for node in graph.nodes
        if node.node_kind is GraphNodeKind.FILE
    }
    imports = [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.IMPORTS]
    assert len(imports) == 1
    assert imports[0].source_node_id == files_by_path["view.jsx"]
    assert imports[0].target_node_id == files_by_path["widget.jsx"]

    calls = [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.CALLS]
    assert len(calls) == 1
    assert calls[0].source_node_id == by_name["View"]
    assert calls[0].target_node_id == by_name["helper"]


def test_ambiguous_real_import_produces_no_edge_and_sorted_candidates(
    tmp_path, monkeypatch
):
    """Two eligible module files must yield evidence, never a chosen edge."""
    _inventory, parsed, graph = _pipeline(
        tmp_path,
        monkeypatch,
        {
            "mod.js": "export function helper() {}\n",
            "mod.ts": "export function helper() {}\n",
            "main.js": 'import { helper } from "./mod";\nfunction run() { helper(); }\n',
        },
    )

    main = _file_of(parsed, "main.js")
    assert [(item.module, tuple(b.imported_name for b in item.bindings)) for item in main.imports] == [
        ("./mod", ("helper",))
    ]

    files_by_path = {
        node.relative_path: node.node_id
        for node in graph.nodes
        if node.node_kind is GraphNodeKind.FILE
    }
    mod_js_file = files_by_path["mod.js"]
    mod_ts_file = files_by_path["mod.ts"]
    # There are two eligible helper symbols (one per module file).
    helper_candidates = tuple(
        sorted(
            node.node_id
            for node in graph.nodes
            if node.node_kind is GraphNodeKind.SYMBOL
            and node.qualified_name.endswith("helper")
        )
    )

    unresolved = {
        (item.reference_kind, item.raw_target, item.reason, item.candidate_node_ids)
        for item in graph.unresolved_references
    }
    expected = {
        (
            ReferenceKind.IMPORT,
            "./mod",
            ResolutionFailureReason.AMBIGUOUS,
            tuple(sorted((mod_js_file, mod_ts_file))),
        ),
        (
            ReferenceKind.CALL,
            "helper",
            ResolutionFailureReason.AMBIGUOUS,
            helper_candidates,
        ),
    }
    assert unresolved == expected
    assert len(graph.unresolved_references) == 2

    # Ambiguity must never become an edge of either kind.
    assert not [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.IMPORTS]
    assert not [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.CALLS]
    for item in graph.unresolved_references:
        assert item.candidate_node_ids == tuple(sorted(set(item.candidate_node_ids)))
        if item.reason is ResolutionFailureReason.AMBIGUOUS:
            assert item.candidate_node_ids
        else:
            assert item.candidate_node_ids == ()


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
    _inventory, parsed, graph = _pipeline(
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

    main = _file_of(parsed, "main.py")
    assert [item.module for item in main.imports] == ["requests"]
    assert [(item.caller_qualified_name, item.callee_text) for item in main.calls] == [
        ("run", "requests.get"),
        ("run", "nowhere"),
    ]

    by_target = {
        item.raw_target: item for item in graph.unresolved_references
    }
    # The complete evidence set, asserted exhaustively: an unanchored package is
    # EXTERNAL, a call through that proven external import is EXTERNAL too, and a
    # bare unknown call is NOT_FOUND rather than an asserted external call.
    assert {
        (item.reference_kind, item.raw_target, item.reason, item.candidate_node_ids)
        for item in graph.unresolved_references
    } == {
        (ReferenceKind.IMPORT, "requests", ResolutionFailureReason.EXTERNAL, ()),
        (ReferenceKind.CALL, "requests.get", ResolutionFailureReason.EXTERNAL, ()),
        (ReferenceKind.CALL, "nowhere", ResolutionFailureReason.NOT_FOUND, ()),
    }
    assert by_target["requests"].reference_kind is ReferenceKind.IMPORT
    assert not [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.IMPORTS]
    assert not [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.CALLS]


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

    bad = _file_of(parsed, "bad.py")
    assert bad.status is ParseStatus.FAILED
    # A failed record carries no extracted declarations.
    assert bad.symbols == () and bad.imports == () and bad.calls == ()

    failed = [node for node in graph.nodes if node.parse_status is ParseStatus.FAILED]
    assert [node.relative_path for node in failed] == ["bad.py"]
    assert all(node.node_kind is GraphNodeKind.FILE for node in failed)
    assert all(node.qualified_name is None for node in failed)
    assert all(node.symbol_kind is None for node in failed)
    # No fabricated references, and the healthy file is unaffected.
    assert [item.relative_path for item in graph.unresolved_references] == []
    assert "good" in {
        node.qualified_name
        for node in graph.nodes
        if node.node_kind is GraphNodeKind.SYMBOL
    }


def _write_bytes_repository(tmp_path, files, namespace="integration-repo"):
    """Write raw bytes, bypassing the newline and encoding translation of text IO."""
    for relative, payload in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    file_inventory = FileScanner().scan(tmp_path, repository_namespace=namespace)
    parse_inventory = CodeParser().parse_inventory(file_inventory)
    return file_inventory, parse_inventory


def test_bom_adjusted_byte_offsets_are_preserved(tmp_path, monkeypatch):
    """A UTF-8 BOM shifts every original-file byte offset by three bytes."""
    source = b"def helper():\n    return 1\n"
    _inventory, parsed = _write_bytes_repository(
        tmp_path,
        {
            "bom.py": b"\xef\xbb\xbf" + source,
            "plain.py": source,
        },
    )

    bom_file = _file_of(parsed, "bom.py")
    plain_file = _file_of(parsed, "plain.py")
    assert [symbol.qualified_name for symbol in bom_file.symbols] == [
        symbol.qualified_name for symbol in plain_file.symbols
    ]
    assert bom_file.symbols[0].location.start_byte == (
        plain_file.symbols[0].location.start_byte + 3
    )
    assert bom_file.symbols[0].location.start_byte == 3

    graph = _rebuild(tmp_path, parsed, monkeypatch)

    by_path = {
        node.relative_path: node
        for node in graph.nodes
        if node.node_kind is GraphNodeKind.SYMBOL
    }
    assert by_path["bom.py"].location.start_byte == 3
    assert by_path["plain.py"].location.start_byte == 0
    # Identity is never derived from an absolute path or an encoding artefact.
    assert by_path["bom.py"].repository_namespace == by_path["plain.py"].repository_namespace


def _rebuild(tmp_path, parsed, monkeypatch):
    file_inventory = FileScanner().scan(
        tmp_path, repository_namespace="integration-repo"
    )
    with monkeypatch.context() as guard:
        _guard_source_reads(guard)
        return build_code_graph(file_inventory, parsed)


def test_typescript_bom_is_recorded_as_typescript(tmp_path, monkeypatch):
    _inventory, parsed = _write_bytes_repository(
        tmp_path,
        {"mod.ts": b"\xef\xbb\xbfexport function helper() {}\n"},
    )

    mod = _file_of(parsed, "mod.ts")
    assert mod.language is ParsedLanguage.TYPESCRIPT
    # Byte offsets are original-file coordinates including the BOM, so the
    # declaration starts after the three BOM bytes and its `export ` modifier.
    assert mod.symbols[0].location.start_byte == 10
    assert mod.symbols[0].location.start_column == 10


def test_unicode_paths_are_preserved_verbatim(tmp_path, monkeypatch):
    _inventory, _parsed, graph = _pipeline(
        tmp_path,
        monkeypatch,
        {"caf\u00e9/na\u00efve.py": "def helper():\n    return '\u2713'\n"},
    )

    paths = {node.relative_path for node in graph.nodes}
    assert "caf\u00e9/na\u00efve.py" in paths
    names = {
        node.name for node in graph.nodes if node.node_kind is GraphNodeKind.SYMBOL
    }
    assert "helper" in names
    # Identities stay lowercase hex of the exact upstream spelling.
    assert all(len(node.node_id) == 64 for node in graph.nodes)
    assert all(node.node_id == node.node_id.lower() for node in graph.nodes)


def test_repeated_builds_are_deterministic_and_isolated(tmp_path, monkeypatch):
    _inventory, _parsed, first = _pipeline(
        tmp_path,
        monkeypatch,
        {"a.py": "def run():\n    helper()\n\ndef helper():\n    return 1\n"},
    )
    original_nodes = copy.deepcopy(first.nodes)

    _inventory_two, _parsed_two, same_input = _pipeline(
        tmp_path,
        monkeypatch,
        {"a.py": "def run():\n    helper()\n\ndef helper():\n    return 1\n"},
    )

    _inventory_three, _parsed_three, second = _pipeline(
        tmp_path,
        monkeypatch,
        {"b.py": "def other():\n    return 2\n"},
    )

    # Two builds of identical input produce an equal but distinct inventory.
    assert first == same_input
    assert first is not same_input

    # A later, different build cannot alter the first graph.
    assert first.nodes == original_nodes
    assert first.nodes is not original_nodes
    assert first != second
    # A later build of a different repository must not alter the first graph.
    assert first.nodes_by_qualified_name.get("other") is None
    assert "a.py" in first.nodes_by_file
    assert "b.py" not in first.nodes_by_file
    assert first.nodes_by_file is not second.nodes_by_file
    assert "b.py" in second.nodes_by_file
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

    with pytest.raises(FrozenInstanceError):
        graph.nodes = ()


def test_graph_build_makes_no_network_call(tmp_path, monkeypatch):
    import socket

    calls = []

    def record(*args, **kwargs):
        calls.append(args)
        raise AssertionError("network access during graph build")

    with monkeypatch.context() as guard:
        for owner, name in (
            (socket, "socket"),
            (socket, "create_connection"),
            (socket, "getaddrinfo"),
        ):
            guard.setattr(owner, name, record)
        _inventory, _parsed, graph = _pipeline(
            tmp_path, monkeypatch, {"a.py": "def run():\n    return 1\n"}
        )

    assert calls == []
    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1


def test_public_exports_are_exactly_the_specified_surface():
    """Four models, four enums, five exceptions, two builder entry points."""
    assert set(code_graph.__all__) == {
        "CodeGraphBuilder",
        "CodeGraphError",
        "CodeGraphIdentityError",
        "CodeGraphInvariantError",
        "CodeGraphResolutionError",
        "CodeGraphValidationError",
        "CodeGraphInventory",
        "GraphEdge",
        "GraphNode",
        "UnresolvedReference",
        "GraphEdgeKind",
        "GraphNodeKind",
        "ReferenceKind",
        "ResolutionFailureReason",
        "build_code_graph",
    }
    assert len(code_graph.__all__) == 15
    assert len(set(code_graph.__all__)) == len(code_graph.__all__)

    models = {CodeGraphInventory, GraphEdge, GraphNode, UnresolvedReference}
    enums = {GraphEdgeKind, GraphNodeKind, ReferenceKind, ResolutionFailureReason}
    exceptions = {
        code_graph.CodeGraphError,
        code_graph.CodeGraphIdentityError,
        code_graph.CodeGraphInvariantError,
        code_graph.CodeGraphResolutionError,
        code_graph.CodeGraphValidationError,
    }
    for value in models | enums | exceptions | {code_graph.CodeGraphBuilder, build_code_graph}:
        assert getattr(code_graph, value.__name__) is value
    # Exactly one direct public subclass level below the root.
    for exception in exceptions - {code_graph.CodeGraphError}:
        assert exception.__bases__ == (code_graph.CodeGraphError,)


def test_public_entry_points_take_only_two_inventories(tmp_path, monkeypatch):
    import inspect

    from backend.code_graph import CodeGraphBuilder

    for function in (build_code_graph, CodeGraphBuilder().build):
        parameters = inspect.signature(function).parameters
        assert list(parameters) == ["file_inventory", "parse_inventory"]

    _inventory, parsed, graph = _pipeline(
        tmp_path, monkeypatch, {"a.py": "def run():\n    return 1\n"}
    )

    assert isinstance(graph, CodeGraphInventory)
    assert isinstance(CodeGraphBuilder().build(_inventory, parsed), CodeGraphInventory)


@pytest.mark.parametrize(
    "keyword",
    [
        "repository_path",
        "path",
        "repository_root",
        "source_reader",
        "read_source",
        "callback",
        "scanner",
    ],
)
def test_public_entry_points_reject_path_and_callback_keywords(tmp_path, keyword):
    """No repository path or source-reading callback may be supplied."""
    from backend.code_graph import CodeGraphBuilder

    _inventory, parsed, _graph = _pipeline_unguarded(tmp_path)

    with pytest.raises(TypeError):
        build_code_graph(_inventory, parsed, **{keyword: tmp_path})
    with pytest.raises(TypeError):
        CodeGraphBuilder().build(_inventory, parsed, **{keyword: tmp_path})


def _pipeline_unguarded(tmp_path):
    (tmp_path / "a.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    file_inventory = FileScanner().scan(
        tmp_path, repository_namespace="integration-repo"
    )
    return file_inventory, CodeParser().parse_inventory(file_inventory), None