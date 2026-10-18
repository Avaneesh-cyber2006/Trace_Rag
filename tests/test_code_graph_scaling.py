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
from backend.code_graph.models import (
    GraphEdgeKind,
    GraphNode,
    ReferenceKind,
    ResolutionFailureReason,
)
import backend.code_graph.resolvers.java as java_resolver
from backend.code_graph.identities import symbol_node_id
from backend.code_graph.references import call_reference, type_references
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
#: never iterates them. ``files_by_path`` and ``file_ids`` are the literal "file
#: maps" the approved plan names; ``parent_ids`` is the O(S) ownership map a
#: scope walk must not reverse-scan; ``java_packages``/``python_modules`` are the
#: package/wildcard indexes; ``by_file_name``/``children`` are the keyed buckets
#: that make same-file and nesting lookups O(1). The single exception is
#: ``structural_edges``, which legitimately performs one linear pass over
#: ``parent_ids``; that pass is asserted explicitly rather than forbidden.
GLOBAL_MAPS = (
    "nodes_by_id",
    "symbols_by_id",
    "by_qualified_name",
    "by_file_name",
    "parent_ids",
    "children",
    "files_by_path",
    "file_ids",
    "java_packages",
    "python_modules",
)

#: The node tuple is a sequence, not a mapping: iterating it must yield GraphNode
#: objects exactly as production does, so it gets a counting *sequence* wrapper.
GLOBAL_SEQUENCES = ("nodes",)


class CountingSequence:
    """Wraps a sequence to count iteration while yielding the original items."""

    def __init__(self, source):
        self.source = source
        self.counts = Counter()

    def __iter__(self):
        self.counts["iter"] += 1
        return iter(self.source)

    def __len__(self):
        return len(self.source)

    def __getitem__(self, item):
        self.counts["get"] += 1
        return self.source[item]


class _CountingIterator:
    """Iterator that records each item it yields from a CountingTuple."""

    def __init__(self, source):
        self._source = source
        self._iterator = tuple.__iter__(source)

    def __iter__(self):
        return self

    def __next__(self):
        item = next(self._iterator)
        self._source.counts["yield"] += 1
        return item


class CountingTuple(tuple):
    """A tuple that counts passes over itself and items yielded from it."""

    def __new__(cls, source):
        instance = super().__new__(cls, source)
        instance.counts = Counter()
        return instance

    def __iter__(self):
        self.counts["iter"] += 1
        return _CountingIterator(self)


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


def test_counting_sequence_yields_the_original_items():
    """A counting sequence must not change what iterating it produces."""
    values = ("alpha", "beta", "gamma")
    counted = CountingSequence(values)

    assert list(counted) == list(values)
    assert counted.counts["iter"] == 1
    assert counted.counts["get"] == 0
    assert len(counted) == 3
    assert counted[1] == "beta"
    assert counted.counts["get"] == 1
    assert list(counted) == list(values)
    assert counted.counts["iter"] == 2


def test_counting_sequence_wraps_graph_nodes_without_changing_them():
    """Instrumenting the node tuple must yield GraphNode objects, not ids."""
    file = parsed(
        "main.py",
        symbols=(symbol("one", start=0, end=10), symbol("two", start=20, end=30)),
    )
    index = build_graph_indexes(validate_inputs(*inventories(file)))
    counted, _counters = counted_index(index)

    nodes = list(counted.nodes)

    assert len(nodes) == 3
    assert all(isinstance(node, GraphNode) for node in nodes), nodes
    assert not any(isinstance(node, str) for node in nodes)
    assert nodes == list(index.nodes)


def test_counting_tuple_counts_passes_and_yielded_items():
    """The build-time instrument must count both passes and items yielded."""
    counted = CountingTuple(("a", "b", "c"))

    assert list(counted) == ["a", "b", "c"]
    assert counted.counts["iter"] == 1
    assert counted.counts["yield"] == 3
    assert list(counted) == ["a", "b", "c"]
    assert counted.counts["iter"] == 2
    assert counted.counts["yield"] == 6


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
    """Clone ``index`` with its global maps/sequences replaced by counters."""
    clone = copy.copy(index)
    counters = {}
    for name in GLOBAL_MAPS:
        counter = CountingMapping(getattr(index, name))
        object.__setattr__(clone, name, counter)
        counters[name] = counter
    for name in GLOBAL_SEQUENCES:
        counter = CountingSequence(getattr(index, name))
        object.__setattr__(clone, name, counter)
        counters[name] = counter
    return clone, counters


