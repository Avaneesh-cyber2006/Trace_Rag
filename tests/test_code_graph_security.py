"""Security boundaries for graph construction from prepared metadata only."""

import ast
import builtins
from dataclasses import FrozenInstanceError
import importlib
import io
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path, PurePosixPath
from types import MappingProxyType

import pytest

from backend.code_graph import build_code_graph
from backend.code_graph.identities import file_node_id, symbol_node_id
from backend.code_graph.models import (
    CodeGraphInventory,
    GraphEdgeKind,
    GraphNode,
    GraphNodeKind,
    ReferenceKind,
    ResolutionFailureReason,
)
from backend.code_parser.models import (
    CallKind,
    CallSite,
    ImportBinding,
    ImportInfo,
    ParseIssue,
    ParseIssueKind,
    ParseStatus,
    ParsedLanguage,
    SymbolKind,
)


def _forbid(*args, **kwargs):
    raise AssertionError("Module 6 crossed the in-memory boundary")


def _patch_io(guard):
    """Patch filesystem, process, import, and network APIs after imports."""
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
        (Path, "open"),
        (Path, "read_text"),
        (Path, "read_bytes"),
        (Path, "write_text"),
        (Path, "write_bytes"),
        (Path, "stat"),
        (Path, "resolve"),
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


def _build_under_guard(monkeypatch, pair):
    with monkeypatch.context() as guard:
        _patch_io(guard)
        return build_code_graph(*pair)


def test_graph_build_has_no_io(cg, monkeypatch):
    pair = cg.pair(cg.parsed(symbols=(cg.symbol("run"),)))

    graph = _build_under_guard(monkeypatch, pair)

    assert len(graph.nodes) == 2


def test_repeated_public_builds_are_equal_fresh_in_memory_results(cg, monkeypatch):
    pair = cg.pair(cg.parsed(symbols=(cg.symbol("run"),)))

    first = _build_under_guard(monkeypatch, pair)
    second = _build_under_guard(monkeypatch, pair)

    assert first == second
    assert first is not second
    assert first.nodes_by_file is not second.nodes_by_file
    with pytest.raises(FrozenInstanceError):
        first.nodes = ()
    with pytest.raises(TypeError):
        first.nodes_by_file["injected.py"] = ()


def test_caller_inputs_are_immutable_and_later_builds_do_not_change_prior_graph(
    cg, monkeypatch
):
    source = cg.parsed(symbols=(cg.symbol("first"),))
    pair = cg.pair(source)
    file_inventory, parse_inventory = pair
    first = _build_under_guard(monkeypatch, pair)
    original_nodes = first.nodes

    with pytest.raises(FrozenInstanceError):
        parse_inventory.files[0].symbols = ()
    with pytest.raises(FrozenInstanceError):
        parse_inventory.files[0].symbols[0].name = "changed"
    with pytest.raises(FrozenInstanceError):
        file_inventory.files[0].relative_path = "changed.py"

    different = cg.pair(cg.parsed(symbols=(cg.symbol("second"),)))
    second = _build_under_guard(monkeypatch, different)

    assert first.nodes == original_nodes
    assert first != second


def _edge_pairs(graph):
    return {
        (edge.edge_kind, edge.source_node_id, edge.target_node_id)
        for edge in graph.edges
    }


def _unresolved_index(graph):
    return {
        (item.reference_kind, item.relative_path, item.raw_target): (
            item.reason,
            item.candidate_node_ids,
            item.source_node_id,
        )
        for item in graph.unresolved_references
    }


def _java_file(cg, path, **kwargs):
    return cg.parsed(path, language=ParsedLanguage.JAVA, **kwargs)


