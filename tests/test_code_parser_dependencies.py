import ast
import sys
from importlib.metadata import version
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