def counted_environment(build):
    """Build an ImportEnvironment whose raw import records count their reads.

    ``build`` receives the environment and populates ``by_file`` and
    ``bindings_by_scope_name`` through the public constructor path, so the
    wrapper can be installed afterwards without touching resolver code.
    """
    environment = build()
    records = CountingMapping(environment.by_file)
    object.__setattr__(environment, "by_file", records)
    return environment, records


def counted_inputs(inputs):
    """Return validated inputs whose per-file collections count their reads."""
    counted = copy.copy(inputs)
    pairs = []
    symbol_reads = Counter()
    for scanned, parsed in inputs.pairs:
        clone = copy.copy(parsed)
        symbols = CountingTuple(parsed.symbols)
        object.__setattr__(clone, "symbols", symbols)
        symbol_reads[parsed.relative_path] = symbols.counts
        pairs.append((scanned, clone))
    object.__setattr__(counted, "pairs", tuple(pairs))
    return counted, symbol_reads


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

    # The key set alone would pass on an empty tree, so each file must also have
    # a populated interval tree whose leaves resolve back to its own symbols.
    for path in sorted(index.file_ids):
        root = index.symbol_ranges_by_file[path].root
        assert root is not None, path
        located = _collect_range_ids(root)
        expected = set(index.by_file_name[(path, name)] for name, _ids in ())
        assert located == {
            next(iter(index.by_file_name[(path, f"symbol_{position}")]))
            for position in range(4)
        }, path


def _collect_range_ids(root):
    """Every node id reachable in a symbol range interval tree."""
    if root is None:
        return set()
    found = {root.node_id}
    for child in (root.left, root.right):
        if child is not None:
            found |= _collect_range_ids(child)
    return found


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
    assert _lookups(counters) > 0
    assert _lookups(counters) <= 15 * len(calls)


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

    assert _lookups(counters) > 0

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
    assert _lookups(counters) > 0
    # Zero iterations is the decisive assertion here: any whole-map scan must
    # iterate one. The lookup bound only caps work per emitted candidate, since
    # enumerating 300 candidates per site legitimately costs more than a scan of
    # this deliberately tiny 301-symbol repository would.
    assert _lookups(counters) <= 10 * len(calls) * 300
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
    environment, records = counted_environment(
        lambda: ImportEnvironment(
            {"main.py": ((item, resolve_import(counted, source, item)),)}
        )
    )

    for item_call in calls:
        result = resolve_reference(
            counted, environment, call_reference(index, source, item_call)
        )
        assert result.target_node_id is not None

    assert _lookups(counters) > 0
    assert _iterations(counters) == 0
    # The raw import records are never touched at all once bindings are indexed:
    # resolution reaches every binding through bindings_by_scope_name.
    assert records.counts["iter"] == 0
    assert records.counts["get"] == 0
    assert _lookups(counters) <= 15 * len(calls)
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
    # The single permitted linear pass is over the precomputed parent map; the
    # node tuple and every other repository-sized map must stay untouched.
    assert counters["parent_ids"].counts["iter"] == 1
    assert _iterations({k: v for k, v in counters.items() if k != "parent_ids"}) == 0


def test_structural_edge_pass_count_is_independent_of_symbol_count():
    """Doubling the symbol count must not add structural-edge passes."""
    passes = {}
    for count in (4, 16, 64):
        file = parsed(
            "flat.py",
            symbols=tuple(
                symbol(f"function_{index}", start=index * 20, end=index * 20 + 10)
                for index in range(count)
            ),
        )
        index = build_graph_indexes(validate_inputs(*inventories(file)))
        counted, counters = counted_index(index)

        structural_edges(counted)

        passes[count] = counters["parent_ids"].counts["iter"]

    assert passes[4] == passes[16] == passes[64] == 1