def test_python_fixture_resolves_import_call_and_inherits_exactly(cg, monkeypatch):
    """Python metadata proves an IMPORTS, a CALLS and an INHERITS edge."""
    item = ImportInfo(
        "pkg.util",
        (ImportBinding("run", "execute"),),
        False,
        (),
        cg.loc(20, 45),
    )
    caller = cg.symbol("main", start=100, end=300)
    base = cg.symbol("Base", kind=SymbolKind.CLASS, start=400, end=500)
    child = cg.symbol(
        "Child",
        kind=SymbolKind.CLASS,
        start=600,
        end=700,
        base_types=("Base",),
    )
    call = CallSite("main", "execute", CallKind.CALL, cg.loc(150, 160))
    source = cg.parsed(
        "main.py",
        symbols=(caller, base, child),
        imports=(item,),
        calls=(call,),
    )
    imported = cg.symbol("run", start=5, end=15)
    target = cg.parsed("pkg/util.py", symbols=(imported,))

    graph = _build_under_guard(monkeypatch, cg.pair(source, target))

    assert (
        GraphEdgeKind.IMPORTS,
        file_node_id("repo", "main.py"),
        file_node_id("repo", "pkg/util.py"),
    ) in _edge_pairs(graph)
    assert (
        GraphEdgeKind.CALLS,
        symbol_node_id("repo", "main.py", caller),
        symbol_node_id("repo", "pkg/util.py", imported),
    ) in _edge_pairs(graph)
    assert (
        GraphEdgeKind.INHERITS,
        symbol_node_id("repo", "main.py", child),
        symbol_node_id("repo", "main.py", base),
    ) in _edge_pairs(graph)
    assert not [
        item
        for item in graph.unresolved_references
        if item.reference_kind is ReferenceKind.IMPORT
    ]


def test_python_unresolved_reference_reasons_are_exact(cg, monkeypatch):
    """Python keeps NOT_FOUND and EXTERNAL with their exact evidence."""
    caller = cg.symbol("main", start=100, end=300)
    missing_call = CallSite("main", "absent_helper", CallKind.CALL, cg.loc(150, 160))
    external = ImportInfo("thirdparty", (), False, (), cg.loc(10, 30))
    source = cg.parsed(
        "main.py",
        symbols=(caller,),
        imports=(external,),
        calls=(missing_call,),
    )

    graph = _build_under_guard(monkeypatch, cg.pair(source))

    index = _unresolved_index(graph)
    assert index[ReferenceKind.CALL, "main.py", "absent_helper"] == (
        ResolutionFailureReason.NOT_FOUND,
        (),
        symbol_node_id("repo", "main.py", caller),
    )
    assert index[ReferenceKind.IMPORT, "main.py", "thirdparty"] == (
        ResolutionFailureReason.EXTERNAL,
        (),
        file_node_id("repo", "main.py"),
    )
    assert not [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.IMPORTS]
    assert not [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.CALLS]


def test_java_fixture_resolves_import_call_inherits_and_implements_exactly(
    cg, monkeypatch
):
    """Java qualified metadata proves all four provable relationship edges."""
    service = cg.symbol(
        "Service", kind=SymbolKind.CLASS, qualified_name="p.Service"
    )
    imported_file = _java_file(cg, "Service.java", symbols=(service,))
    item = ImportInfo("p.Service", (), False, (), cg.loc(1, 10))

    owner = cg.symbol("A", kind=SymbolKind.CLASS, qualified_name="p.A", end=500)
    caller = cg.symbol(
        "run",
        kind=SymbolKind.METHOD,
        qualified_name="p.A.run",
        parent="p.A",
        start=10,
        end=90,
    )
    target_method = cg.symbol(
        "m",
        kind=SymbolKind.METHOD,
        qualified_name="p.A.m",
        parent="p.A",
        start=100,
        end=150,
    )
    call = CallSite("p.A.run", "m", CallKind.CALL, cg.loc(20, 30))

    base = cg.symbol("Base", kind=SymbolKind.CLASS, start=600, end=700)
    child = cg.symbol(
        "Child",
        kind=SymbolKind.CLASS,
        start=800,
        end=900,
        base_types=("Base",),
    )
    port = cg.symbol("Port", kind=SymbolKind.INTERFACE, start=1000, end=1100)
    adapter = cg.symbol(
        "Adapter",
        kind=SymbolKind.CLASS,
        start=1200,
        end=1300,
        implemented_types=("Port",),
    )
    main = _java_file(
        cg,
        "Main.java",
        symbols=(owner, caller, target_method, base, child, port, adapter),
        imports=(item,),
        calls=(call,),
    )

    graph = _build_under_guard(monkeypatch, cg.pair(main, imported_file))

    pairs = _edge_pairs(graph)
    assert (
        GraphEdgeKind.IMPORTS,
        file_node_id("repo", "Main.java"),
        file_node_id("repo", "Service.java"),
    ) in pairs
    assert (
        GraphEdgeKind.CALLS,
        symbol_node_id("repo", "Main.java", caller),
        symbol_node_id("repo", "Main.java", target_method),
    ) in pairs
    assert (
        GraphEdgeKind.INHERITS,
        symbol_node_id("repo", "Main.java", child),
        symbol_node_id("repo", "Main.java", base),
    ) in pairs
    assert (
        GraphEdgeKind.IMPLEMENTS,
        symbol_node_id("repo", "Main.java", adapter),
        symbol_node_id("repo", "Main.java", port),
    ) in pairs
    assert not graph.unresolved_references


