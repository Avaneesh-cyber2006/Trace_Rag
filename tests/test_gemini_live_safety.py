"""Offline subprocess regressions for the opt-in live test's output boundary."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


_ROOT = Path(__file__).resolve().parents[1]
_LIVE_TEST = "tests/integration/test_gemini_embeddings_live.py"
_CHILD = r'''
import logging
import os
import sys
from types import SimpleNamespace
import pytest

mode = sys.argv[1]
sentinel = "synthetic-" + "credential-sentinel"
os.environ["TRACERAG_RUN_GEMINI_LIVE"] = "1"
os.environ["TRACERAG_GEMINI_API_KEY"] = sentinel
logger = logging.getLogger("synthetic.provider")
logger.addHandler(logging.StreamHandler(sys.stdout))
logger.propagate = False
logger.setLevel(logging.WARNING)
logger.disabled = True

def state():
    return [(item, tuple(item.handlers), item.level, item.propagate, item.disabled)
            for item in [logging.getLogger(), logger]] + [
                logging.root.manager.disable, logging.Logger.callHandlers]

def probe(stage):
    # A logger created during the request must also bypass output handlers.
    current = logging.getLogger("synthetic.dynamic." + stage)
    current.addHandler(logging.StreamHandler(sys.stdout))
    current.propagate = False
    if mode.endswith(stage):
        if mode.startswith("exception_only"):
            raise RuntimeError(sentinel)
        logger.warning("provider diagnostic %s", sentinel)
        current.warning("new logger diagnostic %s", sentinel)
        if mode.startswith("exception"):
            raise RuntimeError(sentinel)

class FakeProvider:
    def __init__(self, **kwargs):
        if kwargs != dict(api_key=sentinel, model="gemini-embedding-001",
                          dimensions=3072,
                          compatibility_version="gemini-embedding-001-retrieval-3072-v1",
                          max_batch_size=1):
            raise RuntimeError("unexpected configuration")
        self.identity = SimpleNamespace(dimensions=3072)
        probe("constructor")

    def embed_documents(self, documents):
        if len(documents) != 1 or documents[0].text != "tracerag-live-probe":
            raise RuntimeError("unexpected document request")
        probe("document")
        return (SimpleNamespace(values=(0.25,) * 3072),)

    def embed_query(self, text):
        if text != "tracerag-live-probe":
            raise RuntimeError("unexpected query request")
        probe("query")
        return SimpleNamespace(values=(0.5,) * 3072)

class Plugin:
    report_leaked = False
    restored = True

    def pytest_collection_modifyitems(self, items):
        for item in items:
            item.module.GeminiEmbeddingProvider = FakeProvider

    @pytest.hookimpl(hookwrapper=True, trylast=True)
    def pytest_runtest_call(self, item):
        before = state()
        yield
        self.restored &= before == state()

    def pytest_runtest_logreport(self, report):
        self.report_leaked |= sentinel in str(report.longrepr)
        self.report_leaked |= any(sentinel in content for _, content in report.sections)

plugin = Plugin()
result = pytest.main([sys.argv[2], "-q", "-m", "gemini_live", "--showlocals",
                      "-o", "log_cli=true", "-o", "log_cli_level=INFO"], plugins=[plugin])
if plugin.report_leaked:
    sys.exit(86)
if not plugin.restored:
    sys.exit(87)
sys.exit(result)
'''


@pytest.mark.parametrize("mode", [
    "clean", "leak_constructor", "leak_document", "leak_query",
    "exception_constructor", "exception_document", "exception_query",
    "exception_only_constructor", "exception_only_document", "exception_only_query",
])
def test_live_check_keeps_sensitive_logs_out_of_output_and_failure_reports(mode):
    result = subprocess.run(
        [sys.executable, "-c", _CHILD, mode, _LIVE_TEST],
        cwd=_ROOT, capture_output=True, text=True, timeout=60,
    )
    # Never include child output in a failing assertion: it may contain the leak
    # this regression is intended to detect. The sentinel is synthetic only.
    if "synthetic-credential-sentinel" in result.stdout + result.stderr:
        pytest.fail("live check exposed the synthetic credential in output", pytrace=False)
    if result.returncode == 86:
        pytest.fail("live check exposed the synthetic credential in a report", pytrace=False)
    if result.returncode == 87:
        pytest.fail("live check did not restore logging state", pytrace=False)
    expected = 0 if mode == "clean" else 1
    if result.returncode != expected:
        pytest.fail("live check returned an unexpected subprocess result", pytrace=False)


def test_default_selection_excludes_live_even_with_both_guards_set():
    env = dict(os.environ, TRACERAG_RUN_GEMINI_LIVE="1",
               TRACERAG_GEMINI_API_KEY="synthetic-credential")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", _LIVE_TEST, "--collect-only", "-q"],
        cwd=_ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 5
    assert "1 deselected" in result.stdout
