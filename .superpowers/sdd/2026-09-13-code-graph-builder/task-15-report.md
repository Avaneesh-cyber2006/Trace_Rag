# Module 6 Task 15 fix round 1 report

## Scope and base

Fix round 1 started from review commit `3b884d4c08a76755c855495c3a28503abfccbfc1`.
The change updates only `tests/test_code_graph_security.py` and this report.
No production boundary violation was found, so no production files changed.

## Review findings addressed

- Runtime guards now block common persistence operations through `os` and
  `pathlib`, including remove/unlink, rename/replace, directory creation and
  removal, writes, chmod, and link creation.
- The AST audit rejects filesystem mutation calls as well as read, process,
  network, dynamic-import, eval/exec, and unsafe-deserialization calls. It
  resolves imported aliases and avoids misclassifying in-memory string
  `replace` calls as filesystem writes.
- A committed in-memory AST case proves the audit catches `os.unlink`,
  `os.replace`, `Path.write_text`, and `Path.replace`.
- Each guarded language fixture now asserts an import, call, and type reference
  produces either a graph edge or an explicit unresolved result.
- Mutation checks prove parser/scanner input records and public graph values are
  immutable, and a later build cannot change an earlier graph.

## RED sensitivity probes

Temporary build-entry injections were run under the actual runtime guards.
`os.remove`, `Path.unlink`, and `Path.replace` each raised the guard before any
filesystem operation. Earlier Task 15 probes similarly confirmed that
`Path.read_text`, `subprocess.run`, `socket.create_connection`, and
`importlib.import_module` are blocked. All injection files were removed.
The committed AST sensitivity case parses forbidden mutation calls in memory
and confirms each is reported by the audit.

## Verification

- `tests/test_code_graph_security.py`: 18 passed.
- Required builder/Python/Java/ECMAScript neighbors: 250 passed.
- `git diff --check -- backend/code_graph tests/test_code_graph_security.py`:
  passed.
- No dependencies, upstream production modules, or network access were used.

# Module 6 Task 15 fix round 2 report

## Scope and base

Fix round 2 started from branch HEAD `16b06b668876efd336ad1389830f0e123148ed35`,
whose Task 15 ancestor is review commit `dbd179e509876a8345bc8e089dac68b9684b65b0`.
The change updates only `tests/test_code_graph_security.py` and this report. No
production file changed: the OpenCode handover audit independently confirmed no
Module 6 production security violation exists.

## Handover findings addressed

- The prior language fixture asserted `edge OR unresolved` disjunctions, so a
  fixture in which nothing resolved still passed. Measured outcome of the old
  fixture: only `DEFINES` edges, `import=external`, `call=ambiguous`,
  `inherits=not_found`, `implements=unsupported`.
- Those fixtures are replaced by strict per-language fixtures that assert the
  exact edge kind, exact source node id, exact target node id, and the exact
  unresolved reason plus candidate evidence. Python proves IMPORTS, CALLS and
  INHERITS; Java proves IMPORTS, CALLS, INHERITS and IMPLEMENTS; JavaScript,
  JSX, TypeScript and TSX prove IMPORTS and CALLS; TypeScript proves IMPLEMENTS
  and ECMAScript proves INHERITS.
- Intentionally unresolved references now assert their exact reason:
  Python `NOT_FOUND` and `EXTERNAL`, Java `EXTERNAL`, ECMAScript `EXTERNAL`
  and `AMBIGUOUS` with sorted candidate ids, and a configured alias form stays
  `UNSUPPORTED` without becoming an edge.
- Guard and static-audit symmetry: the static audit already rejected
  `os.truncate` while the runtime guard did not, and neither covered
  `shutil.rmtree/move/copytree` or `tempfile.mkstemp/mkdtemp`. The runtime
  guard now also blocks `os.truncate` and `os.fdopen`; the audit rejects the
  recursive delete/move/copy and temporary-file helpers by resolved module name.
- Immutability evidence now exercises all four public indexes, all three
  canonical tuples, `UnresolvedReference.candidate_node_ids`, cross-build index
  identity, and — most importantly — that a published inventory does not retain
  a caller-owned mutable backing mapping.

## RED and sensitivity evidence

- RED 1 (language fixtures): the strict assertions were run against the old
  fixture metadata first. They failed with `IMPORTS` and `CALLS` absent, while
  the old disjunction still passed. Recorded before the fixtures were changed.
- RED 2 (guard symmetry): the new audit tests failed with
  `assert 'shutil.rmtree' in ()`, `os.fdopen` undetected, and the runtime
  guard tests failed before `os.truncate`/`os.fdopen` were guarded.
- Sensitivity 1: flipping an expected edge kind, a target node id, or an
  unresolved reason is detected by the strict assertions.
