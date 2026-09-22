"""ECMAScript module and imported-binding resolution tests."""

import pytest

from backend.code_graph.identities import symbol_node_id
from backend.code_graph.indexes import build_graph_indexes
from backend.code_graph.models import ReferenceKind, ResolutionFailureReason
from backend.code_graph.references import call_reference, type_references
from backend.code_graph.resolvers.base import (
    Binding,
    ImportEnvironment,
    Resolution,
)
from backend.code_graph.resolvers.ecmascript import resolve_import, resolve_reference
from backend.code_graph.validation import validate_inputs
from backend.code_parser.models import (
    CallKind,
    CallSite,
    ImportBinding,
    ImportInfo,
    ParameterInfo,
    ParsedLanguage,
    SymbolKind,
)


def _index(cg, *files):
    return build_graph_indexes(validate_inputs(*cg.pair(*files)))


def _environment(index, *files):
    return ImportEnvironment(
        {
            file.relative_path: tuple(
                (item, resolve_import(index, file, item)) for item in file.imports
            )
            for file in files
        }
    )


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


def test_typescript_implements_uses_declaration_location(cg):
    interface = cg.symbol("Port", kind=SymbolKind.INTERFACE, start=0, end=50)
    concrete = cg.symbol(
        "Adapter",
        kind=SymbolKind.CLASS,
        start=60,
        end=160,
        implemented_types=("Port",),
    )
    file = cg.parsed(
        "a.ts",
        language=ParsedLanguage.TYPESCRIPT,
        symbols=(interface, concrete),
    )
    index = _index(cg, file)
    source_id = index.by_file_name[("a.ts", "Adapter")][0]
    (reference,) = type_references(index, file, source_id)

    assert reference.location == concrete.location
    assert resolve_reference(
        index, ImportEnvironment({"a.ts": ()}), reference
    ).target_node_id == index.by_file_name[("a.ts", "Port")][0]


@pytest.mark.parametrize(
    ("path", "language"),
    [
        pytest.param("local.js", ParsedLanguage.JAVASCRIPT, id="javascript"),
        pytest.param("local.jsx", ParsedLanguage.JAVASCRIPT, id="jsx"),
        pytest.param("local.ts", ParsedLanguage.TYPESCRIPT, id="typescript"),
        pytest.param("local.tsx", ParsedLanguage.TSX, id="tsx"),
    ],
)
def test_local_function_call_resolves_in_each_ecmascript_mode(
    cg, path, language
):
    helper = cg.symbol("helper", start=0, end=40)
    caller = cg.symbol("caller", start=50, end=150)
    call = CallSite("caller", "helper", CallKind.CALL, cg.loc(80, 90))
    file = cg.parsed(
        path,
        language=language,
        symbols=(helper, caller),
        calls=(call,),
    )
    index = _index(cg, file)

    result = resolve_reference(
        index,
        ImportEnvironment({path: ()}),
        call_reference(index, file, call),
    )

    assert result == Resolution(symbol_node_id("repo", path, helper), None)


def test_nested_lexical_function_precedes_same_file_declaration(cg):
    file_helper = cg.symbol("helper", start=0, end=40)
    caller = cg.symbol("caller", start=50, end=300)
    nested_helper = cg.symbol(
        "helper",
        qualified_name="caller.helper",
        parent="caller",
        start=80,
        end=120,
    )
    call = CallSite("caller", "helper", CallKind.CALL, cg.loc(180, 190))
    file = cg.parsed(
        "scope.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(file_helper, caller, nested_helper),
        calls=(call,),
    )
    index = _index(cg, file)

    result = resolve_reference(
        index,
        ImportEnvironment({"scope.js": ()}),
        call_reference(index, file, call),
    )

    assert result == Resolution(
        symbol_node_id("repo", "scope.js", nested_helper), None
    )


