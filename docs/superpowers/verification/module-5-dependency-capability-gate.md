# Module 5 Dependency and Capability Gate

## Result

**PASS — REVISED VECTOR REPRESENTATION**

Verified on 2026-09-03 and revised on 2026-09-04 after Task 10 executable review. The dependency capability remains a pass under the clarified vector-representation contract below. Task 10 implementation and independent re-review subsequently proved that the unapproved vector mirror is absent, binary32 projection is checked before mutation, and Chroma's embedding field is the sole persisted vector authority. Tasks after Task 10 may proceed under this revised gate.

## Environment and Dependency Pins

- Python: `3.12.13` (`CPython`, Windows x86-64)
- Existing project dependency source: `requirements.txt`
- Official Gemini Python SDK: `google-genai==2.22.0`
- Import surface: `from google import genai`; `from google.genai import types, errors`
- ChromaDB: `chromadb==1.5.9`
- Chroma import surface: `import chromadb`

The pins installed together with the existing requirements in a new temporary virtual environment. Imports succeeded and `python -m pip check` reported `No broken requirements found.` No unrelated direct requirement change is needed. Transitive packages are resolver-owned and are not pinned directly by this milestone.

## Gemini Embedding Configuration

### Selected compatible embedding space

- Provider identity: `gemini`
- Model: `gemini-embedding-001`
- Dimensions: `3072`
- Compatibility configuration version: `gemini-embedding-001-retrieval-3072-v1`
- Document mode: `types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT", output_dimensionality=3072)`
- Query mode: `types.EmbedContentConfig(task_type="RETRIEVAL_QUERY", output_dimensionality=3072)`

The official embeddings guide lists `gemini-embedding-001` as a stable text model, supports `RETRIEVAL_DOCUMENT` and `RETRIEVAL_QUERY`, supports dimensions 128–3072, recommends 768/1536/3072, and states that default 3072-dimensional embeddings are normalized. The 3072-dimensional default avoids the documented manual re-normalization requirement for reduced-dimension `gemini-embedding-001` output.

`gemini-embedding-2` was not selected. Its current official retrieval guidance requires different text prefixes for documents and queries and does not support `task_type`; adding those prefixes would change the approved byte-exact `tracerag-embedding-document-v1` provider input.

### Limits relied upon

- Official input limit: 2,048 tokens per `gemini-embedding-001` input.
- Official output range: 128–3072 dimensions; Module 5 fixes 3072.
- Synchronous SDK API: `client.models.embed_content(...)`.
- SDK batch response ordering: embeddings are returned in request order.
- V1 safe batch capacity: one document per request. The official synchronous batch reference does not publish a maximum request count, so Module 5 does not assume one.
- V1 preflight request-size cap: 1,536 UTF-8 bytes for the complete rendered embedding document or unchanged query text. This conservative adapter-owned bound stays below the 2,048-token model ceiling without adding a tokenizer and leaves safety margin for tokenizer control pieces. Larger inputs fail locally with the typed document/request-size error; they are never truncated.

The Gemini Developer API path in `google-genai==2.22.0` rejects the SDK's `auto_truncate` option as Enterprise-only. The adapter therefore does not request truncation, uses the conservative complete-input byte cap above, and treats an API size rejection as permanent. A future larger bound requires separate tokenizer/API evidence and an embedding compatibility decision if it changes accepted corpus behavior.

### Client construction and credential boundary

`genai.Client(api_key=...)` constructs without making a network request. Task 1 proved this with HTTP requests patched to fail. Credentials remain explicit external input. Module 5 must not use the SDK's environment discovery path, search repository configuration, or expose the client/API key through identity, metadata, logs, or exceptions.

### Verified SDK failure surfaces

`google.genai.errors.APIError` exposes numeric `code`; `ClientError` represents 4xx responses and `ServerError` represents 5xx responses. The adapter classification is:

- `401`, `403` `ClientError` → authentication, permanent;
- `408` `ClientError` → transient, retryable;
- `413` `ClientError` → document/request too large, permanent;
- `429` `ClientError` → rate limit, retryable;
- other `ClientError` values, including invalid model/configuration/request → invalid request/configuration, permanent;
- `500`, `502`, `503`, `504` `ServerError` → transient, retryable;
- other `APIError`/`ServerError` values → provider error, permanent unless a later official contract explicitly classifies them;
- `httpx.TimeoutException` and `httpx.ConnectError` from the pinned SDK transport → transient, retryable;
- `errors.UnknownApiResponseError`, missing embeddings, cardinality mismatch, invalid dimensions, non-numeric/non-finite values → invalid response, permanent; and
- local SDK `ValueError` raised while validating known request/configuration inputs → invalid request/configuration, permanent.