- Sensitivity 2: `mappingproxy` exposes no mutating method, item assignment is
  rejected, and appending to the caller-owned list after construction leaves
  the published index unchanged.
- A control test asserts `str.replace`, `dict.copy` and `list.copy` are not
  reported by the static audit, so the new checks stay discriminating.

## Ruling on remaining low-level descriptor APIs

`os.write`, `os.fsync` and `os.ftruncate` are **deliberately unguarded and also
unaudited**. They are not in the runtime guard and the static audit does not
reject them; this is stated plainly because an earlier revision of this section
incorrectly claimed the audit covered them.

The sole reason they are left out is that they operate only on an
already-open file descriptor. Every path-based opener is guarded
(`builtins.open`, `io.open`, `os.open`, `os.fdopen`, `Path.open`,
`Path.write_text`, `Path.write_bytes`), so Module 6 cannot obtain a descriptor
to pass to them, and guarding interpreter-level write primitives risks breaking
pytest and Python itself, which the approved plan explicitly warns against.

## Independent review of fix round 2

A fresh reviewer agent with no authoring context reviewed the original Task 15
work plus both fix rounds and returned **APPROVED** with no Critical or
Important findings. It independently mutation-tested the guards (23/23 fire with
the exact message and zero filesystem effect), swept the static audit for both
false negatives (0/43 missed) and false positives, confirmed production contains
no filesystem/network/process/eval/exec usage, and confirmed Modules 1-5 are
untouched.

Its Minor findings, and their adjudication:

- **Minor 1 — `os.listdir`/`Path.iterdir` audited but not guarded.** Fixed:
  both added to the runtime guard with a committed blocking test. `Path.glob`
  and `rglob` were already blocked transitively through patched `os.scandir`.
- **Minor 2 — leaf-name rules flag ordinary same-named methods**
  (`seen.remove`, `worker.run`, `db.connect`, `f.truncate`, …). Deferred by
  ruling: the audit is intentionally receiver-agnostic so that a forbidden call
  is caught whatever it is called on, and the approved plan asks for
  attribute-based I/O to be reviewed. Module 6 contains no such calls today.
  Narrowing to known I/O receivers would trade detection breadth for a cosmetic
  gain. A control test pins the three behaviours the spec names
  (`str.replace`, `dict.copy`, `list.copy`) as not flagged.
- **Minor 3 — `assert first.nodes == original_nodes` was tautological.** Fixed:
  the snapshot is now a `copy.deepcopy` taken before the second build, is
  asserted to be a distinct object, and the published index contents are
  compared as well.
- **Minor 4 — the source audit could pass vacuously on an empty package.**
  Fixed: `_audit_code_graph_sources` now returns the audited relative path list,
  the main test asserts 15 audited files, and two new tests prove a planted
  violation is reported and an empty scan is visibly empty.
- **Minor 5 — this report overstated the `os.write`/`os.fsync`/`os.ftruncate`
  defence.** Fixed: corrected above.
- **Minor 6 — module import execution is not blocked at runtime.** Accepted and
  documented: CPython loads modules through `_io.open_code`, not the patched
  openers, so the static allowlist is the real control. The module docstring now
  says so, and a new parametrized test proves the allowlist rejects Module 4/5
  (including a dotted submodule and a nested package), Chroma, NetworkX, the
  parser registry and Gemini, alongside a test pinning the allowed surface.

Nits fixed: duplicate `FrozenInstanceError` import, two unused `cg` fixture
parameters, and a string comparison replaced with enum identity.

Nits deferred by ruling: `is not` on index wrappers cannot by itself detect
shared backing storage (the cross-build test does detect it, so the weaker
assertion is kept as a secondary signal); `_edge_pairs` collapses duplicate
`(kind, source, target)` triples (no fixture exercises two import sites at one
pair, and multi-site edge identity is covered by
`tests/test_code_graph_determinism.py`); line length exceeds 79 characters in a
few places (the repository has no lint configuration and other Module 6 tests
already exceed it).

The reviewer also noted that the fix-round-2 RED claims are not reproducible from
the committed tree, because the commits are linear. That is accurate: the RED was
observed interactively before the fixture change and the probe script and its
output were recorded at the time. The committed sensitivity probes — the
synthetic-tree violation test, the empty-scan test and the mutation-rejecting
index tests — are the durable, re-runnable form of that evidence.

## Verification

- `tests/test_code_graph_security.py`: 36 passed.
- Required builder/Python/Java/ECMAScript neighbors: 250 passed.
- Remaining Module 6 focused files: 239 passed.
- `python -m compileall -q backend/code_graph`: exit 0.
- `git diff --check -- backend/code_graph tests/test_code_graph_security.py`: exit 0.
- No dependency, upstream production module, network, or repository file was
  touched. All pytest basetemps were outside the repository tree.
