# TraceRAG

## Module 1 — Repository Loader

The Repository Loader accepts a public GitHub repository root URL, validates and normalizes it, shallow-clones the repository into a controlled workspace (or safely reuses the matching clone), and returns basic Git and filesystem metadata as `RepositoryInfo`.

It deliberately does **not** scan or filter source files, parse ASTs, analyze dependencies or Git history, create embeddings, access a vector database, call an LLM, install dependencies, or execute any repository code. Those concerns belong to later TraceRAG modules.

### Requirements

- Python 3.11 or newer
- Git available on `PATH`

Install the small dependency set:

```bash
python -m pip install -r requirements.txt
```

### Python usage

```python
from backend.repository_loader import RepositoryLoader

loader = RepositoryLoader()
info = loader.load("https://github.com/pallets/flask")

print(info.repository_name)
print(info.current_commit)
print(info.local_path)
```

Repositories are stored deterministically under `workspace/{owner}_{repository}`. Loading the same URL again reuses an existing clone only when its `origin` identifies the same GitHub repository; it does not fetch or update it.

### Tests

Run the offline unit suite:

```bash
python -m pytest -q
```

Run the optional network-dependent integration test explicitly:

```bash
python -m pytest -q -m integration
```

### Manual check

```bash
python scripts/test_repository_loader.py https://github.com/pallets/flask
```

Example output:

```text
TraceRAG Repository Loader
==========================

Status:              SUCCESS
Repository:          pallets/flask
URL:                 https://github.com/pallets/flask
Branch:              main
Commit:              8bd71fa00000...
Files:               814
Size:                4.2 MB
Local Path:          workspace/pallets_flask
Existing Clone:      No
```

### Security

Cloned contents are untrusted input and are treated strictly as data. The loader uses argument-based Git subprocess calls with a timeout, a fresh empty hooks directory, and isolated system/global Git configuration. It never imports cloned Python modules, runs scripts or package managers, installs repository dependencies, invokes builds/tests, or executes Git hooks. Existing workspace data is never overwritten or silently deleted.

**Current limitation:** Only public GitHub repositories are supported. No token, API key, OAuth flow, or private-repository authentication is implemented.

## Module 2 — File Scanner & Filter

The File Scanner & Filter accepts an existing local repository directory and returns a deterministic `FileInventory` of useful text files. It prunes dependency, cache, and build directories before descent; excludes sensitive, unsupported, oversized, minified, and likely binary files; and classifies included files as source, test, configuration, database, documentation, build metadata, or other text.

Module 2 is independent of GitHub and can scan any accessible directory. Its normal integration with Module 1 is:

```python
from backend.file_scanner import FileScanner
from backend.repository_loader import RepositoryLoader

repository = RepositoryLoader().load("https://github.com/pallets/flask")
inventory = FileScanner(max_file_size_bytes=1_000_000).scan(repository.local_path)

for file in inventory.files:
    print(file.relative_path, file.language, file.category.value)
```

`FileScanner` defaults to a 1,000,000-byte maximum file size and an 8192-byte binary sample. Both limits are configurable positive integers. Files larger than the configured maximum are rejected from metadata alone and are never opened. Eligible files are read only once for a bounded prefix; Module 2 never loads complete contents merely to classify them.

The intentional text allowlist covers common programming languages, JSON/YAML/TOML/XML and related configuration, SQL/Prisma, Markdown/reStructuredText/plain text, build manifests, and useful special files such as `Dockerfile`, `Makefile`, and `.gitignore`. Common dependency lockfiles are excluded while their manifests remain included. Unknown types and SVG files are excluded by default.

Directories such as `.git`, `node_modules`, `venv`, `.venv`, caches, `dist`, `build`, `target`, `vendor`, `bin`, and `obj` are pruned by complete case-insensitive path component. `.github` and useful hidden configuration files are not ignored merely for being hidden.

Actual environment files and obvious key/credential filenames are excluded without reading their contents. `.env.example`, `.env.sample`, `.env.template`, and their prefixed equivalents remain eligible configuration templates. This conservative filename policy is defense in depth, not a comprehensive secret scanner.

All symbolic links and detectable Windows reparse points are excluded and never followed. When non-following metadata identifies a directory-like reparse entry, it is reported in `skipped_directories`; an ordinary POSIX link whose target kind cannot be known safely is reported as an ignored file with reason `SYMLINK`.

Included, ignored, and skipped-directory collections are independently sorted by repository-relative POSIX path. The counters mean:

```text
total_files_seen == included_files + ignored_files
```

Pruned directory contents are never discovered and do not inflate the counters. Module 1's `RepositoryInfo.total_files` uses broader working-tree counting semantics, so it is not expected to equal Module 2's `total_files_seen`.

### Module 2 security and limitations

Repository contents remain untrusted static data. The scanner never imports repository modules, evaluates configuration, reads environment values, executes Git or shell commands, invokes package managers/builds/tests, installs dependencies, calls a network service or LLM, or parses ASTs. It uses only the Python standard library.

An inventory is a snapshot. Files may be replaced after scanning, so later consumers must reconstruct paths from `repository_path` plus `relative_path`, repeat containment/link checks, and handle filesystem races. Module 2 provides a deterministic language hint but does not promise a complete-file encoding, parse source structure, detect every secret, infer all generated code, or implement Module 3+ behavior.
