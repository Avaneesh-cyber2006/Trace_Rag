"""Real Chroma smoke integration: one retrieval, offline generation, cited answer."""

import pytest

pytest.importorskip("chromadb")

from backend.causal_reasoner import AnswerStatus, answer_question, parse_answer
from backend.embedding_vector_store.stores.chroma import ChromaVectorStore
from backend.retrieval import HybridRetriever, RelevancePolicy, build_graph_revision
from backend.embedding_vector_store.synchronization import SemanticIndexer
from test_hybrid_orchestrator_chroma import OfflineProvider, _publish, _record, NAMESPACE
from test_module10_causal import Provider
from test_source_revision_chroma import LocalIndexerProvider
from test_source_revision_persistence import with_omitted_suffix
from test_graph_provenance import NAMESPACE as GRAPH_NAMESPACE, same_file_call_bundle
from test_git_history_models import _change, _inventory
from test_runtime_orchestrator import runtime


def _draft(text, excerpt):
    return parse_answer({
        "status": "answered", "overview": text,
        "claims": [{
            "text": "The cited indexed source contains this statement.",
            "category": "indexed_fact", "citations": ["S1"], "excerpt": excerpt,
        }],
        "hypotheses": [],
        "limitations": ["Indexed bytes do not establish runtime execution."],
    })


def test_real_chroma_one_public_retrieval_and_validated_citation(tmp_path):
    store = ChromaVectorStore(tmp_path / "module10-one-snapshot")
    _publish(store, (_record("a", "def calculate_tax(): return subtotal", (1.0, 0.0, 0.0)),))
    embedding = OfflineProvider()
    provider = Provider(_draft("A source statement is indexed.", "return subtotal"))
    result = answer_question(
        HybridRetriever(embedding, store), provider, NAMESPACE,
        "calculate tax", top_k=1, external_processing_approved=True,
    )
    assert embedding.calls == 1
    assert len(provider.calls) == 1
    assert result.answer.status is AnswerStatus.ANSWERED
    assert [item.label for item in result.citations] == ["S1"]
    assert result.citations[0].content == "def calculate_tax(): return subtotal"
    assert not result.warnings


def test_real_chroma_empty_index_abstains_without_embedding_or_generation(tmp_path):
    store = ChromaVectorStore(tmp_path / "module10-empty-snapshot")
    _publish(store, ())
    embedding = OfflineProvider()
    provider = Provider()
    result = answer_question(
        HybridRetriever(embedding, store), provider, NAMESPACE,
        "calculate tax", top_k=1, external_processing_approved=True,
    )
    assert result.answer.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert not provider.calls and embedding.calls == 0


def test_real_chroma_graph_history_runtime_remain_unverified(tmp_path):
    inventories = with_omitted_suffix(same_file_call_bundle())
    graph = build_graph_revision(*inventories, require_published_source=True)
    store = ChromaVectorStore(tmp_path / "module10-all-streams")
    SemanticIndexer(LocalIndexerProvider(), store).synchronize_with_sources(*inventories)
    history = _inventory(repository_namespace=GRAPH_NAMESPACE, changes=(
        _change(new_path="src/module.py"),
    ))
    embedding = OfflineProvider()
    provider = Provider(_draft("The indexed graph-connected source is relevant.", "alpha"))
    result = answer_question(
        HybridRetriever(embedding, store), provider, GRAPH_NAMESPACE,
        "alpha", top_k=1, graph_revision=graph,
        relevance_policy=RelevancePolicy(min_query_matches=1),
        git_history=history, runtime_evidence=runtime("src/module.py"),
        external_processing_approved=True,
    )
    assert embedding.calls == 1
    assert result.answer.status is AnswerStatus.ANSWERED
    assert result.citations[0].label == "S1"
    assert provider.calls[0].prepared.items[0].kind.value == "indexed_source"
    other = [item for item in provider.calls[0].prepared.items if item.label[:1] in ("H", "R")]
    assert other and all(item.kind.value.startswith("unverified_") for item in other)
