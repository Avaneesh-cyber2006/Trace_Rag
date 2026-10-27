from dataclasses import FrozenInstanceError, fields

import pytest

from backend.runtime_evidence.source_references import (
    NormalizedSource,
    SourceReferenceKind,
    normalize_source_reference,
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
