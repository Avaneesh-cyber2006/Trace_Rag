# Task 13 Report: Relevant PARTIAL/FAILED Evidence

## Scope

Added public-pipeline coverage for partial-parse evidence in all six supported
source forms. A shared resolver helper now changes an otherwise definitive
`NOT_FOUND` into `INCOMPLETE_PARSE` only when the source file has parse loss
overlapping the reference location or a locationless parse issue. Python, Java,
and ECMAScript call and type outcomes use that helper. Positive declarations
remain resolvable, unsupported expressions keep `UNSUPPORTED`, and parse loss
outside the selected lookup has no effect. No source reads, path-based Java
package inference, or fabricated symbols were added.

## RED evidence

The focused public fault probe exposed five failing cases (Python, JavaScript,
JSX, TypeScript, and TSX). In each, a supported missing call whose location
overlapped a `PARTIAL` extraction issue was reported as `NOT_FOUND`; Java already
classified the matching case as incomplete through its type lookup.

```text
5 failed, 10 passed
```

## GREEN evidence

Focused Task 13 suite:

```text
python -m pytest tests/test_code_graph_partial_parses.py -q -p no:cacheprovider
33 passed
```

Required neighboring graph gate, run once:

```text
python -m pytest tests -q -k code_graph -p no:cacheprovider
460 passed, 1803 deselected
```

Scoped `git diff --check -- backend/code_graph/references.py backend/code_graph/resolvers tests/test_code_graph_partial_parses.py` passed. Pytest used the authorized parent checkout's `.superpowers/tmp/m6/t13-*` directories because the isolated worktree ACL denied creating temporary directories inside the worktree.

## Commit

Implementation commit: `67749223091538df3235f0b48a4195f9e0af6b27`

## Review fix round 1

Added a public-pipeline boundary test proving a supported missing call in a
`PARTIAL` source remains `NOT_FOUND` when the located parse issue is disjoint
from that call.

RED was established with a temporary fault probe that treated every `PARTIAL`
file as relevant. The new test then failed because the call was classified as
`INCOMPLETE_PARSE`; the overlap-based production logic was restored before the
final checks.

Focused Task 13 suite: 34 passed. Required graph regression scope: 461 passed,
1803 deselected. Scoped `git diff --check` passed. No production file changed in
this review fix.