def test_index_construction_visits_each_file_and_symbol_a_fixed_number_of_times():
    """Build-time reads are a fixed number of linear passes over files and symbols."""
    by_files = {}
    for file_count in (4, 16, 64):
        files = tuple(
            parsed(
                f"file_{index}.py",
                symbols=tuple(
                    symbol(
                        f"symbol_{position}",
                        start=position * 20,
                        end=position * 20 + 10,
                    )
                    for position in range(4)
                ),
            )
            for index in range(file_count)
        )
        counted, symbol_reads = counted_inputs(validate_inputs(*inventories(*files)))

        build_graph_indexes(counted)

        per_file = {
            path: sum(counter.values()) for path, counter in symbol_reads.items()
        }
        by_files[file_count] = (sorted(set(per_file.values())), sum(per_file.values()))

    passes = {count: value[0] for count, value in by_files.items()}
    assert passes[4] == passes[16] == passes[64]
    assert len(passes[4]) == 1
    assert by_files[16][1] == 4 * by_files[4][1]
    assert by_files[64][1] == 16 * by_files[4][1]

    by_symbols = {}
    for symbol_count in (1, 4, 16, 64):
        file = parsed(
            "one.py",
            symbols=tuple(
                symbol(
                    f"symbol_{position}",
                    start=position * 20,
                    end=position * 20 + 10,
                )
                for position in range(symbol_count)
            ),
        )
        counted, symbol_reads = counted_inputs(validate_inputs(*inventories(file)))

        index = build_graph_indexes(counted)

        only = next(iter(symbol_reads.values()))
        by_symbols[symbol_count] = (only["yield"], len(index.nodes))

    # Every symbol must become exactly one SYMBOL node, so node count is the
    # ground truth the yielded-symbol count has to track. A per-file quadratic
    # rescan would yield S*S items while the node count stays S+1.
    for count, (yielded, node_count) in by_symbols.items():
        assert yielded == count, (count, yielded)
        assert node_count == count + 1, (count, node_count)
    assert by_symbols[16][0] == 16 * by_symbols[1][0]
    assert by_symbols[64][0] == 64 * by_symbols[1][0]


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

    assert _lookups(counters) > 0

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
        assert _iterations(counters) == 0
        # A floor per depth means an entirely uninstrumented index fails here
        # instead of trivially satisfying the doubling bounds below.
        assert costs[depth] > 0

    assert costs[4] > 0
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
    # A non-Java file deliberately carries the same package spelling as the Java
    # types. Its name and qualified name differ only by that prefix, so every
    # package-index condition except the language predicate is satisfied; only
    # that predicate keeps it out, and it can never become a candidate.
    files.append(
        parsed(
            "wild_part.py",
            symbols=(
                symbol(
                    "Python",
                    kind=SymbolKind.CLASS,
                    qualified_name="wild.Python",
                    start=0,
                    end=10,
                ),
            ),
        )
    )
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


def test_java_package_index_ignores_non_java_files_in_the_same_package():
    """Only Java sources may populate the Java package index.

    The Python file carries a ``wild.``-prefixed qualified name and the right
    symbol kind, so the language predicate is the only thing keeping it out of
    the package index. Both the membership assertion and the Java wildcard
    candidate set depend on that predicate holding.
    """
    source, files = _java_wildcard_case(matching=2, unrelated=0)
    index = build_graph_indexes(validate_inputs(*inventories(*files)))

    python_file = file_node_id(NAMESPACE, "wild_part.py")
    assert index.java_packages["wild"] == tuple(
        sorted(file_node_id(NAMESPACE, f"Part{position}.java") for position in range(2))
    )
    assert python_file not in index.java_packages["wild"]

    result = resolve_import(index, source, source.imports[0]).resolution

    assert result.reason is ResolutionFailureReason.AMBIGUOUS
    assert result.candidate_node_ids == index.java_packages["wild"]
    assert python_file not in result.candidate_node_ids


def test_java_package_index_only_accepts_type_declarations():
    """A non-type Java symbol must not register a package entry."""
    package_symbol = symbol(
        "Helper",
        kind=SymbolKind.FUNCTION,
        qualified_name="wild.Helper",
        start=0,
        end=10,
    )
    source = parsed(
        "Use.java",
        language=ParsedLanguage.JAVA,
        imports=(ImportInfo("wild", (), True, (), loc(0, 12)),),
        symbols=(package_symbol,),
    )
    index = build_graph_indexes(validate_inputs(*inventories(source)))

    assert "wild" not in index.java_packages
    assert resolve_import(
        index, source, source.imports[0]
    ).resolution.reason is ResolutionFailureReason.EXTERNAL


