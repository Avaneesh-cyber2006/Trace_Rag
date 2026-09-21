"""Java import and declared-type resolution stays metadata-bounded."""

import pytest

import backend.code_graph.resolvers.java as java_resolver
from backend.code_graph.identities import symbol_node_id
from backend.code_graph.indexes import build_graph_indexes
from backend.code_graph.models import ReferenceKind, ResolutionFailureReason
from backend.code_graph.references import Reference, call_reference, type_references
from backend.code_graph.resolvers.base import ImportEnvironment, Resolution
from backend.code_graph.resolvers.java import resolve_import, resolve_reference
from backend.code_graph.validation import validate_inputs
from backend.code_parser.models import (
    CallKind, CallSite, ImportInfo, ParameterInfo, ParseIssue, ParseIssueKind,
    ParsedLanguage, ParseStatus, SymbolKind,
)


def _java_file(cg, path, **kwargs):
    return cg.parsed(path, language=ParsedLanguage.JAVA, **kwargs)


def _environment(index, *files):
    return ImportEnvironment({
        file.relative_path: tuple(
            (item, resolve_import(index, file, item)) for item in file.imports
        )
        for file in files
    })


def _reference(file, symbol, kind, raw_target):
    return Reference(
        kind,
        file.relative_path,
        ParsedLanguage.JAVA,
        symbol_node_id("repo", file.relative_path, symbol),
        symbol.location,
        raw_target,
    )


def test_java_import_uses_qualified_metadata(cg):
    item = ImportInfo("p.Service", (), False, (), cg.loc(1, 10))
    source = _java_file(cg, "Main.java", imports=(item,))
    service = cg.symbol("Service", kind=SymbolKind.CLASS, qualified_name="p.Service")
    target = _java_file(cg, "odd/Else.java", symbols=(service,))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(index.file_ids["odd/Else.java"], None)
    assert result.bindings[0].local_name == "Service"
    assert result.bindings[0].resolution == Resolution(
        symbol_node_id("repo", "odd/Else.java", service), None
    )


def test_java_import_does_not_infer_package_from_path(cg):
    item = ImportInfo("p.Service", (), False, (), cg.loc(1, 10))
    source = _java_file(cg, "Main.java", imports=(item,))
    target = _java_file(cg, "p/Service.java", symbols=(
        cg.symbol("Service", kind=SymbolKind.CLASS, qualified_name="q.Service"),
    ))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))

    assert resolve_import(index, source, item).resolution == Resolution(
        None, ResolutionFailureReason.EXTERNAL
    )


def test_conflicting_qualified_import_is_ambiguous_after_file_mapping(cg):
    item = ImportInfo("p.Service", (), False, (), cg.loc(1, 10))
    source = _java_file(cg, "Main.java", imports=(item,))
    first = _java_file(cg, "one.java", symbols=(
        cg.symbol("Service", kind=SymbolKind.CLASS, qualified_name="p.Service"),
    ))
    second = _java_file(cg, "two.java", symbols=(
        cg.symbol("Service", kind=SymbolKind.INTERFACE, qualified_name="p.Service"),
    ))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, first, second)))

    assert resolve_import(index, source, item).resolution == Resolution(
        None,
        ResolutionFailureReason.AMBIGUOUS,
        tuple(sorted((index.file_ids["one.java"], index.file_ids["two.java"]))),
    )


def test_duplicate_qualified_types_in_one_file_produce_one_file_target(cg):
    item = ImportInfo("p.Service", (), False, (), cg.loc(1, 10))
    source = _java_file(cg, "Main.java", imports=(item,))
    target = _java_file(cg, "types.java", symbols=(
        cg.symbol("Service", kind=SymbolKind.CLASS, qualified_name="p.Service", start=20, end=80),
        cg.symbol("Service", kind=SymbolKind.CLASS, qualified_name="p.Service", start=100, end=160),
    ))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(index.file_ids["types.java"], None)
    assert result.bindings[0].resolution.reason is ResolutionFailureReason.AMBIGUOUS


def test_package_wildcard_import_is_ambiguous_across_files(cg):
    item = ImportInfo("p", (), True, (), cg.loc(1, 10))
    source = _java_file(cg, "Main.java", imports=(item,))
    first = _java_file(cg, "alpha.java", symbols=(
        cg.symbol("A", kind=SymbolKind.CLASS, qualified_name="p.A"),
    ))
    second = _java_file(cg, "beta.java", symbols=(
        cg.symbol("B", kind=SymbolKind.CLASS, qualified_name="p.B"),
    ))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, first, second)))

    assert resolve_import(index, source, item).resolution == Resolution(
        None,
        ResolutionFailureReason.AMBIGUOUS,
        tuple(sorted((index.file_ids["alpha.java"], index.file_ids["beta.java"]))),
    )


def test_unique_package_wildcard_import_targets_one_file(cg):
    item = ImportInfo("p", (), True, (), cg.loc(1, 10))
    source = _java_file(cg, "Main.java", imports=(item,))
    target = _java_file(cg, "unexpected.java", symbols=(
        cg.symbol("A", kind=SymbolKind.CLASS, qualified_name="p.A"),
    ))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))

    assert resolve_import(index, source, item).resolution == Resolution(
        index.file_ids["unexpected.java"], None
    )


def test_static_wildcard_import_preserves_duplicate_type_ambiguity(cg):
    item = ImportInfo("p.Tools", (), True, ("static",), cg.loc(1, 10))
    source = _java_file(cg, "Main.java", imports=(item,))
    files = []
    for path, start in (("one.java", 0), ("two.java", 200)):
        owner = cg.symbol(
            "Tools", kind=SymbolKind.CLASS, qualified_name="p.Tools", start=start, end=start + 150
        )
        member = cg.symbol(
            "run", kind=SymbolKind.METHOD, qualified_name="p.Tools.run",
            parent="p.Tools", modifiers=("static",), start=start + 20, end=start + 80,
        )
        files.append(_java_file(cg, path, symbols=(owner, member)))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, *files)))

    assert resolve_import(index, source, item).resolution == Resolution(
        None,
        ResolutionFailureReason.AMBIGUOUS,
        tuple(sorted((index.file_ids["one.java"], index.file_ids["two.java"]))),
    )


