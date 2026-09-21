"""ECMAScript module and imported-binding resolution tests."""

import pytest

from backend.code_graph.identities import symbol_node_id
from backend.code_graph.indexes import build_graph_indexes
from backend.code_graph.models import ResolutionFailureReason
from backend.code_graph.resolvers.base import Binding, Resolution
from backend.code_graph.resolvers.ecmascript import resolve_import
from backend.code_graph.validation import validate_inputs
from backend.code_parser.models import (
    ImportBinding,
    ImportInfo,
    ParsedLanguage,
)


def _index(cg, *files):
    return build_graph_indexes(validate_inputs(*cg.pair(*files)))


def test_extensionless_import_has_no_extension_winner(cg):
    item = ImportInfo("./util", (), False, (), cg.loc(1, 10))
    source = cg.parsed(
        "main.ts", language=ParsedLanguage.TYPESCRIPT, imports=(item,)
    )
    first = cg.parsed("util.js", language=ParsedLanguage.JAVASCRIPT)
    second = cg.parsed("util/index.ts", language=ParsedLanguage.TYPESCRIPT)
    index = _index(cg, source, first, second)

    outcome = resolve_import(index, source, item).resolution

    assert outcome.reason is ResolutionFailureReason.AMBIGUOUS
    assert outcome.candidate_node_ids == tuple(
        sorted((index.file_ids["util.js"], index.file_ids["util/index.ts"]))
    )


def test_explicit_supported_extension_selects_only_the_exact_file(cg):
    item = ImportInfo("./util.js", (), False, (), cg.loc(1, 12))
    source = cg.parsed(
        "main.js", language=ParsedLanguage.JAVASCRIPT, imports=(item,)
    )
    exact = cg.parsed("util.js", language=ParsedLanguage.JAVASCRIPT)
    rewrite = cg.parsed("util.ts", language=ParsedLanguage.TYPESCRIPT)
    directory = cg.parsed("util.js/index.js", language=ParsedLanguage.JAVASCRIPT)
    index = _index(cg, source, exact, rewrite, directory)

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(index.file_ids["util.js"], None)


def test_extensionless_import_retains_all_eight_bounded_candidates(cg):
    item = ImportInfo("./lib", (), False, (), cg.loc(1, 10))
    source = cg.parsed(
        "src/main.ts", language=ParsedLanguage.TYPESCRIPT, imports=(item,)
    )
    target_specs = (
        ("src/lib.js", ParsedLanguage.JAVASCRIPT),
        ("src/lib/index.js", ParsedLanguage.JAVASCRIPT),
        ("src/lib.jsx", ParsedLanguage.JAVASCRIPT),
        ("src/lib/index.jsx", ParsedLanguage.JAVASCRIPT),
        ("src/lib.ts", ParsedLanguage.TYPESCRIPT),
        ("src/lib/index.ts", ParsedLanguage.TYPESCRIPT),
        ("src/lib.tsx", ParsedLanguage.TSX),
        ("src/lib/index.tsx", ParsedLanguage.TSX),
    )
    targets = tuple(
        cg.parsed(path, language=language) for path, language in target_specs
    )
    index = _index(cg, source, *targets)

    outcome = resolve_import(index, source, item).resolution

    assert outcome == Resolution(
        None,
        ResolutionFailureReason.AMBIGUOUS,
        tuple(sorted(index.file_ids[path] for path, _ in target_specs)),
    )


@pytest.mark.parametrize(
    ("target_path", "target_language"),
    [
        pytest.param("view.jsx", ParsedLanguage.JAVASCRIPT, id="jsx-is-javascript"),
        pytest.param("view.tsx", ParsedLanguage.TSX, id="tsx"),
    ],
)
def test_supported_ecmascript_file_languages_are_eligible(
    cg, target_path, target_language
):
    item = ImportInfo(f"./{target_path}", (), False, (), cg.loc(1, 20))
    source = cg.parsed(
        "main.ts", language=ParsedLanguage.TYPESCRIPT, imports=(item,)
    )
    target = cg.parsed(target_path, language=target_language)
    index = _index(cg, source, target)

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(index.file_ids[target_path], None)


@pytest.mark.parametrize(
    "module",
    ["../../util", "../util"],
)
def test_relative_path_escape_has_no_internal_target(cg, module):
    item = ImportInfo(module, (), False, (), cg.loc(1, 20))
    source = cg.parsed(
        "main.js", language=ParsedLanguage.JAVASCRIPT, imports=(item,)
    )
    target = cg.parsed("util.js", language=ParsedLanguage.JAVASCRIPT)
    index = _index(cg, source, target)

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(
        None, ResolutionFailureReason.NOT_FOUND
    )


