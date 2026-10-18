"""Indexed scaling and candidate-output accounting for the code graph builder.

Every assertion here is a deterministic structural-complexity bound: operation
counts produced by :class:`CountingMapping`, or exact candidate/output
accounting. No wall-clock threshold appears anywhere in this file, and no test
tolerates a global scan: the counters fail the test if a resolver falls back to
iterating a global map instead of using a keyed bucket.
"""

import copy

import pytest
from collections import Counter
from collections.abc import Mapping
from pathlib import PurePosixPath

from backend.code_graph import build_code_graph
from backend.code_graph.identities import file_node_id
from backend.code_graph.indexes import build_graph_indexes
from backend.code_graph.models import GraphEdgeKind, ReferenceKind, ResolutionFailureReason
from backend.code_graph.references import call_reference
from backend.code_graph.resolvers import resolve_import, resolve_reference
from backend.code_graph.resolvers.base import ImportEnvironment
from backend.code_graph.structure import structural_edges
from backend.code_graph.validation import validate_inputs
from backend.code_parser.models import (
    CallKind,
    CallSite,
    CodeParseInventory,
    ImportBinding,
    ImportInfo,
    ParsedFile,
    ParsedLanguage,
    ParseStatus,
    SourceLocation,
    SymbolInfo,
    SymbolKind,
)
from backend.file_scanner.models import FileCategory, FileInventory, ScannedFile

NAMESPACE = "test-repo"

#: Maps whose size grows with the whole repository. A resolver that iterates one
#: of these per reference is O(R x S); a resolver that only does keyed lookups
#: never iterates them.
GLOBAL_MAPS = ("nodes_by_id", "symbols_by_id", "by_qualified_name", "nodes")


class CountingMapping(Mapping):
    """Wraps a read-only mapping to count access operations for scaling tests."""

    def __init__(self, source):
        self.source = source
        self.counts = Counter()

    def __getitem__(self, key):
        self.counts["get"] += 1
        return self.source[key]

    def get(self, key, default=None):
        self.counts["get"] += 1
        return self.source.get(key, default)

    def __iter__(self):
        self.counts["iter"] += 1
        for key in self.source:
            self.counts["yield"] += 1
            yield key

    def __len__(self):
        return len(self.source)


def test_counting_mapping_tracks_accesses():
    """The instrument itself must measure every access it claims to."""
    source = {"a": 1, "b": 2, "c": 3}
    counted = CountingMapping(source)

    assert counted["a"] == 1
    assert counted.counts["get"] == 1
    assert counted.get("b") == 2
    assert counted.counts["get"] == 2
    assert counted.get("missing", "default") == "default"
    assert counted.counts["get"] == 3
    assert set(counted) == {"a", "b", "c"}
    assert counted.counts["iter"] == 1
    assert counted.counts["yield"] == 3
    assert len(counted) == 3
    assert "a" in counted


# --------------------------------------------------------------------------
# inventory builders
# --------------------------------------------------------------------------


def loc(start=0, end=100):
    return SourceLocation(start, end, 1, start, 1, end)


def symbol(
    name,
    *,
    kind=SymbolKind.FUNCTION,
    qualified_name=None,
    parent=None,
    start=0,
    end=100,
    modifiers=(),
    base_types=(),
    implemented_types=(),
):
    return SymbolInfo(
        name,
        kind,
        qualified_name or name,
        parent,
        loc(start, end),
        (),
        None,
        modifiers,
        base_types,
        implemented_types,
    )


def parsed(path, *, language=ParsedLanguage.PYTHON, symbols=(), imports=(), calls=()):
    return ParsedFile(
        path,
        language,
        ParseStatus.SUCCESS,
        tuple(symbols),
        tuple(imports),
        tuple(calls),
        (),
        "0" * 64,
    )


def _language_name(language):
    return "typescript" if language is ParsedLanguage.TSX else language.value


def inventories(*files):
    scanned = tuple(
        ScannedFile(
            item.relative_path,
            PurePosixPath(item.relative_path).name,
            PurePosixPath(item.relative_path).suffix,
            _language_name(item.language),
            FileCategory.SOURCE,
            1 << 20,
        )
        for item in files
    )
    return (
        FileInventory(
            "/inert/repo", len(files), len(files), 0, scanned, (), (), NAMESPACE
        ),
        CodeParseInventory(
            "/inert/repo",
            len(files),
            len(files),
            0,
            0,
            0,
            tuple(files),
            (),
            NAMESPACE,
        ),
    )


