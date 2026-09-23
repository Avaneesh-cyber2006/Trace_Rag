import pytest

import backend.code_graph.builder as builder_module
from backend.code_graph import build_code_graph
from backend.code_graph.exceptions import CodeGraphValidationError
from backend.code_graph.models import (
    GraphEdgeKind,
    GraphNodeKind,
    ResolutionFailureReason,
)
from backend.code_parser.models import (
    CallKind,
    CallSite,
    ParsedLanguage,
    ParseIssue,
    ParseIssueKind,
    ParseStatus,
    SymbolKind,
)


def test_partial_keeps_trusted_symbols(cg):
    issue = ParseIssue(
        ParseIssueKind.SYNTAX_ERROR, "broken elsewhere", cg.loc(300, 310)
    )
    file = cg.parsed(
        status=ParseStatus.PARTIAL,
        symbols=(cg.symbol("safe"),),
        issues=(issue,),
    )
    graph = build_code_graph(*cg.pair(file))
    assert len(graph.nodes) == 2
    assert all(node.parse_status is ParseStatus.PARTIAL for node in graph.nodes)
    assert sum(node.node_kind is GraphNodeKind.SYMBOL for node in graph.nodes) == 1


@pytest.mark.parametrize(
    "language,path",
    [
        (ParsedLanguage.PYTHON, "mod.py"),
        (ParsedLanguage.JAVA, "mod.java"),
        (ParsedLanguage.JAVASCRIPT, "mod.js"),
        (ParsedLanguage.JAVASCRIPT, "mod.jsx"),
        (ParsedLanguage.TYPESCRIPT, "mod.ts"),
        (ParsedLanguage.TSX, "mod.tsx"),
    ],
)
def test_partial_trusted_call_and_structure_survive_for_each_source_form(
    cg, language, path
):
    is_java = language is ParsedLanguage.JAVA
    owner = (
        cg.symbol("C", kind=SymbolKind.CLASS, qualified_name="pkg.C", start=0, end=400)
        if is_java
        else None
    )
    caller_name = "pkg.C.caller" if is_java else "caller"
    callee_name = "pkg.C.callee" if is_java else "callee"
    parent = "pkg.C" if is_java else None
    caller = cg.symbol(
        "caller",
        kind=SymbolKind.METHOD if is_java else SymbolKind.FUNCTION,
        qualified_name=caller_name,
        parent=parent,
        start=10 if is_java else 0,
        end=100,
    )
    callee = cg.symbol(
        "callee",
        kind=SymbolKind.METHOD if is_java else SymbolKind.FUNCTION,
        qualified_name=callee_name,
        parent=parent,
        start=120,
        end=220,
    )
    location = cg.loc(40, 50)
    call_target = "this.callee" if is_java else "callee"
    call = CallSite(caller_name, call_target, CallKind.CALL, location)
    issue = ParseIssue(
        ParseIssueKind.SYNTAX_ERROR, "truncated later", cg.loc(300, 310)
    )
    file = cg.parsed(
        path,
        language=language,
        status=ParseStatus.PARTIAL,
        symbols=((owner, caller, callee) if owner is not None else (caller, callee)),
        calls=(call,),
        issues=(issue,),
    )
    graph = build_code_graph(*cg.pair(file))

    symbol_count = 3 if is_java else 2
    assert sum(node.node_kind is GraphNodeKind.SYMBOL for node in graph.nodes) == symbol_count
    assert len(
        [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.DEFINES]
    ) == (1 if is_java else 2)
    assert len(
        [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.CONTAINS]
    ) == (2 if is_java else 0)
    assert len([edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.CALLS]) == 1
    assert not graph.unresolved_references


@pytest.mark.parametrize("status", [ParseStatus.PARTIAL, ParseStatus.FAILED])
def test_failed_file_retains_only_file_node_without_issue_references(cg, status):
    issues = (ParseIssue(ParseIssueKind.SYNTAX_ERROR, "broken", cg.loc(10, 20)),)
    file = cg.parsed("broken.py", status=status, issues=issues)
    graph = build_code_graph(*cg.pair(file))
    assert len(graph.nodes) == 1
    assert graph.nodes[0].node_kind is GraphNodeKind.FILE
    assert graph.unresolved_references == ()


@pytest.mark.parametrize(
    "language,path",
    [
        (ParsedLanguage.PYTHON, "mod.py"),
        (ParsedLanguage.JAVA, "mod.java"),
        (ParsedLanguage.JAVASCRIPT, "mod.js"),
        (ParsedLanguage.JAVASCRIPT, "mod.jsx"),
        (ParsedLanguage.TYPESCRIPT, "mod.ts"),
        (ParsedLanguage.TSX, "mod.tsx"),
    ],
)
def test_relevant_partial_loss_classifies_missing_call_as_incomplete(
    cg, language, path
):
    is_java = language is ParsedLanguage.JAVA
    owner = (
        cg.symbol("C", kind=SymbolKind.CLASS, qualified_name="pkg.C", start=0, end=200)
        if is_java
        else None
    )
    caller_name = "pkg.C.caller" if is_java else "caller"
    caller = cg.symbol(
        "caller",
        kind=SymbolKind.METHOD if is_java else SymbolKind.FUNCTION,
        qualified_name=caller_name,
        parent="pkg.C" if is_java else None,
        start=10 if is_java else 0,
        end=100,
    )
    location = cg.loc(40, 50)
    call = CallSite(
        caller_name,
        "this.missing" if is_java else "missing",
        CallKind.CALL,
        location,
    )
    issue = ParseIssue(ParseIssueKind.EXTRACTION_ERROR, "near call", cg.loc(40, 60))
    file = cg.parsed(
        path,
        language=language,
        status=ParseStatus.PARTIAL,
        symbols=(owner, caller) if owner is not None else (caller,),
        calls=(call,),
        issues=(issue,),
    )

    graph = build_code_graph(*cg.pair(file))

    assert len(graph.unresolved_references) == 1
    assert graph.unresolved_references[0].reason is ResolutionFailureReason.INCOMPLETE_PARSE


