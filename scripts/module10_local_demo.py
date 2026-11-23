"""Optional loopback-only TraceRAG demo with synthetic indexed code, no Gemini.

Usage: set TRACERAG_DEMO_TOKEN (>=24 printable ASCII chars) in the terminal,
then run `python -m scripts.module10_local_demo` from the Module 10 worktree.
The bearer token is never printed or written to disk by this module.
"""

from __future__ import annotations

from hashlib import sha256
import os

from backend.api import ApiSecurity, RepositorySession, create_app
from backend.causal_reasoner import parse_answer
from backend.retrieval.hybrid import HybridHit

_DEMO_NAMESPACE = "tracerag/local-demo"
_DEMO_PATH = "examples/demonstration.py"
_DEMO_CONTENT = "def calculate():\n    return 7\n"


class _DemoRetriever:
    def search(self, namespace: str, query: str, top_k: int):
        if namespace != _DEMO_NAMESPACE or not query.strip() or not 1 <= top_k <= 8:
            return ()
        return (HybridHit(
            _DEMO_NAMESPACE,
            sha256((_DEMO_PATH + _DEMO_CONTENT).encode()).hexdigest(),
            sha256(_DEMO_CONTENT.encode()).hexdigest(),
            _DEMO_PATH, "python", "symbol", "function", "calculate", None,
            _DEMO_CONTENT, 0.05, 1, 1, 1,
        ),)


class _OfflineProvider:
    def generate(self, request):
        if not request.external_processing_approved:
            raise ValueError("Local demo request was not approved.")
        # Deliberately fixed demonstration text, NOT a real causal explanation.
        return parse_answer({
            "status": "answered",
            "overview": "Demo only: the synthetic indexed function contains a literal return value.",
            "claims": [{
                "text": "The indexed demonstration code contains a return statement.",
                "category": "indexed_fact",
                "citations": ["S1"],
                "excerpt": "return 7",
            }],
            "hypotheses": [],
            "limitations": [
                "This is an offline synthetic example; no real repository was analyzed.",
                "Indexed source text does not establish what executed at runtime.",
            ],
        })


def create_demo_app(token: str):
    """Build real Module 10 orchestration over in-memory synthetic evidence."""
    return create_app(
        security=ApiSecurity(
            bearer_token=token, allow_external_processing=True,
            allowed_origins=("http://127.0.0.1:5173", "http://localhost:5173"),
        ),
        repositories=(RepositorySession(
            repository_id="offline-demo", display_name="Offline demo · synthetic code",
            repository_namespace=_DEMO_NAMESPACE,
            retriever=_DemoRetriever(), provider=_OfflineProvider(),
            external_processing_permitted=True,
        ),),
    )


def main() -> None:
    token = os.environ.get("TRACERAG_DEMO_TOKEN", "")
    if not token:
        raise SystemExit("Set TRACERAG_DEMO_TOKEN in this local terminal before starting the demo.")
    # Import only when launching, never during an offline test import.
    import uvicorn
    app = create_demo_app(token)
    print("Offline TraceRAG demo listening on 127.0.0.1:8000. No Gemini client is created.")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
