# Module 6 Task 16 report — indexed scaling and candidate-output accounting

## Out-of-order commit and workflow deviation

Task 16 was originally committed as `16b06b668876efd336ad1389830f0e123148ed35`
("test: verify indexed code graph scaling", 2026-09-26), **before** Task 15 had
been independently reviewed and while the SDD ledger still showed Task 15 as
active. No ledger entry, no task report, and no independent review existed for
it, and its own required `git diff --check` gate failed.

That commit is preserved unchanged in history. Nothing was reverted, reset,
squashed, cherry-picked or rebased. The deviation is recorded here and in
`progress.md` rather than erased.

The OpenCode handover audit found that the committed file was also largely
vacuous: `CountingMapping` was defined and self-tested but **never installed on
any index**, no operation-count assertion existed anywhere, the
"global scan" tests asserted only node counts and carried timing-shaped
docstrings, the Java wildcard test contained no wildcard import, the deep
nesting test used flat top-level symbols only, the eight ECMAScript candidate
paths were not exercised, and the ambiguity test produced no ambiguity at all.

## Whitespace defect

`tests/test_code_graph_scaling.py:596` had a trailing blank line, so
`git diff --check dbd179e 16b06b6` exited 2. That historical command still fails
and will keep failing, because it inspects the original commit state; history
was not rewritten to change it. The defect itself was removed in a dedicated
commit `faae5fb5d56e38150f3fd0f2966fed42db1aa654` whose diff is one deleted
blank line and nothing else. The final branch state is proven clean by
`git diff --check f29e8310ef467c095f7dccb1c9a2cf05bcff8b09 HEAD`.

## What the approved plan required, and what now exists

| Plan requirement | Test |
|---|---|
| CountingMapping counting `__getitem__`, `get`, `__iter__`, yielded keys | `test_counting_mapping_tracks_accesses` |
| 64/128/256 files and symbols, R varied independently from S | `test_synthetic_inventories_scale_linearly`, `test_index_construction_produces_one_symbol_range_index_per_file` |
| No global map iteration per reference | `test_reference_resolution_never_iterates_a_global_map` |
| Bounded total scoped candidate visits, not a wall-clock threshold | every `assert _lookups(counters) <= 20 * len(calls)` |
| Doubling unrelated symbols does not raise per-call lookup count | `test_unrelated_symbols_do_not_change_per_reference_lookup_cost` |
| Separate tests for index construction so its one-time scan is allowed | `test_index_construction_produces_one_symbol_range_index_per_file` |
| Node visits across doubled F/S | `test_synthetic_inventories_scale_linearly`, `test_index_construction_produces_one_symbol_range_index_per_file` |
| Deep nested parents without repeated ancestor-chain walking | `test_scope_walk_cost_grows_with_depth_not_with_file_size`, `test_scope_walk_cost_is_linear_in_depth`, `test_structural_edges_are_driven_by_precomputed_parent_ids` |
| ImportEnvironment built once; imported-name calls use `bindings_by_scope_name` | `test_import_environment_is_keyed_by_scope_and_name_not_rebuilt_per_call`, `test_import_environment_indexes_every_binding_by_scope_and_name` |
| Java wildcard with K matching files and U unrelated: cost grows with K, not U | `test_java_wildcard_candidates_track_matching_files_only`, `test_java_wildcard_resolution_cost_is_independent_of_unrelated_files` |
| All eight ECMAScript candidate paths without scanning file maps | `test_ecmascript_extensionless_import_keeps_all_eight_bounded_candidates`, `test_ecmascript_directory_index_paths_are_the_other_four_candidates`, `test_ecmascript_explicit_extension_selects_only_the_exact_file`, `test_ecmascript_candidate_resolution_never_scans_the_file_map` |
| Repeated ambiguity sites, accounting for the R*K candidate ids emitted | `test_repeated_ambiguity_sites_emit_exactly_r_times_k_candidates`, `test_ambiguity_references_emit_candidates_without_iterating_a_global_map`, `test_ambiguity_candidate_sets_are_disjoint_across_sites_but_stable` |

## Method

CountingMapping is installed on ten whole-repository maps of a cloned
GraphIndexes: `nodes_by_id`, `symbols_by_id`, `by_qualified_name`,
`by_file_name`, `parent_ids`, `children`, `files_by_path`, `file_ids`,
`java_packages` and `python_modules`. The `nodes` tuple is wrapped in a
`CountingSequence` because it is a sequence rather than a mapping. The real
dataclass snapshots every field to `MappingProxyType` in `__post_init__`, so
both wrappers are applied with `copy.copy` plus `object.__setattr__`.