def test_explicit_static_import_requires_static_member_of_named_type(cg):
    item = ImportInfo("p.Tools.run", (), False, ("static",), cg.loc(1, 10))
    source = _java_file(cg, "Main.java", imports=(item,))
    owner = cg.symbol(
        "Tools", kind=SymbolKind.CLASS, qualified_name="p.Tools", start=0, end=300
    )
    static_run = cg.symbol(
        "run", kind=SymbolKind.METHOD, qualified_name="p.Tools.run",
        parent="p.Tools", modifiers=("static",), start=20, end=80,
    )
    instance_run = cg.symbol(
        "run", kind=SymbolKind.METHOD, qualified_name="p.Tools.run",
        parent="p.Tools", start=100, end=160,
    )
    target = _java_file(cg, "tools.java", symbols=(owner, static_run, instance_run))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(index.file_ids["tools.java"], None)
    assert result.bindings[0].local_name == "run"
    assert result.bindings[0].resolution == Resolution(
        symbol_node_id("repo", "tools.java", static_run), None
    )


def test_non_static_member_cannot_satisfy_explicit_static_import(cg):
    item = ImportInfo("p.Tools.run", (), False, ("static",), cg.loc(1, 10))
    source = _java_file(cg, "Main.java", imports=(item,))
    owner = cg.symbol(
        "Tools", kind=SymbolKind.CLASS, qualified_name="p.Tools", start=0, end=200
    )
    member = cg.symbol(
        "run", kind=SymbolKind.METHOD, qualified_name="p.Tools.run",
        parent="p.Tools", start=20, end=80,
    )
    target = _java_file(cg, "tools.java", symbols=(owner, member))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))

    assert resolve_import(index, source, item).resolution == Resolution(
        None, ResolutionFailureReason.NOT_FOUND
    )


def test_ambiguous_static_owner_blocks_unique_known_member(cg):
    item = ImportInfo("p.Tools.run", (), False, ("static",), cg.loc(1, 10))
    source = _java_file(cg, "Main.java", imports=(item,))
    first_owner = cg.symbol(
        "Tools", kind=SymbolKind.CLASS, qualified_name="p.Tools", start=0, end=180
    )
    known_member = cg.symbol(
        "run", kind=SymbolKind.METHOD, qualified_name="p.Tools.run",
        parent="p.Tools", modifiers=("static",), start=20, end=80,
    )
    second_owner = cg.symbol(
        "Tools", kind=SymbolKind.CLASS, qualified_name="p.Tools", start=200, end=380
    )
    target = _java_file(
        cg, "tools.java", symbols=(first_owner, known_member, second_owner)
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(index.file_ids["tools.java"], None)
    assert result.bindings[0].resolution == Resolution(
        None,
        ResolutionFailureReason.AMBIGUOUS,
        (symbol_node_id("repo", "tools.java", known_member),),
    )


@pytest.mark.parametrize(
    ("source_kind", "reference_kind", "target_kind"),
    [
        (SymbolKind.CLASS, ReferenceKind.INHERITS, SymbolKind.CLASS),
        (SymbolKind.INTERFACE, ReferenceKind.INHERITS, SymbolKind.INTERFACE),
        (SymbolKind.CLASS, ReferenceKind.IMPLEMENTS, SymbolKind.INTERFACE),
        (SymbolKind.ENUM, ReferenceKind.IMPLEMENTS, SymbolKind.INTERFACE),
    ],
)
def test_java_type_relationship_endpoint_pairs_resolve(
    cg, source_kind, reference_kind, target_kind
):
    target = cg.symbol("Target", kind=target_kind, start=0, end=100)
    source = cg.symbol(
        "Source",
        kind=source_kind,
        start=200,
        end=300,
        base_types=("Target",) if reference_kind is ReferenceKind.INHERITS else (),
        implemented_types=("Target",)
        if reference_kind is ReferenceKind.IMPLEMENTS
        else (),
    )
    file = _java_file(cg, "Types.java", symbols=(target, source))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))
    references = type_references(
        index, file, symbol_node_id("repo", "Types.java", source)
    )

    assert resolve_reference(
        index, ImportEnvironment({"Types.java": ()}), references[0]
    ) == Resolution(symbol_node_id("repo", "Types.java", target), None)


@pytest.mark.parametrize(
    ("source_kind", "reference_kind", "target_kind"),
    [
        (SymbolKind.CLASS, ReferenceKind.INHERITS, SymbolKind.INTERFACE),
        (SymbolKind.INTERFACE, ReferenceKind.INHERITS, SymbolKind.CLASS),
        (SymbolKind.ENUM, ReferenceKind.INHERITS, SymbolKind.CLASS),
        (SymbolKind.INTERFACE, ReferenceKind.IMPLEMENTS, SymbolKind.INTERFACE),
        (SymbolKind.CLASS, ReferenceKind.IMPLEMENTS, SymbolKind.CLASS),
    ],
)
def test_java_forbidden_type_relationship_pairs_are_unsupported(
    cg, source_kind, reference_kind, target_kind
):
    target = cg.symbol("Target", kind=target_kind, start=0, end=100)
    source = cg.symbol("Source", kind=source_kind, start=200, end=300)
    file = _java_file(cg, "Types.java", symbols=(target, source))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))
    reference = _reference(file, source, reference_kind, "Target")

    assert resolve_reference(
        index, ImportEnvironment({"Types.java": ()}), reference
    ) == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def test_java_type_reference_uses_declaring_location(cg):
    base = cg.symbol("Base", kind=SymbolKind.CLASS, start=0, end=100)
    child = cg.symbol(
        "Child", kind=SymbolKind.CLASS, start=200, end=350, base_types=("Base",)
    )
    file = _java_file(cg, "Types.java", symbols=(base, child))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    references = type_references(
        index, file, symbol_node_id("repo", "Types.java", child)
    )

    assert references[0].location == child.location


