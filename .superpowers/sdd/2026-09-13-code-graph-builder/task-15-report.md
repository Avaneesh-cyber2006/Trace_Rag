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

`os.write`, `os.fsync` and `os.ftruncate` operate only on an already-open file
descriptor. Every path-based opener is already guarded (`builtins.open`,
`io.open`, `os.open`, `os.fdopen`, `Path.open`, `Path.write_text`,
`Path.write_bytes`), so Module 6 can never obtain a descriptor to pass to them.
Guarding them would patch interpreter-level write primitives for no reachable
gain and risks breaking pytest and Python itself, which the approved plan
explicitly warns against. They are therefore deliberately left unguarded, and
the static audit rejects them by leaf name where that is unambiguous.

## Verification

- `tests/test_code_graph_security.py`: 36 passed.
- Required builder/Python/Java/ECMAScript neighbors: 250 passed.
- Remaining Module 6 focused files: 239 passed.
- `python -m compileall -q backend/code_graph`: exit 0.
- `git diff --check -- backend/code_graph tests/test_code_graph_security.py`: exit 0.
- No dependency, upstream production module, network, or repository file was
  touched. All pytest basetemps were outside the repository tree.
