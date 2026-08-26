# TraceRAG Module 1: Repository Loader Design

## Scope

Module 1 accepts a public GitHub repository root URL, validates and normalizes it, safely prepares a deterministic workspace location, shallow-clones or reuses the repository, reads basic Git and filesystem metadata, and returns a `RepositoryInfo` value. It remains independent of all later TraceRAG modules and never analyzes or executes repository code.

## Public API and Data Model

`RepositoryLoader(workspace_path="workspace", clone_timeout_seconds=120)` owns configuration. Its `load(repository_url)` method returns an immutable dataclass containing success, owner, repository name, normalized URL, resolved local path, branch or `None`, commit SHA or `None`, regular-file count, working-tree byte size, and whether an existing clone was reused. A convenience `load_repository` function delegates to the class.

The public package exports the loader, URL model and validator, result model, and module exception hierarchy. It does not expose subprocess or GitPython errors.

## Components and Flow

`validator.py` parses HTTP/HTTPS URLs with `urllib.parse`. It accepts only the exact `github.com/<owner>/<repository>[.git]` shape, rejects credentials, ports, queries, fragments, encoded separators, dot segments, empty segments, and GitHub subpages, and normalizes accepted input to HTTPS without `.git`.

`loader.py` verifies Git availability, creates and resolves the configured workspace, derives the destination as `{owner}_{repository_name}`, and proves that destination remains a direct child of the workspace. A missing destination is populated with `git clone --depth 1`. An existing destination is reused only if GitPython can open it and its `origin` normalizes to the requested repository. It is never fetched, updated, overwritten, or silently deleted.

`metadata.py` uses GitPython for read-only branch, commit, and remote inspection. It walks the working tree with `pathlib`/`os.scandir`, prunes every directory named `.git`, counts regular files, and sums sizes from filesystem metadata without reading file contents. Files that disappear or become inaccessible during traversal are skipped safely.

## Clone and Workspace Safety

Git commands use argument arrays, captured text output, no shell, and a configurable timeout. Git availability is checked with `git --version`. Clone invocation disables hooks via command-scoped Git configuration and disables interactive credential prompts. Repository contents remain untrusted data: the loader never imports them, installs dependencies, runs hooks, executes scripts, or invokes builds/tests.

The loader does not pre-create the destination because `git clone` owns that path. On a failed new clone, it removes only the destination that did not exist before the attempt. It never removes a pre-existing path. The workspace itself is resolved and created with explicit error translation, and owner/repository validation prevents traversal before directory naming.

## Error Handling

All errors inherit `RepositoryLoaderError`: `InvalidRepositoryURL`, `RepositoryNotFound`, `RepositoryNotPublic`, `CloneFailed`, `GitNotInstalled`, `WorkspaceError`, and `MetadataExtractionError`. Clone stderr is classified conservatively: known not-found messages map to `RepositoryNotFound`, known authentication/private-access messages map to `RepositoryNotPublic`, and all other failures map to `CloneFailed`. Messages are sanitized and do not echo credentials, environment variables, or raw command objects.

Timeouts become `CloneFailed`. Invalid or mismatched existing directories become `WorkspaceError`. GitPython and filesystem failures during metadata collection become `MetadataExtractionError`.

## Testing and Verification

Pytest unit tests drive implementation. They cover accepted and rejected URLs, normalization, traversal resistance, Git absence, timeout and clone-error translation, partial-clone cleanup, preservation of pre-existing directories, existing-clone reuse and mismatch rejection, detached HEAD support, and `.git` exclusion from counts and sizes. Temporary local Git repositories provide deterministic offline fixtures; subprocess behavior is mocked only for failure paths that cannot be induced safely.

One integration test is marked `integration` and may clone a small public GitHub repository. The default suite excludes it through marker selection, so ordinary tests remain offline. Final verification runs `pytest -q` and then the manual script against a public repository when network access is available, followed by `git rev-parse HEAD` in the resulting workspace.

## Documentation and Packaging

The repository includes package initializers, focused module files, a human-readable manual test script, minimal requirements (`GitPython` and `pytest`), `.gitignore`, `workspace/.gitkeep`, pytest marker configuration, and a README documenting installation, usage, security, and the public-GitHub-only limitation.

No RAG, scanning/filtering, AST work, embeddings, history analysis, runtime execution, authentication, API calls, or other Module 2+ behavior is included.