def test_java_unresolved_import_reason_is_exact(cg, monkeypatch):
    """An unanchored Java package import is EXTERNAL, never a fabricated edge."""
    item = ImportInfo("absent.pkg.Thing", (), False, (), cg.loc(1, 10))
    main = _java_file(cg, "Main.java", imports=(item,))

    graph = _build_under_guard(monkeypatch, cg.pair(main))

    index = _unresolved_index(graph)
    assert index[ReferenceKind.IMPORT, "Main.java", "absent.pkg.Thing"] == (
        ResolutionFailureReason.EXTERNAL,
        (),
        file_node_id("repo", "Main.java"),
    )
    assert not [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.IMPORTS]


@pytest.mark.parametrize(
    "path,language",
    [
        pytest.param("main.js", ParsedLanguage.JAVASCRIPT, id="javascript"),
        pytest.param("main.jsx", ParsedLanguage.JAVASCRIPT, id="jsx"),
        pytest.param("main.ts", ParsedLanguage.TYPESCRIPT, id="typescript"),
        pytest.param("main.tsx", ParsedLanguage.TSX, id="tsx"),
    ],
)
def test_ecmascript_fixture_resolves_import_and_call_exactly(
    cg, monkeypatch, path, language
):
    """ECMAScript relative import plus exported callable proves both edges."""
    suffix = PurePosixPath(path).suffix
    item = ImportInfo(
        f"./util{suffix}",
        (ImportBinding("run", "execute"),),
        False,
        (),
        cg.loc(0, 15),
    )
    caller = cg.symbol("main", start=20, end=100)
    call = CallSite("main", "execute", CallKind.CALL, cg.loc(50, 60))
    source = cg.parsed(
        path, language=language, symbols=(caller,), imports=(item,), calls=(call,)
    )
    exported = cg.symbol("run", modifiers=("export",))
    target = cg.parsed(
        f"util{suffix}", language=language, symbols=(exported,)
    )

    graph = _build_under_guard(monkeypatch, cg.pair(source, target))

    pairs = _edge_pairs(graph)
    assert (
        GraphEdgeKind.IMPORTS,
        file_node_id("repo", path),
        file_node_id("repo", f"util{suffix}"),
    ) in pairs
    assert (
        GraphEdgeKind.CALLS,
        symbol_node_id("repo", path, caller),
        symbol_node_id("repo", f"util{suffix}", exported),
    ) in pairs
    assert not graph.unresolved_references