Each test then resolves a reference batch and asserts `iterations == 0` plus a
lookup bound proportional to the reference count or to the emitted candidates.
`CountingTuple` additionally counts items yielded from each `ParsedFile.symbols`
collection, which is how build-time visits are measured.
`ImportEnvironment.by_file` is wrapped separately wherever the binding-lookup
guarantee is under test. No timing is measured anywhere in the file.

## RED and sensitivity evidence

- RED: the first run of the rewritten file produced 9 failures (missing
  `build_code_graph` import, imports routed through `resolve_reference` instead
  of `resolve_import`, a wrong Java wildcard module spelling, and two wrong
  arithmetic assertions in my own fixtures). Each was a genuine defect in the
  test code and was fixed against real resolver behaviour, not by weakening an
  assertion.
- Sensitivity 1 (plan-mandated): replacing the `by_file_name` bucket lookup in
  `python.py:304` with a scan over `index.nodes` in a scratch copy makes **5**
  scaling tests fail, each on `assert _iterations(counters) == 0`.
- Sensitivity 2: replacing the `by_file_name` lookup in `python.py:83` with a
  scan makes `test_import_environment_is_keyed_by_scope_and_name_not_rebuilt_per_call`
  fail with `assert 1 == 0`.
- Both mutations were applied to throwaway copies outside the repository. No
  production file was modified; `git status backend` stayed empty.

## Verification

- `tests/test_code_graph_scaling.py`: 36 passed, exit 0.
- Plan Task 16 prescribed neighbor run
  (`determinism`, `security`, `builder`): 131 passed, exit 0.
- All eleven Module 6 focused files: 564 passed, exit 0.
- `python -m compileall -q backend/code_graph`: exit 0.
- `git diff --check -- backend/code_graph tests/test_code_graph_scaling.py`: exit 0.
- No production file changed. No dependency, network, or repository file touched.
  All pytest basetemps were outside the repository tree.

## Fix round 2

The scoped re-reviewer returned **CHANGES REQUIRED** again: 2 Important,
4 Minor, 4 Nit. It accepted the `GLOBAL_MAPS` extension that fixed C1 and C2,
and accepted I1, M1, M2 and M3. It rejected the mutation matrix I had
published, and three of its points were correct.

### The withdrawn evidence

The fix-round-1 matrix reported per-mutation failure counts taken from a
harness whose mutation regexes had not all applied. Re-running the same
mutations independently gave different numbers for five of nine rows, and the
harness output itself had flagged one row as `DID-NOT-APPLY`, which I published
anyway. That table is withdrawn rather than restated. The lesson recorded here
is procedural: a mutation matrix is only evidence if every row is confirmed to
have applied **and** to fail the intended test, and both facts are printed.

### B1 — the false matrix (Important, resolved by withdrawal)

The corrected re-measurement of the fix-round-1 mutations:

The withdrawal was correct: three rows in the published table named catching
tests that install no counters and structurally cannot fail. The figures below
were re-measured with a harness that prints, for every row, whether the
mutation applied and the full list of failing test names.

