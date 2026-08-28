from importlib.metadata import version

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