def test_ecmascript_extends_and_typescript_implements_resolve_exactly(
    cg, monkeypatch
):
    """ECMAScript extends and TypeScript implements both prove typed edges."""
    base = cg.symbol("Base", kind=SymbolKind.CLASS, start=0, end=50)
    child = cg.symbol(
        "Child",
        kind=SymbolKind.CLASS,
        start=60,
        end=160,
        base_types=("Base",),
    )
    js_file = cg.parsed("local.js", language=ParsedLanguage.JAVASCRIPT,
                        symbols=(base, child))
    js_graph = _build_under_guard(monkeypatch, cg.pair(js_file))
    assert (
        GraphEdgeKind.INHERITS,
        symbol_node_id("repo", "local.js", child),
        symbol_node_id("repo", "local.js", base),
    ) in _edge_pairs(js_graph)

    port = cg.symbol("Port", kind=SymbolKind.INTERFACE, start=0, end=50)
    adapter = cg.symbol(
        "Adapter",
        kind=SymbolKind.CLASS,
        start=60,
        end=160,
        implemented_types=("Port",),
    )
    ts_file = cg.parsed("a.ts", language=ParsedLanguage.TYPESCRIPT,
                        symbols=(port, adapter))
    ts_graph = _build_under_guard(monkeypatch, cg.pair(ts_file))
    assert (
        GraphEdgeKind.IMPLEMENTS,
        symbol_node_id("repo", "a.ts", adapter),
        symbol_node_id("repo", "a.ts", port),
    ) in _edge_pairs(ts_graph)
    assert not js_graph.unresolved_references
    assert not ts_graph.unresolved_references


def test_ecmascript_unresolved_reasons_are_exact(cg, monkeypatch):
    """ECMAScript keeps EXTERNAL and sorted AMBIGUOUS evidence exactly."""
    external = ImportInfo("lodash", (), False, (), cg.loc(0, 10))
    ambiguous = ImportInfo("./dep", (), False, (), cg.loc(12, 22))
    caller = cg.symbol("main", start=20, end=100)
    source = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(caller,),
        imports=(external, ambiguous),
    )
    first = cg.parsed("dep.js", language=ParsedLanguage.JAVASCRIPT)
    second = cg.parsed("dep.ts", language=ParsedLanguage.TYPESCRIPT)

    graph = _build_under_guard(monkeypatch, cg.pair(source, first, second))

    index = _unresolved_index(graph)
    assert index[ReferenceKind.IMPORT, "main.js", "lodash"] == (
        ResolutionFailureReason.EXTERNAL,
        (),
        file_node_id("repo", "main.js"),
    )
    reason, candidates, source_id = index[ReferenceKind.IMPORT, "main.js", "./dep"]
    assert reason is ResolutionFailureReason.AMBIGUOUS
    assert candidates == tuple(
        sorted((file_node_id("repo", "dep.js"), file_node_id("repo", "dep.ts")))
    )
    assert source_id == file_node_id("repo", "main.js")
    assert not [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.IMPORTS]


def test_ecmascript_unsupported_alias_is_not_turned_into_an_edge(cg, monkeypatch):
    """A configured alias form stays UNSUPPORTED rather than becoming a guess."""
    alias = ImportInfo("@/util", (), False, (), cg.loc(0, 10))
    source = cg.parsed(
        "main.ts", language=ParsedLanguage.TYPESCRIPT, imports=(alias,)
    )
    target = cg.parsed("util.ts", language=ParsedLanguage.TYPESCRIPT)

    graph = _build_under_guard(monkeypatch, cg.pair(source, target))

    index = _unresolved_index(graph)
    assert index[ReferenceKind.IMPORT, "main.ts", "@/util"] == (
        ResolutionFailureReason.UNSUPPORTED,
        (),
        file_node_id("repo", "main.ts"),
    )
    assert not [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.IMPORTS]


def _file_node(path, namespace="repo"):
    return GraphNode(
        node_id=file_node_id(namespace, path),
        node_kind=GraphNodeKind.FILE,
        repository_namespace=namespace,
        relative_path=path,
        language=ParsedLanguage.PYTHON,
        parse_status=ParseStatus.SUCCESS,
        symbol_kind=None,
        name=None,
        qualified_name=None,
        location=None,
    )