@pytest.mark.parametrize("raw_target", ["Base<String>", "@Nonnull Base", "Base[]"])
def test_complex_java_type_expressions_are_unsupported(cg, raw_target):
    base = cg.symbol("Base", kind=SymbolKind.CLASS, start=0, end=100)
    child = cg.symbol("Child", kind=SymbolKind.CLASS, start=200, end=300)
    file = _java_file(cg, "Types.java", symbols=(base, child))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))
    reference = _reference(file, child, ReferenceKind.INHERITS, raw_target)

    assert resolve_reference(
        index, ImportEnvironment({"Types.java": ()}), reference
    ) == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def test_explicit_import_resolves_declared_base(cg):
    item = ImportInfo("p.Base", (), False, (), cg.loc(1, 10))
    child = cg.symbol(
        "Child", kind=SymbolKind.CLASS, qualified_name="q.Child", start=100, end=300
    )
    source = _java_file(cg, "Main.java", imports=(item,), symbols=(child,))
    base = cg.symbol("Base", kind=SymbolKind.CLASS, qualified_name="p.Base")
    target = _java_file(cg, "strange.java", symbols=(base,))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))
    reference = _reference(source, child, ReferenceKind.INHERITS, "Base")

    assert resolve_reference(index, _environment(index, source), reference) == Resolution(
        symbol_node_id("repo", "strange.java", base), None
    )


def test_package_wildcard_resolves_specific_type_despite_ambiguous_import(cg):
    item = ImportInfo("p", (), True, (), cg.loc(1, 10))
    child = cg.symbol("Child", kind=SymbolKind.CLASS, qualified_name="q.Child", start=100, end=300)
    source = _java_file(cg, "Main.java", imports=(item,), symbols=(child,))
    base = cg.symbol("Base", kind=SymbolKind.CLASS, qualified_name="p.Base")
    other = cg.symbol("Other", kind=SymbolKind.CLASS, qualified_name="p.Other")
    first = _java_file(cg, "base.java", symbols=(base,))
    second = _java_file(cg, "other.java", symbols=(other,))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, first, second)))
    environment = _environment(index, source)
    reference = _reference(source, child, ReferenceKind.INHERITS, "Base")

    assert environment.by_file["Main.java"][0][1].resolution.reason is ResolutionFailureReason.AMBIGUOUS
    assert resolve_reference(index, environment, reference) == Resolution(
        symbol_node_id("repo", "base.java", base), None
    )


def test_static_wildcard_type_lookup_ignores_same_named_method(cg):
    item = ImportInfo("p.Tools", (), True, ("static",), cg.loc(1, 10))
    child = cg.symbol("Child", kind=SymbolKind.CLASS, qualified_name="q.Child")
    source = _java_file(cg, "Main.java", imports=(item,), symbols=(child,))
    owner = cg.symbol(
        "Tools", kind=SymbolKind.CLASS, qualified_name="p.Tools", start=0, end=300
    )
    nested = cg.symbol(
        "Base", kind=SymbolKind.CLASS, qualified_name="p.Tools.Base",
        parent="p.Tools", modifiers=("static",), start=20, end=100,
    )
    method = cg.symbol(
        "Base", kind=SymbolKind.METHOD, qualified_name="p.Tools.Base",
        parent="p.Tools", modifiers=("static",), start=120, end=180,
    )
    target = _java_file(cg, "Tools.java", symbols=(owner, nested, method))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))
    reference = _reference(source, child, ReferenceKind.INHERITS, "Base")

    assert resolve_reference(index, _environment(index, source), reference) == Resolution(
        symbol_node_id("repo", "Tools.java", nested), None
    )


def test_ambiguous_explicit_import_is_terminal(cg):
    first_item = ImportInfo("p.Base", (), False, (), cg.loc(1, 10))
    second_item = ImportInfo("r.Base", (), False, (), cg.loc(11, 20))
    child = cg.symbol("Child", kind=SymbolKind.CLASS, qualified_name="q.Child", start=100, end=300)
    source = _java_file(cg, "Main.java", imports=(first_item, second_item), symbols=(child,))
    first_base = cg.symbol("Base", kind=SymbolKind.CLASS, qualified_name="p.Base")
    second_base = cg.symbol("Base", kind=SymbolKind.CLASS, qualified_name="r.Base")
    first = _java_file(cg, "p.java", symbols=(first_base,))
    second = _java_file(cg, "r.java", symbols=(second_base,))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, first, second)))
    reference = _reference(source, child, ReferenceKind.INHERITS, "Base")

    result = resolve_reference(index, _environment(index, source), reference)

    assert result == Resolution(
        None, ResolutionFailureReason.AMBIGUOUS,
        tuple(sorted((
            symbol_node_id("repo", "p.java", first_base),
            symbol_node_id("repo", "r.java", second_base),
        ))),
    )


def test_qualified_type_reference_uses_exact_qualified_bucket(cg):
    child = cg.symbol("Child", kind=SymbolKind.CLASS, qualified_name="q.Child")
    source = _java_file(cg, "Main.java", symbols=(child,))
    base = cg.symbol("Base", kind=SymbolKind.CLASS, qualified_name="p.Base")
    target = _java_file(cg, "odd.java", symbols=(base,))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))
    reference = _reference(source, child, ReferenceKind.INHERITS, "p.Base")

    assert resolve_reference(index, ImportEnvironment({"Main.java": ()}), reference) == Resolution(
        symbol_node_id("repo", "odd.java", base), None
    )