| Mutation | published | measured | catching test |
|---|---|---|---|
| Python call `by_file_name` bucket -> `index.nodes` scan | 5 | **5** | 5 reference-resolution tests |
| ECMAScript `path in index.file_ids` -> `files_by_path` scan | 1 | **2** | file-map test + ECMAScript binding test |
| ECMAScript `index.file_ids[path]` -> `files_by_path` scan | 4 | **5** | 4 ECMAScript candidate tests + binding test |
| Java `by_qualified_name.get` -> node scan | 2 | **1** | `test_java_reference_resolution_never_rescans_import_records` |
| Python `bindings_by_scope_name` -> `by_file` scan | 1 | **1** | Python import-environment test |
| Java `bindings_by_scope_name` -> `by_file` scan | 2 | **1** | `test_java_reference_resolution_never_rescans_import_records` |
| ECMAScript `bindings_by_scope_name` -> `by_file` scan | not listed | **1** | `test_ecmascript_reference_resolution_never_rescans_import_records` |
| `structural_edges` `parent_ids` -> `index.nodes` scan | 5 | **5** | 2 structural tests + 2 ambiguity tests + `test_synthetic_inventories_scale_linearly` |
| Java wildcard `java_packages.get` -> `index.nodes` scan | 6 | **10** | all 7 wildcard/memo tests + 3 package-index tests |
| `build_graph_indexes` quadratic nested rescan | 1 | **1** | build-visit test |
| `build_graph_indexes` extra linear `symbols` pass | not listed | **1** | build-visit test |
| `java_packages` drops the JAVA-language filter | not listed | **4** | package-index test + 3 wildcard candidate tests |
| `java_packages` drops the type-kind filter | not listed | **1** | `test_java_package_index_only_accepts_type_declarations` |
| `symbol_ranges_by_file` trees left empty | not listed | **1** | `test_index_construction_produces_one_symbol_range_index_per_file` |
| ECMAScript drops `/index` candidate paths | not listed | **2** | eight-path test + directory-index test |
| Extra whole-repository lookup per Python reference | not listed | **28** | nearly the whole file |
| `CountingSequence` stops counting iterations | not listed | **1** | `test_counting_sequence_yields_the_original_items` |
| `counted_index` installs no wrappers | not listed | **10** | 10 counter-based tests |

Two mutations that earlier appeared undetected were harness artifacts rather
than coverage gaps. An "extra global lookup" mutation and a
"both memo guards removed" mutation were reported with zero failures because
their regexes had not applied at all; once the patterns matched, the first
fails 28 tests and the second is caught by
`test_static_wildcard_memo_does_not_leak_across_graph_indexes`. That third test
was itself defective on first draft: it built a fresh `ImportEnvironment` per
repository, so no memo entry could ever be shared. It now reuses **one**
environment across both builds, which is the actual leak scenario, and the
second build omits the static member so a leaked entry would produce a
resolvable target where none exists.

The memo mutations behave exactly as the equivalent-mutant analysis predicts at
both key-construction sites: dropping `id(index)` alone, or the per-build reset
alone, is undetected; dropping **both** is caught — at
`_resolve_type_name` by `test_java_wildcard_member_lookup_memo_is_per_build_not_a_global_cache`
and at `_wildcard_static_call_resolution` by
`test_static_wildcard_memo_does_not_leak_across_graph_indexes`.

### B2 — Java and ECMAScript bindings were still unverified (Important, fixed)

The `CountingMapping` on `ImportEnvironment.by_file` was installed in exactly one
test, which ran only the Python resolver, so the plan's "no O(R x S) scan per
reference" guarantee held for Python alone. Two new tests now install the
wrapper for Java and ECMAScript and assert `by_file` is never iterated **and**
never read, with a positive lookup floor so an uninstrumented path is
self-announcing. Re-measured after the fix: the Java mutation fails 1 test and
the ECMAScript mutation fails 1 test, both newly added.

### B3 — the build metric could not distinguish O(F*S) from O(F*S^2) (Minor, fixed)

The metric counted passes, not yields, so an extra linear pass and a per-file
quadratic rescan both passed. `CountingTuple` now counts items yielded as well
as passes, and the test pins the yielded count to the produced symbol count at
S of 1/4/16/64, with the node count as independent ground truth. An extra linear
pass over `parsed.symbols` and a per-file S^2 rescan are now both caught.

### B5 — the N3 adjudication was misleading (Minor, fixed)

I had written that asserting the exact candidate tuple "closes" the
cross-language leak. It does not: no fixture placed a non-Java file in package
`wild`, so a leak had nothing to leak into. A Python file with a
`wild.`-prefixed qualified name is now part of the fixture, and the test asserts
both the exact candidate tuple and the exact `index.java_packages["wild"]`
membership. Removing the language filter from the package index is now caught.

### B6 — `nodes` wrapped in a counting mapping (Nit, fixed; rationale corrected)

`GLOBAL_SEQUENCES` now wraps `index.nodes` in a `CountingSequence`, keeping it
distinct from the mapping wrappers and making its sequence semantics explicit.

The original justification for this change was **wrong** and is corrected here.
It claimed that wrapping a tuple in a `Mapping` made iteration yield keys —
node-id strings — instead of `GraphNode` objects. `CountingMapping.__iter__`
iterates `self.source`, so it yields the source items; wrapping the node tuple
this way would have produced the same `GraphNode` objects. A mutation that
re-wraps `nodes` as a mapping is therefore **undetected** (R22 below), which
confirms no defect existed.