def test_locationless_parse_loss_keeps_truncated_call_incomplete(cg):
    caller = cg.symbol("caller", start=0, end=100)
    location = cg.loc(40, 50)
    call = CallSite("caller", "truncated_name", CallKind.CALL, location)
    issue = ParseIssue(ParseIssueKind.EXTRACTION_ERROR, "truncated", None)
    file = cg.parsed(
        status=ParseStatus.PARTIAL,
        symbols=(caller,),
        calls=(call,),
        issues=(issue,),
    )

    graph = build_code_graph(*cg.pair(file))

    assert not [edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.CALLS]
    assert graph.unresolved_references[0].reason is ResolutionFailureReason.INCOMPLETE_PARSE


def test_dynamic_expression_remains_unsupported_under_relevant_parse_loss(cg):
    caller = cg.symbol("caller", start=0, end=100)
    location = cg.loc(40, 50)
    call = CallSite("caller", "factory().run", CallKind.CALL, location)
    issue = ParseIssue(ParseIssueKind.EXTRACTION_ERROR, "near call", cg.loc(40, 60))
    file = cg.parsed(
        status=ParseStatus.PARTIAL,
        symbols=(caller,),
        calls=(call,),
        issues=(issue,),
    )

    graph = build_code_graph(*cg.pair(file))

    assert graph.unresolved_references[0].reason is ResolutionFailureReason.UNSUPPORTED


def test_parse_loss_does_not_block_a_known_complete_declaration(cg):
    caller = cg.symbol("caller", start=0, end=100)
    target = cg.symbol("target", start=120, end=220)
    location = cg.loc(40, 50)
    call = CallSite("caller", "target", CallKind.CALL, location)
    issue = ParseIssue(ParseIssueKind.EXTRACTION_ERROR, "near call", cg.loc(40, 60))
    file = cg.parsed(
        status=ParseStatus.PARTIAL,
        symbols=(caller, target),
        calls=(call,),
        issues=(issue,),
    )

    graph = build_code_graph(*cg.pair(file))

    assert len([edge for edge in graph.edges if edge.edge_kind is GraphEdgeKind.CALLS]) == 1
    assert not graph.unresolved_references


def test_unrelated_failed_file_does_not_change_a_complete_miss(cg):
    caller = cg.symbol("caller", start=0, end=100)
    call = CallSite("caller", "missing", CallKind.CALL, cg.loc(40, 50))
    source = cg.parsed("main.py", symbols=(caller,), calls=(call,))
    unrelated = cg.parsed("other.py", status=ParseStatus.FAILED)

    graph = build_code_graph(*cg.pair(source, unrelated))

    assert graph.unresolved_references[0].reason is ResolutionFailureReason.NOT_FOUND


def test_valid_top_level_call_remains_unsupported(cg):
    call = CallSite(None, "target", CallKind.CALL, cg.loc(20, 30))
    file = cg.parsed(calls=(call,))

    graph = build_code_graph(*cg.pair(file))

    assert graph.unresolved_references[0].reason is ResolutionFailureReason.UNSUPPORTED


@pytest.mark.parametrize(
    "language,path",
    [
        (ParsedLanguage.PYTHON, "mod.py"),
        (ParsedLanguage.JAVA, "mod.java"),
        (ParsedLanguage.JAVASCRIPT, "mod.js"),
        (ParsedLanguage.JAVASCRIPT, "mod.jsx"),
        (ParsedLanguage.TYPESCRIPT, "mod.ts"),
        (ParsedLanguage.TSX, "mod.tsx"),
    ],
)
@pytest.mark.parametrize(
    "status,issues,expected",
    [
        (ParseStatus.PARTIAL, ("overlapping",), ResolutionFailureReason.INCOMPLETE_PARSE),
        (ParseStatus.SUCCESS, (), ResolutionFailureReason.UNSUPPORTED),
    ],
    ids=("owner-lost", "valid-top-level"),
)
def test_unowned_calls_keep_loss_and_supported_syntax_distinct(
    cg, language, path, status, issues, expected
):
    location = cg.loc(40, 50)
    call = CallSite(None, "target", CallKind.CALL, location)
    parse_issues = (
        (ParseIssue(ParseIssueKind.EXTRACTION_ERROR, "owner missing", cg.loc(40, 60)),)
        if issues
        else ()
    )
    file = cg.parsed(
        path,
        language=language,
        status=status,
        calls=(call,),
        issues=parse_issues,
    )

    graph = build_code_graph(*cg.pair(file))

    assert graph.unresolved_references[0].reason is expected


def test_failed_extraction_is_rejected_before_resolvers_run(cg, monkeypatch):
    failed = cg.parsed(
        status=ParseStatus.FAILED,
        symbols=(cg.symbol("contradiction"),),
    )
    pair = cg.pair(failed)

    def resolver_must_not_run():
        raise AssertionError("resolver dispatched before input validation")

    monkeypatch.setattr(builder_module, "load_resolvers", resolver_must_not_run)
    with pytest.raises(CodeGraphValidationError):
        build_code_graph(*pair)