@pytest.mark.parametrize("matching", [2, 4, 8])
def test_java_wildcard_candidates_are_exactly_the_matching_package_files(matching):
    """Wildcard candidate set is exactly the matching package files."""
    source, files = _java_wildcard_case(matching, unrelated=200)
    index = build_graph_indexes(validate_inputs(*inventories(*files)))

    result = resolve_import(index, source, source.imports[0]).resolution

    assert result.reason is ResolutionFailureReason.AMBIGUOUS
    expected = tuple(
        sorted(
            file_node_id(NAMESPACE, f"Part{position}.java")
            for position in range(matching)
        )
    )
    assert result.candidate_node_ids == expected


def test_java_wildcard_resolution_cost_is_independent_of_unrelated_files():
    """Adding unrelated files must not add per-reference work."""
    measurements = {}
    for unrelated in (0, 200, 800):
        source, files = _java_wildcard_case(matching=4, unrelated=unrelated)
        index = build_graph_indexes(validate_inputs(*inventories(*files)))
        counted, counters = counted_index(index)
        item = source.imports[0]

        for _ in range(5):
            resolve_import(counted, source, item)

        assert _iterations(counters) == 0, unrelated
        measurements[unrelated] = _lookups(counters)

    # The measurement must be provably non-zero, otherwise the equality below
    # would be the vacuous 0 == 0 == 0.
    assert measurements[0] > 0
    assert _lookups(counters) > 0

    assert measurements[0] == measurements[200] == measurements[800]


def test_java_wildcard_member_lookup_memo_is_per_build_not_a_global_cache():
    """The wildcard member memo must not survive across GraphIndexes instances."""
    item = ImportInfo("p", (), True, (), loc(0, 10))
    child = symbol(
        "Child",
        kind=SymbolKind.CLASS,
        qualified_name="q.Child",
        base_types=("Base",),
        start=100,
        end=400,
    )
    source = parsed(
        "Main.java",
        language=ParsedLanguage.JAVA,
        imports=(item,),
        symbols=(child,),
    )
    first_base = symbol("Base", kind=SymbolKind.CLASS, qualified_name="p.Base")
    first_target = parsed(
        "first.java",
        language=ParsedLanguage.JAVA,
        symbols=(first_base,),
    )
    first_index = build_graph_indexes(validate_inputs(*inventories(source, first_target)))
    environment = ImportEnvironment(
        {"Main.java": ((item, resolve_import(first_index, source, item)),)}
    )
    source_id = first_index.by_file_name[("Main.java", "Child")][0]
    (reference,) = type_references(first_index, source, source_id)

    first = resolve_reference(first_index, environment, reference)

    second_base = symbol("Base", kind=SymbolKind.CLASS, qualified_name="p.Base")
    second_target = parsed(
        "second.java",
        language=ParsedLanguage.JAVA,
        symbols=(second_base,),
    )
    second_index = build_graph_indexes(
        validate_inputs(*inventories(source, second_target))
    )
    second = resolve_reference(second_index, environment, reference)

    assert first.target_node_id == symbol_node_id(NAMESPACE, "first.java", first_base)
    assert second.target_node_id == symbol_node_id(NAMESPACE, "second.java", second_base)


def test_java_reference_resolution_never_rescans_import_records(monkeypatch):
    """Java must reach bindings through bindings_by_scope_name, not by_file."""
    item = ImportInfo(
        "p.Base",
        (ImportBinding("Base", "Base"),),
        False,
        (),
        loc(0, 10),
    )
    child = symbol(
        "Child",
        kind=SymbolKind.CLASS,
        qualified_name="p.Child",
        base_types=("Base",),
        start=100,
        end=400,
    )
    source = parsed(
        "Child.java", language=ParsedLanguage.JAVA, imports=(item,), symbols=(child,)
    )
    base = symbol("Base", kind=SymbolKind.CLASS, qualified_name="p.Base")
    target = parsed("Base.java", language=ParsedLanguage.JAVA, symbols=(base,))
    index = build_graph_indexes(validate_inputs(*inventories(source, target)))
    counted, counters = counted_index(index)
    environment, records = counted_environment(
        lambda: ImportEnvironment(
            {"Child.java": ((item, resolve_import(counted, source, item)),)}
        )
    )
    source_id = counted.by_file_name[("Child.java", "Child")][0]
    (reference,) = type_references(counted, source, source_id)

    result = resolve_reference(counted, environment, reference)

    assert result.target_node_id is not None
    assert records.counts["iter"] == 0
    assert records.counts["get"] == 0
    assert _iterations(counters) == 0
    assert _lookups(counters) > 0
    assert _lookups(counters) <= 40