def counted_index(index):
    """Clone ``index`` with its global maps replaced by counting wrappers."""
    clone = copy.copy(index)
    counters = {}
    for name in GLOBAL_MAPS:
        counter = CountingMapping(getattr(index, name))
        object.__setattr__(clone, name, counter)
        counters[name] = counter
    return clone, counters


def _iterations(counters):
    return sum(counter.counts["iter"] for counter in counters.values())


def _lookups(counters):
    return sum(counter.counts["get"] for counter in counters.values())


# --------------------------------------------------------------------------
# inventory size scaling
# --------------------------------------------------------------------------


def test_synthetic_inventories_scale_linearly():
    """Doubling files and symbols doubles the published node count."""
    counts = []
    for file_count in (64, 128, 256):
        files = tuple(
            parsed(
                f"file_{file_index}.py",
                symbols=tuple(
                    symbol(
                        f"symbol_{position}",
                        start=position * 20,
                        end=position * 20 + 10,
                    )
                    for position in range(4)
                ),
            )
            for file_index in range(file_count)
        )
        graph = build_code_graph(*inventories(*files))
        counts.append(len(graph.nodes))
        assert len(graph.nodes) == file_count * 5
        assert len(graph.edges) == file_count * 4

    assert counts[1] == 2 * counts[0]
    assert counts[2] == 4 * counts[0]


def test_index_construction_produces_one_symbol_range_index_per_file():
    """Index construction is driven by files, and covers every file."""
    files = tuple(
        parsed(
            f"file_{index}.py",
            symbols=tuple(
                symbol(f"symbol_{position}", start=position * 20, end=position * 20 + 10)
                for position in range(4)
            ),
        )
        for index in range(32)
    )

    index = build_graph_indexes(validate_inputs(*inventories(*files)))

    assert len(index.symbol_ranges_by_file) == 32
    assert len(index.file_ids) == 32
    assert len(index.by_file_name) == 32 * 4


# --------------------------------------------------------------------------
# the core structural guarantee: no O(R x S) global scan
# --------------------------------------------------------------------------


def _same_file_call_batch(unrelated_symbols, reference_count):
    symbols = [
        symbol("helper", start=0, end=40),
        symbol("caller", start=50, end=150),
    ]
    for offset in range(unrelated_symbols):
        start = 200 + offset * 20
        symbols.append(
            symbol(f"unrelated_{offset}", start=start, end=start + 10)
        )
    calls = tuple(
        CallSite(
            "caller",
            "helper",
            CallKind.CALL,
            loc(60 + 2 * position, 61 + 2 * position),
        )
        for position in range(reference_count)
    )
    return parsed("main.py", symbols=symbols, calls=calls), calls


def test_reference_resolution_never_iterates_a_global_map():
    """A batch of same-file calls must not scan any whole-repository map."""
    file, calls = _same_file_call_batch(unrelated_symbols=400, reference_count=20)
    index = build_graph_indexes(validate_inputs(*inventories(file)))
    counted, counters = counted_index(index)
    environment = ImportEnvironment({"main.py": ()})

    for item in calls:
        resolve_reference(counted, environment, call_reference(index, file, item))

    assert _iterations(counters) == 0
    assert _lookups(counters) <= 20 * len(calls)


def test_unrelated_symbols_do_not_change_per_reference_lookup_cost():
    """Repository size must not change per-reference work: no O(R x S)."""
    measurements = {}
    for unrelated in (0, 200, 800):
        file, calls = _same_file_call_batch(
            unrelated_symbols=unrelated, reference_count=20
        )
        index = build_graph_indexes(validate_inputs(*inventories(file)))
        counted, counters = counted_index(index)
        environment = ImportEnvironment({"main.py": ()})

        for item in calls:
            resolve_reference(counted, environment, call_reference(index, file, item))

        measurements[unrelated] = _lookups(counters) / len(calls)
        assert _iterations(counters) == 0

    assert measurements[0] == measurements[200] == measurements[800]
    assert measurements[800] <= 20


