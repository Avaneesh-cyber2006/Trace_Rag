import ast
import sys
from importlib.metadata import version
from importlib.util import resolve_name
from pathlib import Path

import pytest
from tree_sitter import Language, Parser
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript


EXPECTED_VERSIONS = {
    "tree-sitter": "0.25.2",
    "tree-sitter-python": "0.25.0",
    "tree-sitter-java": "0.23.5",
    "tree-sitter-javascript": "0.25.0",
    "tree-sitter-typescript": "0.23.2",
}


def test_tree_sitter_dependency_versions_are_exact() -> None:
    assert {name: version(name) for name in EXPECTED_VERSIONS} == EXPECTED_VERSIONS


@pytest.mark.parametrize(
    ("language_capsule", "source", "root_type"),
    [
        (tree_sitter_python.language, b"def f():\n    pass\n", "module"),
        (tree_sitter_java.language, b"class A {}\n", "program"),
        (tree_sitter_javascript.language, b"function f() {}\n", "program"),
        (tree_sitter_typescript.language_typescript, b"interface A {}\n", "program"),
        (tree_sitter_typescript.language_tsx, b"const x = <div />;\n", "program"),
    ],
)
def test_pinned_grammar_initializes_and_parses_minimal_fixture(
    language_capsule: object, source: bytes, root_type: str
) -> None:
    language = Language(language_capsule())  # intended 0.25.2 API
    parser = Parser(language)
    tree = parser.parse(source)
    assert tree.root_node.type == root_type
    assert not tree.root_node.has_error


def test_code_chunker_uses_only_standard_library_and_approved_backend_boundaries() -> None:
    package = Path(__file__).parents[1] / "backend" / "code_chunker"
    approved_backend_modules = {
        "backend.code_parser.exceptions",
        "backend.code_parser.models",
        "backend.code_parser.reader",
        "backend.file_scanner.models",
    }
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        absolute_modules = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        absolute_modules += [
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 0
        ]
        assert all(
            module.split(".", 1)[0] in sys.stdlib_module_names
            or module in approved_backend_modules
            for module in absolute_modules
        ), (path, absolute_modules)
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"eval", "exec", "compile", "__import__"}
            for node in ast.walk(tree)
        ), path


def _embedding_import_targets(tree: ast.AST, package_name: str) -> set[str]:
    targets = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = resolve_name("." * node.level + module, package_name)
            targets.add(module)
            targets.update(module + "." + alias.name for alias in node.names)
    return targets


@pytest.mark.parametrize(
    ("source", "target"),
    (
        ("import google.genai as sdk", "google.genai"),
        ("from google import genai as sdk", "google.genai"),
        ("from google.genai import types", "google.genai.types"),
        ("from chromadb.config import Settings", "chromadb.config.Settings"),
        ("from .providers import gemini", "backend.embedding_vector_store.providers.gemini"),
        ("from .stores.chroma import ChromaVectorStore", "backend.embedding_vector_store.stores.chroma"),
    ),
)
def test_embedding_dependency_import_detection_handles_aliases_and_relative_edges(source, target):
    assert target in _embedding_import_targets(ast.parse(source), "backend.embedding_vector_store")


def test_embedding_dependency_boundary_keeps_sdk_imports_at_edges_and_core_neutral():
    root = Path(__file__).parents[1]
    package = root / "backend" / "embedding_vector_store"
    facade_paths = {"__init__.py", "providers/__init__.py", "stores/__init__.py"}
    upstream = {"backend.code_chunker.models", "backend.code_parser.models"}
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package).as_posix()
        package_name = ".".join(path.relative_to(root).parts[:-1])
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        targets = _embedding_import_targets(tree, package_name)
        for target in targets:
            top = target.split(".", 1)[0]
            if top in {"google", "httpx", "pydantic"}:
                assert relative == "providers/gemini.py", (relative, target)
            elif top == "chromadb":
                assert relative == "stores/chroma.py", (relative, target)
            elif target.startswith("backend.embedding_vector_store"):
                if relative not in facade_paths:
                    assert target not in {
                        "backend.embedding_vector_store",
                        "backend.embedding_vector_store.providers",
                        "backend.embedding_vector_store.stores",
                    }, (relative, target)
                    assert not any(
                        target == boundary or target.startswith(boundary + ".")
                        for boundary in (
                            "backend.embedding_vector_store.providers.gemini",
                            "backend.embedding_vector_store.stores.chroma",
                        )
                    ), (relative, target)
            elif top == "backend":
                assert any(target == module or target.startswith(module + ".") for module in upstream), (relative, target)
            else:
                assert top in sys.stdlib_module_names and top != "importlib", (relative, target)
        assert not any(
            isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
            for node in ast.walk(tree)
        ), path
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"eval", "exec", "compile", "__import__"}
            for node in ast.walk(tree)
        ), path


def test_embedding_readme_documents_required_operational_contracts():
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
    required = (
        "Module 5", "TRACERAG_GEMINI_API_KEY", "persistence_root",
        "google-genai==2.22.0", "chromadb==1.5.9", "gemini-embedding-001",
        "gemini-embedding-001-retrieval-3072-v1", "3072", "RETRIEVAL_DOCUMENT",
        "RETRIEVAL_QUERY", "1,536 UTF-8 bytes", "max_batch_size=1",
        "tracerag-embedding-document-v1", "UNCHANGED", "RepositoryIndexNotFound",
        "EmbeddingSpaceMismatch", "empty index", "active pointer", "os.replace",
        "score = 1 - (distance / 2)", "1e-6", "binary32", "single-process",
        "multi-process", "TRACERAG_RUN_GEMINI_LIVE", "-m gemini_live",
        "not integration and not gemini_live", "Modules 6–10",
    )
    assert not [fact for fact in required if fact not in readme]