def test_named_import_alias_call_uses_exported_callable(cg):
    item = ImportInfo(
        "./util.js",
        (ImportBinding("run", "execute"),),
        False,
        (),
        cg.loc(0, 15),
    )
    caller = cg.symbol("main", start=20, end=100)
    call = CallSite("main", "execute", CallKind.CALL, cg.loc(50, 60))
    source = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(caller,),
        imports=(item,),
        calls=(call,),
    )
    exported = cg.symbol("run", modifiers=("export",))
    target = cg.parsed(
        "util.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(exported,),
    )
    index = _index(cg, source, target)

    result = resolve_reference(
        index,
        _environment(index, source, target),
        call_reference(index, source, call),
    )

    assert result == Resolution(
        symbol_node_id("repo", "util.js", exported), None
    )


def test_default_import_call_requires_modifier_backed_export(cg):
    item = ImportInfo(
        "./util.js",
        (ImportBinding("default", "execute"),),
        False,
        (),
        cg.loc(0, 15),
    )
    caller = cg.symbol("main", start=20, end=100)
    call = CallSite("main", "execute", CallKind.CALL, cg.loc(50, 60))
    source = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(caller,),
        imports=(item,),
        calls=(call,),
    )
    exported = cg.symbol("worker", modifiers=("export", "default"))
    target = cg.parsed(
        "util.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(exported,),
    )
    index = _index(cg, source, target)

    result = resolve_reference(
        index,
        _environment(index, source, target),
        call_reference(index, source, call),
    )

    assert result == Resolution(
        symbol_node_id("repo", "util.js", exported), None
    )


def test_namespace_member_call_uses_exact_export(cg):
    item = ImportInfo(
        "./util.js",
        (ImportBinding("*", "util"),),
        True,
        (),
        cg.loc(0, 15),
    )
    caller = cg.symbol("main", start=20, end=100)
    call = CallSite("main", "util.run", CallKind.CALL, cg.loc(50, 60))
    source = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(caller,),
        imports=(item,),
        calls=(call,),
    )
    exported = cg.symbol("run", modifiers=("export",))
    target = cg.parsed(
        "util.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(exported,),
    )
    index = _index(cg, source, target)

    result = resolve_reference(
        index,
        _environment(index, source, target),
        call_reference(index, source, call),
    )

    assert result == Resolution(
        symbol_node_id("repo", "util.js", exported), None
    )


def test_missing_named_export_blocks_unrelated_repository_fallback(cg):
    item = ImportInfo(
        "./barrel.js",
        (ImportBinding("reexported", None),),
        False,
        (),
        cg.loc(0, 15),
    )
    caller = cg.symbol("main", start=20, end=100)
    call = CallSite("main", "reexported", CallKind.CALL, cg.loc(50, 60))
    source = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(caller,),
        imports=(item,),
        calls=(call,),
    )
    barrel = cg.parsed("barrel.js", language=ParsedLanguage.JAVASCRIPT)
    unrelated_symbol = cg.symbol("reexported", modifiers=("export",))
    unrelated = cg.parsed(
        "other.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(unrelated_symbol,),
    )
    index = _index(cg, source, barrel, unrelated)

    result = resolve_reference(
        index,
        _environment(index, source, barrel, unrelated),
        call_reference(index, source, call),
    )

    assert result == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