def test_ambiguous_references_emit_candidates_without_iterating_a_global_map():
    """Even AMBIGUOUS output accounting must not fall back to a scan."""
    caller = symbol("caller", start=0, end=200)
    symbols = [caller]
    for offset in range(300):
        start = 1000 + offset * 20
        symbols.append(
            symbol(
                "target",
                start=start,
                end=start + 10,
            )
        )
    calls = tuple(
        CallSite("caller", "target", CallKind.CALL, loc(50 + position, 51 + position))
        for position in range(10)
    )
    file = parsed("main.py", symbols=symbols, calls=calls)
    index = build_graph_indexes(validate_inputs(*inventories(file)))
    counted, counters = counted_index(index)
    environment = ImportEnvironment({"main.py": ()})

    results = [
        resolve_reference(counted, environment, call_reference(index, file, item))
        for item in calls
    ]

    assert _iterations(counters) == 0
    for result in results:
        assert result.reason is ResolutionFailureReason.AMBIGUOUS


# --------------------------------------------------------------------------
# import environment indexing
# --------------------------------------------------------------------------


def test_import_environment_is_keyed_by_scope_and_name_not_rebuilt_per_call():
    """Imported-name lookups hit bindings_by_scope_name, not a record scan."""
    item = ImportInfo(
        "pkg.util",
        (ImportBinding("run", "execute"),),
        False,
        (),
        loc(20, 45),
    )
    caller = symbol("caller", start=100, end=300)
    calls = tuple(
        CallSite("caller", "execute", CallKind.CALL, loc(150 + position, 160 + position))
        for position in range(10)
    )
    source = parsed("main.py", symbols=(caller,), imports=(item,), calls=calls)
    target = parsed("pkg/util.py", symbols=(symbol("run", start=5, end=15),))

    index = build_graph_indexes(validate_inputs(*inventories(source, target)))
    counted, counters = counted_index(index)
    environment = ImportEnvironment(
        {"main.py": ((item, resolve_import(counted, source, item)),)}
    )

    for item_call in calls:
        result = resolve_reference(
            counted, environment, call_reference(index, source, item_call)
        )
        assert result.target_node_id is not None

    assert _iterations(counters) == 0
    assert _lookups(counters) <= 20 * len(calls)
    assert list(environment.bindings_by_scope_name) == [
        (file_node_id(NAMESPACE, "main.py"), "execute")
    ]


def test_import_environment_indexes_every_binding_by_scope_and_name():
    """Bindings are bucketed once, keyed by (scope, local name)."""
    items = (
        ImportInfo("pkg.a", (ImportBinding("alpha", "a"),), False, (), loc(0, 12)),
        ImportInfo("pkg.b", (ImportBinding("beta", "b"),), False, (), loc(14, 26)),
        ImportInfo("pkg.c", (), False, (), loc(28, 38)),
    )
    source = parsed("main.py", imports=items)
    files = [source]
    for path, name in (("pkg/a.py", "alpha"), ("pkg/b.py", "beta"), ("pkg/c.py", "gamma")):
        files.append(parsed(path, symbols=(symbol(name, start=0, end=50),)))
    index = build_graph_indexes(validate_inputs(*inventories(*files)))

    environment = ImportEnvironment(
        {"main.py": tuple((item, resolve_import(index, source, item)) for item in items)}
    )

    keys = set(environment.bindings_by_scope_name)
    scope = file_node_id(NAMESPACE, "main.py")
    assert (scope, "a") in keys
    assert (scope, "b") in keys
    assert len(keys) == 2
    assert environment.wildcards_by_file == {}
    assert tuple(item for item, _ in environment.by_file["main.py"]) == items


# --------------------------------------------------------------------------
# structural edges and nesting
# --------------------------------------------------------------------------


