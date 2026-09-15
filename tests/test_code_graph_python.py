"""Python import resolution tests."""

import pytest

from backend.code_graph.indexes import build_graph_indexes
from backend.code_graph.identities import symbol_node_id
from backend.code_graph.models import ResolutionFailureReason
from backend.code_graph.resolvers.base import Binding, Resolution
from backend.code_graph.resolvers.python import resolve_import
from backend.code_graph.validation import validate_inputs
from backend.code_parser.models import (
    ImportBinding,
    ImportInfo,
    ParseIssue,
    ParseIssueKind,
    ParseStatus,
)


def test_absolute_module_import(cg):
    item = ImportInfo(
        "pkg.util",
        (ImportBinding("pkg.util", None),),
        False,
        (),
        cg.loc(1, 9),
    )
    source = cg.parsed("main.py", imports=(item,))
    target = cg.parsed("pkg/util.py")
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))

    result = resolve_import(index, source, item)

    assert result.resolution.target_node_id == index.file_ids["pkg/util.py"]


def test_module_and_package_candidates_remain_ambiguous(cg):
    item = ImportInfo("pkg", (), False, (), cg.loc(1, 4))
    source = cg.parsed("main.py", imports=(item,))
    module = cg.parsed("pkg.py")
    package = cg.parsed("pkg/__init__.py")
    index = build_graph_indexes(
        validate_inputs(*cg.pair(source, module, package))
    )

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(
        None,
        ResolutionFailureReason.AMBIGUOUS,
        tuple(
            sorted(
                (
                    index.file_ids["pkg.py"],
                    index.file_ids["pkg/__init__.py"],
                )
            )
        ),
    )


@pytest.mark.parametrize(
    ("source_path", "module", "target_path"),
    [
        pytest.param("pkg/main.py", ".util", "pkg/util.py", id="same-package"),
        pytest.param(
            "pkg/sub/main.py",
            "..util",
            "pkg/util.py",
            id="parent-package",
        ),
    ],
)
def test_relative_module_imports_anchor_to_package_directory(
    cg, source_path, module, target_path
):
    item = ImportInfo(module, (), False, (), cg.loc(1, 9))
    source = cg.parsed(source_path, imports=(item,))
    target = cg.parsed(target_path)
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(index.file_ids[target_path], None)


@pytest.mark.parametrize(
    ("source_path", "module"),
    [
        pytest.param("main.py", ".util", id="root-file-has-no-package"),
        pytest.param("pkg/main.py", "...util", id="escape-above-root"),
    ],
)
def test_invalid_relative_imports_are_not_found(cg, source_path, module):
    item = ImportInfo(module, (), False, (), cg.loc(1, 9))
    source = cg.parsed(source_path, imports=(item,))
    target = cg.parsed("util.py")
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(
        None, ResolutionFailureReason.NOT_FOUND
    )


def test_missing_absolute_package_is_external(cg):
    item = ImportInfo("thirdparty.client", (), False, (), cg.loc(1, 18))
    source = cg.parsed("main.py", imports=(item,))
    index = build_graph_indexes(validate_inputs(*cg.pair(source)))

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(
        None, ResolutionFailureReason.EXTERNAL
    )


def test_failed_file_remains_a_valid_module_import_target(cg):
    item = ImportInfo("broken", (), False, (), cg.loc(1, 7))
    source = cg.parsed("main.py", imports=(item,))
    target = cg.parsed("broken.py", status=ParseStatus.FAILED)
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(index.file_ids["broken.py"], None)


def test_distinct_from_import_binding_resolves_a_top_level_symbol(cg):
    item = ImportInfo(
        "pkg.util",
        (ImportBinding("run", "execute"),),
        False,
        (),
        cg.loc(20, 45),
    )
    owner = cg.symbol("load", start=10, end=100)
    source = cg.parsed("main.py", symbols=(owner,), imports=(item,))
    imported = cg.symbol("run", start=5, end=15)
    target = cg.parsed("pkg/util.py", symbols=(imported,))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))

    result = resolve_import(index, source, item)

    assert result.bindings == (
        Binding(
            "execute",
            symbol_node_id("repo", "main.py", owner),
            "symbol",
            Resolution(symbol_node_id("repo", "pkg/util.py", imported), None),
        ),
    )