def test_ecmascript_reference_resolution_never_rescans_import_records():
    """ECMAScript must reach bindings through bindings_by_scope_name too."""
    item = ImportInfo(
        "./util.js",
        (ImportBinding("run", "execute"),),
        False,
        (),
        loc(0, 10),
    )
    caller = symbol("main", start=20, end=100)
    source = parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        symbols=(caller,),
        imports=(item,),
        calls=(CallSite("main", "execute", CallKind.CALL, loc(50, 60)),),
    )
    exported = symbol("run", modifiers=("export",), start=0, end=40)
    target = parsed("util.js", language=ParsedLanguage.JAVASCRIPT, symbols=(exported,))
    index = build_graph_indexes(validate_inputs(*inventories(source, target)))
    counted, counters = counted_index(index)
    environment, records = counted_environment(
        lambda: ImportEnvironment(
            {"main.js": ((item, resolve_import(counted, source, item)),)}
        )
    )

    result = resolve_reference(
        counted, environment, call_reference(index, source, source.calls[0])
    )

    assert result.target_node_id == symbol_node_id(NAMESPACE, "util.js", exported)
    assert records.counts["iter"] == 0
    assert records.counts["get"] == 0
    assert _iterations(counters) == 0
    assert _lookups(counters) > 0
    assert _lookups(counters) <= 40
    assert list(environment.bindings_by_scope_name) == [
        (file_node_id(NAMESPACE, "main.js"), "execute")
    ]


def test_type_name_memo_does_not_leak_across_graph_indexes():
    """A type-name memo entry must never answer a different build.

    Both builds share the same package, paths, file ids and wildcard item, and a
    single ImportEnvironment is reused across them. Only the presence of the
    ``p.Base`` declaration differs. Without build scoping, the second build would
    be answered from the first build's cached candidates.
    """
    base_symbol = symbol(
        "Base", kind=SymbolKind.CLASS, qualified_name="p.Base", end=300
    )

    def build(with_base):
        item = ImportInfo("p", (), True, (), loc(0, 10))
        child = symbol(
            "Child",
            kind=SymbolKind.CLASS,
            qualified_name="q.Child",
            base_types=("Base",),
            start=100,
            end=400,
        )
        source = parsed(
            "Main.java",
            language=ParsedLanguage.JAVA,
            imports=(item,),
            symbols=(child,),
        )
        target = parsed(
            "base.java",
            language=ParsedLanguage.JAVA,
            symbols=(base_symbol,) if with_base else (),
        )
        return source, item, target

    def indexes(with_base):
        source, item, target = build(with_base)
        index = build_graph_indexes(validate_inputs(*inventories(source, target)))
        return index, source, item

    first_index, first_source, first_item = indexes(True)
    second_index, _second_source, _second_item = indexes(False)

    assert first_index.file_ids == second_index.file_ids

    # One environment reused across both builds is the leak scenario.
    environment = ImportEnvironment(
        {
            "Main.java": (
                (first_item, resolve_import(first_index, first_source, first_item)),
            )
        }
    )
    first_ref = type_references(
        first_index, first_source, first_index.by_file_name[("Main.java", "Child")][0]
    )[0]
    second_ref = type_references(
        second_index, first_source, second_index.by_file_name[("Main.java", "Child")][0]
    )[0]

    original = java_resolver._wildcard_type_candidates
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    java_resolver._wildcard_type_candidates = counted
    try:
        first = resolve_reference(first_index, environment, first_ref)
        second = resolve_reference(second_index, environment, second_ref)
    finally:
        java_resolver._wildcard_type_candidates = original

    assert len(calls) == 2
    assert first.target_node_id == symbol_node_id(NAMESPACE, "base.java", base_symbol)
    # The second repository has no p.Base declaration, so the wildcard package
    # resolves to nothing there. What matters is that it is unresolved: a
    # leaked entry would hand back the first build's node id.
    assert second.target_node_id is None
    assert second.reason in (
        ResolutionFailureReason.NOT_FOUND,
        ResolutionFailureReason.EXTERNAL,
    )
    assert second.candidate_node_ids == ()