@pytest.mark.parametrize(
    "callee",
    ["service.run", 'service["run"]', "util.run.call", "run<T>"],
)
def test_unproven_or_complex_member_call_is_unsupported(cg, callee):
    caller = cg.symbol("main", start=0, end=100)
    call = CallSite("main", callee, CallKind.CALL, cg.loc(40, 50))
    file = cg.parsed(
        "main.ts",
        language=ParsedLanguage.TYPESCRIPT,
        symbols=(caller,),
        calls=(call,),
    )
    index = _index(cg, file)

    result = resolve_reference(
        index,
        ImportEnvironment({"main.ts": ()}),
        call_reference(index, file, call),
    )

    assert result == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def test_computed_namespace_member_call_is_unsupported(cg):
    item = ImportInfo(
        "./util.js",
        (ImportBinding("*", "util"),),
        True,
        (),
        cg.loc(0, 15),
    )
    caller = cg.symbol("main", start=20, end=100)
    call = CallSite("main", 'util["run"]', CallKind.CALL, cg.loc(50, 60))
    source = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(caller,),
        imports=(item,),
        calls=(call,),
    )
    exported = cg.symbol("run", modifiers=("export",))
    target = cg.parsed(
        "util.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(exported,),
    )
    index = _index(cg, source, target)

    result = resolve_reference(
        index,
        _environment(index, source, target),
        call_reference(index, source, call),
    )

    assert result == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def test_top_level_call_keeps_null_source_and_is_unsupported(cg):
    helper = cg.symbol("helper", start=0, end=20)
    call = CallSite(None, "helper", CallKind.CALL, cg.loc(40, 50))
    file = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(helper,),
        calls=(call,),
    )
    index = _index(cg, file)
    reference = call_reference(index, file, call)

    assert reference.source_node_id is None
    assert resolve_reference(
        index, ImportEnvironment({"main.js": ()}), reference
    ) == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def test_unknown_bare_call_is_not_found(cg):
    caller = cg.symbol("main", start=0, end=100)
    call = CallSite("main", "missing", CallKind.CALL, cg.loc(40, 50))
    file = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(caller,),
        calls=(call,),
    )
    index = _index(cg, file)

    result = resolve_reference(
        index,
        ImportEnvironment({"main.js": ()}),
        call_reference(index, file, call),
    )

    assert result == Resolution(None, ResolutionFailureReason.NOT_FOUND)


def test_ambiguous_same_file_call_preserves_sorted_candidates(cg):
    first = cg.symbol("helper", start=0, end=20)
    second = cg.symbol("helper", start=30, end=50)
    caller = cg.symbol("main", start=60, end=160)
    call = CallSite("main", "helper", CallKind.CALL, cg.loc(100, 110))
    file = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(first, second, caller),
        calls=(call,),
    )
    index = _index(cg, file)
    expected = tuple(
        sorted(
            (
                symbol_node_id("repo", "main.js", first),
                symbol_node_id("repo", "main.js", second),
            )
        )
    )

    result = resolve_reference(
        index,
        ImportEnvironment({"main.js": ()}),
        call_reference(index, file, call),
    )

    assert result == Resolution(
        None, ResolutionFailureReason.AMBIGUOUS, expected
    )


def test_constructor_call_targets_explicit_constructor_child(cg):
    service = cg.symbol(
        "Service", kind=SymbolKind.CLASS, start=0, end=100
    )
    constructor = cg.symbol(
        "constructor",
        kind=SymbolKind.CONSTRUCTOR,
        qualified_name="Service.constructor",
        parent="Service",
        start=20,
        end=50,
    )
    caller = cg.symbol("main", start=120, end=220)
    call = CallSite("main", "Service", CallKind.CONSTRUCTOR, cg.loc(160, 170))
    file = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(service, constructor, caller),
        calls=(call,),
    )
    index = _index(cg, file)

    result = resolve_reference(
        index,
        ImportEnvironment({"main.js": ()}),
        call_reference(index, file, call),
    )

    assert result == Resolution(
        symbol_node_id("repo", "main.js", constructor), None
    )


def test_constructor_call_does_not_fabricate_default_constructor(cg):
    service = cg.symbol(
        "Service", kind=SymbolKind.CLASS, start=0, end=100
    )
    caller = cg.symbol("main", start=120, end=220)
    call = CallSite("main", "Service", CallKind.CONSTRUCTOR, cg.loc(160, 170))
    file = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(service, caller),
        calls=(call,),
    )
    index = _index(cg, file)

    result = resolve_reference(
        index,
        ImportEnvironment({"main.js": ()}),
        call_reference(index, file, call),
    )

    assert result == Resolution(None, ResolutionFailureReason.NOT_FOUND)


@pytest.mark.parametrize(
    ("path", "language"),
    [
        pytest.param("types.js", ParsedLanguage.JAVASCRIPT, id="javascript"),
        pytest.param("types.ts", ParsedLanguage.TYPESCRIPT, id="typescript"),
    ],
)
def test_class_extends_same_file_class(cg, path, language):
    base = cg.symbol("Base", kind=SymbolKind.CLASS, start=0, end=50)
    child = cg.symbol(
        "Child",
        kind=SymbolKind.CLASS,
        start=60,
        end=160,
        base_types=("Base",),
    )
    file = cg.parsed(path, language=language, symbols=(base, child))
    index = _index(cg, file)
    source_id = symbol_node_id("repo", path, child)
    (reference,) = type_references(index, file, source_id)

    result = resolve_reference(
        index, ImportEnvironment({path: ()}), reference
    )

    assert result == Resolution(symbol_node_id("repo", path, base), None)


