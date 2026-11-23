# Module 10 — Increment 5: Local FastAPI boundary

## Scope

An explicit app factory (`backend.api.create_app`) exposes the *already validated* Module 10 one-shot answer pipeline. It does not register/index arbitrary repositories, read browser-supplied local file paths, accept Git/runtime inventories from HTTP, initialize a Gemini client, or make a network request at startup. No changes to Modules 1–9.

Endpoints:

- `GET /api/v1/health`: content-free local readiness response, no credentials or configured repository names.
- `GET /api/v1/repositories`: bearer-authenticated, lists only in-process registered repository IDs and display names. Does not expose namespaces, local paths, or inventory objects.
- `POST /api/v1/answer`: bearer-authenticated, strictly validated JSON containing `repository_id`, `question`, optional integer `top_k` (1–8) and **explicit** `approve_external_processing` Boolean. Rejects all extra fields. Produces a structured answer with admitted redacted citation items and safe warnings, or sanitized HTTP error. No HTML is rendered by the API.

`POST /api/v1/answer` **never** accepts a repository path, Git URL, provider key, raw repository inventory, arbitrary model name, or prompt instructions. The repository ID is resolved in the authenticated server-side registry. The answer request traverses one Module 9 retrieval, evidence assembly, redaction, one generation call (only after all approvals), and server-owned citation validation, as verified in Increment 4.

## Security contract

`create_app` requires an `ApiSecurity` object with a nontrivial server-only bearer token and explicit `RepositorySession` bindings. **Generation remains disabled by default.** All three gates are required to invoke the answer service: `ApiSecurity.allow_external_processing=True`, the selected `RepositorySession.external_processing_permitted=True`, and the individual request's `approve_external_processing=true`. The Gemini adapter has its own separate opt-in `GeminiSettings.enabled` check. Neither a fake provider nor Gemini is invoked on denied requests.

- Authentication: bearer header, constant-time comparison, no query-string token or cookie. The session token grants access to all repositories registered in this *one* local application instance. It is **not** production user authentication or a multi-tenant authorization model.
- Browser origins: none allowed by default; optionally choose from the explicit localhost development origins. Strict host policy allows `localhost`, `127.0.0.1`, and `testserver` for offline tests. CORS is **not** authorization.
- Requests: <=12 KiB JSON by default (checked while reading streams); duplicate JSON keys, unexpected fields, invalid types, and NaN are rejected. Query <=4,096 UTF-8 bytes and `top_k <=8`. Fixed in-memory per-app 60-second rate window, default 30 attempts. This is not a distributed DoS safeguard.
- Responses: JSON only, `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`. Source raw hashes, repository namespaces and server paths are deliberately omitted. The citation content is redacted **heuristically**, not guaranteed nonsensitive.
- Errors: fixed error codes. No SDK errors, snippets, API keys, internal paths, or untrusted request bodies are returned in error payloads. Provider/draft problems -> 502; source/provenance problem -> 409; unapproved -> 403; invalid request -> 422. No automatic retry.

**Local-only policy:** Bind Uvicorn to `127.0.0.1` and never expose this development API through a public IP, reverse proxy or LAN. The `TrustedHostMiddleware` and bearer header do not provide a production internet threat model. Before remote/shared deployment, add authenticated identities, per-repository role checks, TLS, durable/per-user consent records, audit controls, a distributed rate limiter, resource lifecycle management, and stronger sensitive-content classification.

## Dependency installation

Optional API packages are in `requirements-api.txt`. This intentionally does not modify the pinned Module 5/9 `requirements.txt`. If needed, install into the existing Python virtual environment:

```powershell
& $python -m pip install -r requirements-api.txt
```

Use only an explicitly configured **server-side** app factory. No global `app` is exported for accidental startup. Example for an already constructed, authorized `retriever` and `provider`:

```python
import os
import uvicorn
from backend.api import ApiSecurity, RepositorySession, create_app

app = create_app(
    security=ApiSecurity(
        bearer_token=os.environ["TRACERAG_LOCAL_API_TOKEN"],
        allow_external_processing=False,  # no provider requests by default
        allowed_origins=("http://127.0.0.1:5173",),
    ),
    repositories=(RepositorySession(
        repository_id="repo1",
        display_name="Selected indexed repository",
        repository_namespace=authorized_namespace,
        retriever=retriever, provider=provider,
        external_processing_permitted=False,
    ),),
)
uvicorn.run(app, host="127.0.0.1", port=8765)
```

This code is a *configuration illustration*, not a runnable repository onboarding command: the authorized namespace, retriever and provider must already exist in the server's trusted application layer. Generating a random token, configuring Gemini credentials and recording informed repository-owner consent remain explicit setup tasks. The React UI and local session onboarding are later work.

## Verification and limitations

`tests/test_module10_api.py` uses a fake service for API-only validation; `tests/test_module10_api_integration.py` invokes the actual `answer_question` orchestration with fake Module 9 and provider dependencies. They cover opt-in denial before retrieval, unknown repository IDs, content-type/body limits, duplicate JSON fields, CORS and trusted origins, rate limiting, invalid model citations, real abstention, provenance failures and sanitized provider errors. Neither test sends data to Gemini.

Run all Increment 0–5 tests plus selected Module 9/Module 5 compatibility and real-Chroma tests on Windows. Keep Module 10 changes uncommitted until the final staged review. Client-side safe rendering, citation navigation and end-to-end accessibility belong to Increment 6; multi-user production hardening is not complete in this increment.
