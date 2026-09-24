"""Security boundaries for graph construction from prepared metadata only."""

import ast
import builtins
import importlib
import io
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from backend.code_graph import build_code_graph
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
        (Path, "open"),
        (Path, "read_text"),
        (Path, "read_bytes"),
        (Path, "stat"),
        (Path, "resolve"),
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


@pytest.mark.parametrize(
    "language,path,module,imported",
    [
        (ParsedLanguage.PYTHON, "main.py", "pkg.dep", "pkg.dep"),
        (ParsedLanguage.JAVA, "Main.java", "pkg.Dep", "pkg.Dep"),
        (ParsedLanguage.JAVASCRIPT, "main.js", "./dep", "./dep"),
        (ParsedLanguage.TYPESCRIPT, "main.ts", "./dep", "./dep"),
        (ParsedLanguage.TSX, "main.tsx", "./dep", "./dep"),
    ],
)
def test_all_language_dispatch_paths_use_only_prepared_metadata(
    cg, monkeypatch, language, path, module, imported
):
    is_java = language is ParsedLanguage.JAVA
    owner_name = "pkg.C" if is_java else "C"
    caller_name = f"{owner_name}.run" if is_java else "run"
    owner = cg.symbol(
        "C",
        kind=SymbolKind.CLASS,
        qualified_name=owner_name,
        base_types=("Base",),
        implemented_types=("Contract",),
        start=0,
        end=100,
    )
    caller = cg.symbol(
        "run",
        kind=SymbolKind.METHOD if is_java else SymbolKind.FUNCTION,
        qualified_name=caller_name,
        parent=owner_name if is_java else None,
        start=10 if is_java else 0,
        end=80,
    )
    bindings = (
        (ImportBinding(imported, "dep"),)
        if language
        in {
            ParsedLanguage.PYTHON,
            ParsedLanguage.JAVASCRIPT,
            ParsedLanguage.TYPESCRIPT,
            ParsedLanguage.TSX,
        }
        else ()
    )
    import_item = ImportInfo(
        module,
        bindings,
        False,
        (),
        cg.loc(1, 10),
    )
    call_target = "pkg.Dep.execute" if is_java else "dep.execute"
    call = CallSite(caller_name, call_target, CallKind.CALL, cg.loc(20, 30))
    source = cg.parsed(
        path,
        language=language,
        symbols=(owner, caller),
        imports=(import_item,),
        calls=(call,),
    )
    target_path = {
        ParsedLanguage.PYTHON: "dep.py",
        ParsedLanguage.JAVA: "pkg/Dep.java",
        ParsedLanguage.JAVASCRIPT: "dep.js",
        ParsedLanguage.TYPESCRIPT: "dep.ts",
        ParsedLanguage.TSX: "dep.tsx",
    }[language]
    target_symbols = (
        (
            cg.symbol(
                "Dep",
                kind=SymbolKind.CLASS,
                qualified_name="pkg.Dep",
                start=0,
                end=100,
            ),
            cg.symbol(
                "execute",
                kind=SymbolKind.METHOD,
                qualified_name="pkg.Dep.execute",
                parent="pkg.Dep",
                start=10,
                end=80,
            ),
        )
        if is_java
        else (cg.symbol("execute"),)
    )
    target = cg.parsed(
        target_path,
        language=language,
        symbols=target_symbols,
    )

    graph = _build_under_guard(monkeypatch, cg.pair(source, target))

    assert len(graph.nodes) >= 3


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
        if (
            resolved_name in {"eval", "exec", "builtins.eval", "builtins.exec"}
            or leaf_name in forbidden_io
            or resolved_name in unsafe_deserializers
        ):
            yield resolved_name or leaf_name


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


def test_code_graph_sources_stay_within_approved_dependencies_and_apis():
    root = Path(__file__).resolve().parents[1]

    assert _audit_code_graph_sources(root) == []
