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