@pytest.mark.parametrize("module", ["", ".", "./", "./a//b", "./bad\\path"])
def test_empty_or_malformed_specifier_is_unsupported(cg, module):
    item = ImportInfo(module, (), False, (), cg.loc(1, 20))
    source = cg.parsed(
        "main.js", language=ParsedLanguage.JAVASCRIPT, imports=(item,)
    )
    index = _index(cg, source)

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(
        None, ResolutionFailureReason.UNSUPPORTED
    )


def test_missing_relative_file_is_not_found(cg):
    item = ImportInfo("./missing", (), False, (), cg.loc(1, 20))
    source = cg.parsed(
        "main.js", language=ParsedLanguage.JAVASCRIPT, imports=(item,)
    )
    index = _index(cg, source)

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(
        None, ResolutionFailureReason.NOT_FOUND
    )


@pytest.mark.parametrize("module", ["react", "@scope/pkg", "ordinary-alias"])
def test_bare_package_specifier_is_external(cg, module):
    item = ImportInfo(module, (), False, (), cg.loc(1, 20))
    source = cg.parsed(
        "main.js", language=ParsedLanguage.JAVASCRIPT, imports=(item,)
    )
    index = _index(cg, source)

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(
        None, ResolutionFailureReason.EXTERNAL
    )


def test_configured_alias_form_is_unsupported(cg):
    item = ImportInfo("@/util", (), False, (), cg.loc(1, 20))
    source = cg.parsed(
        "main.ts", language=ParsedLanguage.TYPESCRIPT, imports=(item,)
    )
    index = _index(cg, source)

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(
        None, ResolutionFailureReason.UNSUPPORTED
    )


def test_unsupported_explicit_extension_does_not_probe_supported_files(cg):
    item = ImportInfo("./util.mjs", (), False, (), cg.loc(1, 20))
    source = cg.parsed(
        "main.js", language=ParsedLanguage.JAVASCRIPT, imports=(item,)
    )
    target = cg.parsed("util.js", language=ParsedLanguage.JAVASCRIPT)
    index = _index(cg, source, target)

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(
        None, ResolutionFailureReason.UNSUPPORTED
    )


def test_named_export_binding_uses_exact_name_and_preserves_alias(cg):
    item = ImportInfo(
        "./util.js",
        (ImportBinding("run", "execute"),),
        False,
        (),
        cg.loc(1, 30),
    )
    source = cg.parsed(
        "main.js", language=ParsedLanguage.JAVASCRIPT, imports=(item,)
    )
    exported = cg.symbol("run", modifiers=("export",))
    target = cg.parsed(
        "util.js", language=ParsedLanguage.JAVASCRIPT, symbols=(exported,)
    )
    index = _index(cg, source, target)
    exported_id = symbol_node_id("repo", "util.js", exported)

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(index.file_ids["util.js"], None)
    assert result.bindings == (
        Binding(
            "execute",
            index.file_ids["main.js"],
            "symbol",
            Resolution(exported_id, None),
        ),
    )


def test_unexported_same_name_does_not_become_named_export(cg):
    item = ImportInfo(
        "./util.js",
        (ImportBinding("run", None),),
        False,
        (),
        cg.loc(1, 30),
    )
    source = cg.parsed(
        "main.js", language=ParsedLanguage.JAVASCRIPT, imports=(item,)
    )
    hidden = cg.symbol("run")
    target = cg.parsed(
        "util.js", language=ParsedLanguage.JAVASCRIPT, symbols=(hidden,)
    )
    index = _index(cg, source, target)

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(index.file_ids["util.js"], None)
    assert result.bindings == (
        Binding(
            "run",
            index.file_ids["main.js"],
            "blocked",
            Resolution(None, ResolutionFailureReason.UNSUPPORTED),
        ),
    )


def test_default_requires_export_and_default_modifier_evidence(cg):
    item = ImportInfo(
        "./util.js",
        (ImportBinding("default", "run"),),
        False,
        (),
        cg.loc(1, 30),
    )
    source = cg.parsed(
        "main.js", language=ParsedLanguage.JAVASCRIPT, imports=(item,)
    )
    sole_export = cg.symbol("only", modifiers=("export",))
    target = cg.parsed(
        "util.js", language=ParsedLanguage.JAVASCRIPT, symbols=(sole_export,)
    )
    index = _index(cg, source, target)

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(index.file_ids["util.js"], None)
    assert result.bindings == (
        Binding(
            "run",
            index.file_ids["main.js"],
            "blocked",
            Resolution(None, ResolutionFailureReason.UNSUPPORTED),
        ),
    )