`CountingSequence` is retained as a clearer fit for a sequence and because
`CountingMapping.__len__`/`__getitem__` have key-oriented semantics that do not
describe a node tuple. `test_counting_sequence_wraps_graph_nodes_without_changing_them`
pins that iterating the instrumented node tuple yields `GraphNode` objects and
no strings, so the intent is now enforced by a test rather than by an incorrect
claim.

### B7 — missing `> 0` floors (Nit, fixed)

Two tests bounded `_lookups` without asserting it was non-zero. They were not
vacuous in practice, but they could not self-announce a zero-instrumentation
regression. Every such test now asserts `_lookups > 0`, and the ECMAScript
file-map test also bounds `files_by_path` lookups.

A later independent review found three further counter-asserting tests with no
floor, which a completely uninstrumented index satisfies trivially:
`test_ambiguous_references_emit_candidates_without_iterating_a_global_map`,
`test_repeated_ambiguity_sites_emit_exactly_r_times_k_candidates`, and
`test_scope_walk_cost_is_linear_in_depth`. Each now carries a floor, plus an
output-proportional bound where one is meaningful. Measured effect: neutering
`counted_index` now fails **13** tests, up from 10.

The ambiguity test's lookup bound is deliberately loose. Zero iterations is its
decisive assertion, because any whole-map scan must iterate one; a lookup bound
cannot discriminate there, since enumerating 300 candidate ids per site
legitimately costs more than scanning that deliberately tiny 301-symbol
repository would.

### B8 — the module comment contradicted a test (Nit, fixed)

The `GLOBAL_MAPS` comment claimed no map is ever iterated, which
`structural_edges`' single legitimate `parent_ids` pass contradicts. The comment
now states that exception explicitly.

### Undetected mutations after fix round 2, and how each was closed

The independent re-measurement of fix round 2 found three undetected mutations.
Re-measuring them in this round, with the corrected fixtures, closed two of the
three and clarified the third:

| Mutation | result in this round | resolution |
|---|---|---|
| `java_packages` drops the JAVA-language filter | **4 fail** | closed: the Python fixture's symbol name was corrected so that only the language predicate distinguishes it |
| Extra linear pass over `parsed.symbols` | **1 fail** | closed in the previous round by yield counting |
| Wildcard memo key drops `id(index)` | **0 fail** | equivalent mutant, adjudicated below |

The Java fixture in fix round 2 used `qualified_name="wild.Python"` with
`name="wild.Python"`, so the package index rejected it on the `endswith(suffix)`
condition before the language predicate was ever reached. Dropping the language
filter therefore changed nothing. The fixture now uses `name="Python"` with
`qualified_name="wild.Python"`, which satisfies every package-index condition
except the language predicate, and the mutation is caught by four tests.

### The two memo mutations are equivalent mutants, not gaps

Dropping `id(index)` from a memo key, and dropping the per-build reset, are each
individually undetected because each is genuinely redundant: the memo has two
independent guards against a cross-build leak, and either one alone prevents
it. A property-level test cannot distinguish these equivalent mutants and
should not try to. What matters is that removing **both** is caught, and that
is now verified at **both** key-construction sites:

| Mutation | `_resolve_type_name` key | `_wildcard_static_call_resolution` key |
|---|---|---|
| drop `id(index)` only | undetected | undetected |
| drop per-build reset only | undetected | undetected |
| drop **both** | **caught** — `test_java_wildcard_member_lookup_memo_is_per_build_not_a_global_cache` | **caught** — `test_static_wildcard_memo_does_not_leak_across_graph_indexes` |

Both guards are retained. Comments in `_WildcardMemo.get_or_compute` and
`_memoized_wildcard` now state that each guard is individually redundant, name
the two covering tests, and identify the reset — which compares `GraphIndexes`
instances — as the exact guard rather than the caller's `id()`. An earlier
revision of those comments asserted that removing either guard alone is caught,
which measurement showed to be false.

## Independent review

The table below, as first written after fix round 1, contained materially false
rows. It was assembled from harness output whose mutation regexes did not all
apply, and the failure counts for five rows were never independently
confirmed. The re-reviewer re-measured them and found real discrepancies. The
false rows are withdrawn rather than restated, and the corrected figures are in
the fix round 2 matrix below, each re-derived by an independent harness that
reports every failing test by name.

