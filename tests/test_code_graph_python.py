"""Python import resolution tests."""

import pytest

from backend.code_graph.indexes import build_graph_indexes
from backend.code_graph.identities import symbol_node_id
from backend.code_graph.models import ResolutionFailureReason
from backend.code_graph.references import call_reference, type_references
from backend.code_graph.resolvers.base import (
    Binding,
    ImportEnvironment,
    Resolution,
)
from backend.code_graph.resolvers.python import resolve_import, resolve_reference
from backend.code_graph.validation import validate_inputs
from backend.code_parser.models import (
    CallKind,
    CallSite,
    ImportBinding,
    ImportInfo,
    ParameterInfo,
    ParsedLanguage,
    ParseIssue,
    ParseIssueKind,
    ParseStatus,
    SymbolKind,
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


@pytest.mark.parametrize(
    ("module", "target_path"),
    [
        pytest.param("class", "class.py", id="root-keyword"),
        pytest.param("pkg.class", "pkg/class.py", id="nested-keyword"),
    ],
)
def test_reserved_keyword_module_component_is_unsupported(
    cg, module, target_path
):
    item = ImportInfo(module, (), False, (), cg.loc(1, 20))
    source = cg.parsed("main.py", imports=(item,))
    target = cg.parsed(target_path)
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(
        None, ResolutionFailureReason.UNSUPPORTED
    )


@pytest.mark.parametrize(
    ("imported_name", "alias", "local_name"),
    [
        pytest.param("class", None, "class", id="imported-keyword"),
        pytest.param("run", "class", "class", id="alias-keyword"),
        pytest.param("run", "bad.name", "bad.name", id="dotted-alias"),
    ],
)
def test_invalid_binding_name_is_unsupported_without_losing_file_import(
    cg, imported_name, alias, local_name
):
    item = ImportInfo(
        "pkg.util",
        (ImportBinding(imported_name, alias),),
        False,
        (),
        cg.loc(1, 30),
    )
    source = cg.parsed("main.py", imports=(item,))
    target = cg.parsed(
        "pkg/util.py",
        symbols=(cg.symbol(imported_name), cg.symbol("run", start=40, end=50)),
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(index.file_ids["pkg/util.py"], None)
    assert result.bindings == (
        Binding(
            local_name,
            index.file_ids["main.py"],
            "blocked",
            Resolution(None, ResolutionFailureReason.UNSUPPORTED),
        ),
    )


def test_soft_keywords_remain_valid_import_identifiers(cg):
    item = ImportInfo(
        "pkg.match",
        (ImportBinding("match", "case"),),
        False,
        (),
        cg.loc(1, 30),
    )
    source = cg.parsed("main.py", imports=(item,))
    imported = cg.symbol("match")
    target = cg.parsed("pkg/match.py", symbols=(imported,))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(index.file_ids["pkg/match.py"], None)
    assert result.bindings == (
        Binding(
            "case",
            index.file_ids["main.py"],
            "symbol",
            Resolution(symbol_node_id("repo", "pkg/match.py", imported), None),
        ),
    )


def _import_environment(index, *files):
    return ImportEnvironment(
        {
            file.relative_path: tuple(
                (item, resolve_import(index, file, item))
                for item in file.imports
            )
            for file in files
        }
    )


def _call_result(index, environment, file, call):
    return resolve_reference(index, environment, call_reference(index, file, call))


def _base_result(index, environment, file, symbol):
    source_id = symbol_node_id("repo", file.relative_path, symbol)
    reference = type_references(index, file, source_id)[0]
    return resolve_reference(index, environment, reference)


def test_same_file_call(cg):
    call = CallSite("run", "helper", CallKind.CALL, cg.loc(20, 25))
    file = cg.parsed(
        symbols=(
            cg.symbol("run"),
            cg.symbol("helper", start=200, end=250),
        ),
        calls=(call,),
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    result = _call_result(index, ImportEnvironment({file.relative_path: ()}), file, call)

    assert result.target_node_id == index.by_file_name[("a.py", "helper")][0]


def test_nested_lexical_function_and_recursion_resolve(cg):
    outer = cg.symbol("outer", start=0, end=400)
    helper = cg.symbol(
        "helper",
        qualified_name="outer.helper",
        parent="outer",
        start=20,
        end=100,
    )
    run = cg.symbol(
        "run",
        qualified_name="outer.run",
        parent="outer",
        start=150,
        end=300,
    )
    helper_call = CallSite("outer.run", "helper", CallKind.CALL, cg.loc(180, 190))
    recursive_call = CallSite("outer.run", "run", CallKind.CALL, cg.loc(210, 220))
    file = cg.parsed(
        symbols=(outer, helper, run),
        calls=(helper_call, recursive_call),
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))
    environment = ImportEnvironment({file.relative_path: ()})

    assert _call_result(index, environment, file, helper_call) == Resolution(
        symbol_node_id("repo", "a.py", helper), None
    )
    assert _call_result(index, environment, file, recursive_call) == Resolution(
        symbol_node_id("repo", "a.py", run), None
    )


def test_self_method_requires_direct_instance_method_context(cg):
    owner = cg.symbol("Service", kind=SymbolKind.CLASS, start=0, end=400)
    caller = cg.symbol(
        "run",
        kind=SymbolKind.METHOD,
        qualified_name="Service.run",
        parent="Service",
        start=20,
        end=170,
        parameters=(ParameterInfo("self", None, None),),
    )
    target = cg.symbol(
        "helper",
        kind=SymbolKind.METHOD,
        qualified_name="Service.helper",
        parent="Service",
        start=200,
        end=300,
    )
    call = CallSite("Service.run", "self.helper", CallKind.CALL, cg.loc(80, 90))
    file = cg.parsed(symbols=(owner, caller, target), calls=(call,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    result = _call_result(index, ImportEnvironment({"a.py": ()}), file, call)

    assert result == Resolution(symbol_node_id("repo", "a.py", target), None)


@pytest.mark.parametrize("parameters", [(), (ParameterInfo("cls", None, None),)])
def test_self_method_without_self_parameter_is_unsupported(cg, parameters):
    owner = cg.symbol("Service", kind=SymbolKind.CLASS, start=0, end=400)
    caller = cg.symbol(
        "run",
        kind=SymbolKind.METHOD,
        qualified_name="Service.run",
        parent="Service",
        start=20,
        end=170,
        parameters=parameters,
    )
    target = cg.symbol(
        "helper",
        kind=SymbolKind.METHOD,
        qualified_name="Service.helper",
        parent="Service",
        start=200,
        end=300,
    )
    call = CallSite("Service.run", "self.helper", CallKind.CALL, cg.loc(80, 90))
    file = cg.parsed(symbols=(owner, caller, target), calls=(call,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    result = _call_result(index, ImportEnvironment({"a.py": ()}), file, call)

    assert result == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def test_nested_parameter_shadows_method_self_context(cg):
    owner = cg.symbol("Service", kind=SymbolKind.CLASS, start=0, end=500)
    method = cg.symbol(
        "run",
        kind=SymbolKind.METHOD,
        qualified_name="Service.run",
        parent="Service",
        start=20,
        end=400,
        parameters=(ParameterInfo("self", None, None),),
    )
    nested = cg.symbol(
        "inner",
        qualified_name="Service.run.inner",
        parent="Service.run",
        start=60,
        end=180,
        parameters=(ParameterInfo("self", None, None),),
    )
    target = cg.symbol(
        "helper",
        kind=SymbolKind.METHOD,
        qualified_name="Service.helper",
        parent="Service",
        start=420,
        end=470,
    )
    call = CallSite(
        "Service.run.inner", "self.helper", CallKind.CALL, cg.loc(100, 110)
    )
    file = cg.parsed(symbols=(owner, method, nested, target), calls=(call,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    result = _call_result(index, ImportEnvironment({"a.py": ()}), file, call)

    assert result == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def test_class_members_are_not_bare_python_lexical_bindings(cg):
    owner = cg.symbol("Service", kind=SymbolKind.CLASS, start=0, end=400)
    caller = cg.symbol(
        "run",
        kind=SymbolKind.METHOD,
        qualified_name="Service.run",
        parent="Service",
        start=20,
        end=150,
    )
    member = cg.symbol(
        "helper",
        kind=SymbolKind.METHOD,
        qualified_name="Service.helper",
        parent="Service",
        start=200,
        end=300,
    )
    top_level = cg.symbol("helper", start=500, end=600)
    call = CallSite("Service.run", "helper", CallKind.CALL, cg.loc(80, 90))
    file = cg.parsed(symbols=(owner, caller, member, top_level), calls=(call,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    result = _call_result(index, ImportEnvironment({"a.py": ()}), file, call)

    assert result == Resolution(symbol_node_id("repo", "a.py", top_level), None)


def test_same_file_class_method_call_is_anchored_by_the_class(cg):
    owner = cg.symbol("Service", kind=SymbolKind.CLASS, start=0, end=400)
    method = cg.symbol(
        "run",
        kind=SymbolKind.METHOD,
        qualified_name="Service.run",
        parent="Service",
        start=20,
        end=100,
    )
    caller = cg.symbol("main", start=500, end=700)
    call = CallSite("main", "Service.run", CallKind.CALL, cg.loc(550, 560))
    file = cg.parsed(symbols=(owner, method, caller), calls=(call,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    result = _call_result(index, ImportEnvironment({"a.py": ()}), file, call)

    assert result == Resolution(symbol_node_id("repo", "a.py", method), None)


def test_unrelated_qualified_method_requires_a_proven_receiver_anchor(cg):
    caller = cg.symbol("main", start=0, end=200)
    call = CallSite("main", "Service.run", CallKind.CALL, cg.loc(50, 60))
    source = cg.parsed("main.py", symbols=(caller,), calls=(call,))
    owner = cg.symbol("Service", kind=SymbolKind.CLASS, start=0, end=300)
    method = cg.symbol(
        "run",
        kind=SymbolKind.METHOD,
        qualified_name="Service.run",
        parent="Service",
        start=20,
        end=100,
    )
    unrelated = cg.parsed("other.py", symbols=(owner, method))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, unrelated)))

    result = _call_result(
        index, ImportEnvironment({"main.py": ()}), source, call
    )

    assert result == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def test_parameter_name_conflict_blocks_same_file_fallback(cg):
    caller = cg.symbol(
        "run",
        start=0,
        end=150,
        parameters=(ParameterInfo("helper", None, None),),
    )
    target = cg.symbol("helper", start=200, end=300)
    call = CallSite("run", "helper", CallKind.CALL, cg.loc(50, 60))
    file = cg.parsed(symbols=(caller, target), calls=(call,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    result = _call_result(index, ImportEnvironment({"a.py": ()}), file, call)

    assert result == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


@pytest.mark.parametrize(
    "callee",
    ["", "class", "getattr", "globals", "thing.run", "items[0].run"],
)
def test_dynamic_or_unknown_python_calls_are_unsupported(cg, callee):
    caller = cg.symbol("run", start=0, end=150)
    call = CallSite("run", callee, CallKind.CALL, cg.loc(50, 60))
    file = cg.parsed(symbols=(caller,), calls=(call,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    result = _call_result(index, ImportEnvironment({"a.py": ()}), file, call)

    assert result == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def test_missing_and_cross_file_bare_names_do_not_resolve(cg):
    caller = cg.symbol("run", start=0, end=150)
    call = CallSite("run", "helper", CallKind.CALL, cg.loc(50, 60))
    source = cg.parsed("main.py", symbols=(caller,), calls=(call,))
    other_python = cg.parsed("other.py", symbols=(cg.symbol("helper"),))
    other_java = cg.parsed(
        "Other.java",
        language=ParsedLanguage.JAVA,
        symbols=(cg.symbol("helper"),),
    )
    index = build_graph_indexes(
        validate_inputs(*cg.pair(source, other_python, other_java))
    )

    result = _call_result(index, ImportEnvironment({"main.py": ()}), source, call)

    assert result == Resolution(None, ResolutionFailureReason.NOT_FOUND)


def test_duplicate_same_file_targets_are_ambiguous_with_sorted_ids(cg):
    caller = cg.symbol("run", start=0, end=150)
    first = cg.symbol("helper", start=200, end=250)
    second = cg.symbol("helper", start=300, end=350)
    call = CallSite("run", "helper", CallKind.CALL, cg.loc(50, 60))
    file = cg.parsed(symbols=(caller, first, second), calls=(call,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    result = _call_result(index, ImportEnvironment({"a.py": ()}), file, call)

    candidate_ids = tuple(
        sorted(
            (
                symbol_node_id("repo", "a.py", first),
                symbol_node_id("repo", "a.py", second),
            )
        )
    )
    assert result == Resolution(
        None, ResolutionFailureReason.AMBIGUOUS, candidate_ids
    )


def test_ambiguous_class_anchors_preserve_one_known_member_candidate(cg):
    first_class = cg.symbol(
        "Service", kind=SymbolKind.CLASS, start=0, end=180
    )
    method = cg.symbol(
        "run",
        kind=SymbolKind.METHOD,
        qualified_name="Service.run",
        parent="Service",
        start=20,
        end=80,
    )
    second_class = cg.symbol(
        "Service", kind=SymbolKind.CLASS, start=200, end=380
    )
    caller = cg.symbol("main", start=500, end=700)
    call = CallSite("main", "Service.run", CallKind.CALL, cg.loc(550, 560))
    file = cg.parsed(
        symbols=(first_class, method, second_class, caller), calls=(call,)
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    result = _call_result(index, ImportEnvironment({"a.py": ()}), file, call)

    candidate = symbol_node_id("repo", "a.py", method)
    assert result == Resolution(
        None, ResolutionFailureReason.AMBIGUOUS, (candidate,)
    )


def test_imported_function_alias_resolves(cg):
    item = ImportInfo(
        "pkg.util",
        (ImportBinding("run", "execute"),),
        False,
        (),
        cg.loc(1, 20),
    )
    caller = cg.symbol("main", start=100, end=300)
    call = CallSite("main", "execute", CallKind.CALL, cg.loc(150, 160))
    source = cg.parsed("main.py", symbols=(caller,), imports=(item,), calls=(call,))
    target = cg.symbol("run")
    imported = cg.parsed("pkg/util.py", symbols=(target,))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, imported)))

    result = _call_result(
        index, _import_environment(index, source), source, call
    )

    assert result == Resolution(
        symbol_node_id("repo", "pkg/util.py", target), None
    )


def test_module_qualified_member_call_resolves(cg):
    item = ImportInfo(
        "pkg.util",
        (ImportBinding("pkg.util", "util"),),
        False,
        (),
        cg.loc(1, 20),
    )
    caller = cg.symbol("main", start=100, end=300)
    call = CallSite("main", "util.run", CallKind.CALL, cg.loc(150, 160))
    source = cg.parsed("main.py", symbols=(caller,), imports=(item,), calls=(call,))
    target = cg.symbol("run")
    imported = cg.parsed("pkg/util.py", symbols=(target,))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, imported)))

    result = _call_result(
        index, _import_environment(index, source), source, call
    )

    assert result == Resolution(
        symbol_node_id("repo", "pkg/util.py", target), None
    )


def test_imported_ambiguous_class_preserves_known_method_candidate(cg):
    item = ImportInfo(
        "pkg.models",
        (ImportBinding("Service", "Alias"),),
        False,
        (),
        cg.loc(1, 20),
    )
    caller = cg.symbol("main", start=100, end=300)
    call = CallSite("main", "Alias.run", CallKind.CALL, cg.loc(150, 160))
    source = cg.parsed("main.py", symbols=(caller,), imports=(item,), calls=(call,))
    first_class = cg.symbol(
        "Service", kind=SymbolKind.CLASS, start=0, end=180
    )
    method = cg.symbol(
        "run",
        kind=SymbolKind.METHOD,
        qualified_name="Service.run",
        parent="Service",
        start=20,
        end=80,
    )
    second_class = cg.symbol(
        "Service", kind=SymbolKind.CLASS, start=200, end=380
    )
    imported = cg.parsed(
        "pkg/models.py", symbols=(first_class, method, second_class)
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(source, imported)))

    result = _call_result(
        index, _import_environment(index, source), source, call
    )

    candidate = symbol_node_id("repo", "pkg/models.py", method)
    assert result == Resolution(
        None, ResolutionFailureReason.AMBIGUOUS, (candidate,)
    )


def test_blocked_import_form_prevents_weaker_call_fallback(cg):
    item = ImportInfo(
        "x",
        (ImportBinding("x", "y"),),
        False,
        (),
        cg.loc(1, 20),
    )
    caller = cg.symbol("main", start=100, end=300)
    call = CallSite("main", "y", CallKind.CALL, cg.loc(150, 160))
    source = cg.parsed("main.py", symbols=(caller,), imports=(item,), calls=(call,))
    module = cg.parsed("x.py", symbols=(cg.symbol("x"),))
    coincidence = cg.parsed("other.py", symbols=(cg.symbol("y"),))
    index = build_graph_indexes(
        validate_inputs(*cg.pair(source, module, coincidence))
    )

    result = _call_result(
        index, _import_environment(index, source), source, call
    )

    assert result == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def test_imported_external_binding_propagates_to_call(cg):
    item = ImportInfo(
        "thirdparty.client",
        (ImportBinding("run", "execute"),),
        False,
        (),
        cg.loc(1, 20),
    )
    caller = cg.symbol("main", start=100, end=300)
    call = CallSite("main", "execute", CallKind.CALL, cg.loc(150, 160))
    source = cg.parsed("main.py", symbols=(caller,), imports=(item,), calls=(call,))
    index = build_graph_indexes(validate_inputs(*cg.pair(source)))

    result = _call_result(
        index, _import_environment(index, source), source, call
    )

    assert result == Resolution(None, ResolutionFailureReason.EXTERNAL)


def test_constructor_call_targets_only_extracted_constructor(cg):
    owner = cg.symbol("Service", kind=SymbolKind.CLASS, start=0, end=400)
    constructor = cg.symbol(
        "__init__",
        kind=SymbolKind.CONSTRUCTOR,
        qualified_name="Service.__init__",
        parent="Service",
        start=20,
        end=100,
    )
    caller = cg.symbol("main", start=500, end=700)
    call = CallSite("main", "Service", CallKind.CONSTRUCTOR, cg.loc(550, 560))
    file = cg.parsed(symbols=(owner, constructor, caller), calls=(call,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    result = _call_result(index, ImportEnvironment({"a.py": ()}), file, call)

    assert result == Resolution(
        symbol_node_id("repo", "a.py", constructor), None
    )


def test_class_without_extracted_constructor_does_not_resolve_call(cg):
    owner = cg.symbol("Service", kind=SymbolKind.CLASS, start=0, end=400)
    caller = cg.symbol("main", start=500, end=700)
    call = CallSite("main", "Service", CallKind.CONSTRUCTOR, cg.loc(550, 560))
    file = cg.parsed(symbols=(owner, caller), calls=(call,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    result = _call_result(index, ImportEnvironment({"a.py": ()}), file, call)

    assert result == Resolution(None, ResolutionFailureReason.NOT_FOUND)


def test_ambiguous_imported_constructor_reports_constructor_candidates(cg):
    item = ImportInfo(
        "pkg.models",
        (ImportBinding("Service", "Factory"),),
        False,
        (),
        cg.loc(1, 20),
    )
    caller = cg.symbol("main", start=100, end=300)
    call = CallSite("main", "Factory", CallKind.CONSTRUCTOR, cg.loc(150, 160))
    source = cg.parsed("main.py", symbols=(caller,), imports=(item,), calls=(call,))
    first_class = cg.symbol(
        "Service", kind=SymbolKind.CLASS, start=0, end=180
    )
    first_constructor = cg.symbol(
        "__init__",
        kind=SymbolKind.CONSTRUCTOR,
        qualified_name="Service.__init__",
        parent="Service",
        start=20,
        end=80,
    )
    second_class = cg.symbol(
        "Service", kind=SymbolKind.CLASS, start=200, end=380
    )
    second_constructor = cg.symbol(
        "__init__",
        kind=SymbolKind.CONSTRUCTOR,
        qualified_name="Service.__init__",
        parent="Service",
        start=220,
        end=280,
    )
    imported = cg.parsed(
        "pkg/models.py",
        symbols=(
            first_class,
            first_constructor,
            second_class,
            second_constructor,
        ),
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(source, imported)))

    result = _call_result(
        index, _import_environment(index, source), source, call
    )

    candidates = tuple(
        sorted(
            (
                symbol_node_id("repo", "pkg/models.py", first_constructor),
                symbol_node_id("repo", "pkg/models.py", second_constructor),
            )
        )
    )
    assert result == Resolution(None, ResolutionFailureReason.AMBIGUOUS, candidates)


def test_qualified_nested_constructor_targets_extracted_constructor(cg):
    outer = cg.symbol("Outer", kind=SymbolKind.CLASS, start=0, end=500)
    nested = cg.symbol(
        "Inner",
        kind=SymbolKind.CLASS,
        qualified_name="Outer.Inner",
        parent="Outer",
        start=20,
        end=300,
    )
    constructor = cg.symbol(
        "__init__",
        kind=SymbolKind.CONSTRUCTOR,
        qualified_name="Outer.Inner.__init__",
        parent="Outer.Inner",
        start=40,
        end=100,
    )
    caller = cg.symbol("main", start=600, end=800)
    call = CallSite(
        "main", "Outer.Inner", CallKind.CONSTRUCTOR, cg.loc(650, 660)
    )
    file = cg.parsed(symbols=(outer, nested, constructor, caller), calls=(call,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    result = _call_result(index, ImportEnvironment({"a.py": ()}), file, call)

    assert result == Resolution(
        symbol_node_id("repo", "a.py", constructor), None
    )


def test_same_file_declared_base_resolves_only_to_class(cg):
    base = cg.symbol("Base", kind=SymbolKind.CLASS, start=0, end=100)
    child = cg.symbol(
        "Child",
        kind=SymbolKind.CLASS,
        start=200,
        end=300,
        base_types=("Base",),
    )
    file = cg.parsed(symbols=(base, child))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    result = _base_result(index, ImportEnvironment({"a.py": ()}), file, child)

    assert result == Resolution(symbol_node_id("repo", "a.py", base), None)


def test_nested_class_resolves_sibling_declared_base(cg):
    outer = cg.symbol("Outer", kind=SymbolKind.CLASS, start=0, end=500)
    base = cg.symbol(
        "Base",
        kind=SymbolKind.CLASS,
        qualified_name="Outer.Base",
        parent="Outer",
        start=20,
        end=150,
    )
    child = cg.symbol(
        "Child",
        kind=SymbolKind.CLASS,
        qualified_name="Outer.Child",
        parent="Outer",
        start=200,
        end=350,
        base_types=("Base",),
    )
    file = cg.parsed(symbols=(outer, base, child))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    result = _base_result(index, ImportEnvironment({"a.py": ()}), file, child)

    assert result == Resolution(symbol_node_id("repo", "a.py", base), None)


def test_imported_base_alias_resolves(cg):
    item = ImportInfo(
        "pkg.base",
        (ImportBinding("Base", "Parent"),),
        False,
        (),
        cg.loc(1, 20),
    )
    child = cg.symbol(
        "Child",
        kind=SymbolKind.CLASS,
        start=100,
        end=300,
        base_types=("Parent",),
    )
    source = cg.parsed("main.py", symbols=(child,), imports=(item,))
    base = cg.symbol("Base", kind=SymbolKind.CLASS)
    imported = cg.parsed("pkg/base.py", symbols=(base,))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, imported)))

    result = _base_result(
        index, _import_environment(index, source), source, child
    )

    assert result == Resolution(
        symbol_node_id("repo", "pkg/base.py", base), None
    )


@pytest.mark.parametrize("base_text", ["Base[T]", "factory()", "pkg..Base", "class"])
def test_complex_or_invalid_base_expression_is_unsupported(cg, base_text):
    child = cg.symbol(
        "Child",
        kind=SymbolKind.CLASS,
        base_types=(base_text,),
    )
    file = cg.parsed(symbols=(child,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    result = _base_result(index, ImportEnvironment({"a.py": ()}), file, child)

    assert result == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def test_non_class_same_name_does_not_satisfy_declared_base(cg):
    function = cg.symbol("Base", start=0, end=100)
    child = cg.symbol(
        "Child",
        kind=SymbolKind.CLASS,
        start=200,
        end=300,
        base_types=("Base",),
    )
    file = cg.parsed(symbols=(function, child))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    result = _base_result(index, ImportEnvironment({"a.py": ()}), file, child)

    assert result == Resolution(None, ResolutionFailureReason.NOT_FOUND)


def test_duplicate_base_classes_are_ambiguous_with_sorted_ids(cg):
    first = cg.symbol("Base", kind=SymbolKind.CLASS, start=0, end=100)
    second = cg.symbol("Base", kind=SymbolKind.CLASS, start=120, end=220)
    child = cg.symbol(
        "Child",
        kind=SymbolKind.CLASS,
        start=300,
        end=400,
        base_types=("Base",),
    )
    file = cg.parsed(symbols=(first, second, child))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    result = _base_result(index, ImportEnvironment({"a.py": ()}), file, child)

    candidates = tuple(
        sorted(
            (
                symbol_node_id("repo", "a.py", first),
                symbol_node_id("repo", "a.py", second),
            )
        )
    )
    assert result == Resolution(None, ResolutionFailureReason.AMBIGUOUS, candidates)