def test_typescript_interface_extends_interface(cg):
    base = cg.symbol("Base", kind=SymbolKind.INTERFACE, start=0, end=50)
    child = cg.symbol(
        "Child",
        kind=SymbolKind.INTERFACE,
        start=60,
        end=160,
        base_types=("Base",),
    )
    file = cg.parsed(
        "types.ts",
        language=ParsedLanguage.TYPESCRIPT,
        symbols=(base, child),
    )
    index = _index(cg, file)
    (reference,) = type_references(
        index, file, symbol_node_id("repo", "types.ts", child)
    )

    result = resolve_reference(
        index, ImportEnvironment({"types.ts": ()}), reference
    )

    assert result == Resolution(
        symbol_node_id("repo", "types.ts", base), None
    )


def test_class_to_interface_inherits_is_unsupported(cg):
    interface = cg.symbol(
        "Port", kind=SymbolKind.INTERFACE, start=0, end=50
    )
    concrete = cg.symbol(
        "Adapter",
        kind=SymbolKind.CLASS,
        start=60,
        end=160,
        base_types=("Port",),
    )
    file = cg.parsed(
        "types.ts",
        language=ParsedLanguage.TYPESCRIPT,
        symbols=(interface, concrete),
    )
    index = _index(cg, file)
    (reference,) = type_references(
        index, file, symbol_node_id("repo", "types.ts", concrete)
    )

    result = resolve_reference(
        index, ImportEnvironment({"types.ts": ()}), reference
    )

    assert result == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def test_generic_type_expression_is_unsupported(cg):
    base = cg.symbol("Base", kind=SymbolKind.CLASS, start=0, end=50)
    child = cg.symbol(
        "Child",
        kind=SymbolKind.CLASS,
        start=60,
        end=160,
        base_types=("Base<T>",),
    )
    file = cg.parsed(
        "types.ts",
        language=ParsedLanguage.TYPESCRIPT,
        symbols=(base, child),
    )
    index = _index(cg, file)
    (reference,) = type_references(
        index, file, symbol_node_id("repo", "types.ts", child)
    )

    result = resolve_reference(
        index, ImportEnvironment({"types.ts": ()}), reference
    )

    assert result == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def test_unrelated_python_name_does_not_create_ecmascript_call_bridge(cg):
    caller = cg.symbol("main", start=0, end=100)
    call = CallSite("main", "remote", CallKind.CALL, cg.loc(40, 50))
    source = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(caller,),
        calls=(call,),
    )
    python_remote = cg.symbol("remote")
    python = cg.parsed("remote.py", symbols=(python_remote,))
    index = _index(cg, source, python)

    result = resolve_reference(
        index,
        ImportEnvironment({"main.js": (), "remote.py": ()}),
        call_reference(index, source, call),
    )

    assert result == Resolution(None, ResolutionFailureReason.NOT_FOUND)


def test_dynamic_cross_language_call_text_is_unsupported(cg):
    caller = cg.symbol("main", start=0, end=100)
    call = CallSite(
        "main", "python:remote()", CallKind.CALL, cg.loc(40, 50)
    )
    source = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(caller,),
        calls=(call,),
    )
    python_remote = cg.symbol("remote")
    python = cg.parsed("remote.py", symbols=(python_remote,))
    index = _index(cg, source, python)

    result = resolve_reference(
        index,
        ImportEnvironment({"main.js": (), "remote.py": ()}),
        call_reference(index, source, call),
    )

    assert result == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def test_declaring_type_member_does_not_shadow_base_lookup(cg):
    base = cg.symbol("Base", kind=SymbolKind.CLASS, start=0, end=50)
    child = cg.symbol(
        "Child",
        kind=SymbolKind.CLASS,
        start=60,
        end=200,
        base_types=("Base",),
    )
    member = cg.symbol(
        "Base",
        kind=SymbolKind.METHOD,
        qualified_name="Child.Base",
        parent="Child",
        start=100,
        end=130,
    )
    file = cg.parsed(
        "types.ts",
        language=ParsedLanguage.TYPESCRIPT,
        symbols=(base, child, member),
    )
    index = _index(cg, file)
    (reference,) = type_references(
        index, file, symbol_node_id("repo", "types.ts", child)
    )

    result = resolve_reference(
        index, ImportEnvironment({"types.ts": ()}), reference
    )

    assert result == Resolution(
        symbol_node_id("repo", "types.ts", base), None
    )


