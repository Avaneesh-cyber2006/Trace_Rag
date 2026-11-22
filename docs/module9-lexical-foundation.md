# Module 9 — Lexical retrieval foundation (increment 1)

Status: **development only**, not a complete Module 9 integration.

This increment adds an immutable, in-memory, repository-scoped BM25 search index.
It accepts *explicitly supplied, already validated* source-chunk evidence and
returns metadata-preserving ranked hits. It does not open files, query Chroma,
read Git history, process runtime logs, execute source code, or fetch data.

## Public interface

```python
from backend.retrieval import LexicalDocument, LexicalIndex

index = LexicalIndex(repository_namespace, tuple_of_lexical_documents)
hits = index.search("exception handler", top_k=10)
```

Each `LexicalDocument` carries the repository namespace, chunk ID, content hash,
relative source path, language, chunk/symbol kind, qualified names and content.
A `LexicalHit` preserves those identifiers and adds a positive BM25 score and
one-based rank. **BM25 scores are not probabilities** and must not be directly
compared with Module 5's normalized vector scores; rank fusion belongs to a
subsequent increment.

## Boundaries and assumptions

- Inputs are records from one trusted, consistent published repository snapshot.
  This increment does not validate authenticity against Chroma or pin a vector
  generation. The future hybrid orchestrator must pin one active generation,
  check evidence identity, and rebuild its lexical index on publication changes.
- Duplicate chunk IDs, mixed repository namespaces, invalid relative paths,
  invalid lowercase SHA-256 identifiers, invalid metadata, and excessive input
  sizes are rejected before building postings.
- Maximum 8,192 documents, 16,384 source characters per document, and
  16,777,216 source characters in one index. Query maximum is 4,096 characters;
  `top_k` must be an integer between 1 and 100.
- Tokens are normalized with Unicode NFKC and casefolding, split on underscores,
  and common ASCII camel-case boundaries. No stemming, fuzzy matching or
  language detection is performed. Repeated query terms count only once.
- Ranking uses BM25 with `k1=1.2` and `b=0.75`; ties sort by chunk ID. Ranking is
  deterministic regardless of source-document input order.
- Error messages and default model reprs avoid source content, but the caller
  owns and must protect source text, returned hits and in-memory index data.

## Deferred work

This does **not** implement Module 5 snapshot orchestration, persisted lexical
indexes, hybrid/RRF fusion, graph expansion, relevance thresholds, reranking,
causal claims, or LLM context assembly. Each needs its own tests and API review.

## Verification

Run in the dedicated Module 9 worktree:

```text
python -m pytest tests/test_lexical_retrieval.py -q -p no:cacheprovider
```

The first increment is ready for review once tests pass on Windows Python 3.12;
it must not be merged into `main` merely because its isolated tests pass.