PUBLIC_INDEX_NAMES = (
    "outgoing_edges",
    "incoming_edges",
    "nodes_by_file",
    "nodes_by_qualified_name",
)


def test_published_inventory_is_frozen_and_slotted(cg, monkeypatch):
    graph = _build_under_guard(monkeypatch, cg.pair(cg.parsed(symbols=(cg.symbol("run"),))))

    assert graph.__dataclass_params__.frozen
    assert not hasattr(graph, "__dict__")
    for name in ("repository_namespace", "nodes", "edges", "unresolved_references"):
        with pytest.raises(FrozenInstanceError):
            setattr(graph, name, ())


def test_canonical_record_tuples_reject_append_and_replacement(cg, monkeypatch):
    graph = _build_under_guard(monkeypatch, cg.pair(cg.parsed(symbols=(cg.symbol("run"),))))

    for name in ("nodes", "edges", "unresolved_references"):
        record = getattr(graph, name)
        assert type(record) is tuple
        with pytest.raises(AttributeError):
            record.append(None)
        with pytest.raises(TypeError):
            record[0] = None


def test_every_public_index_rejects_mutation_and_hides_its_backing_store(
    cg, monkeypatch
):
    graph = _build_under_guard(monkeypatch, cg.pair(cg.parsed(symbols=(cg.symbol("run"),))))

    for name in PUBLIC_INDEX_NAMES:
        index = getattr(graph, name)
        assert isinstance(index, MappingProxyType), name
        with pytest.raises(TypeError):
            index["injected"] = ()
        for mutator in ("pop", "popitem", "clear", "update", "setdefault", "__setitem__"):
            assert not hasattr(index, mutator), (name, mutator)
        leaked = [
            attribute
            for attribute in dir(index)
            if attribute in {"source", "_mapping", "_data", "_snapshot", "mapping"}
        ]
        assert leaked == [], name


def test_published_indexes_do_not_retain_a_caller_owned_mutable_mapping():
    node = _file_node("a.py")
    backing = {"a.py": [node]}
    edges_backing = {file_node_id("repo", "a.py"): []}

    inventory = CodeGraphInventory(
        repository_namespace="repo",
        nodes=(node,),
        edges=(),
        unresolved_references=(),
        outgoing_edges=edges_backing,
        incoming_edges=edges_backing,
        nodes_by_file=backing,
        nodes_by_qualified_name={},
    )

    backing["a.py"].append(_file_node("b.py"))
    backing["c.py"] = []
    edges_backing[file_node_id("repo", "a.py")].append("injected")

    assert inventory.nodes_by_file["a.py"] == (node,)
    assert "c.py" not in inventory.nodes_by_file
    assert inventory.outgoing_edges[file_node_id("repo", "a.py")] == ()


def test_unresolved_candidate_ids_are_immutable_and_sorted_evidence(cg, monkeypatch):
    source = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        imports=(ImportInfo("./dep", (), False, (), cg.loc(0, 10)),),
    )
    first = cg.parsed("dep.js", language=ParsedLanguage.JAVASCRIPT)
    second = cg.parsed("dep.ts", language=ParsedLanguage.TYPESCRIPT)

    graph = _build_under_guard(monkeypatch, cg.pair(source, first, second))

    (reference,) = [
        item
        for item in graph.unresolved_references
        if item.reference_kind is ReferenceKind.IMPORT
    ]
    assert reference.reason is ResolutionFailureReason.AMBIGUOUS
    assert type(reference.candidate_node_ids) is tuple
    assert list(reference.candidate_node_ids) == sorted(reference.candidate_node_ids)
    assert set(reference.candidate_node_ids) == {
        file_node_id("repo", "dep.js"),
        file_node_id("repo", "dep.ts"),
    }
    with pytest.raises(AttributeError):
        reference.candidate_node_ids.append("x")
    with pytest.raises(TypeError):
        reference.candidate_node_ids[0] = "x"
    with pytest.raises(FrozenInstanceError):
        reference.reason = ResolutionFailureReason.NOT_FOUND