def test_ambiguous_namespace_scope_preserves_known_member_not_file(cg):
    first_owner = cg.symbol("first", start=0, end=100)
    second_owner = cg.symbol("second", start=10, end=110)
    caller = cg.symbol("main", start=120, end=220)
    item = ImportInfo(
        "./util.js",
        (ImportBinding("*", "util"),),
        True,
        (),
        cg.loc(20, 30),
    )
    call = CallSite("main", "util.run", CallKind.CALL, cg.loc(150, 160))
    source = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(first_owner, second_owner, caller),
        imports=(item,),
        calls=(call,),
    )
    exported = cg.symbol("run", modifiers=("export",))
    target = cg.parsed(
        "util.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(exported,),
    )
    index = _index(cg, source, target)
    known = symbol_node_id("repo", "util.js", exported)

    result = resolve_reference(
        index,
        _environment(index, source, target),
        call_reference(index, source, call),
    )

    assert result == Resolution(
        None, ResolutionFailureReason.AMBIGUOUS, (known,)
    )


def test_unsupported_explicit_binding_blocks_ambiguous_weaker_binding(cg):
    blocked = ImportInfo(
        "./barrel.js",
        (ImportBinding("run", "execute"),),
        False,
        (),
        cg.loc(0, 10),
    )
    uncertain = ImportInfo(
        "./util",
        (ImportBinding("run", "execute"),),
        False,
        (),
        cg.loc(11, 20),
    )
    caller = cg.symbol("main", start=30, end=120)
    call = CallSite("main", "execute", CallKind.CALL, cg.loc(60, 70))
    source = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(caller,),
        imports=(blocked, uncertain),
        calls=(call,),
    )
    barrel = cg.parsed("barrel.js", language=ParsedLanguage.JAVASCRIPT)
    exported = cg.symbol("run", modifiers=("export",))
    first = cg.parsed(
        "util.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(exported,),
    )
    second = cg.parsed("util.ts", language=ParsedLanguage.TYPESCRIPT)
    index = _index(cg, source, barrel, first, second)

    result = resolve_reference(
        index,
        _environment(index, source, barrel, first, second),
        call_reference(index, source, call),
    )

    assert result == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def test_bare_method_name_does_not_infer_current_receiver(cg):
    file_function = cg.symbol("run", start=0, end=20)
    owner = cg.symbol(
        "Service", kind=SymbolKind.CLASS, start=30, end=200
    )
    method = cg.symbol(
        "run",
        kind=SymbolKind.METHOD,
        qualified_name="Service.run",
        parent="Service",
        start=50,
        end=150,
    )
    call = CallSite("Service.run", "run", CallKind.CALL, cg.loc(100, 110))
    file = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(file_function, owner, method),
        calls=(call,),
    )
    index = _index(cg, file)

    result = resolve_reference(
        index,
        ImportEnvironment({"main.js": ()}),
        call_reference(index, file, call),
    )

    assert result == Resolution(
        symbol_node_id("repo", "main.js", file_function), None
    )


