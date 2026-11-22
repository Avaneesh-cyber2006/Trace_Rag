# Module 9 — Final integration and release gate

This checkpoint is intentionally **not** a Module 10 LLM feature. It verifies
Module 9's complete retrieval composition and the boundaries between evidence
classes. The nine earlier increments remain opt-in/backward-compatible.

## Invariants

1. Lexical and semantic retrieval read one pinned Module 5 publication.
2. The returned code chunks remain the authoritative indexed source content;
   untrusted input cannot introduce new code text or override ranks.
3. Optional relevance gating occurs before final top-k, and source-verified
   graph expansion uses the same pinned publication and selected hits.
4. The published original-file SHA-256 revision must match graph provenance.
   Same-chunk/different-source evidence must be rejected **before** embedding.
5. Git history (Module 7) and runtime clues (Module 8) are separate,
   explicitly unverified annotations; neither changes code ranking nor proves
   causality. Both are preflight-validated before embedding and before a store
   lease. Raw runtime messages, trace IDs, and commit messages are excluded.
6. Empty snapshots do not embed. Corruption, wrong namespaces and malformed
   evidence fail closed. Query paths must never mutate the vector store.

## New regression cases

- `tests/test_module9_final_integration.py`: one-lease hybrid + relevance +
  pinned graph + Git history + runtime, identical baseline hits and graph,
  metadata separation, stale graph rejection, and preflight rejection of
  malformed/tampered history without embedding or store access.
- `tests/test_module9_final_chroma.py`: persistent real-Chroma cross-stream
  integration and reopened-generation provenance. This needs the project's
  installed Chroma dependency; local stubs are **not** sufficient.

## Verification before committing Module 9

On the Windows feature worktree, run all selected Module 9 regression tests
(including real Chroma), then the **complete offline project test suite** using
`python -m pytest -q -p no:cacheprovider` with a unique `--basetemp` location.
The default pytest selection excludes opt-in network/live tests per pytest.ini.
Check `git diff --check`, `git status --short`, and the branch/HEAD before
making any commit. Do not stage, commit, merge, push or touch `main` as part
of this patch application. A successful Module 9 suite alone is not evidence
that the complete project regression suite passed.