def test_default_binding_resolves_unique_modifier_backed_export(cg):
    item = ImportInfo(
        "./util.js",
        (ImportBinding("default", "run"),),
        False,
        (),
        cg.loc(1, 30),
    )
    source = cg.parsed(
        "main.js", language=ParsedLanguage.JAVASCRIPT, imports=(item,)
    )
    default = cg.symbol("worker", modifiers=("export", "default"))
    target = cg.parsed(
        "util.js", language=ParsedLanguage.JAVASCRIPT, symbols=(default,)
    )
    index = _index(cg, source, target)

    result = resolve_import(index, source, item)

    assert result.bindings == (
        Binding(
            "run",
            index.file_ids["main.js"],
            "symbol",
            Resolution(symbol_node_id("repo", "util.js", default), None),
        ),
    )


def test_conflicting_exported_declarations_remain_ambiguous(cg):
    item = ImportInfo(
        "./util.js",
        (ImportBinding("run", None),),
        False,
        (),
        cg.loc(1, 30),
    )
    source = cg.parsed(
        "main.js", language=ParsedLanguage.JAVASCRIPT, imports=(item,)
    )
    first = cg.symbol("run", modifiers=("export",), start=0, end=30)
    second = cg.symbol("run", modifiers=("export",), start=40, end=70)
    target = cg.parsed(
        "util.js", language=ParsedLanguage.JAVASCRIPT, symbols=(first, second)
    )
    index = _index(cg, source, target)
    candidates = tuple(
        sorted(
            (
                symbol_node_id("repo", "util.js", first),
                symbol_node_id("repo", "util.js", second),
            )
        )
    )

    result = resolve_import(index, source, item)

    assert result.bindings == (
        Binding(
            "run",
            index.file_ids["main.js"],
            "symbol",
            Resolution(None, ResolutionFailureReason.AMBIGUOUS, candidates),
        ),
    )


def test_namespace_binding_is_scoped_to_the_unique_module(cg):
    item = ImportInfo(
        "./util.js",
        (ImportBinding("*", "util"),),
        True,
        (),
        cg.loc(1, 30),
    )
    source = cg.parsed(
        "main.js", language=ParsedLanguage.JAVASCRIPT, imports=(item,)
    )
    target = cg.parsed("util.js", language=ParsedLanguage.JAVASCRIPT)
    unrelated = cg.parsed("other.js", language=ParsedLanguage.JAVASCRIPT)
    index = _index(cg, source, target, unrelated)

    result = resolve_import(index, source, item)

    assert result.bindings == (
        Binding(
            "util",
            index.file_ids["main.js"],
            "module",
            Resolution(index.file_ids["util.js"], None),
        ),
    )


def test_wildcard_marker_without_namespace_binding_blocks_item_bindings(cg):
    item = ImportInfo(
        "./util.js",
        (ImportBinding("run", None),),
        True,
        (),
        cg.loc(1, 30),
    )
    source = cg.parsed(
        "main.js", language=ParsedLanguage.JAVASCRIPT, imports=(item,)
    )
    exported = cg.symbol("run", modifiers=("export",))
    target = cg.parsed(
        "util.js", language=ParsedLanguage.JAVASCRIPT, symbols=(exported,)
    )
    index = _index(cg, source, target)

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(index.file_ids["util.js"], None)
    assert result.bindings == (
        Binding(
            "run",
            index.file_ids["main.js"],
            "blocked",
            Resolution(None, ResolutionFailureReason.UNSUPPORTED),
        ),
    )


def test_default_and_namespace_import_is_a_valid_wildcard_shape(cg):
    item = ImportInfo(
        "./util.js",
        (
            ImportBinding("default", "primary"),
            ImportBinding("*", "namespace"),
        ),
        True,
        (),
        cg.loc(1, 40),
    )
    source = cg.parsed(
        "main.js", language=ParsedLanguage.JAVASCRIPT, imports=(item,)
    )
    default = cg.symbol("worker", modifiers=("export", "default"))
    target = cg.parsed(
        "util.js", language=ParsedLanguage.JAVASCRIPT, symbols=(default,)
    )
    index = _index(cg, source, target)

    result = resolve_import(index, source, item)

    assert result.bindings == (
        Binding(
            "primary",
            index.file_ids["main.js"],
            "symbol",
            Resolution(symbol_node_id("repo", "util.js", default), None),
        ),
        Binding(
            "namespace",
            index.file_ids["main.js"],
            "module",
            Resolution(index.file_ids["util.js"], None),
        ),
    )


