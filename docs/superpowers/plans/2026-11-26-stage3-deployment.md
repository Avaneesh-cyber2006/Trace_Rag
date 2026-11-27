# Stage 3 Deployment Implementation Plan

**Status:** proposed roadmap only; no implementation or deployment authorized.
**Goal:** make TraceRAG deployable with explicit identities, provider consent,
reproducible indexes, bounded resource use, and verifiable evidence.
**Architecture:** retain the provider-neutral generation boundary and independent
citation validator. Introduce authorization and provider policy before external
work; run indexing separately from request serving with exclusive store ownership.
**Tech stack:** existing Python/FastAPI, React, and Chroma. Provider runtimes,
identity service, secret backend, hosting platform, and CI service remain decisions
for the implementation design; this roadmap does not select paid services.
**Spec:** accepted Stage 2 engineering report and the user's release-review scope.

For implementation, use the executing-plans skill after owner approval. Treat
each phase as a separately reviewable change with failing security/contract tests
first, implementation second, and review before integration. Commits and external
processing still require the applicable owner authorization.

## Constraints and gates

- Never relax strict parsing, exact excerpt validation, evidence kinds or privacy.
- Never silently send data to a new destination on fallback, including a local
  runtime whose endpoint or telemetry sends data outside the approved boundary.
- No reindexing, cloud calls, new credentials, container deployment or public
  exposure is authorized by this document.
- Preserve the pilot index. Use separate model/version namespaces for new indexes.
- Stage 2 provider outages are accepted operational limitations, not solved faults.

## Ordered work packages

### 1. Deployment design and threat model

- [ ] Document users/tenants, repository ownership, data classifications, retention,
  deployment topology, latency/cost objectives and trusted network boundaries.
- [ ] Decide single-owner Chroma service versus an approved concurrent-serving
  alternative. Define backup, restore, migrations and rollback before data moves.
- [ ] Deliver `docs/deployment-design.md` with abuse cases and acceptance criteria.
  Review cross-tenant leakage, prompt injection, SSRF, secrets and runaway cost.

### 2. Production authentication and authorization

- [ ] Introduce `backend/api/auth.py` and repository authorization policy; integrate
  with `backend/api/app.py`. Replace the shared local session bearer with verified
  user/service identity, expiration, revocation and least-privilege repository access.
- [ ] Decide OIDC/session flow, CSRF protection where cookies are used, strict
  CORS, trusted proxies and TLS termination. Never trust tenant/repository IDs alone.
- [ ] Test anonymous denial, cross-tenant access, expired/revoked identities,
  consent revocation during queued work, and no outbound calls on denied requests.

### 3. Multi-provider generation and consent-aware fallback

- [ ] Extend `backend/causal_reasoner/providers/` with separate local/cloud adapters
  implementing the existing `ReasoningProvider` contract. Keep SDK details at edges.
- [ ] Add capability metadata: schema support, context/output limits, destination,
  model version, telemetry/data handling, timeout and cost characteristics.
- [ ] Add provider routing policy outside adapters. Consent must cover repository,
  operation, destination, data category and model policy. Local-only requests stay
  local. A new destination requires new consent; unavailability alone grants none.
- [ ] Define total request deadline, cost/attempt cap, cancellation and circuit
  breakers across retries and fallbacks. Default schema failures to fail closed;
  any repair strategy needs a separately approved, tested budget.
- [ ] Test local/cloud contract parity, malformed output, prompt injection,
  denied fallback, exhausted budgets, unavailable local runtimes and cancellation.
  Apply the same independent citation validation to every provider.

### 4. Local embeddings and full repository indexing

- [ ] Add a local embedding adapter under `backend/embedding_vector_store/providers/`.
  Pin model/version/dimensions and retrieval task semantics. Never mix incompatible
  vectors in the same namespace or silently swap query embedding models.
- [ ] Evaluate retrieval quality on approved representative code queries, including
  lexical-only terms, missing evidence and language coverage; define acceptance
  thresholds before benchmarking. Record hardware/memory and offline behavior.
- [ ] Add an indexing job entry point using existing scanner/parser/chunker modules.
  Require repository scope approval, exclusions/secret filtering, content and
  revision identity, incremental updates, deletion handling and atomic publication.
- [ ] Test interrupted jobs, stale revisions, duplicate requests, crash recovery,
  exclusive ownership and rollback without touching the 12-chunk pilot namespace.
- [ ] Release gate: approved full-repository coverage report and retrieval evaluation,
  not merely a successful index creation call.

### 5. Secret management and bounded operations

- [ ] Inject server credentials through an approved secret manager or deployment
  secret mount; never bake them into images, client bundles or logs. Define rotation,
  expiry, revocation, audit access and redacted startup failures.
- [ ] Replace process-local request throttling with deployment-appropriate per-user,
  tenant and provider limits, concurrency caps, queue bounds and token/cost budgets.
- [ ] Instrument safe request IDs, provider status categories, retry counts, latency,
  validation failures, index revision and budget consumption. Avoid source text,
  prompts, credentials, response bodies and high-cardinality sensitive labels.
- [ ] Test saturation, malicious large requests, metric/log leakage, secret rotation
  during requests, provider outages and budget races across concurrent workers.

### 6. Docker and CI/CD

- [ ] Add reviewed backend/frontend Dockerfiles, `.dockerignore`, and local Compose
  configuration only after topology is approved. Use pinned dependencies,
  non-root processes, minimal runtime images, read-only filesystems where feasible,
  explicit persistent volumes, resource limits and graceful shutdown.
- [ ] Match volume ownership and process count to the store ownership model. Do not
  scale workers against one embedded Chroma directory without a supported design.
- [ ] Add CI for backend tests on Windows and Linux, frontend tests/typecheck/build,
  dependency/security scanning, secret scanning, image/SBOM checks and artifacts.
  No live Gemini tests or secrets in untrusted pull-request jobs.
- [ ] Separate build, staging, migration and production promotion. Require explicit
  approvals and short-lived deployment credentials; preserve immutable artifacts
  and a tested rollback path. Live-provider tests remain manually budgeted.

### 7. Deployment security and end-to-end sign-off

- [ ] Restrict ingress/egress, validate provider endpoint allowlists and TLS,
  disable debug interfaces, set safe headers, configure retention/backups and
  exercise incident/credential-revocation procedures.
- [ ] On an approved staging deployment, test real login, repository authorization,
  provider-specific consent, retrieval, answer rendering and citation navigation in
  a supported browser. Verify exact source/revision provenance, not just HTTP 200.
- [ ] Cover insufficient evidence, redaction, malicious source text, 401/403/429,
  provider 504s, fallback denial, browser reload/logout, cancellation and token expiry.
- [ ] Test restore/reindex/rollback and measure load/latency/cost with explicit budgets.
  Approve public deployment only after all gates and remaining risks are accepted.

## Deliverable and approval boundaries

Each phase produces its tests, operator documentation and a reviewable diff.
Resolve architecture choices before implementing dependent phases. Provider outage
tolerance must be measured rather than inferred from retries. Stage 3 planning is
ready; production release is blocked on the gates above and the outstanding live
browser sign-off. No phase has been implemented by this release review.
