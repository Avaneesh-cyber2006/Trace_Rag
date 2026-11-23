# Module 10 — Increment 1: Immutable Evidence Bundle

## Scope

`backend.causal_reasoner.assemble_evidence()` is an **offline, pure projection** of an
already completed Module 9 retrieval call. No code execution, network access,
provider/embedding calls, Chroma lease, Git invocation or source-file reads.
It does not implement Gemini generation, answers, redaction, API or UI.

## Calling contract

Call exactly one Module 9 public retrieval method, then pass all parts of
**that one returned result** into the assembler. Never combine results from
separate retrieval calls or invent graph context from a matching path.

```python
retrieved = retriever.search_with_runtime(
    namespace, question, 8, runtime_inventory,
    graph_revision=graph_revision, git_history=history_inventory,
)
bundle = assemble_evidence(
    namespace, question, retrieved.hits,
    graph_context=retrieved.graph_context,
    history=retrieved.history,
    runtime=retrieved.runtime,
)
```

The application is responsible for loading/authorizing the indexed repository
and server-owned inventories. This function validates shape, hashes, namespace,
rank and cross-references but **cannot cryptographically verify that the caller
actually invoked Module 9**. Module 9 owns the pinned publication lease and
source-revision verification. Do not expose `assemble_evidence` as a public API
accepting browser-supplied retrieval records.

## Label semantics

- `S1`, `S2`, ...: original retrieved source chunks and optional additional
  source-backed graph neighbor chunks. Hashes are rechecked, source content is
  kept intact (never truncated), and path plus chunk identity are preserved.
- `G1`, ...: graph relationship ID plus seed (`S...`) and related source
  (`source_label`), **only from Module 9's revision-bound graph result**.
- `H1`, ...: reported Git changes for selected source paths, unverified against
  the published source revision and not causal proof.
- `R1`, ...: sanitized frame metadata for selected source paths, unverified
  against the published source revision and not causal proof. Reported line
  numbers are never treated as authoritative source lines.

Labels are request-local. The registry is a tuple of frozen-slotted dataclasses;
`resolve(label)` returns only emitted labels. Omissions report bounds or
incomplete source inventories; empty hits never produce invented context.
The default policy selects up to 8 primary hits, 8 graph edges, 5 history
changes and 5 runtime frames per selected path, 64 records in total, and a
65,536-byte evidence record budget. Whole items are skipped when over budget.
Graph neighbor + edge admission is atomic (no orphan neighbor if the edge will
not fit). These are encoded byte bounds, **not a Gemini token estimate**.

## Privacy boundary / security warning

**This bundle contains unredacted source code. `provider_safe` is always
`False`. Do NOT send it to an LLM, browser, logger, telemetry, or external
service.** Increment 2 must introduce independent redaction and strict citation
validation before any model request. `repr()` intentionally omits raw content,
paths, namespace, and free-form context, but runtime memory still contains the
source. Applications must treat all code and metadata as untrusted data.

No source line ranges are inferred from Module 9 chunks. Graph edges denote
static code relationships, and historical/runtime associations are only clues.
No causal conclusion, claim-to-evidence semantic verification, or standalone
answer quality claim is made in this increment.

## Verification

```powershell
python -m pytest tests/test_module10_evidence.py -q -p no:cacheprovider
```

Before commit/merge, run the existing full offline suite on the Windows
project environment, including Module 9 real-Chroma tests, and review the final
staged diff. Existing Modules 1–9 files and their tests must remain unchanged.