def test_static_wildcard_memo_does_not_leak_across_graph_indexes():
    """The static-call wildcard memo must also be private to its build.

    ``_wildcard_static_call_resolution`` constructs a second memo key
    independently of the type-name path. Two structurally identical
    repositories must each recompute, otherwise the second build would be
    answered from the first build's entry.
    """
    static = ImportInfo("p.Tools", (), True, ("static",), loc(0, 14))
    owner = symbol(
        "Main", kind=SymbolKind.CLASS, qualified_name="q.Main",
        start=100, end=400,
    )
    caller = symbol(
        "run",
        kind=SymbolKind.METHOD,
        qualified_name="q.Main.run",
        parent="q.Main",
        start=120,
        end=200,
    )
    call = CallSite("q.Main.run", "work", CallKind.CALL, loc(130, 140))
    source = parsed(
        "Main.java",
        language=ParsedLanguage.JAVA,
        imports=(static,),
        symbols=(owner, caller),
        calls=(call,),
    )
    tools = symbol(
        "Tools", kind=SymbolKind.CLASS, qualified_name="p.Tools", end=300
    )
    worker = symbol(
        "work",
        kind=SymbolKind.METHOD,
        qualified_name="p.Tools.work",
        parent="p.Tools",
        modifiers=("static",),
        start=20,
        end=80,
    )

    def index_with(with_worker):
        target = parsed(
            "Tools.java",
            language=ParsedLanguage.JAVA,
            symbols=(tools, worker) if with_worker else (tools,),
        )
        return build_graph_indexes(validate_inputs(*inventories(source, target)))

    first_index = index_with(True)
    second_index = index_with(False)

    # Identical package, paths, file ids and wildcard items across both builds.
    assert first_index.file_ids == second_index.file_ids

    # One environment reused across both builds is the leak scenario: a memo
    # entry created for the first build would otherwise answer the second.
    environment = ImportEnvironment(
        {"Main.java": ((static, resolve_import(first_index, source, static)),)}
    )
    first = resolve_reference(
        first_index, environment, call_reference(first_index, source, call)
    )
    second = resolve_reference(
        second_index, environment, call_reference(second_index, source, call)
    )

    assert first.target_node_id == symbol_node_id(NAMESPACE, "Tools.java", worker)
    # The second repository genuinely has no such member, so it must stay
    # unresolved rather than being answered from the first build's entry.
    assert second.target_node_id is None
    assert second.reason is ResolutionFailureReason.NOT_FOUND
    assert second.candidate_node_ids == ()


def test_wildcard_memo_does_not_share_entries_across_environments():
    """A memo held by one environment must never serve another build."""
    def build():
        item = ImportInfo("p", (), True, (), loc(0, 10))
        child = symbol(
            "Child",
            kind=SymbolKind.CLASS,
            qualified_name="q.Child",
            base_types=("Base",),
            start=100,
            end=400,
        )
        source = parsed(
            "Main.java",
            language=ParsedLanguage.JAVA,
            imports=(item,),
            symbols=(child,),
        )
        base = symbol("Base", kind=SymbolKind.CLASS, qualified_name="p.Base")
        target = parsed(
            "base.java", language=ParsedLanguage.JAVA, symbols=(base,)
        )
        index = build_graph_indexes(validate_inputs(*inventories(source, target)))
        environment = ImportEnvironment(
            {"Main.java": ((item, resolve_import(index, source, item)),)}
        )
        source_id = index.by_file_name[("Main.java", "Child")][0]
        (reference,) = type_references(index, source, source_id)
        return index, environment, reference

    first_index, first_env, first_ref = build()
    second_index, second_env, second_ref = build()

    # Resolving through the first environment, then through a separately
    # constructed one, must reach the candidate factory again. A module-level or
    # otherwise shared memo would answer the second build from the first entry.
    first = resolve_reference(first_index, first_env, first_ref)
    second = resolve_reference(second_index, second_env, second_ref)

    assert first.target_node_id is not None
    assert second.target_node_id is not None
    assert first_env._wildcard_memo is not second_env._wildcard_memo
    assert first_env._wildcard_memo._values != {}