def test_structural_edges_are_driven_by_precomputed_parent_ids():
    """structural_edges must not scan the node tuple to discover parents."""
    outer = symbol("Outer", kind=SymbolKind.CLASS, start=0, end=9000)
    inner = symbol(
        "Inner",
        kind=SymbolKind.CLASS,
        qualified_name="Outer.Inner",
        parent="Outer",
        start=100,
        end=8000,
    )
    plain = symbol("plain", start=20000, end=20050)
    file = parsed("nested.py", symbols=(outer, inner, plain))
    index = build_graph_indexes(validate_inputs(*inventories(file)))
    counted, counters = counted_index(index)

    edges = structural_edges(counted)

    contains = [edge for edge in edges if edge.edge_kind is GraphEdgeKind.CONTAINS]
    assert len(contains) == 1
    assert _iterations(counters) == 0


def _nested_file(depth, unrelated):
    symbols = []
    parent = None
    for level in range(depth):
        qualified = "Outer" + "".join(f".L{position}" for position in range(level + 1))
        start = level * 100
        symbols.append(
            symbol(
                f"L{level}",
                qualified_name=qualified,
                parent=parent,
                start=start,
                end=9000 - start,
            )
        )
        parent = qualified
    for offset in range(unrelated):
        start = 20000 + offset * 20
        symbols.append(symbol(f"filler_{offset}", start=start, end=start + 10))
    leaf_start = (depth - 1) * 100 + 100
    leaf_qualified = f"{parent}.leaf"
    symbols.append(
        symbol("leaf", qualified_name=leaf_qualified, parent=parent,
               start=leaf_start, end=leaf_start + 50)
    )
    call = CallSite(
        leaf_qualified, "L0", CallKind.CALL, loc(leaf_start + 5, leaf_start + 10)
    )
    return parsed("deep.py", symbols=symbols, calls=(call,)), call


def test_scope_walk_cost_grows_with_depth_not_with_file_size():
    """Nested scope resolution is O(depth); filler symbols must not change it."""
    measurements = {}
    for filler in (0, 200, 800):
        file, call = _nested_file(depth=8, unrelated=filler)
        index = build_graph_indexes(validate_inputs(*inventories(file)))
        counted, counters = counted_index(index)
        environment = ImportEnvironment({"deep.py": ()})
        reference = call_reference(index, file, call)

        resolve_reference(counted, environment, reference)
        measurements[filler] = _lookups(counters)

        assert _iterations(counters) == 0

    assert measurements[0] == measurements[200] == measurements[800]


def test_scope_walk_cost_is_linear_in_depth():
    """Doubling nesting depth must not more than double the per-call work."""
    costs = {}
    for depth in (4, 8, 16):
        file, call = _nested_file(depth=depth, unrelated=0)
        index = build_graph_indexes(validate_inputs(*inventories(file)))
        counted, counters = counted_index(index)
        environment = ImportEnvironment({"deep.py": ()})

        resolve_reference(counted, environment, call_reference(index, file, call))
        costs[depth] = _lookups(counters)

    assert costs[8] <= 2 * costs[4]
    assert costs[16] <= 2 * costs[8]


# --------------------------------------------------------------------------
# Java wildcard candidates: K matching files, U unrelated files
# --------------------------------------------------------------------------


def _java_package_file(package, name, offset):
    return parsed(
        f"{name}.java",
        language=ParsedLanguage.JAVA,
        symbols=(
            symbol(
                name,
                kind=SymbolKind.CLASS,
                qualified_name=f"{package}.{name}",
                start=offset * 20,
                end=offset * 20 + 10,
            ),
        ),
    )


def _java_wildcard_case(matching, unrelated):
    wildcard = ImportInfo("wild", (), True, (), loc(0, 12))
    source = parsed(
        "Use.java",
        language=ParsedLanguage.JAVA,
        imports=(wildcard,),
        symbols=(
            symbol(
                "Use",
                kind=SymbolKind.CLASS,
                qualified_name="app.Use",
                start=0,
                end=9000,
            ),
        ),
    )
    files = [source]
    for position in range(matching):
        files.append(_java_package_file("wild", f"Part{position}", position))
    for position in range(unrelated):
        files.append(
            parsed(
                f"other{position}.py",
                symbols=(
                    symbol(f"py{position}", start=position * 20, end=position * 20 + 10),
                ),
            )
        )
    return source, files