def test_unrelated_nested_type_is_not_visible_as_same_file_base(cg):
    owner = cg.symbol(
        "Owner", kind=SymbolKind.CLASS, qualified_name="q.Owner", start=0, end=180
    )
    nested = cg.symbol(
        "Inner", kind=SymbolKind.CLASS, qualified_name="q.Owner.Inner",
        parent="q.Owner", start=20, end=100,
    )
    child = cg.symbol(
        "Child", kind=SymbolKind.CLASS, qualified_name="q.Child", start=200, end=300
    )
    file = _java_file(cg, "Types.java", symbols=(owner, nested, child))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))
    reference = _reference(file, child, ReferenceKind.INHERITS, "Inner")

    assert resolve_reference(
        index, ImportEnvironment({"Types.java": ()}), reference
    ) == Resolution(None, ResolutionFailureReason.NOT_FOUND)


def test_anchored_partial_package_missing_type_is_incomplete(cg):
    item = ImportInfo("p", (), True, (), cg.loc(1, 10))
    child = cg.symbol("Child", kind=SymbolKind.CLASS, qualified_name="q.Child")
    source = _java_file(cg, "Main.java", imports=(item,), symbols=(child,))
    marker = cg.symbol("Marker", kind=SymbolKind.CLASS, qualified_name="p.Marker")
    partial = _java_file(
        cg, "opaque.java", status=ParseStatus.PARTIAL, symbols=(marker,),
        issues=(ParseIssue(ParseIssueKind.EXTRACTION_ERROR, "truncated", None),),
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(source, partial)))
    reference = _reference(source, child, ReferenceKind.INHERITS, "Base")

    assert resolve_reference(index, _environment(index, source), reference) == Resolution(
        None, ResolutionFailureReason.INCOMPLETE_PARSE
    )


def test_unrelated_partial_and_failed_files_do_not_taint_missing_type(cg):
    child = cg.symbol("Child", kind=SymbolKind.CLASS, qualified_name="q.Child")
    source = _java_file(cg, "Main.java", symbols=(child,))
    marker = cg.symbol("Marker", kind=SymbolKind.CLASS, qualified_name="other.Marker")
    partial = _java_file(
        cg, "partial.java", status=ParseStatus.PARTIAL, symbols=(marker,),
        issues=(ParseIssue(ParseIssueKind.EXTRACTION_ERROR, "truncated", None),),
    )
    failed = _java_file(cg, "p/Base.java", status=ParseStatus.FAILED)
    index = build_graph_indexes(validate_inputs(*cg.pair(source, partial, failed)))
    reference = _reference(source, child, ReferenceKind.INHERITS, "Base")

    assert resolve_reference(
        index, ImportEnvironment({"Main.java": ()}), reference
    ) == Resolution(None, ResolutionFailureReason.NOT_FOUND)


