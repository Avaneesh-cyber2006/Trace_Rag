from dataclasses import FrozenInstanceError, fields

import pytest

from backend.runtime_evidence.source_references import (
    NormalizedSource,
    SourceReferenceKind,
    normalize_source_reference,
)
from backend.runtime_evidence.identities import source_reference_sha256
from backend.runtime_evidence.ingestor import RuntimeEvidenceIngestor
from backend.runtime_evidence.models import (
    FrameAssociation,
    GraphBinding,
    PathMatchBasis,
    RuntimeArtifact,
    RuntimeArtifactFormat,
    RuntimeArtifactStatus,
    RuntimeEvidenceRequest,
    RuntimeFrameLanguage,
)


@pytest.mark.parametrize("source, kind, path, components", [
    (None, SourceReferenceKind.NONE, None, ()),
    ("pkg/app.py", SourceReferenceKind.RELATIVE, "pkg/app.py", ("pkg", "app.py")),
    ("pkg//", SourceReferenceKind.INVALID, None, ()),
    ("./pkg/app.py", SourceReferenceKind.RELATIVE, "pkg/app.py", ("pkg", "app.py")),
    ("././pkg/app.py", SourceReferenceKind.RELATIVE, "pkg/app.py",
     ("pkg", "app.py")),
    ("app.py", SourceReferenceKind.BASENAME, None, ("app.py",)),
    ("/home/u/app.py", SourceReferenceKind.ABSOLUTE, None, ("home", "u", "app.py")),
    (r"C:\repo\pkg\app.py", SourceReferenceKind.ABSOLUTE, None,
     ("repo", "pkg", "app.py")),
    (r"C:\repo/pkg\app.py", SourceReferenceKind.ABSOLUTE, None,
     ("repo", "pkg", "app.py")),
    ("C:foo", SourceReferenceKind.INVALID, None, ()),
    ("//server/share/a.py", SourceReferenceKind.EXTERNAL, None, ()),
    ("https://example/a.py", SourceReferenceKind.EXTERNAL, None, ()),
    ("webpack:///src/app.js", SourceReferenceKind.RELATIVE, "src/app.js",
     ("src", "app.js")),
    ("webpack:///./src/app.js", SourceReferenceKind.RELATIVE, "src/app.js",
     ("src", "app.js")),
    ("webpack://C:/repo/a.py", SourceReferenceKind.ABSOLUTE, None,
     ("repo", "a.py")),
    ("webpack:///C:/repo/a.py", SourceReferenceKind.ABSOLUTE, None,
     ("repo", "a.py")),
    ("webpack://C:foo", SourceReferenceKind.INVALID, None, ()),
    ("webpack:////server/share", SourceReferenceKind.EXTERNAL, None, ()),
    ("file:///C:/repo/a.py", SourceReferenceKind.ABSOLUTE, None,
     ("repo", "a.py")),
    ("file:/C:/repo/a.py", SourceReferenceKind.ABSOLUTE, None,
     ("repo", "a.py")),
    ("file://host/share/a.py", SourceReferenceKind.EXTERNAL, None, ()),
    ("file:////server/share/a.py", SourceReferenceKind.EXTERNAL, None, ()),
    ("file:/home/u/a.py", SourceReferenceKind.ABSOLUTE, None,
     ("home", "u", "a.py")),
    ("file:/a%5Cb.py", SourceReferenceKind.ABSOLUTE, None, ("a", "b.py")),
    ("file:///home/u/a%20b.py", SourceReferenceKind.ABSOLUTE, None,
     ("home", "u", "a b.py")),
])
def test_normalization_matrix(source, kind, path, components):
    result = normalize_source_reference(source, 1024)
    assert (result.kind, result.relative_path, result.components) == (
        kind, path, components)


@pytest.mark.parametrize("source", [
    "", "   ", "a//b", "a/./b", "../a", "a/../b", "a/",
    "a\x00b", "a\x01b", "a\ud800b", "file:/a?x",
    "file:/a#x", "file:/bad%2", "file:/bad%ZZ", "file:/bad%FF",
    "file:", "file:/a/%2e%2e/b.py", "file:/", "file://", "file:///", "file:relative",
    "webpack:foo", "webpack://",
])
def test_rejected_sources_are_invalid_without_retaining_text(source):
    result = normalize_source_reference(source, 1024)
    assert result == normalize_source_reference("", 1024)
    assert result.kind is SourceReferenceKind.INVALID


def test_long_surrogate_and_controls_are_invalid_but_interior_tab_is_control():
    assert normalize_source_reference("x" * 1025, 1024).kind is SourceReferenceKind.INVALID
    assert normalize_source_reference("  a/b.py  ", 10).relative_path == "a/b.py"
    assert normalize_source_reference("a\tb.py", 10).kind is SourceReferenceKind.INVALID


def test_case_and_unicode_are_never_folded():
    assert normalize_source_reference("Pkg/É.py", 1024).relative_path == "Pkg/É.py"


def test_source_reference_exact_byte_limit_is_accepted():
    at_limit = "é/" + "b" * 1021
    assert len(at_limit.encode("utf-8")) == 1024
    result = normalize_source_reference(at_limit, 1024)
    assert (result.kind, result.relative_path, result.components) == (
        SourceReferenceKind.RELATIVE, at_limit, ("é", "b" * 1021))
    assert normalize_source_reference(at_limit + "c", 1024).kind is (
        SourceReferenceKind.INVALID)


@pytest.mark.parametrize("source", [
    "file:///C:/repo/a.py",
    "file:/C:/repo/a.py",
    "file://host/share/a.py",
    "file:////server/share/a.py",
    "webpack://C:/repo/a.py",
    "webpack:///C:/repo/a.py",
    "webpack:////server/share",
    "/home/u/app.py",
    r"C:\repo\pkg\app.py",
    "//server/share/a.py",
    "https://example/a.py",
])
def test_raw_source_and_machine_paths_are_never_retained_in_a_path(
    source,
):
    result = normalize_source_reference(source, 1024)
    assert result.relative_path is None
    assert source not in (result.relative_path, result.basename)
    assert source not in result.components
    assert source not in repr(result)
    assert "C:" not in repr(result)
    assert "C:\\" not in repr(result)
    assert "server/share" not in repr(result)