The specific defects the withdrawn table concealed are now fixed: `GLOBAL_MAPS`
had been extended, which is what actually closed C1 and C2, but the Java and
ECMAScript binding lookups were still unverified (I2 half-fixed), the
per-file build metric could not distinguish O(F*S) from O(F*S^2) (B3), and
`nodes` was wrapped in a counting *mapping* so that iterating it under
instrumentation yielded node-id strings rather than `GraphNode` objects (B6) —
a latent correctness defect in the instrument itself.


A fresh reviewer agent with no authoring context reviewed Task 16 and returned
**CHANGES REQUIRED**: 2 Critical, 4 Important, 4 Minor and 3 Nit findings. It
confirmed the plan-mandated sensitivity probe genuinely reproduces, and that
no resolver semantics changed and scope is clean, but proved by twelve
independent mutations that several tests could not fail.

### Critical findings and their fixes

**C1 — `test_java_wildcard_resolution_cost_is_independent_of_unrelated_files`
asserted `0 == 0 == 0`.** The Java wildcard `resolve_import` path touches none
of the four instrumented maps, because `GLOBAL_MAPS` omitted the file and
package maps it actually uses. Root cause and fix: `GLOBAL_MAPS` was extended
from four entries to eleven, adding `by_file_name`, `parent_ids`, `children`,
`files_by_path`, `file_ids`, `java_packages` and `python_modules`. The test now
also asserts `_iterations(counters) == 0` and `measurements[0] > 0`, so a
zero-instrumentation regression is self-announcing instead of passing silently.

**C2 — `test_ecmascript_candidate_resolution_never_scans_the_file_map` could
not fail.** The ECMAScript relative-import probe reads `index.file_ids` and
`index.files_by_path`, neither instrumented, so all four counters stayed empty.
A mutation replacing the probe with a scan of `index.files_by_path` passed.
Fix: `files_by_path` and `file_ids` are now instrumented, and the test asserts
zero iterations on each named map, a positive `file_ids` lookup count (so the
assertion cannot be satisfied by an uninstrumented path), and a measured
per-resolution bound.

### Important findings and their fixes

**I1 — `test_java_wildcard_memo_is_per_build_and_not_a_global_cache` never
exercised the memo.** `_memoized_wildcard` is reachable only from
`java.resolve_reference`; the test called `resolve_import` three times. Fix: the
test was replaced by two correct ones —
`test_java_wildcard_member_lookup_memo_is_per_build_not_a_global_cache` resolves
an INHERITS reference through a wildcard-scoped environment and proves the
result follows the current `GraphIndexes` across two builds, and
`test_java_wildcard_member_lookup_is_computed_once_per_environment` counts
`_wildcard_type_candidates` calls and asserts exactly one for two identical
lookups.

**I2 — the `bindings_by_scope_name` guarantee was asserted by name only.**
`ImportEnvironment` was never instrumented, so replacing the keyed lookup with a
scan of `environment.by_file` passed. Fix: `ImportEnvironment.by_file` is now
wrapped in a `CountingMapping`, and the test asserts it is never iterated **and
never read at all** during reference resolution — a strictly stronger claim than
the one originally made.

**I3 — no build-time complexity was measured.** `counted_index()` only applies
after construction. Fix: a `CountingTuple` wrapper counts reads of each
`ParsedFile.symbols` collection, and
`test_index_construction_visits_each_file_and_symbol_a_fixed_number_of_times`
asserts that each file's symbols are read a constant number of times across
4/16/64 files and that total visits grow exactly linearly with the file count.
The plan's "count node visits during `build_graph_indexes`" requirement is now
met without any production test seam.

**I4 — `GLOBAL_MAPS` omitted the maps a global scan would actually use.** Fixed
together with C1 and C2; the constant is now eleven maps.

### Minor findings and their fixes

**M1** the Java wildcard test checked cardinality, not identity — it now asserts
the exact candidate id tuple. **M2** a test named `..._are_disjoint...` asserted
the opposite (`len(candidates) == 1`); renamed to
`..._are_identical_across_sites_and_sorted`. **M3** a test named
`..._keeps_all_eight_bounded_candidates` asserted four; renamed to
`..._keeps_four_direct_file_candidates`, and a new
`test_ecmascript_all_eight_candidate_paths_are_bounded` now asserts
`len(direct) + len(through_index) == 8` with disjoint sets, making the plan's
"eight" load-bearing rather than described. **M4** the report's own coverage
table overstated the Java wildcard and ECMAScript rows; it is replaced by the
requirement-to-test mapping below, which now reflects what the tests assert.