def test_java_call_without_containing_type_is_unsupported(cg):
    method = cg.symbol("run", kind=SymbolKind.METHOD)
    file = _java_file(cg, "Main.java", symbols=(method,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))
    reference = _reference(file, method, ReferenceKind.CALL, "run")

    assert resolve_reference(
        index, ImportEnvironment({"Main.java": ()}), reference
    ) == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def test_overloads_cannot_use_missing_argument_evidence(cg):
    owner = cg.symbol(
        "A", kind=SymbolKind.CLASS, qualified_name="p.A", end=500
    )
    run = cg.symbol(
        "run", kind=SymbolKind.METHOD, qualified_name="p.A.run",
        parent="p.A", start=10, end=90,
    )
    first = cg.symbol(
        "m", kind=SymbolKind.METHOD, qualified_name="p.A.m",
        parent="p.A", start=100, end=150,
    )
    second = cg.symbol(
        "m", kind=SymbolKind.METHOD, qualified_name="p.A.m",
        parent="p.A", start=160, end=220,
        parameters=(ParameterInfo("x", "int", None),),
    )
    call = CallSite("p.A.run", "this.m", CallKind.CALL, cg.loc(20, 30))
    file = _java_file(
        cg, "A.java", symbols=(owner, run, first, second), calls=(call,)
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    outcome = resolve_reference(
        index,
        ImportEnvironment({"A.java": ()}),
        call_reference(index, file, call),
    )

    assert outcome.reason is ResolutionFailureReason.AMBIGUOUS
    assert len(outcome.candidate_node_ids) == 2
    assert outcome.target_node_id is None


@pytest.mark.parametrize("callee_text", ["m", "this.m"])
def test_current_type_calls_resolve_from_bare_and_this_syntax(cg, callee_text):
    owner = cg.symbol(
        "A", kind=SymbolKind.CLASS, qualified_name="p.A", end=500
    )
    caller = cg.symbol(
        "run", kind=SymbolKind.METHOD, qualified_name="p.A.run",
        parent="p.A", start=10, end=90,
    )
    target = cg.symbol(
        "m", kind=SymbolKind.METHOD, qualified_name="p.A.m",
        parent="p.A", start=100, end=150,
    )
    call = CallSite("p.A.run", callee_text, CallKind.CALL, cg.loc(20, 30))
    file = _java_file(cg, "A.java", symbols=(owner, caller, target), calls=(call,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    assert resolve_reference(
        index,
        ImportEnvironment({"A.java": ()}),
        call_reference(index, file, call),
    ) == Resolution(symbol_node_id("repo", "A.java", target), None)


def test_type_qualified_call_requires_static_method(cg):
    owner = cg.symbol(
        "Main", kind=SymbolKind.CLASS, qualified_name="p.Main", end=300
    )
    caller = cg.symbol(
        "run", kind=SymbolKind.METHOD, qualified_name="p.Main.run",
        parent="p.Main", start=10, end=90,
    )
    tools = cg.symbol(
        "Tools", kind=SymbolKind.CLASS, qualified_name="p.Tools",
        start=400, end=700,
    )
    static_method = cg.symbol(
        "work", kind=SymbolKind.METHOD, qualified_name="p.Tools.work",
        parent="p.Tools", modifiers=("static",), start=420, end=480,
    )
    call = CallSite("p.Main.run", "Tools.work", CallKind.CALL, cg.loc(20, 30))
    file = _java_file(
        cg, "Main.java", symbols=(owner, caller, tools, static_method), calls=(call,)
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    assert resolve_reference(
        index,
        ImportEnvironment({"Main.java": ()}),
        call_reference(index, file, call),
    ) == Resolution(symbol_node_id("repo", "Main.java", static_method), None)


def test_type_qualified_call_rejects_instance_method(cg):
    owner = cg.symbol(
        "Main", kind=SymbolKind.CLASS, qualified_name="p.Main", end=300
    )
    caller = cg.symbol(
        "run", kind=SymbolKind.METHOD, qualified_name="p.Main.run",
        parent="p.Main", start=10, end=90,
    )
    tools = cg.symbol(
        "Tools", kind=SymbolKind.CLASS, qualified_name="p.Tools",
        start=400, end=700,
    )
    instance_method = cg.symbol(
        "work", kind=SymbolKind.METHOD, qualified_name="p.Tools.work",
        parent="p.Tools", start=420, end=480,
    )
    call = CallSite("p.Main.run", "Tools.work", CallKind.CALL, cg.loc(20, 30))
    file = _java_file(
        cg,
        "Main.java",
        symbols=(owner, caller, tools, instance_method),
        calls=(call,),
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    assert resolve_reference(
        index,
        ImportEnvironment({"Main.java": ()}),
        call_reference(index, file, call),
    ) == Resolution(None, ResolutionFailureReason.NOT_FOUND)


def test_explicit_static_import_resolves_bare_call(cg):
    item = ImportInfo("p.Tools.work", (), False, ("static",), cg.loc(1, 9))
    owner = cg.symbol(
        "Main", kind=SymbolKind.CLASS, qualified_name="q.Main",
        start=100, end=400,
    )
    caller = cg.symbol(
        "run", kind=SymbolKind.METHOD, qualified_name="q.Main.run",
        parent="q.Main", start=120, end=200,
    )
    call = CallSite("q.Main.run", "work", CallKind.CALL, cg.loc(130, 140))
    source = _java_file(
        cg, "Main.java", symbols=(owner, caller), imports=(item,), calls=(call,)
    )
    tools = cg.symbol(
        "Tools", kind=SymbolKind.CLASS, qualified_name="p.Tools", end=300
    )
    target = cg.symbol(
        "work", kind=SymbolKind.METHOD, qualified_name="p.Tools.work",
        parent="p.Tools", modifiers=("static",), start=20, end=80,
    )
    target_file = _java_file(cg, "Tools.java", symbols=(tools, target))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target_file)))

    assert resolve_reference(
        index, _environment(index, source), call_reference(index, source, call)
    ) == Resolution(symbol_node_id("repo", "Tools.java", target), None)


def test_parameter_receiver_conflict_blocks_type_qualified_call(cg):
    owner = cg.symbol(
        "Main", kind=SymbolKind.CLASS, qualified_name="p.Main", end=300
    )
    caller = cg.symbol(
        "run", kind=SymbolKind.METHOD, qualified_name="p.Main.run",
        parent="p.Main", start=10, end=90,
        parameters=(ParameterInfo("Tools", "Object", None),),
    )
    tools = cg.symbol(
        "Tools", kind=SymbolKind.CLASS, qualified_name="p.Tools",
        start=400, end=700,
    )
    target = cg.symbol(
        "work", kind=SymbolKind.METHOD, qualified_name="p.Tools.work",
        parent="p.Tools", modifiers=("static",), start=420, end=480,
    )
    call = CallSite("p.Main.run", "Tools.work", CallKind.CALL, cg.loc(20, 30))
    file = _java_file(
        cg, "Main.java", symbols=(owner, caller, tools, target), calls=(call,)
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    assert resolve_reference(
        index,
        ImportEnvironment({"Main.java": ()}),
        call_reference(index, file, call),
    ) == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def test_external_static_binding_propagates_to_call(cg):
    item = ImportInfo("external.Tools.work", (), False, ("static",), cg.loc(1, 9))
    owner = cg.symbol(
        "Main", kind=SymbolKind.CLASS, qualified_name="q.Main",
        start=100, end=400,
    )
    caller = cg.symbol(
        "run", kind=SymbolKind.METHOD, qualified_name="q.Main.run",
        parent="q.Main", start=120, end=200,
    )
    call = CallSite("q.Main.run", "work", CallKind.CALL, cg.loc(130, 140))
    source = _java_file(
        cg, "Main.java", symbols=(owner, caller), imports=(item,), calls=(call,)
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(source)))

    assert resolve_reference(
        index, _environment(index, source), call_reference(index, source, call)
    ) == Resolution(None, ResolutionFailureReason.EXTERNAL)


def test_unknown_receiver_is_unsupported(cg):
    owner = cg.symbol(
        "Main", kind=SymbolKind.CLASS, qualified_name="p.Main", end=300
    )
    caller = cg.symbol(
        "run", kind=SymbolKind.METHOD, qualified_name="p.Main.run",
        parent="p.Main", start=10, end=90,
    )
    call = CallSite("p.Main.run", "value.work", CallKind.CALL, cg.loc(20, 30))
    file = _java_file(cg, "Main.java", symbols=(owner, caller), calls=(call,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    assert resolve_reference(
        index,
        ImportEnvironment({"Main.java": ()}),
        call_reference(index, file, call),
    ) == Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def test_constructor_call_selects_explicit_constructor_child(cg):
    owner = cg.symbol(
        "Main", kind=SymbolKind.CLASS, qualified_name="p.Main", end=300
    )
    caller = cg.symbol(
        "run", kind=SymbolKind.METHOD, qualified_name="p.Main.run",
        parent="p.Main", start=10, end=90,
    )
    service = cg.symbol(
        "Service", kind=SymbolKind.CLASS, qualified_name="p.Service",
        start=400, end=700,
    )
    constructor = cg.symbol(
        "Service", kind=SymbolKind.CONSTRUCTOR,
        qualified_name="p.Service.Service", parent="p.Service",
        start=420, end=480,
    )
    call = CallSite("p.Main.run", "Service", CallKind.CONSTRUCTOR, cg.loc(20, 30))
    file = _java_file(
        cg, "Main.java", symbols=(owner, caller, service, constructor), calls=(call,)
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    assert resolve_reference(
        index,
        ImportEnvironment({"Main.java": ()}),
        call_reference(index, file, call),
    ) == Resolution(symbol_node_id("repo", "Main.java", constructor), None)


def test_missing_implicit_constructor_is_not_found(cg):
    owner = cg.symbol(
        "Main", kind=SymbolKind.CLASS, qualified_name="p.Main", end=300
    )
    caller = cg.symbol(
        "run", kind=SymbolKind.METHOD, qualified_name="p.Main.run",
        parent="p.Main", start=10, end=90,
    )
    service = cg.symbol(
        "Service", kind=SymbolKind.CLASS, qualified_name="p.Service",
        start=400, end=700,
    )
    call = CallSite("p.Main.run", "Service", CallKind.CONSTRUCTOR, cg.loc(20, 30))
    file = _java_file(cg, "Main.java", symbols=(owner, caller, service), calls=(call,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    assert resolve_reference(
        index,
        ImportEnvironment({"Main.java": ()}),
        call_reference(index, file, call),
    ) == Resolution(None, ResolutionFailureReason.NOT_FOUND)


def test_constructor_overloads_are_ambiguous(cg):
    owner = cg.symbol(
        "Main", kind=SymbolKind.CLASS, qualified_name="p.Main", end=300
    )
    caller = cg.symbol(
        "run", kind=SymbolKind.METHOD, qualified_name="p.Main.run",
        parent="p.Main", start=10, end=90,
    )
    service = cg.symbol(
        "Service", kind=SymbolKind.CLASS, qualified_name="p.Service",
        start=400, end=800,
    )
    first = cg.symbol(
        "Service", kind=SymbolKind.CONSTRUCTOR,
        qualified_name="p.Service.Service", parent="p.Service",
        start=420, end=480,
    )
    second = cg.symbol(
        "Service", kind=SymbolKind.CONSTRUCTOR,
        qualified_name="p.Service.Service", parent="p.Service",
        start=500, end=560,
        parameters=(ParameterInfo("value", "int", None),),
    )
    call = CallSite("p.Main.run", "Service", CallKind.CONSTRUCTOR, cg.loc(20, 30))
    file = _java_file(
        cg, "Main.java", symbols=(owner, caller, service, first, second), calls=(call,)
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))

    outcome = resolve_reference(
        index,
        ImportEnvironment({"Main.java": ()}),
        call_reference(index, file, call),
    )

    assert outcome == Resolution(
        None,
        ResolutionFailureReason.AMBIGUOUS,
        tuple(sorted((
            symbol_node_id("repo", "Main.java", first),
            symbol_node_id("repo", "Main.java", second),
        ))),
    )


def test_ambiguous_type_preserves_single_known_static_candidate(cg):
    item = ImportInfo("p.Tools", (), False, (), cg.loc(1, 9))
    owner = cg.symbol(
        "Main", kind=SymbolKind.CLASS, qualified_name="q.Main",
        start=100, end=400,
    )
    caller = cg.symbol(
        "run", kind=SymbolKind.METHOD, qualified_name="q.Main.run",
        parent="q.Main", start=120, end=200,
    )
    call = CallSite("q.Main.run", "Tools.work", CallKind.CALL, cg.loc(130, 140))
    source = _java_file(
        cg, "Main.java", symbols=(owner, caller), imports=(item,), calls=(call,)
    )
    first_tools = cg.symbol(
        "Tools", kind=SymbolKind.CLASS, qualified_name="p.Tools", end=300
    )
    known = cg.symbol(
        "work", kind=SymbolKind.METHOD, qualified_name="p.Tools.work",
        parent="p.Tools", modifiers=("static",), start=20, end=80,
    )
    second_tools = cg.symbol(
        "Tools", kind=SymbolKind.CLASS, qualified_name="p.Tools", end=300
    )
    first = _java_file(cg, "one.java", symbols=(first_tools, known))
    second = _java_file(cg, "two.java", symbols=(second_tools,))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, first, second)))

    assert resolve_reference(
        index, _environment(index, source), call_reference(index, source, call)
    ) == Resolution(
        None,
        ResolutionFailureReason.AMBIGUOUS,
        (symbol_node_id("repo", "one.java", known),),
    )


def test_mixed_explicit_static_bindings_preserve_every_call_candidate(cg):
    first_item = ImportInfo(
        "p.Tools.work", (), False, ("static",), cg.loc(1, 9)
    )
    second_item = ImportInfo(
        "q.Tools.work", (), False, ("static",), cg.loc(10, 18)
    )
    owner = cg.symbol(
        "Main", kind=SymbolKind.CLASS, qualified_name="r.Main",
        start=100, end=400,
    )
    caller = cg.symbol(
        "run", kind=SymbolKind.METHOD, qualified_name="r.Main.run",
        parent="r.Main", start=120, end=200,
    )
    call = CallSite("r.Main.run", "work", CallKind.CALL, cg.loc(130, 140))
    source = _java_file(
        cg,
        "Main.java",
        symbols=(owner, caller),
        imports=(first_item, second_item),
        calls=(call,),
    )
    targets = []
    files = []
    for path, package in (
        ("p-one.java", "p"),
        ("p-two.java", "p"),
        ("q.java", "q"),
    ):
        tools = cg.symbol(
            "Tools", kind=SymbolKind.CLASS,
            qualified_name=f"{package}.Tools", end=300,
        )
        target = cg.symbol(
            "work", kind=SymbolKind.METHOD,
            qualified_name=f"{package}.Tools.work", parent=f"{package}.Tools",
            modifiers=("static",), start=20, end=80,
        )
        targets.append(target)
        files.append(_java_file(cg, path, symbols=(tools, target)))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, *files)))

    outcome = resolve_reference(
        index, _environment(index, source), call_reference(index, source, call)
    )

    assert outcome == Resolution(
        None,
        ResolutionFailureReason.AMBIGUOUS,
        tuple(sorted(
            symbol_node_id("repo", file.relative_path, target)
            for file, target in zip(files, targets)
        )),
    )