def test_same_scope_call_filters_noncallable_exact_name(cg):
    callable_target = cg.symbol("helper", start=0, end=20)
    noncallable = cg.symbol(
        "helper", kind=SymbolKind.CLASS, start=30, end=50
    )
    caller = cg.symbol("main", start=60, end=160)
    call = CallSite("main", "helper", CallKind.CALL, cg.loc(100, 110))
    file = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(callable_target, noncallable, caller),
        calls=(call,),
    )
    index = _index(cg, file)

    result = resolve_reference(
        index,
        ImportEnvironment({"main.js": ()}),
        call_reference(index, file, call),
    )

    assert result == Resolution(
        symbol_node_id("repo", "main.js", callable_target), None
    )


def test_ambiguous_import_retains_only_eligible_callable_candidates(cg):
    item = ImportInfo(
        "./util",
        (ImportBinding("run", "execute"),),
        False,
        (),
        cg.loc(0, 15),
    )
    caller = cg.symbol("main", start=20, end=100)
    call = CallSite("main", "execute", CallKind.CALL, cg.loc(50, 60))
    source = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(caller,),
        imports=(item,),
        calls=(call,),
    )
    callable_target = cg.symbol("run", modifiers=("export",))
    first = cg.parsed(
        "util.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(callable_target,),
    )
    noncallable = cg.symbol(
        "run", kind=SymbolKind.CONSTANT, modifiers=("export",)
    )
    second = cg.parsed(
        "util.ts",
        language=ParsedLanguage.TYPESCRIPT,
        symbols=(noncallable,),
    )
    index = _index(cg, source, first, second)
    known = symbol_node_id("repo", "util.js", callable_target)

    result = resolve_reference(
        index,
        _environment(index, source, first, second),
        call_reference(index, source, call),
    )

    assert result == Resolution(
        None, ResolutionFailureReason.AMBIGUOUS, (known,)
    )


def test_zero_eligible_local_tier_blocks_imported_callable_fallback(cg):
    item = ImportInfo(
        "./util.js",
        (ImportBinding("run", "helper"),),
        False,
        (),
        cg.loc(0, 15),
    )
    local_class = cg.symbol(
        "helper", kind=SymbolKind.CLASS, start=20, end=50
    )
    caller = cg.symbol("main", start=60, end=160)
    call = CallSite("main", "helper", CallKind.CALL, cg.loc(100, 110))
    source = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(local_class, caller),
        imports=(item,),
        calls=(call,),
    )
    exported = cg.symbol("run", modifiers=("export",))
    target = cg.parsed(
        "util.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(exported,),
    )
    index = _index(cg, source, target)

    result = resolve_reference(
        index,
        _environment(index, source, target),
        call_reference(index, source, call),
    )

    assert result == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def test_enclosing_callable_parameter_blocks_file_fallback(cg):
    file_helper = cg.symbol("helper", start=0, end=20)
    outer = cg.symbol(
        "outer",
        start=30,
        end=300,
        parameters=(ParameterInfo("helper", None, None),),
    )
    inner = cg.symbol(
        "inner",
        qualified_name="outer.inner",
        parent="outer",
        start=60,
        end=220,
    )
    call = CallSite("outer.inner", "helper", CallKind.CALL, cg.loc(150, 160))
    file = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(file_helper, outer, inner),
        calls=(call,),
    )
    index = _index(cg, file)

    result = resolve_reference(
        index,
        ImportEnvironment({"main.js": ()}),
        call_reference(index, file, call),
    )

    assert result == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def test_nearer_lexical_declaration_precedes_outer_parameter(cg):
    outer = cg.symbol(
        "outer",
        start=0,
        end=300,
        parameters=(ParameterInfo("helper", None, None),),
    )
    inner = cg.symbol(
        "inner",
        qualified_name="outer.inner",
        parent="outer",
        start=30,
        end=250,
    )
    helper = cg.symbol(
        "helper",
        qualified_name="outer.inner.helper",
        parent="outer.inner",
        start=60,
        end=100,
    )
    call = CallSite("outer.inner", "helper", CallKind.CALL, cg.loc(150, 160))
    file = cg.parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(outer, inner, helper),
        calls=(call,),
    )
    index = _index(cg, file)

    result = resolve_reference(
        index,
        ImportEnvironment({"main.js": ()}),
        call_reference(index, file, call),
    )

    assert result == Resolution(
        symbol_node_id("repo", "main.js", helper), None
    )
