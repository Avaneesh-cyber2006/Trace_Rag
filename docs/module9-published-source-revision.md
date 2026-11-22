# Module 9 — Increment 6: published original-file source revisions

Increment 5 already rejects graph expansion when published chunks cannot reconstruct
all original source bytes. This increment adds an **opt-in, persisted** provenance
contract that permits ordinary Module 4 chunk gaps without ever reading live
working-tree bytes at retrieval time.

## Indexing and graph construction

Given matched, trusted Module 2/3/4 inventories captured from the same input
revision, index through:

```python
indexer.synchronize_with_sources(files, parsed, chunks)
revision = build_graph_revision(
    files, parsed, chunks, require_published_source=True,
)
```

The source-revision builder validates the Module 6 scanner/parser input
relationship, namespace and root consistency, the exact parsed/chunked file
set, successful/partial parse states, matching original-file SHA-256 from Modules
3 and 4, every source span's byte boundary, each chunk's UTF-8 byte length
and content digest, and the complete chunk manifest fingerprint. The original
per-file source SHA-256 is **not** computed by guessing omitted text or by
rereading a potentially newer file. Inputs must be captured in a trusted,
same-revision indexing pipeline: Python dataclasses can be forged by an
untrusted caller, and this is not a cryptographic authentication signature.

The source evidence is a bounded, canonical UTF-8 JSON string stored **inside
the immutable Chroma collection's metadata**, keyed by `source_revision`.
No auxiliary sidecar or independent active pointer is introduced. Publication
still commits through Module 5's original atomic active-collection pointer.
An existing collection without that metadata remains an ordinary unprovenanced
index. Data remains under the existing Chroma schema version; the new control
key is optional and validated if present. A source proof is tied to the
repository namespace, complete chunk manifest fingerprint, and every scanned
source file's original SHA-256, including files with no published chunks.

`SemanticIndexer.synchronize(chunks)` retains the original unprovenanced API.
Calling it on a previously provenanced index deliberately publishes an
**unprovenanced** generation, even when all chunk identities are unchanged;
otherwise provenance would be silently carried over without matching source
inputs. Conversely, `synchronize_with_sources()` publishes a new generation
if source bytes change outside all stored chunks, reusing existing embeddings
when chunk identities and embedding model are unchanged. An identical chunk
and source revision returns `UNCHANGED`.

## Retrieval and failure policy

`HybridRetriever.search_with_graph(..., revision)` holds one Module 5 lease
across the manifest read, provenance check, embedding, semantic search, and
graph expansion. A graph constructed with `require_published_source=True`
requires **exact equality** with the pinned collection's source revision.
The validated, complete published chunk manifest must also match the source
revision's chunk fingerprint; the stored per-file hashes must match those used
to rebuild the graph. Any missing, legacy, malformed, stale, foreign, or
inconsistent proof rejects graph expansion **before** embedding.

Legacy calls to `build_graph_revision(files, parsed, chunks)` retain Increment
5's strict gapless original-byte reconstruction requirement. When pointed at
an index that *does* contain a persisted source revision, even these legacy
proofs must match the pinned source hashes, preventing a fully-covered graph
from being used against an index built with changed omitted bytes.

No graph serialization/registry, automatic graph restoration, or real-file read
is introduced. A caller must still retain or rebuild the graph revision from
trusted captured inputs. `HybridRetriever.search()` without graph remains
available for legacy/unprovenanced indexes.

## Resource and trust limits

- At most 8,192 scanned source files and 8,192 indexed chunks.
- At most 2 MiB canonical source-revision metadata per index generation.
- Relative source paths and lowercase 64-character SHA-256 values are checked.
- The Chroma collection and pointer are trusted local storage, not an
authenticated tamper-resistant repository. An attacker who can alter the
underlying database and reconstruct checksums can forge provenance.
- A separate already-published graph from an unrelated revision is never
accepted based only on matching symbol names, paths, or chunk subsets.

## Verification

`tests/test_source_revision_persistence.py` tests gap acceptance only when
source evidence is published, missing/changed original-file digests, same
chunks with different omitted bytes, legacy guardrails, strict canonical
manifest decoding, and the opt-in indexer's publish/reuse/downgrade behavior.

`tests/test_source_revision_chroma.py` exercises real local Chroma persistence,
reopened adapters, unchanged sync, same-chunk source-only drift, provenance
downgrade, missing older proof rejection, and candidate corruption rejection.
These tests use only offline deterministic embedding vectors.

## Module 5 compatibility hardening

Module 5's original `VectorStore.begin_candidate` signature and the exact frozen
`RepositoryIndexSnapshot` / `CandidateIndex` dataclass fields are unchanged.
Original-file provenance is an **opt-in adapter extension**, carried by an
adapter-owned generation token and persisted in that generation's control
metadata. `ChromaVectorStore.begin_candidate_with_source_revision(...)` is used
only by `SemanticIndexer.synchronize_with_sources(...)`; ordinary indexing keeps
its canonical lifecycle. Stores without the extension reject proof-bearing
publication rather than silently dropping evidence. The source-revision builder
validates scanner/parser/chunker associations independently and never imports
Module 6 from the Module 5 package; Module 9 graph building still runs the
independent full Module 6 validator before graph provenance is trusted.