def test_mixed_explicit_type_bindings_preserve_every_type_candidate(cg):
    first_item = ImportInfo("p.Base", (), False, (), cg.loc(1, 9))
    second_item = ImportInfo("q.Base", (), False, (), cg.loc(10, 18))
    child = cg.symbol(
        "Child", kind=SymbolKind.CLASS, qualified_name="r.Child",
        start=100, end=300,
    )
    source = _java_file(
        cg,
        "Main.java",
        symbols=(child,),
        imports=(first_item, second_item),
    )
    targets = []
    files = []
    for path, package in (
        ("p-one.java", "p"),
        ("p-two.java", "p"),
        ("q.java", "q"),
    ):
        target = cg.symbol(
            "Base", kind=SymbolKind.CLASS, qualified_name=f"{package}.Base"
        )
        targets.append(target)
        files.append(_java_file(cg, path, symbols=(target,)))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, *files)))
    reference = _reference(source, child, ReferenceKind.INHERITS, "Base")

    outcome = resolve_reference(index, _environment(index, source), reference)

    assert outcome == Resolution(
        None,
        ResolutionFailureReason.AMBIGUOUS,
        tuple(sorted(
            symbol_node_id("repo", file.relative_path, target)
            for file, target in zip(files, targets)
        )),
    )