### Nits

**N1** the identifier `..._r_times_k_candidates` is what a naive `time` grep
hits; the file imports no `time` module and contains no wall-clock threshold.
**N2** the Java wildcard sortedness assertion is enforced upstream by
`Resolution.__post_init__`; retained as documentation. **N3** the unrelated
Java-wildcard fixtures are `.py` while the matching files are in a Java package,
so a cross-language package leak would not show up in the count; the test now
asserts the exact candidate tuple instead of only a count, which closes it.

### RED evidence for this fix round

Extending `GLOBAL_MAPS` and rewriting the four tests produced 6 genuine
failures, each corrected against real resolver behaviour rather than by
relaxing an assertion: a missing `base_types` on the Java wildcard fixture (no
INHERITS reference existed to resolve), a duplicate `main.js` path in the
eight-path fixture rejected by validation, an over-tight `file_ids` budget of 8
against a measured 13 per resolution, a wrong attribute path in the
`CountingTuple` helper, and two wrong import-environment expectations. The
final suite is 26 passed, exit 0.

## Verified coverage after the fix round

| Plan requirement | Test |
|---|---|
| CountingMapping counts `__getitem__`, `get`, `__iter__`, yielded keys | `test_counting_mapping_tracks_accesses`, `test_counting_sequence_yields_the_original_items`, `test_counting_sequence_wraps_graph_nodes_without_changing_them`, `test_counting_tuple_counts_passes_and_yielded_items` |
| 64/128/256 files and symbols | `test_synthetic_inventories_scale_linearly` |
| Index construction covers every file, allowed its one linear pass | `test_index_construction_produces_one_symbol_range_index_per_file` |
| No global map iteration per reference | `test_reference_resolution_never_iterates_a_global_map`, `test_ambiguous_references_emit_candidates_without_iterating_a_global_map` |
| Lookup cost bounded by reference count, not a wall-clock threshold | every `assert _lookups(counters) <= 20 * len(calls)` |
| Doubling unrelated symbols does not raise per-reference cost | `test_unrelated_symbols_do_not_change_per_reference_lookup_cost` |
| Node visits during `build_graph_indexes` across doubled F/S | `test_index_construction_visits_each_file_and_symbol_a_fixed_number_of_times` |
| Node visits in `structural_edges` across doubled F/S | `test_structural_edges_are_driven_by_precomputed_parent_ids`, `test_structural_edge_pass_count_is_independent_of_symbol_count` |
| Deep nested parents without repeated ancestor-chain walking | `test_scope_walk_cost_grows_with_depth_not_with_file_size`, `test_scope_walk_cost_is_linear_in_depth` |
| ImportEnvironment built once; imported-name calls use `bindings_by_scope_name`, never a record scan | `test_import_environment_is_keyed_by_scope_and_name_not_rebuilt_per_call`, `test_import_environment_indexes_every_binding_by_scope_and_name` |
| Java wildcard K matching files, cost independent of U unrelated files | `test_java_wildcard_candidates_are_exactly_the_matching_package_files`, `test_java_wildcard_resolution_cost_is_independent_of_unrelated_files` |
| Java wildcard member enumeration is memoized per build, never a global cache | `test_java_wildcard_member_lookup_memo_is_per_build_not_a_global_cache`, `test_java_wildcard_member_lookup_is_computed_once_per_environment`, `test_type_name_memo_does_not_leak_across_graph_indexes`, `test_static_wildcard_memo_does_not_leak_across_graph_indexes`, `test_wildcard_memo_does_not_share_entries_across_environments` |
| All eight ECMAScript candidate paths | `test_ecmascript_all_eight_candidate_paths_are_bounded`, `test_ecmascript_extensionless_import_keeps_four_direct_file_candidates`, `test_ecmascript_directory_index_paths_are_the_other_four_candidates`, `test_ecmascript_explicit_extension_selects_only_the_exact_file` |
| Eight ECMAScript paths without scanning file maps | `test_ecmascript_candidate_resolution_never_scans_the_file_map` |
| Repeated ambiguity sites accounting for R*K emitted candidate ids | `test_repeated_ambiguity_sites_emit_exactly_r_times_k_candidates`, `test_ambiguity_candidate_sets_are_identical_across_sites_and_sorted` |