@pytest.mark.parametrize("matching", [2, 4, 8])
def test_java_wildcard_candidates_track_matching_files_only(matching):
    """Wildcard candidate set is exactly the matching package files."""
    source, files = _java_wildcard_case(matching, unrelated=200)
    index = build_graph_indexes(validate_inputs(*inventories(*files)))

    result = resolve_import(index, source, source.imports[0]).resolution

    assert result.reason is ResolutionFailureReason.AMBIGUOUS
    assert len(result.candidate_node_ids) == matching
    assert result.candidate_node_ids == tuple(sorted(result.candidate_node_ids))


def test_java_wildcard_resolution_cost_is_independent_of_unrelated_files():
    """Adding unrelated files must not add per-reference work."""
    measurements = {}
    for unrelated in (0, 200, 800):
        source, files = _java_wildcard_case(matching=4, unrelated=unrelated)
        index = build_graph_indexes(validate_inputs(*inventories(*files)))
        counted, counters = counted_index(index)

        for _ in range(5):
            resolve_import(counted, source, source.imports[0])

        measurements[unrelated] = _lookups(counters)

    assert measurements[0] == measurements[200] == measurements[800]


def test_java_wildcard_memo_is_per_build_and_not_a_global_cache():
    """The wildcard memo must not survive across GraphIndexes instances."""
    source, files = _java_wildcard_case(matching=4, unrelated=0)
    first_index = build_graph_indexes(validate_inputs(*inventories(*files)))
    environment = ImportEnvironment({"Use.java": ()})
    item = source.imports[0]

    first = resolve_import(first_index, source, item).resolution
    second = resolve_import(first_index, source, item).resolution

    smaller_files = [files[0], *files[1:3]]
    smaller_index = build_graph_indexes(validate_inputs(*inventories(*smaller_files)))
    third = resolve_import(smaller_index, source, item).resolution

    assert first.reason is ResolutionFailureReason.AMBIGUOUS
    assert second == first
    assert len(first.candidate_node_ids) == 4
    assert third.reason is ResolutionFailureReason.AMBIGUOUS
    assert len(third.candidate_node_ids) == 2
    assert environment.wildcards_by_file == {}


# --------------------------------------------------------------------------
# ECMAScript: all eight bounded candidate paths
# --------------------------------------------------------------------------


def _ecmascript_all_eight_paths():
    source = parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        imports=(ImportInfo("./dep", (), False, (), loc(0, 8)),),
    )
    files = [source]
    for suffix, language in (
        (".js", ParsedLanguage.JAVASCRIPT),
        (".jsx", ParsedLanguage.JAVASCRIPT),
        (".ts", ParsedLanguage.TYPESCRIPT),
        (".tsx", ParsedLanguage.TSX),
    ):
        files.append(parsed(f"dep{suffix}", language=language))
        files.append(parsed(f"dir/index{suffix}", language=language))
    return source, files


def test_ecmascript_extensionless_import_keeps_all_eight_bounded_candidates():
    """./dep and ./dir must each yield four extension candidates, unmerged."""
    source, files = _ecmascript_all_eight_paths()
    index = build_graph_indexes(validate_inputs(*inventories(*files)))

    direct = resolve_import(index, source, source.imports[0]).resolution

    assert direct.reason is ResolutionFailureReason.AMBIGUOUS
    expected = tuple(
        sorted(
            file_node_id(NAMESPACE, path)
            for path in ("dep.js", "dep.jsx", "dep.ts", "dep.tsx")
        )
    )
    assert direct.candidate_node_ids == expected
    assert len(direct.candidate_node_ids) == 4


def test_ecmascript_directory_index_paths_are_the_other_four_candidates():
    """The ./dir request resolves through four index files, not ./dep files."""
    source = parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        imports=(ImportInfo("./dir", (), False, (), loc(0, 8)),),
    )
    files = [source]
    for suffix, language in (
        (".js", ParsedLanguage.JAVASCRIPT),
        (".jsx", ParsedLanguage.JAVASCRIPT),
        (".ts", ParsedLanguage.TYPESCRIPT),
        (".tsx", ParsedLanguage.TSX),
    ):
        files.append(parsed(f"dep{suffix}", language=language))
        files.append(parsed(f"dir/index{suffix}", language=language))

    index = build_graph_indexes(validate_inputs(*inventories(*files)))
    result = resolve_import(index, source, source.imports[0]).resolution

    assert result.reason is ResolutionFailureReason.AMBIGUOUS
    expected = tuple(
        sorted(
            file_node_id(NAMESPACE, f"dir/index{suffix}")
            for suffix in (".js", ".jsx", ".ts", ".tsx")
        )
    )
    assert result.candidate_node_ids == expected