def test_separate_builds_publish_disjoint_indexes_and_ignore_later_input(
    cg, monkeypatch
):
    first_input = cg.parsed("a.py", symbols=(cg.symbol("alpha"),))
    second_input = cg.parsed("b.py", symbols=(cg.symbol("beta"),))

    first = _build_under_guard(monkeypatch, cg.pair(first_input))
    first_nodes = first.nodes
    first_index = dict(first.nodes_by_file)

    second = _build_under_guard(monkeypatch, cg.pair(second_input))

    for name in PUBLIC_INDEX_NAMES:
        assert getattr(first, name) is not getattr(second, name), name
    assert first is not second
    assert first.nodes == first_nodes
    assert dict(first.nodes_by_file) == first_index
    assert first.nodes != second.nodes
    assert first.nodes_by_qualified_name.get("beta") is None


def test_every_symbol_and_file_node_kind_is_published(cg, monkeypatch):
    """Publication keeps only FILE and SYMBOL nodes under one namespace."""
    source = cg.parsed(
        "main.py",
        symbols=(cg.symbol("run"), cg.symbol("K", kind=SymbolKind.CLASS)),
    )

    graph = _build_under_guard(monkeypatch, cg.pair(source))

    assert {node.node_kind for node in graph.nodes} == {
        GraphNodeKind.FILE,
        GraphNodeKind.SYMBOL,
    }
    assert {node.repository_namespace for node in graph.nodes} == {"repo"}


def test_ambiguous_import_and_partial_parse_stay_in_memory(cg, monkeypatch):
    source = cg.parsed(
        "main.ts",
        language=ParsedLanguage.TYPESCRIPT,
        status=ParseStatus.PARTIAL,
        imports=(ImportInfo("./dep", (), False, (), cg.loc(1, 10)),),
        issues=(
            ParseIssue(ParseIssueKind.SYNTAX_ERROR, "truncated", cg.loc(200, 210)),
        ),
    )
    first = cg.parsed("dep.js", language=ParsedLanguage.JAVASCRIPT)
    second = cg.parsed("dep.ts", language=ParsedLanguage.TYPESCRIPT)

    graph = _build_under_guard(monkeypatch, cg.pair(source, first, second))

    assert any(node.parse_status is ParseStatus.PARTIAL for node in graph.nodes)
    assert any(
        item.reason.value == "ambiguous" for item in graph.unresolved_references
    )


@pytest.mark.parametrize(
    "raw_target",
    [
        "eval(\"__import__('os').system('whoami')\")",
        "powershell -Command Start-Process calc.exe",
        "https://example.invalid/.env",
        "../.env",
        r"C:\\repo\\..\\secret.py",
    ],
)
def test_hostile_call_text_remains_inert_and_exact(cg, monkeypatch, raw_target):
    caller = cg.symbol("run")
    source = cg.parsed(
        symbols=(caller,),
        calls=(CallSite("run", raw_target, CallKind.CALL, cg.loc(20, 30)),),
    )

    graph = _build_under_guard(monkeypatch, cg.pair(source))

    assert [item.raw_target for item in graph.unresolved_references] == [raw_target]


def _module_name_for(path):
    return "backend." + ".".join(
        path.with_suffix("").relative_to(Path("backend")).parts
    )


def _imported_modules(tree, module_name):
    package = module_name.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                yield item.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package_parts = package.split(".") if package else []
                keep = len(package_parts) - (node.level - 1)
                base_parts = package_parts[:keep]
                if node.module:
                    base_parts.extend(node.module.split("."))
                base = ".".join(base_parts)
                yield base
                if node.module is None:
                    for item in node.names:
                        if item.name != "*":
                            yield f"{base}.{item.name}".strip(".")
            else:
                yield node.module or ""