Retry decisions use types plus numeric codes, never message substrings. Module 5 supplies its own bounded retry policy; the pinned SDK defaults to one attempt when no `HttpRetryOptions` is configured.

## Chroma Capability Decisions

### Persistent external-vector storage

`chromadb.PersistentClient(path=<external path>, settings=Settings(anonymized_telemetry=False))` persisted synthetic embeddings, exact documents, and metadata across a fresh Python process. Collections created with `embedding_function=None` and explicit embeddings did not invoke an embedding function. Reopening used `get_collection(name, embedding_function=None)`.

The caller must supply persistence outside the analyzed repository. Module 5 never uses Chroma's default `./chroma` path.

### Revised authoritative vector representation

ChromaDB 1.5.9 persists and returns finite component values in a binary32-valued representation. Exact Python-float round-trip is not a supported invariant. Module 5 therefore validates the provider vector first, performs a checked component-wise IEEE-754 binary32 projection before a Chroma write, and rejects a finite Python float if its projection is non-finite. It does not clip or normalize vectors.

The Chroma embedding field is the sole persisted vector authority. Manifest reads return Chroma's finite, dimension-valid persisted values; no JSON metadata field, pointer/control file, or second per-chunk manifest may mirror the original vector. Search consequently operates on the persisted Chroma representation.

A fresh-process executable probe wrote already projected vectors and reopened the collection in a child Python process. Its cases showed:

- exactly representable `(0.5, -0.25, 0.125)` reopened exactly;
- ordinary non-unit `(0.1, -0.2, 0.3)` did not equal the original, and returned components such as `0.10000000894069672` differed from the submitted binary32 projection `0.10000000149011612` by a small number of binary32 ULPs;
- negative non-unit `(-0.7, -1.1, 0.9)` likewise included a returned component a few binary32 ULPs from the submitted projection;
- small-magnitude `(1e-30, -3e-20, 2e-10)` and representative normalized-like `(0.5773502691896258, -0.5773502691896258, 0.5773502691896258)` reopened consistently as their binary32-valued representations; and
- self-query cosine distances over those persisted values ranged from `-2.384185791015625e-07` through `1.1920928955078125e-07`, demonstrating a small floating boundary error around mathematical zero.

This evidence distinguishes exact source/evidence persistence from derived-vector numeric representation: Chroma documents must still reproduce exact `CodeChunk.content` and metadata must still reproduce exact provenance. The selected provider compatibility identity remains `gemini-embedding-001-retrieval-3072-v1`, because it captures provider/model/task/dimension semantics. Store representation is owned solely by `tracerag-chroma-schema-v1`. As Module 5 is pre-release, this is a clarification of schema V1 rather than a data migration or compatibility-version change.

### Collection identifiers

The pinned runtime enforces 3–512 characters; characters are `[a-zA-Z0-9._-]`; the first and last are alphanumeric; consecutive periods and IP-address names are rejected. Module 5 uses the stricter safe subset:

```text
tr5-<64 lowercase hexadecimal namespace digest>-<private lowercase generation token>
```

The full original repository namespace remains in control and record metadata and is validated on every read, so the digest is never the sole identity authority.

### Metadata and null encoding

The pinned native boundary accepts scalar string, integer, float, and boolean values and homogeneous scalar lists. Although its Python type alias includes `None`, an actual add with a `None` metadata value raises `TypeError`.

Module 5 therefore encodes optional `symbol_kind`, `qualified_name`, and `parent_qualified_name` by omitting the corresponding metadata key when the value is `None`. A present key must contain a valid nonempty string. Missing decodes to `None`; no sentinel can collide with repository-controlled text.

### Metric, distance, and public score

Collections use:

```python
configuration={"hnsw": {"space": "cosine"}}
```

Official Chroma documentation defines returned cosine distance as:

```text
d = 1 - cosine_similarity
```

The executable prototype returned approximately `0.0`, `1.0`, and `2.0` for identical, orthogonal, and opposite unit vectors. Because cosine similarity lies in `[-1,1]`, Module 5 converts distance to the required public range with:

```text
score = 1 - (distance / 2)
```

Thus distance `0 → 1.0`, `1 → 0.5`, and `2 → 0.0`; higher is more similar. The existing executable capability test proves an absolute `1e-6` tolerance, and the revised fresh-process cases observed endpoint error no larger than `2.384185791015625e-07`. A finite distance within `1e-6` of `0` or `2` is treated as that endpoint before applying the unchanged formula. Non-finite values or distances farther outside `[0,2]` are corruption; arbitrary invalid values are never silently clipped.

## Durable Active-generation Publication

### Mechanism

Each repository uses isolated immutable candidate collections plus a small checksum-protected JSON active-pointer file under the caller-supplied persistence control root. The pointer contains exactly schema version, complete repository namespace, and private active collection locator. Publication is:

1. completely write and validate the private candidate collection;
2. serialize canonical compact JSON plus SHA-256 checksum to a same-directory temporary pointer;
3. flush the temporary file and call `os.fsync` on its handle;
4. replace the active pointer with same-filesystem `os.replace`; and
5. reread and validate the durable pointer to resolve the publication outcome.

The `os.replace` is the logical commit point. This mechanism uses only small storage-control metadata, not a second per-chunk manifest. Vector records remain the synchronization manifest.

### Failure and boundary results

- Failure before `os.replace`: the previous pointer and collection remain authoritative; the candidate is unreachable.
- Process exit immediately before `os.replace`: restart reads the complete previous pointer.
- Process exit immediately after `os.replace`: restart reads the complete new checksummed pointer.
- Exception with uncertain local outcome: reread the pointer. Report success only if it identifies the fully validated candidate; otherwise raise and retain the old authority. Missing, malformed, checksum-invalid, namespace-mismatched, or missing-target pointers fail closed as corruption.
- Publication success followed by cleanup failure: the new pointer remains authoritative. Old/candidate cleanup is maintenance and is not part of correctness.
- Restart with abandoned candidates or obsolete collections: only the checksummed pointer selects authority; timestamps and collection ordering are ignored.

The executable tests used subprocess exits at both sides of the replacement boundary and proved an unambiguous old-or-new pointer after restart. They did not claim distributed-filesystem durability; V1 supports a local persistence root on one filesystem.

## Concurrency Result

- Same repository namespace: one in-process nonblocking writer guard from active inspection through publication outcome resolution. A second writer fails with a typed busy/configuration error.
- Different namespaces: independent in-process guards; Chroma operations remain subject to the single local client/process deployment below.
- Readers: a search resolves one active pointer and retains that immutable collection snapshot. Candidate collections are invisible. Old collections are retained until in-process readers release them; cleanup cannot invalidate an active read.
- Multi-process mutation/read sharing: not supported in V1 and must be rejected/documented. Current Chroma local persistence documentation does not provide the transaction/snapshot contract needed by this design, and concurrent `PersistentClient` behavior has unresolved upstream locking reports. V1 uses one owning process and does not invent a distributed lock.

## Commands and Evidence

Baseline:

```powershell
Python 3.12.13
python -m pip check
python -m pytest -q -rs
```

Baseline result: `575 passed, 3 skipped, 1 deselected`; skips were Windows symlink-privilege cases.

Clean gate environment:

```powershell
python -m venv C:\Users\avane\AppData\Local\Temp\tracerag-module5-gate-20260903-01
python -m pip install -r requirements.txt
python -m pip install google-genai==2.22.0 chromadb==1.5.9
python -m pip check
python -c "from google import genai; from google.genai import types; import chromadb"
python -m pytest tests/capability/test_module5_dependency_capabilities.py -q -rs
```

Final gate result: `22 passed`; one Chroma deprecation warning notes its legacy embedding-function configuration loader while the current documented `embedding_function=None` behavior remains functional. `pip check` reports no broken requirements.

Task 10 representation probe:

```powershell
& 'C:\Users\avane\AppData\Local\Temp\tracerag-module5-gate-20260903-01\Scripts\python.exe' .superpowers/sdd/2026-09-03-embedding-vector-store/task-10-f32-probe.py
```

The probe used ChromaDB 1.5.9, inserted already projected synthetic values, and delegated reopen/read/query to a new Python process. The five cases and observed values are recorded in “Revised authoritative vector representation” above. The original capability suite's `1e-6` distance tolerance covers the maximum observed self-distance boundary error. Task 10 converted this evidence into tracked production and regression behavior; its independent scoped re-review reported no Critical or Important findings.

## Official Sources Consulted

Accessed 2026-09-03:

- Google Gemini embeddings guide: https://ai.google.dev/gemini-api/docs/embeddings
- Google Gemini embeddings API reference: https://ai.google.dev/api/embeddings
- Official Google Gen AI Python SDK reference: https://googleapis.github.io/python-genai/
- Official Google Gen AI Python SDK repository/source: https://github.com/googleapis/python-genai
- Chroma collection management: https://docs.trychroma.com/docs/collections/manage-collections
- Chroma collection configuration and distance definitions: https://docs.trychroma.com/docs/collections/configure
- Chroma Python collection reference: https://docs.trychroma.com/reference/python/collection
- Chroma Python client reference: https://docs.trychroma.com/reference/python/client
- Chroma persistent-client guide: https://docs.trychroma.com/docs/run-chroma/clients
- Chroma official repository and concurrency issue evidence: https://github.com/chroma-core/chroma

No real Gemini request was made. No credential, repository source chunk, real provider vector, or raw secret-bearing response is present in this artifact; all displayed vector values are fixed synthetic capability inputs.