def test_wildcard_member_lookup_is_memoized_per_environment(cg, monkeypatch):
    item = ImportInfo("p", (), True, (), cg.loc(1, 10))
    child = cg.symbol("Child", kind=SymbolKind.CLASS, qualified_name="q.Child")
    source = _java_file(cg, "Main.java", imports=(item,), symbols=(child,))
    base = cg.symbol("Base", kind=SymbolKind.CLASS, qualified_name="p.Base")
    target = _java_file(cg, "base.java", symbols=(base,))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))
    environment = _environment(index, source)
    reference = _reference(source, child, ReferenceKind.INHERITS, "Base")
    original = java_resolver._wildcard_type_candidates
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(java_resolver, "_wildcard_type_candidates", counted)

    assert resolve_reference(index, environment, reference).target_node_id is not None
    assert resolve_reference(index, environment, reference).target_node_id is not None
    assert calls == 1


def test_wildcard_memo_does_not_leak_across_graph_indexes(cg):
    item = ImportInfo("p", (), True, (), cg.loc(1, 10))
    child = cg.symbol("Child", kind=SymbolKind.CLASS, qualified_name="q.Child")
    source = _java_file(cg, "Main.java", imports=(item,), symbols=(child,))
    first_base = cg.symbol("Base", kind=SymbolKind.CLASS, qualified_name="p.Base")
    first_target = _java_file(cg, "first.java", symbols=(first_base,))
    first_index = build_graph_indexes(validate_inputs(*cg.pair(source, first_target)))
    environment = _environment(first_index, source)
    reference = _reference(source, child, ReferenceKind.INHERITS, "Base")

    first_result = resolve_reference(first_index, environment, reference)

    second_base = cg.symbol("Base", kind=SymbolKind.CLASS, qualified_name="p.Base")
    second_target = _java_file(cg, "second.java", symbols=(second_base,))
    second_index = build_graph_indexes(validate_inputs(*cg.pair(source, second_target)))
    second_result = resolve_reference(second_index, environment, reference)

    assert first_result.target_node_id == symbol_node_id("repo", "first.java", first_base)
    assert second_result.target_node_id == symbol_node_id("repo", "second.java", second_base)


def test_explicit_import_precedes_same_package_qualified_fallback(cg):
    item = ImportInfo("q.Base", (), False, (), cg.loc(1, 10))
    child = cg.symbol(
        "Child", kind=SymbolKind.CLASS, qualified_name="p.Child", start=100, end=300
    )
    source = _java_file(cg, "Main.java", imports=(item,), symbols=(child,))
    package_base = cg.symbol("Base", kind=SymbolKind.CLASS, qualified_name="p.Base")
    imported_base = cg.symbol("Base", kind=SymbolKind.CLASS, qualified_name="q.Base")
    package_file = _java_file(cg, "package.java", symbols=(package_base,))
    imported_file = _java_file(cg, "imported.java", symbols=(imported_base,))
    index = build_graph_indexes(
        validate_inputs(*cg.pair(source, package_file, imported_file))
    )
    reference = _reference(source, child, ReferenceKind.INHERITS, "Base")

    assert resolve_reference(index, _environment(index, source), reference) == Resolution(
        symbol_node_id("repo", "imported.java", imported_base), None
    )