def _is_allowed_module(module_name):
    if module_name.split(".", 1)[0] in sys.stdlib_module_names:
        return True
    if module_name == "backend.code_graph" or module_name.startswith(
        "backend.code_graph."
    ):
        return True
    return module_name in {
        "backend.file_scanner.models",
        "backend.code_parser.models",
    }


def _forbidden_calls(tree, module_name):
    unsafe_deserializers = {
        "pickle.load",
        "pickle.loads",
        "marshal.load",
        "marshal.loads",
        "yaml.load",
        "joblib.load",
        "cloudpickle.loads",
        "dill.load",
        "dill.loads",
        "jsonpickle.decode",
        "torch.load",
        "numpy.load",
        "np.load",
    }
    forbidden_io = {
        "open",
        "read_text",
        "read_bytes",
        "iterdir",
        "glob",
        "rglob",
        "resolve",
        "stat",
        "scandir",
        "listdir",
        "walk",
        "run",
        "Popen",
        "popen",
        "call",
        "check_call",
        "check_output",
        "system",
        "socket",
        "connect",
        "create_connection",
        "import_module",
        "__import__",
        "write_text",
        "write_bytes",
        "unlink",
        "remove",
        "rename",
        "replace",
        "mkdir",
        "makedirs",
        "rmdir",
        "removedirs",
        "touch",
        "chmod",
        "symlink",
        "symlink_to",
        "link",
        "hardlink_to",
        "truncate",
        "fdopen",
    }
    recursive_mutations = {
        "shutil.rmtree",
        "shutil.move",
        "shutil.copy",
        "shutil.copy2",
        "shutil.copyfile",
        "shutil.copymode",
        "shutil.copystat",
        "shutil.copytree",
        "tempfile.mkstemp",
        "tempfile.mkdtemp",
        "tempfile.NamedTemporaryFile",
        "tempfile.TemporaryFile",
        "tempfile.NamedTemporaryDirectory",
    }
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split(".", 1)[0]] = item.name
        elif isinstance(node, ast.ImportFrom):
            base = next(
                _imported_modules(
                    ast.Module(body=[node], type_ignores=[]), module_name
                ),
                "",
            )
            for item in node.names:
                if item.name != "*":
                    aliases[item.asname or item.name] = f"{base}.{item.name}".strip(".")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = []
        current = node.func
        while isinstance(current, ast.Attribute):
            dotted.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            dotted.append(current.id)
        dotted_name = ".".join(reversed(dotted))
        parts = dotted_name.split(".") if dotted_name else []
        if parts and parts[0] in aliases:
            parts = aliases[parts[0]].split(".") + parts[1:]
        resolved_name = ".".join(parts)
        leaf_name = parts[-1] if parts else ""
        path_replace = False
        if (
            leaf_name == "replace"
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Call)
        ):
            constructor = node.func.value.func
            constructor_name = (
                constructor.id
                if isinstance(constructor, ast.Name)
                else ""
            )
            if constructor_name in aliases:
                constructor_name = aliases[constructor_name]
            path_replace = constructor_name in {"pathlib.Path", "Path"}
        if (
            resolved_name in {"eval", "exec", "builtins.eval", "builtins.exec"}
            or (leaf_name in forbidden_io and leaf_name != "replace")
            or resolved_name == "os.replace"
            or resolved_name in recursive_mutations
            or path_replace
            or resolved_name in unsafe_deserializers
        ):
            yield resolved_name or ("pathlib.Path.replace" if path_replace else leaf_name)


def _audit_code_graph_sources(root):
    violations = []
    for source_path in sorted((root / "backend" / "code_graph").rglob("*.py")):
        module_name = _module_name_for(source_path.relative_to(root))
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"), filename=str(source_path)
        )
        for imported in _imported_modules(tree, module_name):
            if not _is_allowed_module(imported):
                violations.append(f"{source_path}: forbidden import {imported}")
        for call_name in _forbidden_calls(tree, module_name):
            violations.append(f"{source_path}: forbidden call {call_name}")
    return violations


