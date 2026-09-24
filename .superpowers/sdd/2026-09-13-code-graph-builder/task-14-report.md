# Module 6 Task 14 execution report

## Scope and base

Executed Task 14 in `C:\Users\avane\Desktop\Studies\Projects\Trace_Rag_Module6_Worktree`.
The starting HEAD was `72ce7a4dc2429a4634ac788c827bc91f7a653d9c`, exactly the
requested `BASE72ce7a4dc2429a4634ac788c827bc91f7a653d9c`. The worktree was clean
before edits. Verification used the primary repository's existing
`.venv/Scripts/python.exe` (Python 3.12.14), offline, with cache disabled and
short basetemps under `.superpowers/tmp/m6/`.

## Baseline and RED evidence

Before adding tests, `tests/test_code_graph_determinism.py` did not exist;
`tests/test_code_graph_validation.py` passed its 148-test baseline. Existing
production code already implemented full `SymbolInfo` equality before
projection, equality-before-dedup for graph records, canonical sorting, and
mapping snapshots, so no production change was needed.

The focused tests passed against the existing behavior. To prove sensitivity,
I temporarily disabled the builder's graph-record equality guard and restored
it immediately afterward. Both different-reason and different-candidate
same-reference-ID cases failed with the expected `DID NOT RAISE
CodeGraphIdentityError`.

Additional temporary faults were each restored from the clean checkout:

- Disabling the full-symbol collision guard made all six changed-evidence cases
  fail to raise `CodeGraphIdentityError` (or fail later with an invariant error).
- Disabling identical-symbol dedup made the duplicate-collapse test fail on
  duplicate node IDs.
- Removing canonical unresolved ordering made the inventory/reference
  permutation test fail on the canonical-order invariant.

The first two fixture attempts failed before exercising the requested behavior:
one used calls outside a callable source / a non-callable type reference, and a
subsequent edit introduced a literal newline escape. I corrected the fixtures,
then obtained the valid green runs and fault-probe RED results above.

## Changes

Added `tests/test_code_graph_determinism.py` coverage for independent file,
parse, symbol, import, call, and base-type ordering; identical duplicate symbol,
import, and call collapse; full same-ID symbol evidence conflicts across
modifiers, base types, implemented types, parent, parameters, and locations;
same unresolved ID conflicts for differing reason/candidates; source-site edge
identity; namespace and repository-path identity behavior; Unicode and
case-distinct names/paths; duplicate-qualified-name and Java wildcard ambiguity
permutations with sorted candidates and no speculative edges; and detached
immutable `GraphIndexes` mapping snapshots.

Added a validation regression proving Unicode paths that differ by case remain
distinct and are sorted by their exact spelling. The existing validation test
`test_duplicate_paths_across_scanner_and_parser_categories_are_rejected`
continues to cover repeated file records. Production code in `indexes.py` and
`references.py` was left unchanged after confirming its accumulators satisfy the
specified equality-before-dedup behavior.

## Verification

- Baseline validation: 148 passed.
- Focused Task 14 selectors: 166 passed.
- Identity, partial-parse, and builder neighbors: 110 passed.
- `git diff --check -- backend/code_graph/indexes.py backend/code_graph/references.py tests/test_code_graph_determinism.py tests/test_code_graph_validation.py`: passed.
- No dependencies, seeds, source rereads, upstream production code, Modules 7–10,
  or network use.

Independent controller review remains pending.