def test_explicit_import_precedes_partial_same_package_fallback(cg):
    item = ImportInfo("q.Base", (), False, (), cg.loc(1, 10))
    child = cg.symbol(
        "Child", kind=SymbolKind.CLASS, qualified_name="p.Child", start=100, end=300
    )
    source = _java_file(cg, "Main.java", imports=(item,), symbols=(child,))
    marker = cg.symbol("Marker", kind=SymbolKind.CLASS, qualified_name="p.Marker")
    partial = _java_file(
        cg,
        "partial.java",
        status=ParseStatus.PARTIAL,
        symbols=(marker,),
        issues=(ParseIssue(ParseIssueKind.EXTRACTION_ERROR, "truncated", None),),
    )
    imported_base = cg.symbol("Base", kind=SymbolKind.CLASS, qualified_name="q.Base")
    imported_file = _java_file(cg, "imported.java", symbols=(imported_base,))
    index = build_graph_indexes(
        validate_inputs(*cg.pair(source, partial, imported_file))
    )
    reference = _reference(source, child, ReferenceKind.INHERITS, "Base")

    assert resolve_reference(index, _environment(index, source), reference) == Resolution(
        symbol_node_id("repo", "imported.java", imported_base), None
    )


def test_nearest_enclosing_type_precedes_top_level_same_file_type(cg):
    outer = cg.symbol(
        "Outer", kind=SymbolKind.CLASS, qualified_name="p.Outer", start=0, end=500
    )
    enclosing_base = cg.symbol(
        "Base", kind=SymbolKind.CLASS, qualified_name="p.Outer.Base",
        parent="p.Outer", start=20, end=150,
    )
    child = cg.symbol(
        "Child", kind=SymbolKind.CLASS, qualified_name="p.Outer.Child",
        parent="p.Outer", start=200, end=350,
    )
    top_level_base = cg.symbol(
        "Base", kind=SymbolKind.CLASS, qualified_name="p.Base", start=600, end=750
    )
    file = _java_file(
        cg, "Types.java", symbols=(outer, enclosing_base, child, top_level_base)
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))
    reference = _reference(file, child, ReferenceKind.INHERITS, "Base")

    assert resolve_reference(
        index, ImportEnvironment({"Types.java": ()}), reference
    ) == Resolution(symbol_node_id("repo", "Types.java", enclosing_base), None)


@pytest.mark.parametrize(
    ("module", "is_wildcard", "modifiers"),
    [
        ("p.Missing", False, ()),
        ("p.Tools.run", False, ("static",)),
        ("p.Tools", True, ("static",)),
    ],
)
def test_anchored_partial_package_makes_missing_import_incomplete(
    cg, module, is_wildcard, modifiers
):
    item = ImportInfo(module, (), is_wildcard, modifiers, cg.loc(1, 10))
    source = _java_file(cg, "Main.java", imports=(item,))
    marker = cg.symbol("Marker", kind=SymbolKind.CLASS, qualified_name="p.Marker")
    partial = _java_file(
        cg,
        "opaque.java",
        status=ParseStatus.PARTIAL,
        symbols=(marker,),
        issues=(ParseIssue(ParseIssueKind.EXTRACTION_ERROR, "truncated", None),),
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(source, partial)))

    result = resolve_import(index, source, item)

    assert result.resolution == Resolution(
        None, ResolutionFailureReason.INCOMPLETE_PARSE
    )
    if result.bindings:
        assert result.bindings[0].resolution == Resolution(
            None, ResolutionFailureReason.INCOMPLETE_PARSE
        )


def test_static_wildcard_reference_preserves_anchored_partial_owner(
    cg, monkeypatch
):
    item = ImportInfo("p.Tools", (), True, ("static",), cg.loc(1, 10))
    child = cg.symbol(
        "Child", kind=SymbolKind.CLASS, qualified_name="q.Child", start=100, end=300
    )
    source = _java_file(cg, "Main.java", imports=(item,), symbols=(child,))
    marker = cg.symbol("Marker", kind=SymbolKind.CLASS, qualified_name="p.Marker")
    partial = _java_file(
        cg,
        "opaque.java",
        status=ParseStatus.PARTIAL,
        symbols=(marker,),
        issues=(ParseIssue(ParseIssueKind.EXTRACTION_ERROR, "truncated", None),),
    )
    index = build_graph_indexes(validate_inputs(*cg.pair(source, partial)))
    environment = _environment(index, source)
    reference = _reference(source, child, ReferenceKind.INHERITS, "Base")
    original = java_resolver._wildcard_type_candidates
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(java_resolver, "_wildcard_type_candidates", counted)

    assert environment.by_file["Main.java"][0][1].resolution == Resolution(
        None, ResolutionFailureReason.INCOMPLETE_PARSE
    )
    expected = Resolution(None, ResolutionFailureReason.INCOMPLETE_PARSE)
    assert resolve_reference(index, environment, reference) == expected
    assert resolve_reference(index, environment, reference) == expected
    assert calls == 1


@pytest.mark.parametrize("wildcard_has_other_type", [False, True])
def test_same_package_type_survives_nonmatching_wildcard(
    cg, wildcard_has_other_type
):
    item = ImportInfo("r", (), True, (), cg.loc(1, 10))
    child = cg.symbol(
        "Child", kind=SymbolKind.CLASS, qualified_name="p.Child", start=100, end=300
    )
    source = _java_file(cg, "Main.java", imports=(item,), symbols=(child,))
    base = cg.symbol("Base", kind=SymbolKind.CLASS, qualified_name="p.Base")
    package_file = _java_file(cg, "package.java", symbols=(base,))
    files = [source, package_file]
    if wildcard_has_other_type:
        other = cg.symbol("Other", kind=SymbolKind.CLASS, qualified_name="r.Other")
        files.append(_java_file(cg, "wildcard.java", symbols=(other,)))
    index = build_graph_indexes(validate_inputs(*cg.pair(*files)))
    reference = _reference(source, child, ReferenceKind.INHERITS, "Base")

    assert resolve_reference(index, _environment(index, source), reference) == Resolution(
        symbol_node_id("repo", "package.java", base), None
    )