def test_java_wildcard_member_lookup_is_computed_once_per_environment(monkeypatch):
    """Repeated identical wildcard member lookups must be memoized."""
    item = ImportInfo("p", (), True, (), loc(0, 10))
    child = symbol(
        "Child",
        kind=SymbolKind.CLASS,
        qualified_name="q.Child",
        base_types=("Base",),
        start=100,
        end=400,
    )
    source = parsed(
        "Main.java",
        language=ParsedLanguage.JAVA,
        imports=(item,),
        symbols=(child,),
    )
    base = symbol("Base", kind=SymbolKind.CLASS, qualified_name="p.Base")
    target = parsed("base.java", language=ParsedLanguage.JAVA, symbols=(base,))
    index = build_graph_indexes(validate_inputs(*inventories(source, target)))
    environment = ImportEnvironment(
        {"Main.java": ((item, resolve_import(index, source, item)),)}
    )
    source_id = index.by_file_name[("Main.java", "Child")][0]
    (reference,) = type_references(index, source, source_id)
    original = java_resolver._wildcard_type_candidates
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(java_resolver, "_wildcard_type_candidates", counted)

    assert resolve_reference(index, environment, reference).target_node_id is not None
    assert resolve_reference(index, environment, reference).target_node_id is not None
    assert len(calls) == 1


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


def test_ecmascript_extensionless_import_keeps_four_direct_file_candidates():
    """./dep must yield the four supported extensions, unmerged and unranked."""
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


def test_ecmascript_all_eight_candidate_paths_are_bounded():
    """The approved plan's eight paths: four direct files plus four index files."""
    direct_source = parsed(
        "main.js",
        language=ParsedLanguage.JAVASCRIPT,
        imports=(ImportInfo("./dep", (), False, (), loc(0, 8)),),
    )
    index_source = parsed(
        "second.js",
        language=ParsedLanguage.JAVASCRIPT,
        imports=(ImportInfo("./dir", (), False, (), loc(0, 8)),),
    )
    files = []
    for suffix, language in (
        (".js", ParsedLanguage.JAVASCRIPT),
        (".jsx", ParsedLanguage.JAVASCRIPT),
        (".ts", ParsedLanguage.TYPESCRIPT),
        (".tsx", ParsedLanguage.TSX),
    ):
        files.append(parsed(f"dep{suffix}", language=language))
        files.append(parsed(f"dir/index{suffix}", language=language))
    index = build_graph_indexes(
        validate_inputs(*inventories(direct_source, index_source, *files))
    )

    direct = resolve_import(index, direct_source, direct_source.imports[0]).resolution
    through_index = resolve_import(
        index, index_source, index_source.imports[0]
    ).resolution

    assert len(direct.candidate_node_ids) + len(through_index.candidate_node_ids) == 8
    assert set(direct.candidate_node_ids).isdisjoint(through_index.candidate_node_ids)


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
    """Resolving the eight candidate paths must not iterate the file maps."""
    source, files = _ecmascript_all_eight_paths()
    index = build_graph_indexes(validate_inputs(*inventories(*files)))
    counted, counters = counted_index(index)
    item = source.imports[0]

    for _ in range(10):
        resolve_import(counted, source, item)

    assert counters["files_by_path"].counts["iter"] == 0
    assert counters["file_ids"].counts["iter"] == 0
    assert counters["nodes"].counts["iter"] == 0
    assert _iterations(counters) == 0
    # The probe must actually touch the file maps by key, so the assertions
    # above are not vacuously satisfied by an uninstrumented path.
    assert counters["file_ids"].counts["get"] > 0
    assert counters["file_ids"].counts["get"] <= 16 * 10


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
    assert _lookups(counters) > 0
    # Each of the R sites emits K candidate ids, so the emitted work is bounded
    # by R*K rather than by any repository-wide symbol count.
    assert _lookups(counters) <= 10 * references * targets
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


def test_ambiguity_candidate_sets_are_identical_across_sites_and_sorted():
    """Distinct sites share one candidate set; each set stays sorted and unique."""
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