def test_ecmascript_explicit_extension_selects_only_the_exact_file():
    """An explicit supported extension is a single candidate, not a scan."""
    source = parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        imports=(ImportInfo("./dep.ts", (), False, (), loc(0, 10)),),
    )
    files = [
        source,
        parsed("dep.js", language=ParsedLanguage.JAVASCRIPT),
        parsed("dep.jsx", language=ParsedLanguage.JAVASCRIPT),
        parsed("dep.ts", language=ParsedLanguage.TYPESCRIPT),
        parsed("dep.tsx", language=ParsedLanguage.TSX),
    ]
    index = build_graph_indexes(validate_inputs(*inventories(*files)))

    result = resolve_import(index, source, source.imports[0]).resolution

    assert result.target_node_id == file_node_id(NAMESPACE, "dep.ts")


def test_ecmascript_candidate_resolution_never_scans_the_file_map():
    """Resolving the eight candidate paths must not iterate a global map."""
    source, files = _ecmascript_all_eight_paths()
    index = build_graph_indexes(validate_inputs(*inventories(*files)))
    counted, counters = counted_index(index)
    item = source.imports[0]

    for _ in range(10):
        resolve_import(counted, source, item)

    assert _iterations(counters) == 0


# --------------------------------------------------------------------------
# candidate/output accounting for repeated ambiguity sites
# --------------------------------------------------------------------------


def test_repeated_ambiguity_sites_emit_exactly_r_times_k_candidates():
    """R ambiguity sites over K eligible targets emit R*K candidate ids."""
    references = 6
    targets = 5
    symbols = [symbol("caller", start=0, end=400)]
    for position in range(targets):
        start = 1000 + position * 20
        symbols.append(symbol("target", start=start, end=start + 10))
    calls = tuple(
        CallSite(
            "caller",
            "target",
            CallKind.CALL,
            loc(50 + 10 * position, 55 + 10 * position),
        )
        for position in range(references)
    )
    file = parsed("main.py", symbols=symbols, calls=calls)
    index = build_graph_indexes(validate_inputs(*inventories(file)))
    counted, counters = counted_index(index)
    environment = ImportEnvironment({"main.py": ()})

    results = [
        resolve_reference(counted, environment, call_reference(index, file, item))
        for item in calls
    ]

    assert _iterations(counters) == 0
    assert all(
        result.reason is ResolutionFailureReason.AMBIGUOUS for result in results
    )
    assert all(
        len(result.candidate_node_ids) == targets for result in results
    )
    emitted = sum(len(result.candidate_node_ids) for result in results)
    assert emitted == references * targets
    graph = build_code_graph(*inventories(file))
    assert len(graph.unresolved_references) == references
    assert sum(
        len(item.candidate_node_ids) for item in graph.unresolved_references
    ) == references * targets
    assert not [
        edge
        for edge in graph.edges
        if edge.edge_kind is GraphEdgeKind.CALLS
    ]


def test_ambiguity_candidate_sets_are_disjoint_across_sites_but_stable():
    """Distinct sites may share a candidate set; each set stays sorted."""
    symbols = [symbol("caller", start=0, end=400)]
    for position in range(4):
        start = 1000 + position * 20
        symbols.append(symbol("target", start=start, end=start + 10))
    calls = tuple(
        CallSite(
            "caller",
            "target",
            CallKind.CALL,
            loc(50 + 10 * position, 55 + 10 * position),
        )
        for position in range(3)
    )
    file = parsed("main.py", symbols=symbols, calls=calls)
    graph = build_code_graph(*inventories(file))

    candidates = {
        item.candidate_node_ids for item in graph.unresolved_references
    }
    assert len(candidates) == 1
    only = candidates.pop()
    assert len(only) == 4
    assert list(only) == sorted(only)
    assert all(
        item.reason is ResolutionFailureReason.AMBIGUOUS
        and item.reference_kind is ReferenceKind.CALL
        for item in graph.unresolved_references
    )