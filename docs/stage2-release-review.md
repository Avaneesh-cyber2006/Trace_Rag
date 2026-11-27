# Stage 2 reliability release review

Date: 2026-11-26. Disposition: ready for owner approval as a bounded reliability
release, not a declaration of production deployment readiness. The owner accepted
the remaining provider 504 failures. No further schema change or Gemini call was
made for this release review. Stage 3 is documentation only.

## Git and exact proposed commit

Working branch: `fix/module10-generation-reliability`.
Both HEAD and local main: `21711c789381c3c44be53d38a7fb8474a9fcab38`.
No remote mutation, commit, merge, push or deployment was performed. The initially
empty staging area is populated only with this explicit allowlist for review:

1. `backend/causal_reasoner/answer_models.py`
2. `backend/causal_reasoner/providers/gemini.py`
3. `tests/test_module10_gemini_adapter.py`
4. `docs/module10-gemini-provider-increment3.md`
5. `docs/stage2-generation-reliability-review.md`
6. `docs/stage2-release-review.md`
7. `docs/superpowers/plans/2026-11-26-stage3-deployment.md`

Proposed commit message:

```text
fix(reasoning): bound Gemini retries and expose safe validation diagnostics

Constrain generation to request-local evidence labels and kinds, preserve
strict local validation, and reject truncated or blocked responses.
Keep retries opt-in and limited to one transient HTTP retry; disable SDK
retries and automatic function calling.

Add sanitized validation/provider categories and regression coverage.
Document live verification limits, launcher compatibility, and the proposed
Stage 3 deployment roadmap without implementing deployment changes.
```

## Complete accumulated change review

Reviewed the entire diff against main, including the pre-existing retry and
structured-generation changes, not just the latest diagnostic edits. Independent
review found no actionable correctness/security regression. No dependency, API
response contract, database, indexing or frontend behavior changes are included.

- One generation attempt remains the default; a server can opt into two total
  attempts, retrying only HTTP 500/502/503/504 with a one-second delay. SDK retries
  are disabled. Quota/auth errors and invalid output do not trigger repair loops.
- Dynamic schema labels and categories restrict generation but do not establish
  trust. Local parsing and the independent citation registry validator remain
  authoritative, including exact source-substring checks and evidence kinds.
- Parser acceptance rules remain unchanged; exceptions now carry static categories
  and field paths. Diagnostic logs do not contain model text or arbitrary JSON keys.
- Pre-embedding query privacy, redacted request sealing, and server/repository/
  per-request external consent checks are unchanged. API bearer authentication is
  unchanged. This preserves the current local-session boundary; it does not turn
  that shared bearer into multi-tenant authorization.
- Truncated/blocked output is rejected, even if it happens to contain parseable JSON.

## Test evidence and exclusions

Final code suite: **4,421 passed, 5 skipped, 2 deselected** in 531.63 seconds.
Targeted regressions: **129 passed**. Frontend: **21 passed**, with TypeScript and
production build passing. Only two existing dependency deprecation warnings:
Starlette/httpx test client and Chroma legacy embedding configuration.
No production code changed after those results. Release-review changes are docs.

The five skip candidates were rerun offline with a fresh external temporary
directory to confirm their actual reasons:

| Test | Why skipped / significance |
| --- | --- |
| `test_file_scanner.py::test_file_and_broken_symlinks_are_ignored` | WinError 1314: symlink privilege unavailable; real symlink behavior remains untested on this host |
| `test_file_scanner.py::test_directory_symlink_escape_is_never_traversed` | Same privilege limitation; requires a capable CI host |
| `test_code_parser.py::test_reader_rejects_symlink_swap_without_reading_target` | Same privilege limitation; real symlink-swap defense needs platform coverage |
| `test_git_history_adapter.py::test_glob_characters_in_a_path_filter_are_treated_literally` | Windows cannot create filenames containing `*` or `?`; run on POSIX |
| `test_chroma_vector_store.py::test_fork_discards_inherited_chroma_state_without_releasing_parent_ownership` | POSIX fork unavailable; validate inherited ownership handling on Linux |

The two deselections are deliberate defaults in `pytest.ini`:

- `test_repository_loader.py::test_public_repository_integration`: clones an
  external public GitHub repository; offline suite does not cover that network path.
- `integration/test_gemini_embeddings_live.py::test_verified_document_and_query_modes_return_safe_vectors_without_leaks`:
  explicitly gated live Gemini embedding test; never run implicitly in CI/review.

These are coverage limits, not hidden failing tests. Plan Windows and Linux CI
with symlink privileges rather than removing guards or weakening tests.

## File and credential hygiene

The initial untracked inventory comprised 23 top-level entries and 128 files
including nested repositories. Four `rgtest*` directories contain 109 files of
miniature repositories and synthetic Git objects, without configured remotes or
active hooks. Thirteen `run_git_test*` scripts, four hash/zlib scripts and the
107-byte `valid-raw.bin` synthetic commit payload are unrelated experiments.
They were inspected without execution and are preserved outside the allowlist.

The allowlist contains no `.env`, credentials, logs, generated build output,
indexes or temporary verification scripts. Credential-pattern inspection found
no real credential indicators; synthetic test sentinels are intentional. Pattern
scanning is not proof against every possible secret format. Verification drivers
and sanitized logs under LOCALAPPDATA are outside this repository and excluded.
The report contains operational paths and counts, never secret values.

## Launcher/configuration decision

The adapter is backward compatible for existing `GeminiSettings` callers;
`max_attempts=1` remains the default. Structured generation is automatic after
authorization. No mandatory settings or index migration is needed.

There is no checked-in production Gemini launcher. The checked-in local demo is
synthetic and must not be promoted to production. The external Downloads launcher
pins old file hashes and requires a deliberate post-approval update before use
with this release; do not delete its integrity guard. Production startup, secrets,
consent, index ownership and total request deadlines belong to Stage 3. The
temporary budget-specific verification driver is not a production deliverable.
The adapter documentation is updated to remove obsolete no-retry/no-log claims.

## Accepted limitations and release gates

- Gemini availability: question 2's approved repeat produced two HTTP 504s,
  safely classified as SERVICE_ERROR; accepted by the owner. All 11 authorized
  calls were consumed (4 embeddings, 7 generations). No new calls are authorized.
- Two live API investigations succeeded with independently verified citation
  content. The exact original historical ANSWER_SCHEMA_INVALID cause is unknown;
  the reproducible schema/prompt contract gap and diagnostic loss were addressed.
- Index scope is only 12 chunks from three source files at the pilot revision.
  It is not a whole-repository analysis or an index of the changed working tree.
- Supported-browser input controls and frontend fixture tests passed. Rendering
  and clicking citations from a live authenticated answer are not browser-verified.
- Current bearer authentication and process-local rate limiting are for a local
  session, not production identities, per-user repository authorization or scaling.
- Redaction is heuristic; citation validation verifies references and excerpts,
  not semantic truth or runtime causation. Provider output remains untrusted.

The owner may approve this reliability commit with these limits. Stage 3 planning
is ready; public deployment is not. See the linked roadmap for security, indexing,
provider, packaging and end-to-end gates. No Stage 3 work has been implemented.

[Stage 3 roadmap](superpowers/plans/2026-11-26-stage3-deployment.md)
