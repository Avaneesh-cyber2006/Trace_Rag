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

Cloned contents are untrusted input and are treated strictly as data. The loader uses argument-based Git subprocess calls with a timeout and disabled hooks. It never imports cloned Python modules, runs scripts or package managers, installs repository dependencies, invokes builds/tests, or executes Git hooks. Existing workspace data is never overwritten or silently deleted.

**Current limitation:** Only public GitHub repositories are supported. No token, API key, OAuth flow, or private-repository authentication is implemented.