def test_dotted_direct_import_creates_a_module_binding(cg):
    item = ImportInfo(
        "pkg.util",
        (ImportBinding("pkg.util", "util"),),
        False,
        (),
        cg.loc(1, 20),
    )
    source = cg.parsed("main.py", imports=(item,))
    target = cg.parsed("pkg/util.py")
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))

    result = resolve_import(index, source, item)

    assert result.bindings == (
        Binding(
            "util",
            index.file_ids["main.py"],
            "module",
            Resolution(index.file_ids["pkg/util.py"], None),
        ),
    )


def test_indistinguishable_import_form_preserves_file_and_blocks_binding(cg):
    item = ImportInfo(
        "x",
        (ImportBinding("x", "y"),),
        False,
        (),
        cg.loc(1, 14),
    )
    source = cg.parsed("main.py", imports=(item,))
    member = cg.symbol("x")
    target = cg.parsed("x.py", symbols=(member,))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(index.file_ids["x.py"], None)
    assert result.bindings == (
        Binding(
            "y",
            index.file_ids["main.py"],
            "blocked",
            Resolution(None, ResolutionFailureReason.UNSUPPORTED),
        ),
    )


@pytest.mark.parametrize("target_status", [ParseStatus.PARTIAL, ParseStatus.FAILED])
def test_missing_member_in_incomplete_target_is_incomplete_parse(
    cg, target_status
):
    item = ImportInfo(
        "pkg.util",
        (ImportBinding("missing", None),),
        False,
        (),
        cg.loc(1, 25),
    )
    source = cg.parsed("main.py", imports=(item,))
    issues = (
        ParseIssue(ParseIssueKind.EXTRACTION_ERROR, "incomplete", None),
    )
    target = cg.parsed(
        "pkg/util.py",
        status=target_status,
        issues=issues,
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(index.file_ids["pkg/util.py"], None)
    assert result.bindings == (
        Binding(
            "missing",
            index.file_ids["main.py"],
            "symbol",
            Resolution(None, ResolutionFailureReason.INCOMPLETE_PARSE),
        ),
    )


def test_wildcard_preserves_only_bounded_scope_evidence(cg):
    item = ImportInfo("pkg.util", (), True, (), cg.loc(1, 23))
    source = cg.parsed("main.py", imports=(item,))
    imported = cg.symbol("run")
    target = cg.parsed("pkg/util.py", symbols=(imported,))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(index.file_ids["pkg/util.py"], None)
    assert result.bindings == ()


def test_repeated_local_name_bindings_are_all_preserved(cg):
    item = ImportInfo(
        "pkg.util",
        (
            ImportBinding("first", "same"),
            ImportBinding("second", "same"),
        ),
        False,
        (),
        cg.loc(1, 40),
    )
    source = cg.parsed("main.py", imports=(item,))
    first = cg.symbol("first", start=1, end=10)
    second = cg.symbol("second", start=20, end=30)
    target = cg.parsed("pkg/util.py", symbols=(first, second))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))

    result = resolve_import(index, source, item)

    assert result.bindings == (
        Binding(
            "same",
            index.file_ids["main.py"],
            "symbol",
            Resolution(symbol_node_id("repo", "pkg/util.py", first), None),
        ),
        Binding(
            "same",
            index.file_ids["main.py"],
            "symbol",
            Resolution(symbol_node_id("repo", "pkg/util.py", second), None),
        ),
    )


def test_ambiguous_binding_scope_blocks_only_the_dependent_binding(cg):
    first_owner = cg.symbol("first_owner", start=0, end=100)
    second_owner = cg.symbol("second_owner", start=10, end=110)
    item = ImportInfo(
        "pkg.util",
        (ImportBinding("run", None),),
        False,
        (),
        cg.loc(20, 30),
    )
    source = cg.parsed(
        "main.py",
        symbols=(first_owner, second_owner),
        imports=(item,),
    )
    target = cg.parsed("pkg/util.py", symbols=(cg.symbol("run"),))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(index.file_ids["pkg/util.py"], None)
    assert result.bindings == (
        Binding(
            "run",
            index.file_ids["main.py"],
            "blocked",
            Resolution(None, ResolutionFailureReason.AMBIGUOUS),
        ),
    )