def test_dependency_audit_rejects_module_4_or_5_imports_in_memory():
    tree = ast.parse("from backend.embedding_vector_store import VectorStore")

    imported = tuple(_imported_modules(tree, "backend.code_graph.audit"))

    assert imported
    assert any(not _is_allowed_module(module) for module in imported)


def test_dependency_audit_rejects_forbidden_calls_and_aliased_deserialization():
    tree = ast.parse("import pickle as codec\ncodec.loads(payload)\n")

    calls = tuple(_forbidden_calls(tree, "backend.code_graph.audit"))

    assert calls == ("pickle.loads",)


def test_dependency_audit_rejects_filesystem_mutation_calls_in_memory():
    tree = ast.parse(
        "import os as filesystem\n"
        "filesystem.unlink('source.py')\n"
        "filesystem.replace('source.py', 'other.py')\n"
        "from pathlib import Path as P\n"
        "P('source.py').write_text('changed')\n"
        "P('source.py').replace('other.py')\n"
    )

    calls = tuple(_forbidden_calls(tree, "backend.code_graph.audit"))

    assert "os.unlink" in calls
    assert "os.replace" in calls
    assert "write_text" in calls
    assert "replace" in calls


def test_dependency_audit_rejects_file_writing_primitives_in_memory():
    tree = ast.parse(
        "import os\n"
        "os.truncate('source.py', 0)\n"
        "os.fdopen(3, 'w')\n"
    )

    calls = tuple(_forbidden_calls(tree, "backend.code_graph.audit"))

    assert "os.truncate" in calls
    assert "os.fdopen" in calls


def test_dependency_audit_rejects_recursive_move_and_copy_helpers_in_memory():
    tree = ast.parse(
        "import shutil as fs\n"
        "fs.rmtree('tree')\n"
        "fs.move('a', 'b')\n"
        "fs.copytree('a', 'b')\n"
        "import tempfile\n"
        "tempfile.mkstemp()\n"
        "tempfile.mkdtemp()\n"
    )

    calls = tuple(_forbidden_calls(tree, "backend.code_graph.audit"))

    assert "shutil.rmtree" in calls
    assert "shutil.move" in calls
    assert "shutil.copytree" in calls
    assert "tempfile.mkstemp" in calls
    assert "tempfile.mkdtemp" in calls


def test_dependency_audit_does_not_flag_harmless_same_named_methods():
    tree = ast.parse(
        "values = {'a': 1}\n"
        "values.copy()\n"
        "label = 'a-b'\n"
        "label.replace('-', '_')\n"
        "pairs = [(1, 2)]\n"
        "pairs.copy()\n"
    )

    assert tuple(_forbidden_calls(tree, "backend.code_graph.audit")) == ()


def test_recursive_delete_is_blocked_by_the_runtime_guard_under_build(
    cg, monkeypatch, tmp_path
):
    victim = tmp_path / "payload.txt"
    victim.write_text("untrusted", encoding="utf-8")
    directory = tmp_path / "tree"
    directory.mkdir()
    (directory / "inner.txt").write_text("inner", encoding="utf-8")

    with pytest.raises(AssertionError, match="in-memory boundary"):
        with monkeypatch.context() as guard:
            _patch_io(guard)
            shutil.rmtree(directory)

    assert victim.read_text(encoding="utf-8") == "untrusted"
    assert (directory / "inner.txt").read_text(encoding="utf-8") == "inner"


def test_file_descriptor_writers_are_blocked_by_the_runtime_guard(
    cg, monkeypatch, tmp_path
):
    victim = tmp_path / "payload.txt"
    victim.write_text("abcdef", encoding="utf-8")

    with pytest.raises(AssertionError, match="in-memory boundary"):
        with monkeypatch.context() as guard:
            _patch_io(guard)
            os.truncate(victim, 0)

    assert victim.read_text(encoding="utf-8") == "abcdef"


def test_code_graph_sources_stay_within_approved_dependencies_and_apis():
    root = Path(__file__).resolve().parents[1]

    assert _audit_code_graph_sources(root) == []