@pytest.mark.parametrize(
    ("imported_name", "alias"),
    [
        pytest.param("run\u200c", "execute", id="imported-zwnj"),
        pytest.param("run", "execute\u200c", id="alias-zwnj"),
    ],
)
def test_ecmascript_identifier_part_is_preserved_exactly(
    cg, imported_name, alias
):
    item = ImportInfo(
        "./util.js",
        (ImportBinding(imported_name, alias),),
        False,
        (),
        cg.loc(1, 30),
    )
    source = cg.parsed(
        "main.js", language=ParsedLanguage.JAVASCRIPT, imports=(item,)
    )
    exported = cg.symbol(imported_name, modifiers=("export",))
    target = cg.parsed(
        "util.js", language=ParsedLanguage.JAVASCRIPT, symbols=(exported,)
    )
    index = _index(cg, source, target)

    result = resolve_import(index, source, item)

    assert result.bindings == (
        Binding(
            alias,
            index.file_ids["main.js"],
            "symbol",
            Resolution(symbol_node_id("repo", "util.js", exported), None),
        ),
    )


def test_ambiguous_module_preserves_single_known_named_export_candidate(cg):
    item = ImportInfo(
        "./util",
        (ImportBinding("run", None),),
        False,
        (),
        cg.loc(1, 30),
    )
    source = cg.parsed(
        "main.ts", language=ParsedLanguage.TYPESCRIPT, imports=(item,)
    )
    exported = cg.symbol("run", modifiers=("export",))
    first = cg.parsed(
        "util.js", language=ParsedLanguage.JAVASCRIPT, symbols=(exported,)
    )
    second = cg.parsed("util.ts", language=ParsedLanguage.TYPESCRIPT)
    index = _index(cg, source, first, second)
    known = symbol_node_id("repo", "util.js", exported)

    result = resolve_import(index, source, item)

    assert result.bindings == (
        Binding(
            "run",
            index.file_ids["main.ts"],
            "symbol",
            Resolution(None, ResolutionFailureReason.AMBIGUOUS, (known,)),
        ),
    )


def test_ambiguous_binding_scope_preserves_known_target_and_file_import(cg):
    first_owner = cg.symbol("first", start=0, end=100)
    second_owner = cg.symbol("second", start=10, end=110)
    item = ImportInfo(
        "./util.js",
        (ImportBinding("run", None),),
        False,
        (),
        cg.loc(20, 30),
    )
    source = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(first_owner, second_owner),
        imports=(item,),
    )
    exported = cg.symbol("run", modifiers=("export",))
    target = cg.parsed(
        "util.js", language=ParsedLanguage.JAVASCRIPT, symbols=(exported,)
    )
    index = _index(cg, source, target)
    known = symbol_node_id("repo", "util.js", exported)

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(index.file_ids["util.js"], None)
    assert result.bindings == (
        Binding(
            "run",
            index.file_ids["main.js"],
            "blocked",
            Resolution(None, ResolutionFailureReason.AMBIGUOUS, (known,)),
        ),
    )


def test_import_modifiers_block_binding_without_losing_file_import(cg):
    item = ImportInfo(
        "./util.js",
        (ImportBinding("run", None),),
        False,
        ("unsupported-form",),
        cg.loc(1, 30),
    )
    source = cg.parsed(
        "main.js", language=ParsedLanguage.JAVASCRIPT, imports=(item,)
    )
    exported = cg.symbol("run", modifiers=("export",))
    target = cg.parsed(
        "util.js", language=ParsedLanguage.JAVASCRIPT, symbols=(exported,)
    )
    index = _index(cg, source, target)

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(index.file_ids["util.js"], None)
    assert result.bindings == (
        Binding(
            "run",
            index.file_ids["main.js"],
            "blocked",
            Resolution(None, ResolutionFailureReason.UNSUPPORTED),
        ),
    )


def test_missing_export_map_evidence_is_unsupported(cg):
    item = ImportInfo(
        "./barrel.js",
        (ImportBinding("reexported", None),),
        False,
        (),
        cg.loc(1, 30),
    )
    source = cg.parsed(
        "main.js", language=ParsedLanguage.JAVASCRIPT, imports=(item,)
    )
    target = cg.parsed("barrel.js", language=ParsedLanguage.JAVASCRIPT)
    index = _index(cg, source, target)

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(index.file_ids["barrel.js"], None)
    assert result.bindings == (
        Binding(
            "reexported",
            index.file_ids["main.js"],
            "blocked",
            Resolution(None, ResolutionFailureReason.UNSUPPORTED),
        ),
    )
