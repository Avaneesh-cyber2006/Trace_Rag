# Module 10 — Increment 6: Local React evidence workbench

## Verified scope and limitations

This increment adds `frontend/` (React + TypeScript + Vite, fixed-origin proxy) and an explicitly optional **offline synthetic** demo server (`scripts/module10_local_demo.py`). The UI implements the **existing** Increment 5 local-session API, with no backend API schema changes and no changes to Modules 1–9. The demo exercises the real one-retrieval/one-redaction/one-citation-validation pipeline with an in-memory *fake* retriever/provider; it is not real repository indexing, Gemini, or proof of causality.

The UI has a repository selector (server-registered entries only), a question form with UTF-8 byte count, 1–8 retrieval options, a **request-specific external-processing approval checkbox**, a cited answer display, explicit limitation and hypothesis panels, evidence navigation, and sanitized API error messages. All source and model text is rendered by normal React text nodes; never `dangerouslySetInnerHTML` or dynamic HTML. Code excerpts are shown as inert text and may be very long; no executable syntax highlighting or source-line claims are made. It explicitly marks Git/runtime observations as unverified.

Token is entered by the user and held only in React component memory (no `localStorage`, `sessionStorage`, cookie or URL parameter). Clear it by refreshing/closing the tab; changing it cancels any in-flight request and clears results. The code sends it only in an `Authorization: Bearer` header to a fixed same-origin `/api` endpoint. Browser debugging tools and extensions can still inspect in-memory data. The Vite proxy is **development-only** and should not be exposed outside the local machine. Browser origins are not authorization; backend bearer and repository binding are required.

The real API blocks external generation unless all three approval gates pass (server, repository, request). The UI never sets the first two. The optional demo explicitly allows them **only for a provider that does not make outbound requests**, so the checkbox exercises the request gate but no source data is transmitted to Gemini. For any real repository, obtain informed permission before enabling server and repository flags.

## Requirements

- Python virtual environment with Increment 5 optional FastAPI requirements installed.
- Node.js 22.12+ (or 20.19+) and npm. Dependencies are version-pinned in `frontend/package.json`. `npm install` creates `frontend/package-lock.json` on Windows; keep that file for final integration review. `frontend/node_modules/` and `frontend/dist/` are ignored.
- Local loopback ports **8000** and **5173** available. No additional database or network request for the demo. Initial npm package installation *does* require npm registry connectivity.

## Offline demonstration (PowerShell 5.1, two terminals)

In **Terminal A**, from `Trace_Rag_Module10_Worktree`:

```powershell
$repo = 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag_Module10_Worktree'
$python = 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe'
$bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$env:TRACERAG_DEMO_TOKEN = [Convert]::ToBase64String($bytes)
Set-Location $repo
& $python -m scripts.module10_local_demo
```

In **Terminal B**:

```powershell
Set-Location 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag_Module10_Worktree\frontend'
npm install --ignore-scripts
npm run typecheck
npm test
npm run build
npm run dev
```

Open `http://127.0.0.1:5173` in the browser. Terminal B does not receive the server token automatically: from Terminal A, copy the token value using `$env:TRACERAG_DEMO_TOKEN` in your trusted terminal and paste it into the **Session bearer token** field. Click Connect, select **Offline demo · synthetic code**, enter e.g. `What does calculate return?`, check the request approval, and Analyze. Click `S1` to see the synthetic source excerpt. No Google API key or external generation is used.

**Do not store the demo token in your repository, shell profile, history, logs, or screenshots.** Close the terminal to clear its environment variable. Do not use this demo script as a production launcher or to expose a real private indexed repository.

## Quality gates

- `npm run typecheck`, `npm test`, `npm run build` (React/TS tests for API contract, XSS-safe text, citation navigation, consent, changed token, error states).
- `python -m pytest tests/test_module10_local_demo.py` (real HTTP-to-orchestration, synthetic local evidence).
- Re-run the selected Module 10 + Module 9 real-Chroma regression suite after adding this increment.
- No real Gemini calls; no automatic repository ingestion, secret storage, source-line assertions, or production deployment.

**Increment 7:** full offline regression, API/browser smoke with actual local server, supply-chain/build review, staged diff review before local commit. No merge or push during Increment 6.