def test_external_references_retain_no_components_or_basename():
    for source in (
        "//server/share/a.py",
        "https://example/a.py",
        "file://host/share/a.py",
        "file:////server/share/a.py",
        "webpack:////server/share",
    ):
        result = normalize_source_reference(source, 1024)
        assert result.kind is SourceReferenceKind.EXTERNAL
        assert result.relative_path is None
        assert result.components == ()
        assert result.basename is None


def test_invalid_references_retain_no_components_or_basename():
    for source in (
        "C:foo",
        "webpack://C:foo",
        "file:/a/%2e%2e/b.py",
        "file:/a/%5C..%5Cb.py",
        "a\\..\\b.py",
    ):
        result = normalize_source_reference(source, 1024)
        assert result.kind is SourceReferenceKind.INVALID
        assert result.relative_path is None
        assert result.components == ()
        assert result.basename is None


def test_relative_percent_escapes_are_not_decoded():
    result = normalize_source_reference("a/%2e%2e/b.py", 1024)
    assert (result.kind, result.relative_path, result.components) == (
        SourceReferenceKind.RELATIVE, "a/%2e%2e/b.py",
        ("a", "%2e%2e", "b.py"))


def test_basename_keeps_component_but_never_a_relative_path():
    result = normalize_source_reference("./app.py", 1024)
    assert (result.kind, result.relative_path, result.components, result.basename) == (
        SourceReferenceKind.BASENAME, None, ("app.py",), "app.py")


def test_normalized_source_is_frozen_and_slotted():
    result = normalize_source_reference("pkg/app.py", 1024)
    assert [field.name for field in fields(NormalizedSource)] == [
        "kind",
        "relative_path",
        "components",
        "basename",
    ]
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.kind = SourceReferenceKind.NONE


@pytest.mark.parametrize("source", [
    "file:/a/%2e%2e/b.py",
    "file:/a/%2E%2E/b.py",
    "file:/a/../b.py",
    "file:///C:/repo/%2e%2e/b.py",
    "file:///C:/repo/..%5Cb.py",
    "file:/bad%2",
    "file:/bad%ZZ",
    "file:/bad%FF",
    "file:/bad%C3",
    "file:/a%00b.py",
    "file:/a%01b.py",
    "file:/a%5C..%5Cb.py",
    "file:/a?x",
    "file:/a#x",
    "file:/a%3Fx",
    "file:/a%23x",
    "webpack://C:foo",
    "webpack://",
    "webpack:foo",
    "webpack:/src/app.js",
])
def test_additional_file_and_webpack_rejections(source):
    result = normalize_source_reference(source, 1024)
    assert result == normalize_source_reference("", 1024)
    assert result.kind is SourceReferenceKind.INVALID


def test_file_and_webpack_forms_reclassify_without_forced_relative():
    assert normalize_source_reference("file:///C:/repo/a.py", 1024).kind is (
        SourceReferenceKind.ABSOLUTE)
    assert normalize_source_reference("file:////server/share/a.py", 1024).kind is (
        SourceReferenceKind.EXTERNAL)
    assert normalize_source_reference("webpack://C:foo", 1024).kind is (
        SourceReferenceKind.INVALID)
    assert normalize_source_reference("webpack:////server/share", 1024).kind is (
        SourceReferenceKind.EXTERNAL)
    relative = normalize_source_reference("webpack:///./src/app.js", 1024)
    assert (relative.kind, relative.relative_path) == (
        SourceReferenceKind.RELATIVE, "src/app.js")


# ---------------------------------------------------------------------------
# Task 14: graph validation and association lookup index
# ---------------------------------------------------------------------------

from types import MappingProxyType, SimpleNamespace

import backend.runtime_evidence.association as association
from backend.code_graph import CodeGraphInventory, GraphNode, GraphNodeKind
from backend.runtime_evidence.association import build_association_index
from backend.runtime_evidence.exceptions import RuntimeEvidenceGraphError


def _loc(start_line=1, end_line=1, start_column=0, end_column=0):
    return SimpleNamespace(
        start_byte=0, end_byte=10, start_line=start_line,
        start_column=start_column, end_line=end_line, end_column=end_column)


def _node(node_id, kind, path, *, namespace="repo", location=None):
    return GraphNode(
        node_id=node_id, node_kind=kind, repository_namespace=namespace,
        relative_path=path, language="python", parse_status="success",
        symbol_kind=None, name=None, qualified_name=None, location=location)


def _graph(nodes, *, namespace="repo"):
    return CodeGraphInventory(
        namespace, tuple(nodes), (), (),
        MappingProxyType({}), MappingProxyType({}),
        MappingProxyType({}), MappingProxyType({}))


def _loc_fields(**overrides):
    values = {
        "start_byte": 0,
        "end_byte": 10,
        "start_line": 1,
        "start_column": 0,
        "end_line": 1,
        "end_column": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _build(nodes, *, namespace="repo", max_namespace_bytes=256):
    return build_association_index(
        _graph(nodes, namespace=namespace),
        repository_namespace=namespace,
        max_namespace_bytes=max_namespace_bytes,
    )


def _file_node(node_id="file-main", path="pkg/main.py"):
    return _node(node_id, GraphNodeKind.FILE, path)


def _symbol_node(node_id, path="pkg/main.py", **location):
    return _node(
        node_id, GraphNodeKind.SYMBOL, path, location=_loc_fields(**location)
    )


def test_indexes_exact_file_and_symbol_buckets():
    graph = _graph([
        _node("file-main", GraphNodeKind.FILE, "pkg/main.py"),
        _node("sym-b", GraphNodeKind.SYMBOL, "pkg/main.py",
              location=_loc(8, 20, 0, 0)),
        _node("sym-a", GraphNodeKind.SYMBOL, "pkg/main.py",
              location=_loc(1, 7, 0, 0)),
    ])
    index = build_association_index(graph, repository_namespace="repo",
                                    max_namespace_bytes=256)
    assert index.files_by_path["pkg/main.py"].node_id == "file-main"
    assert [f.node_id for f in index.files_by_basename["main.py"]] == ["file-main"]
    assert [(s.node_id, s.start_line, s.last_covered_line)
            for s in index.symbols_by_file["pkg/main.py"]] == [
        ("sym-a", 1, 6), ("sym-b", 8, 19)]


def test_namespace_multibyte_at_limit_and_mismatch():
    namespace = "é" * 128
    graph = _graph([], namespace=namespace)
    assert build_association_index(
        graph, repository_namespace=namespace, max_namespace_bytes=256)
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(graph, repository_namespace=namespace + "a",
                                max_namespace_bytes=256)
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(graph, repository_namespace=namespace,
                                max_namespace_bytes=255)


def test_graph_failures_are_fixed_and_content_free():
    sentinel = "SENTINEL-PATH"
    graph = _graph([_node("f", GraphNodeKind.FILE, "../" + sentinel)])
    with pytest.raises(RuntimeEvidenceGraphError) as caught:
        build_association_index(graph, repository_namespace="repo",
                                max_namespace_bytes=256)
    assert sentinel not in str(caught.value)
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(object(), repository_namespace="repo",
                                max_namespace_bytes=256)


def test_graph_paths_reject_c0_tab_and_surrogates():
    for path in ("a\x01b", "a\tb", "a\ud800b"):
        graph = _graph([_node("f", GraphNodeKind.FILE, path)])
        with pytest.raises(RuntimeEvidenceGraphError):
            build_association_index(graph, repository_namespace="repo",
                                    max_namespace_bytes=256)


def test_none_graph_returns_none_immediately():
    assert build_association_index(
        None, repository_namespace="repo", max_namespace_bytes=256) is None


def test_empty_graph_produces_empty_immutable_index():
    index = _build([])
    assert isinstance(index, association.AssociationIndex)
    assert dict(index.files_by_path) == {}
    assert dict(index.files_by_basename) == {}
    assert dict(index.symbols_by_file) == {}
    assert isinstance(index.files_by_path, MappingProxyType)
    assert isinstance(index.files_by_basename, MappingProxyType)
    assert isinstance(index.symbols_by_file, MappingProxyType)


def test_duplicate_node_ids_are_rejected():
    graph = _graph([
        _node("same", GraphNodeKind.FILE, "a.py"),
        _node("same", GraphNodeKind.FILE, "b.py"),
    ])
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(graph, repository_namespace="repo",
                                max_namespace_bytes=256)


@pytest.mark.parametrize("node_id", ["", "a\ud800b", "a\x00b", None, 7, True])
def test_node_ids_must_be_nonempty_scalar_strings(node_id):
    graph = _graph([_node(node_id, GraphNodeKind.FILE, "a.py")])
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(graph, repository_namespace="repo",
                                max_namespace_bytes=256)


def test_node_namespace_must_equal_request_namespace():
    graph = _graph(
        [_node("f", GraphNodeKind.FILE, "a.py", namespace="other")],
        namespace="repo",
    )
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(graph, repository_namespace="repo",
                                max_namespace_bytes=256)


@pytest.mark.parametrize("namespace", [None, "", "a\ud800b", "a\x00b", True, 7])
def test_node_namespace_must_be_nonempty_scalar_text(namespace):
    graph = _graph([_node("f", GraphNodeKind.FILE, "a.py",
                          namespace=namespace)])
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(graph, repository_namespace="repo",
                                max_namespace_bytes=256)


def test_duplicate_file_paths_are_rejected():
    graph = _graph([
        _node("file-a", GraphNodeKind.FILE, "pkg/main.py"),
        _node("file-b", GraphNodeKind.FILE, "pkg/main.py"),
    ])
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(graph, repository_namespace="repo",
                                max_namespace_bytes=256)


def test_file_node_must_not_have_a_location():
    graph = _graph([
        _node("file-main", GraphNodeKind.FILE, "pkg/main.py",
              location=_loc()),
    ])
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(graph, repository_namespace="repo",
                                max_namespace_bytes=256)


def test_symbol_location_must_be_structurally_complete():
    missing_column = SimpleNamespace(
        start_byte=0, end_byte=10, start_line=1, start_column=0,
        end_line=1)
    for location in (None, missing_column, object()):
        graph = _graph([
            _file_node(),
            _node("sym", GraphNodeKind.SYMBOL, "pkg/main.py",
                  location=location),
        ])
        with pytest.raises(RuntimeEvidenceGraphError):
            build_association_index(graph, repository_namespace="repo",
                                    max_namespace_bytes=256)


def test_symbol_path_must_have_exactly_one_file_node():
    for nodes in (
        [_symbol_node("sym", path="pkg/other.py")],
        [
            _file_node(path="pkg/other.py"),
            _symbol_node("sym", path="pkg/main.py"),
        ],
    ):
        graph = _graph(nodes)
        with pytest.raises(RuntimeEvidenceGraphError):
            build_association_index(graph, repository_namespace="repo",
                                    max_namespace_bytes=256)


@pytest.mark.parametrize("overrides", [
    {"start_byte": True},
    {"start_byte": "0"},
    {"end_byte": 1.5},
    {"start_byte": -1},
    {"end_byte": -1},
    {"start_line": True},
    {"start_line": 0},
    {"end_line": 0},
    {"end_line": -3},
    {"start_column": True},
    {"start_column": -1},
    {"end_column": -1},
    {"start_byte": 11, "end_byte": 10},
    {"start_line": 3, "end_line": 2, "start_column": 0, "end_column": 0},
])
def test_invalid_location_fields_are_rejected(overrides):
    graph = _graph([_file_node(), _symbol_node("sym", **overrides)])
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(graph, repository_namespace="repo",
                                max_namespace_bytes=256)


def test_same_line_start_column_must_not_exceed_end_column():
    graph = _graph([
        _file_node(),
        _symbol_node("sym", start_line=2, end_line=2, start_column=4,
                     end_column=3),
    ])
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(graph, repository_namespace="repo",
                                max_namespace_bytes=256)
    accepted = _build([
        _file_node(),
        _symbol_node("sym", start_line=2, end_line=2, start_column=4,
                     end_column=4),
    ])
    assert accepted.symbols_by_file["pkg/main.py"][0].last_covered_line == 2


def test_different_line_columns_are_not_ordered():
    index = _build([
        _file_node(),
        _symbol_node("sym", start_line=2, end_line=5, start_column=9,
                     end_column=1),
    ])
    assert index.symbols_by_file["pkg/main.py"][0].last_covered_line == 5


def test_zero_end_column_uses_the_previous_line_and_may_be_zero():
    index = _build([
        _file_node(),
        _symbol_node("first", start_line=1, end_line=1, end_column=0),
        _symbol_node("second", start_line=5, end_line=9, end_column=0),
    ])
    assert [(s.node_id, s.start_line, s.last_covered_line)
            for s in index.symbols_by_file["pkg/main.py"]] == [
        ("first", 1, 0), ("second", 5, 8)]


def test_positive_end_column_keeps_the_end_line():
    index = _build([
        _file_node(),
        _symbol_node("sym", start_line=4, end_line=9, end_column=3),
    ])
    assert index.symbols_by_file["pkg/main.py"][0].last_covered_line == 9


def test_nodes_must_be_an_exact_tuple():
    node = _file_node()
    graph = object.__new__(CodeGraphInventory)
    object.__setattr__(graph, "repository_namespace", "repo")
    object.__setattr__(graph, "nodes", [node])
    object.__setattr__(graph, "edges", ())
    object.__setattr__(graph, "unresolved_references", ())
    object.__setattr__(graph, "outgoing_edges", MappingProxyType({}))
    object.__setattr__(graph, "incoming_edges", MappingProxyType({}))
    object.__setattr__(graph, "nodes_by_file", MappingProxyType({}))
    object.__setattr__(graph, "nodes_by_qualified_name", MappingProxyType({}))
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(graph, repository_namespace="repo",
                                max_namespace_bytes=256)

    class _TupleSubclass(tuple):
        pass

    graph = _graph([])
    object.__setattr__(graph, "nodes", _TupleSubclass((node,)))
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(graph, repository_namespace="repo",
                                max_namespace_bytes=256)


def test_nodes_must_be_exact_graph_node_instances():
    class _NodeSubclass(GraphNode):
        __slots__ = ()

    node = _NodeSubclass(
        node_id="file-main", node_kind=GraphNodeKind.FILE,
        repository_namespace="repo", relative_path="pkg/main.py",
        language="python", parse_status="success", symbol_kind=None,
        name=None, qualified_name=None, location=None)
    graph = _graph([node])
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(graph, repository_namespace="repo",
                                max_namespace_bytes=256)


@pytest.mark.parametrize("kind", ["file", "symbol", None, 1])
def test_node_kind_must_be_an_exact_graph_node_kind(kind):
    location = _loc() if kind == "symbol" else None
    graph = _graph([_node("node", kind, "pkg/main.py", location=location)])
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(graph, repository_namespace="repo",
                                max_namespace_bytes=256)


@pytest.mark.parametrize("path", [
    "",
    "a\x00b",
    "a\x01b",
    "a\tb",
    "a\ud800b",
    "/abs.py",
    "C:foo",
    "C:/foo",
    "a\\b.py",
    "a//b",
    "a/./b",
    "a/../b",
    "../a",
    "a/",
])
def test_invalid_relative_paths_are_rejected(path):
    graph = _graph([_node("f", GraphNodeKind.FILE, path)])
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(graph, repository_namespace="repo",
                                max_namespace_bytes=256)


def test_true_namespace_mismatch_is_rejected():
    graph = _graph([], namespace="other")
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(graph, repository_namespace="repo",
                                max_namespace_bytes=256)


@pytest.mark.parametrize("namespace", [None, "", "a\ud800b", "a\x00b", True, 7])
def test_request_namespace_must_be_nonempty_scalar_text(namespace):
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(_graph([], namespace="repo"),
                                repository_namespace=namespace,
                                max_namespace_bytes=256)


@pytest.mark.parametrize("namespace", [None, "", "a\ud800b", True, 7])
def test_graph_namespace_must_be_nonempty_scalar_text(namespace):
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(_graph([], namespace=namespace),
                                repository_namespace="repo",
                                max_namespace_bytes=256)


@pytest.mark.parametrize("limit", [-1, 0, True, "256", 1.5, None])
def test_max_namespace_bytes_must_be_a_positive_exact_int(limit):
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(_graph([], namespace="repo"),
                                repository_namespace="repo",
                                max_namespace_bytes=limit)


def test_request_namespace_one_byte_over_the_limit_is_rejected():
    namespace = "é" * 128 + "a"
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(_graph([], namespace="repo"),
                                repository_namespace=namespace,
                                max_namespace_bytes=256)


def test_graph_namespace_one_byte_over_the_limit_is_rejected():
    namespace = "é" * 128 + "a"
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(_graph([], namespace=namespace),
                                repository_namespace="repo",
                                max_namespace_bytes=256)


def test_mapping_keys_and_buckets_are_lexically_ordered():
    index = _build([
        _node("z-file", GraphNodeKind.FILE, "z/main.py"),
        _node("a-file", GraphNodeKind.FILE, "a/main.py"),
        _node("m-file", GraphNodeKind.FILE, "pkg/m.py"),
        _node("z-sym", GraphNodeKind.SYMBOL, "pkg/m.py",
              location=_loc_fields(start_line=4, end_line=4)),
        _node("a-sym", GraphNodeKind.SYMBOL, "pkg/m.py",
              location=_loc_fields(start_line=2, end_line=2)),
        _node("main-sym", GraphNodeKind.SYMBOL, "z/main.py",
              location=_loc_fields(start_line=2, end_line=2)),
    ])
    assert list(index.files_by_path) == [
        "a/main.py", "pkg/m.py", "z/main.py"]
    assert list(index.files_by_basename) == ["m.py", "main.py"]
    assert [entry.node_id
            for entry in index.files_by_basename["main.py"]] == [
        "a-file", "z-file"]
    assert list(index.symbols_by_file) == ["pkg/m.py", "z/main.py"]


def test_symbol_buckets_sort_by_start_last_covered_and_node_id():
    index = _build([
        _file_node(),
        _symbol_node("sym-c", start_line=1, end_line=5),
        _symbol_node("sym-b", start_line=1, end_line=3),
        _symbol_node("sym-a", start_line=1, end_line=3),
    ])
    assert [symbol.node_id
            for symbol in index.symbols_by_file["pkg/main.py"]] == [
        "sym-a", "sym-b", "sym-c"]


def test_index_is_frozen_and_uses_immutable_snapshots():
    index = _build([_file_node(), _symbol_node("sym")])
    assert not hasattr(index, "__dict__")
    assert type(index.files_by_basename["main.py"]) is tuple
    assert type(index.symbols_by_file["pkg/main.py"]) is tuple
    with pytest.raises(TypeError):
        index.files_by_path["other.py"] = None
    with pytest.raises(TypeError):
        index.files_by_basename["main.py"] = ()
    with pytest.raises(TypeError):
        index.symbols_by_file["pkg/main.py"] = ()
    with pytest.raises(FrozenInstanceError):
        index.files_by_path = MappingProxyType({})


def test_index_retains_no_graph_reference_or_graph_records():
    graph = _graph([_file_node(), _symbol_node("sym")])
    index = build_association_index(graph, repository_namespace="repo",
                                    max_namespace_bytes=256)
    assert not hasattr(index, "__dict__")
    files = list(index.files_by_path.values())
    for bucket in index.files_by_basename.values():
        files.extend(bucket)
    symbols = []
    for bucket in index.symbols_by_file.values():
        symbols.extend(bucket)
    assert all(type(entry) is association.GraphFileEntry for entry in files)
    assert all(
        type(symbol) is association.GraphSymbolEntry for symbol in symbols)
    assert not any(
        type(value) in (CodeGraphInventory, GraphNode)
        for value in files + symbols)


def test_building_an_index_reads_no_source_files(monkeypatch):
    def _deny(*args, **kwargs):
        raise AssertionError("filesystem access is not allowed")

    monkeypatch.setattr("builtins.open", _deny)
    index = _build([
        _file_node(),
        _symbol_node("sym", start_line=2, end_line=3, end_column=1),
    ])
    assert index.files_by_path["pkg/main.py"].node_id == "file-main"
    assert index.symbols_by_file["pkg/main.py"][0].node_id == "sym"


def test_bypassed_inventory_ignores_edges_and_public_mappings():
    graph = object.__new__(CodeGraphInventory)
    object.__setattr__(graph, "repository_namespace", "repo")
    object.__setattr__(
        graph, "nodes", (_node("file-main", GraphNodeKind.FILE,
                               "pkg/main.py"),))
    object.__setattr__(graph, "edges", ("not-an-edge",))
    object.__setattr__(graph, "unresolved_references", (object(),))
    object.__setattr__(
        graph, "outgoing_edges", MappingProxyType({"bad": ("bad",)}))
    object.__setattr__(
        graph, "incoming_edges", MappingProxyType({"bad": ("bad",)}))
    object.__setattr__(
        graph, "nodes_by_file", MappingProxyType({"bad": ("bad",)}))
    object.__setattr__(
        graph, "nodes_by_qualified_name", MappingProxyType({"bad": ("bad",)}))
    index = build_association_index(graph, repository_namespace="repo",
                                    max_namespace_bytes=256)
    assert index.files_by_path["pkg/main.py"].node_id == "file-main"


def test_bypassed_inventory_with_absent_unused_fields_is_accepted():
    graph = object.__new__(CodeGraphInventory)
    object.__setattr__(graph, "repository_namespace", "repo")
    object.__setattr__(
        graph, "nodes", (_node("file-main", GraphNodeKind.FILE,
                               "pkg/main.py"),))
    index = build_association_index(graph, repository_namespace="repo",
                                    max_namespace_bytes=256)
    assert index.files_by_path["pkg/main.py"].node_id == "file-main"


# ---------------------------------------------------------------------------
# Task 15: frame source matching and line association
# ---------------------------------------------------------------------------

from backend.runtime_evidence.association import (
    AssociationIndex, AssociationResult, GraphFileEntry, GraphSymbolEntry,
    associate_frames, match_symbols_by_line,
)
from backend.runtime_evidence.drafts import FrameDraft, IssueDraft
from backend.runtime_evidence.identities import frame_id, source_reference_sha256
from backend.runtime_evidence.models import (
    FrameAssociation, PathMatchBasis, RuntimeFrameLanguage, RuntimeIssueCode,
)


def _draft(source=None, line=None, column=None, function="f"):
    return FrameDraft(RuntimeFrameLanguage.PYTHON, function, source, line,
                      column)


def _assoc(drafts, graph_index, *, namespace="repo", sequence=0,
           source_line=1, config=None):
    from backend.runtime_evidence.config import RuntimeEvidenceConfig
    return associate_frames(
        tuple(drafts), event_id="e" * 64, event_sequence=sequence,
        source_line=source_line, repository_namespace=namespace,
        config=config or RuntimeEvidenceConfig(), graph_index=graph_index)


def _index(files, symbols=()):
    by_path = {f.relative_path: f for f in files}
    by_base = {}
    for f in files:
        by_base.setdefault(f.components[-1], []).append(f)
    by_file = {}
    for s in symbols:
        by_file.setdefault(s[0], []).append(s[2])
    return AssociationIndex(
        MappingProxyType(by_path),
        MappingProxyType({k: tuple(v) for k, v in by_base.items()}),
        MappingProxyType({k: tuple(v) for k, v in by_file.items()}))


def _file(node_id, path):
    return GraphFileEntry(node_id, path, tuple(path.split("/")))


def test_no_graph_relative_candidate_is_unverified_and_has_hash():
    result = _assoc([_draft("pkg/app.py", 10)], None)
    frame = result.frames[0]
    assert frame.path_match_basis is PathMatchBasis.UNVERIFIED_RELATIVE_PATH
    assert frame.association is FrameAssociation.NOT_EVALUATED
    assert frame.relative_path == "pkg/app.py"
    assert frame.source_reference_sha256
    assert frame.candidate_node_ids == ()


def test_exact_file_and_symbol_association_and_line_sweep():
    file = GraphFileEntry("file", "pkg/app.py", ("pkg", "app.py"))
    one = GraphSymbolEntry("sym", 10, 20)
    index = _index([file], [(file.relative_path, file, one)])
    result = _assoc([_draft("pkg/app.py", 15)], index)
    frame = result.frames[0]
    assert frame.association is FrameAssociation.SYMBOL
    assert frame.primary_node_id == "sym"
    assert frame.candidate_node_ids == ("file", "sym")
    file_only = _assoc([_draft("pkg/app.py", 21)], index).frames[0]
    assert file_only.association is FrameAssociation.FILE
    assert file_only.primary_node_id == "file"
    assert file_only.candidate_node_ids == ("file",)


def test_exclusive_end_line_and_ambiguous_symbols():
    file = GraphFileEntry("file", "pkg/app.py", ("pkg", "app.py"))
    a = GraphSymbolEntry("a", 10, 19)  # end_line 20, end_column 0
    b = GraphSymbolEntry("b", 15, 20)
    index = _index([file], [(file.relative_path, file, a),
                            (file.relative_path, file, b)])
    at_19 = _assoc([_draft("pkg/app.py", 19)], index).frames[0]
    assert at_19.association is FrameAssociation.AMBIGUOUS_SYMBOL
    assert at_19.primary_node_id is None
    assert at_19.candidate_node_ids == ("a", "b", "file")
    at_20 = _assoc([_draft("pkg/app.py", 20)], index).frames[0]
    assert at_20.association is FrameAssociation.SYMBOL
    assert at_20.primary_node_id == "b"


def test_relative_retention_and_resolved_absolute_and_basename_paths():
    file = GraphFileEntry("file", "pkg/app.py", ("pkg", "app.py"))
    index = _index([file])

    unmatched = _assoc([_draft("other/app.py", 1)], index).frames[0]
    assert unmatched.path_match_basis is PathMatchBasis.UNMATCHED
    assert unmatched.relative_path == "other/app.py"
    assert unmatched.source_reference_sha256
    assert unmatched.association is FrameAssociation.UNASSOCIATED

    absolute = _assoc([_draft("/repo/pkg/app.py", 1)], index).frames[0]
    assert absolute.path_match_basis is PathMatchBasis.UNIQUE_PATH_SUFFIX
    assert absolute.relative_path == "pkg/app.py"
    assert absolute.association is FrameAssociation.FILE

    basename = _assoc([_draft("app.py", 1)], index).frames[0]
    assert basename.path_match_basis is PathMatchBasis.UNIQUE_BASENAME
    assert basename.relative_path == "pkg/app.py"
    assert basename.association is FrameAssociation.FILE

    ambiguous_index = _index([
        GraphFileEntry("file-a", "one/src/app.py", ("one", "src", "app.py")),
        GraphFileEntry("file-b", "two/src/app.py", ("two", "src", "app.py")),
    ])
    ambiguous = _assoc([_draft("src/app.py", 1)], ambiguous_index).frames[0]
    assert ambiguous.path_match_basis is PathMatchBasis.AMBIGUOUS
    assert ambiguous.relative_path == "src/app.py"
    assert ambiguous.source_reference_sha256
    assert ambiguous.association is FrameAssociation.UNASSOCIATED
    assert ambiguous.candidate_node_ids == ()


def test_match_symbols_by_line_boundaries_and_repeated_lines():
    a = GraphSymbolEntry("a", 10, 20)
    b = GraphSymbolEntry("b", 15, 15)
    empty = GraphSymbolEntry("empty", 30, 29)
    result = match_symbols_by_line((15, 10, 20, 25, 15, 10, 30),
                                   (b, empty, a))
    assert [tuple(s.node_id for s in group) for group in result] == [
        ("a", "b"), ("a",), ("a",), (), ("a", "b"), ("a",), ()]
    assert result[0] == result[4]
    assert result[1] == result[5]
    assert match_symbols_by_line((), (a,)) == ()
    assert [tuple(s.node_id for s in group)
            for group in match_symbols_by_line((9, 10, 21), (a,))] == [
        (), ("a",), ()]


def test_unique_and_ambiguous_relative_suffix_retention():
    file = _file("file", "pkg/src/app.py")
    unique = _assoc([_draft("src/app.py", 1)], _index([file])).frames[0]
    assert unique.path_match_basis is PathMatchBasis.UNIQUE_PATH_SUFFIX
    assert unique.relative_path == "pkg/src/app.py"
    assert unique.source_reference_sha256 == source_reference_sha256(
        "pkg/src/app.py")
    assert unique.association is FrameAssociation.FILE
    assert unique.primary_node_id == "file"
    assert unique.candidate_node_ids == ("file",)

    ambiguous_index = _index([
        _file("one", "one/src/app.py"),
        _file("two", "two/src/app.py")])
    ambiguous = _assoc([_draft("src/app.py", 1)], ambiguous_index).frames[0]
    assert ambiguous.path_match_basis is PathMatchBasis.AMBIGUOUS
    assert ambiguous.relative_path == "src/app.py"
    assert ambiguous.source_reference_sha256 == source_reference_sha256(
        "src/app.py")
    assert ambiguous.association is FrameAssociation.UNASSOCIATED
    assert ambiguous.primary_node_id is None
    assert ambiguous.candidate_node_ids == ()

    unmatched = _assoc([_draft("other/app.py", 1)], _index([file])).frames[0]
    assert unmatched.path_match_basis is PathMatchBasis.UNMATCHED
    assert unmatched.relative_path == "other/app.py"
    assert unmatched.source_reference_sha256 == source_reference_sha256(
        "other/app.py")
    assert unmatched.association is FrameAssociation.UNASSOCIATED


def test_suffix_matching_is_component_wise_and_case_sensitive():
    file = _file("file", "pkg/src/App.py")
    index = _index([file])
    for source in ("rc/App.py", "kg/src/App.py", "pkg/src/app.py"):
        frame = _assoc([_draft(source, 1)], index).frames[0]
        assert frame.path_match_basis is PathMatchBasis.UNMATCHED
        assert frame.relative_path == source
    exact = _assoc([_draft("pkg/src/App.py", 1)], index).frames[0]
    assert exact.path_match_basis is PathMatchBasis.EXACT_RELATIVE_PATH
    assert exact.relative_path == "pkg/src/App.py"


def test_basename_unique_ambiguous_and_unmatched():
    file = _file("file", "pkg/app.py")
    unique = _assoc([_draft("app.py", 1)], _index([file])).frames[0]
    assert unique.path_match_basis is PathMatchBasis.UNIQUE_BASENAME
    assert unique.relative_path == "pkg/app.py"
    assert unique.source_reference_sha256 == source_reference_sha256(
        "pkg/app.py")
    assert unique.association is FrameAssociation.FILE
    assert unique.primary_node_id == "file"

    ambiguous_index = _index([
        _file("one", "one/app.py"), _file("two", "two/app.py")])
    ambiguous = _assoc([_draft("app.py", 1)], ambiguous_index).frames[0]
    assert ambiguous.path_match_basis is PathMatchBasis.AMBIGUOUS
    assert ambiguous.relative_path is None
    assert ambiguous.source_reference_sha256 is None
    assert ambiguous.association is FrameAssociation.UNASSOCIATED
    assert ambiguous.candidate_node_ids == ()

    unmatched = _assoc([_draft("other.py", 1)], _index([file])).frames[0]
    assert unmatched.path_match_basis is PathMatchBasis.UNMATCHED
    assert unmatched.relative_path is None
    assert unmatched.source_reference_sha256 is None
    assert unmatched.association is FrameAssociation.UNASSOCIATED


@pytest.mark.parametrize("source", [
    "/repo/pkg/app.py",
    r"C:\repo\pkg\app.py",
    "file:///C:/repo/pkg/app.py",
    "webpack://C:/repo/pkg/app.py",
])
def test_absolute_posix_drive_file_and_webpack_forms_resolve_by_suffix(source):
    file = _file("file", "pkg/app.py")
    frame = _assoc([_draft(source, 1)], _index([file])).frames[0]
    assert frame.path_match_basis is PathMatchBasis.UNIQUE_PATH_SUFFIX
    assert frame.relative_path == "pkg/app.py"
    assert frame.association is FrameAssociation.FILE
    assert "C:" not in repr(frame)
    assert source not in repr(frame)


def test_absolute_suffix_ambiguity_and_no_match_retain_no_path():
    ambiguous_index = _index([
        _file("root", "app.py"), _file("nested", "x/app.py")])
    ambiguous = _assoc([_draft("/x/app.py", 1)],
                       ambiguous_index).frames[0]
    assert ambiguous.path_match_basis is PathMatchBasis.AMBIGUOUS
    assert ambiguous.relative_path is None
    assert ambiguous.source_reference_sha256 is None
    assert ambiguous.association is FrameAssociation.UNASSOCIATED

    unmatched = _assoc([_draft("/repo/other.py", 1)],
                       _index([_file("file", "pkg/app.py")])).frames[0]
    assert unmatched.path_match_basis is PathMatchBasis.UNMATCHED
    assert unmatched.relative_path is None
    assert unmatched.source_reference_sha256 is None
    assert unmatched.association is FrameAssociation.UNASSOCIATED


def test_external_invalid_and_missing_sources_retain_no_path():
    index = _index([_file("file", "pkg/app.py")])
    external = _assoc([_draft("https://example/app.py", 1)],
                      index).frames[0]
    assert external.path_match_basis is PathMatchBasis.EXTERNAL_OR_NON_FILE
    assert external.relative_path is None
    assert external.source_reference_sha256 is None
    assert external.association is FrameAssociation.UNASSOCIATED

    unc = _assoc([_draft("//server/share/app.py", 1)], None).frames[0]
    assert unc.path_match_basis is PathMatchBasis.EXTERNAL_OR_NON_FILE
    assert unc.relative_path is None
    assert unc.association is FrameAssociation.NOT_EVALUATED

    missing = _assoc([_draft(None, 1)], index)
    assert missing.frames[0].path_match_basis is PathMatchBasis.UNMATCHED
    assert missing.frames[0].relative_path is None
    assert missing.frames[0].association is FrameAssociation.UNASSOCIATED
    assert missing.issues == ()

    invalid = _assoc([_draft("a\x00b", 1)], index, sequence=4,
                     source_line=8)
    frame = invalid.frames[0]
    assert frame.path_match_basis is PathMatchBasis.UNMATCHED
    assert frame.relative_path is None
    assert frame.association is FrameAssociation.UNASSOCIATED
    assert len(invalid.issues) == 1
    assert type(invalid.issues[0]) is IssueDraft
    assert invalid.issues[0].code is RuntimeIssueCode.INVALID_SOURCE_LOCATION
    assert invalid.issues[0].event_sequence == 4
    assert invalid.issues[0].source_line == 8


@pytest.mark.parametrize("line, column", [
    (0, None),
    (-3, None),
    (True, None),
    ("4", None),
    (None, -1),
    (None, True),
])
def test_invalid_line_or_column_becomes_none_with_one_issue(line, column):
    index = _index([_file("file", "pkg/app.py")])
    result = _assoc([_draft("pkg/app.py", line, column)], index,
                    sequence=3, source_line=99)
    frame = result.frames[0]
    assert frame.line is None
    assert frame.column is None
    assert frame.association is FrameAssociation.FILE
    assert frame.primary_node_id == "file"
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.code is RuntimeIssueCode.INVALID_SOURCE_LOCATION
    assert issue.event_sequence == 3
    assert issue.source_line == 99


def test_invalid_column_is_dropped_but_the_valid_line_still_associates():
    file = _file("file", "pkg/app.py")
    symbol = GraphSymbolEntry("sym", 1, 5)
    index = _index([file], [(file.relative_path, file, symbol)])
    result = _assoc([_draft("pkg/app.py", 3, -1)], index, sequence=2,
                    source_line=17)
    frame = result.frames[0]
    assert frame.line == 3
    assert frame.column is None
    assert frame.association is FrameAssociation.SYMBOL
    assert frame.primary_node_id == "sym"
    assert len(result.issues) == 1
    assert result.issues[0].source_line == 17


def test_invalid_source_and_location_emit_only_one_issue():
    index = _index([_file("file", "pkg/app.py")])
    result = _assoc([_draft("a\x00b", 0, -1)], index, sequence=2,
                    source_line=5)
    frame = result.frames[0]
    assert frame.path_match_basis is PathMatchBasis.UNMATCHED
    assert frame.relative_path is None
    assert frame.line is None
    assert frame.column is None
    assert frame.association is FrameAssociation.UNASSOCIATED
    assert len(result.issues) == 1
    assert result.issues[0].code is RuntimeIssueCode.INVALID_SOURCE_LOCATION


def test_columns_are_retained_and_never_used_for_association():
    file = _file("file", "pkg/app.py")
    symbol = GraphSymbolEntry("sym", 1, 5)
    index = _index([file], [(file.relative_path, file, symbol)])
    for column in (0, 1, 4, 999):
        frame = _assoc([_draft("pkg/app.py", 3, column)],
                       index).frames[0]
        assert frame.column == column
        assert frame.association is FrameAssociation.SYMBOL
        assert frame.primary_node_id == "sym"


def test_candidate_ids_are_sorted_when_the_file_sorts_after_the_symbol():
    file = _file("z-file", "pkg/app.py")
    symbol = GraphSymbolEntry("a-sym", 1, 10)
    index = _index([file], [(file.relative_path, file, symbol)])
    frame = _assoc([_draft("pkg/app.py", 5)], index).frames[0]
    assert frame.association is FrameAssociation.SYMBOL
    assert frame.primary_node_id == "a-sym"
    assert frame.candidate_node_ids == ("a-sym", "z-file")
    file_only = _assoc([_draft("pkg/app.py", 11)], index).frames[0]
    assert file_only.association is FrameAssociation.FILE
    assert file_only.candidate_node_ids == ("z-file",)


def test_source_hash_is_present_exactly_when_a_path_is_retained():
    file = _file("file", "pkg/app.py")
    index = _index([file])
    ambiguous_relative = _index([
        _file("a", "one/src/app.py"), _file("b", "two/src/app.py")])
    ambiguous_absolute = _index([
        _file("root", "app.py"), _file("nested", "x/app.py")])
    frames = [
        _assoc([_draft("pkg/app.py", 1)], None).frames[0],
        _assoc([_draft("other/app.py", 1)], index).frames[0],
        _assoc([_draft("src/app.py", 1)], ambiguous_relative).frames[0],
        _assoc([_draft("pkg/app.py", 1)], index).frames[0],
        _assoc([_draft("app.py", 1)], index).frames[0],
        _assoc([_draft("app.py", 1)],
               _index([_file("a", "one/app.py"),
                       _file("b", "two/app.py")])).frames[0],
        _assoc([_draft("other.py", 1)], index).frames[0],
        _assoc([_draft("/x/app.py", 1)], ambiguous_absolute).frames[0],
        _assoc([_draft("/other/x.py", 1)], index).frames[0],
        _assoc([_draft("https://example/a.py", 1)], index).frames[0],
        _assoc([_draft(None, 1)], index).frames[0],
        _assoc([_draft("", 1)], index).frames[0],
    ]
    for frame in frames:
        assert (frame.relative_path is not None) == (
            frame.source_reference_sha256 is not None)
        if frame.relative_path is not None:
            assert frame.source_reference_sha256 == (
                source_reference_sha256(frame.relative_path))


def test_no_graph_absolute_and_basename_are_unmatched_without_path():
    for source in ("/repo/pkg/app.py", r"C:\repo\pkg\app.py", "app.py"):
        frame = _assoc([_draft(source, 1)], None).frames[0]
        assert frame.path_match_basis is PathMatchBasis.UNMATCHED
        assert frame.relative_path is None
        assert frame.source_reference_sha256 is None
        assert frame.association is FrameAssociation.NOT_EVALUATED
        assert frame.candidate_node_ids == ()


def test_graph_file_only_unknown_path_and_outside_line():
    file = _file("file", "pkg/app.py")
    symbol = GraphSymbolEntry("sym", 1, 10)
    index = _index([file], [(file.relative_path, file, symbol)])
    no_line = _assoc([_draft("pkg/app.py")], index).frames[0]
    assert no_line.association is FrameAssociation.FILE
    assert no_line.primary_node_id == "file"
    assert no_line.candidate_node_ids == ("file",)
    outside = _assoc([_draft("pkg/app.py", 11)], index).frames[0]
    assert outside.association is FrameAssociation.FILE
    unknown = _assoc([_draft("missing/app.py", 1)], index).frames[0]
    assert unknown.association is FrameAssociation.UNASSOCIATED
    assert unknown.primary_node_id is None
    assert unknown.candidate_node_ids == ()


def test_frame_ids_and_ordinals_are_occurrence_based():
    result = _assoc([_draft("pkg/app.py", 1), _draft(None)], None)
    assert type(result) is AssociationResult
    assert [frame.ordinal for frame in result.frames] == [0, 1]
    assert result.frames[0].frame_id == frame_id("repo", "e" * 64, 0)
    assert result.frames[1].frame_id == frame_id("repo", "e" * 64, 1)
    assert result.issues == ()


def test_pipeline_associates_exact_unmatched_and_ambiguous_paths():
    content = (
        b"Traceback (most recent call last):\n"
        b'  File "pkg/main.py", line 10, in run\n'
        b'  File "pkg/x.py", line 5, in ambiguous\n'
        b'  File "misc/other.py", line 7, in missing\n'
        b"ValueError: boom\n"
    )
    graph = _graph((
        _node("file-main", GraphNodeKind.FILE, "pkg/main.py"),
        _node("sym-run", GraphNodeKind.SYMBOL, "pkg/main.py",
              location=_loc(start_line=1, end_line=20)),
        _node("file-x1", GraphNodeKind.FILE, "a/pkg/x.py"),
        _node("file-x2", GraphNodeKind.FILE, "b/pkg/x.py"),
    ))
    inv = RuntimeEvidenceIngestor().ingest(RuntimeEvidenceRequest(
        "repo", (
            RuntimeArtifact("trace.stack",
                            RuntimeArtifactFormat.STACK_TRACE_TEXT,
                            content, RuntimeFrameLanguage.PYTHON),
        ),
    ), graph)
    assert inv.graph_binding is GraphBinding.NAMESPACE_MATCHED_UNVERSIONED
    assert len(inv.artifacts) == 1
    assert len(inv.events) == 1
    artifact = inv.artifacts[0]
    assert artifact.status is RuntimeArtifactStatus.COMPLETE
    (event,) = inv.events
    exact, ambiguous, unmatched = event.frames
    assert (
        exact.relative_path, exact.path_match_basis, exact.association,
        exact.primary_node_id, exact.candidate_node_ids,
    ) == (
        "pkg/main.py", PathMatchBasis.EXACT_RELATIVE_PATH,
        FrameAssociation.SYMBOL, "sym-run", ("file-main", "sym-run"),
    )
    assert (
        ambiguous.relative_path, ambiguous.path_match_basis,
        ambiguous.association, ambiguous.primary_node_id,
        ambiguous.candidate_node_ids,
    ) == (
        "pkg/x.py", PathMatchBasis.AMBIGUOUS,
        FrameAssociation.UNASSOCIATED, None, (),
    )
    assert (
        unmatched.relative_path, unmatched.path_match_basis,
        unmatched.association, unmatched.primary_node_id,
        unmatched.candidate_node_ids,
    ) == (
        "misc/other.py", PathMatchBasis.UNMATCHED,
        FrameAssociation.UNASSOCIATED, None, (),
    )
    for frame in event.frames:
        assert frame.source_reference_sha256 == (
            source_reference_sha256(frame.relative_path))
    assert dict(inv.frames_by_file) == {
        "pkg/main.py": (exact,),
        "pkg/x.py": (ambiguous,),
        "misc/other.py": (unmatched,),
    }
    assert dict(inv.frames_by_node) == {"sym-run": (exact,)}
    assert inv.issues == ()
