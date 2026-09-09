"""Offline subprocess regressions for the opt-in live test's output boundary."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


_ROOT = Path(__file__).resolve().parents[1]
_LIVE_TEST = "tests/integration/test_gemini_embeddings_live.py"
_SENTINELS = (
    "synthetic-credential-sentinel",
    "synthetic-input-text-sentinel",
    "0.2468135791357924",
    "synthetic-raw-response-payload-sentinel",
)
_GENERIC_FAILURES = (
    "live Gemini logging exposed sensitive request or response data",
    "live Gemini request failed",
)
_CHILD = r'''
import logging
import os
import sys
from types import SimpleNamespace
import pytest

path = sys.argv[1]
leak_kind = sys.argv[2]
stage_to_probe = sys.argv[3]
credential_sentinel = "synthetic-" + "credential-sentinel"
input_sentinel = "synthetic-" + "input-text-sentinel"
vector_component_sentinel = 0.2468135791357924
raw_response_sentinel = "synthetic-" + "raw-response-payload-sentinel"
sentinels = (
    credential_sentinel,
    input_sentinel,
    repr(vector_component_sentinel),
    raw_response_sentinel,
)
generic_failures = (
    "live Gemini logging exposed sensitive request or response data",
    "live Gemini request failed",
)
os.environ["TRACERAG_RUN_GEMINI_LIVE"] = "1"
os.environ["TRACERAG_GEMINI_API_KEY"] = credential_sentinel
logger = logging.getLogger("synthetic.provider")
logger.addHandler(logging.StreamHandler(sys.stdout))
logger.propagate = False
logger.setLevel(logging.WARNING)
logger.disabled = True

def state():
    return [(item, tuple(item.handlers), item.level, item.propagate, item.disabled)
            for item in [logging.getLogger(), logger]] + [
                logging.root.manager.disable, logging.Logger.callHandlers]

def payload():
    if leak_kind == "credential":
        return credential_sentinel
    if leak_kind == "input":
        return input_sentinel
    if leak_kind == "component":
        return repr(vector_component_sentinel)
    if leak_kind == "raw_response":
        # The live test treats these response-shape words as sensitive too.
        return "raw embeddings response " + raw_response_sentinel
    raise RuntimeError("unexpected leak kind")

def probe(stage):
    # A logger created during the request must also bypass output handlers.
    current = logging.getLogger("synthetic.dynamic." + stage)
    current.addHandler(logging.StreamHandler(sys.stderr))
    current.propagate = False
    if stage != stage_to_probe:
        return
    value = payload()
    logger.warning("provider diagnostic %s", value)
    current.warning("new logger diagnostic %s", value)
    if path == "exception":
        raise RuntimeError(value)

def vector(fill):
    return (vector_component_sentinel,) + (fill,) * 3071

class FakeProvider:
    def __init__(self, **kwargs):
        if kwargs != dict(api_key=credential_sentinel,
                          model="gemini-embedding-001", dimensions=3072,
                          compatibility_version="gemini-embedding-001-retrieval-3072-v1",
                          max_batch_size=1):
            raise RuntimeError("unexpected configuration")
        self.identity = SimpleNamespace(dimensions=3072)
        probe("constructor")

    def embed_documents(self, documents):
        if len(documents) != 1 or documents[0].text != input_sentinel:
            raise RuntimeError("unexpected document request")
        probe("document")
        return (SimpleNamespace(values=vector(0.25)),)

    def embed_query(self, text):
        if text != input_sentinel:
            raise RuntimeError("unexpected query request")
        probe("query")
        return SimpleNamespace(values=vector(0.5))

class Plugin:
    report_leaked = False
    restored = True
    failure_longreprs = []

    def pytest_collection_modifyitems(self, items):
        for item in items:
            item.module.GeminiEmbeddingProvider = FakeProvider
            item.module._PROBE_TEXT = input_sentinel

    @pytest.hookimpl(hookwrapper=True, trylast=True)
    def pytest_runtest_call(self, item):
        before = state()
        yield
        self.restored &= before == state()

    def pytest_runtest_logreport(self, report):
        report_output = [str(report.longrepr)]
        report_output.extend(content for _, content in report.sections)
        report_output.extend(
            getattr(report, name, "")
            for name in ("capstdout", "capstderr", "caplog")
        )
        self.report_leaked |= any(
            sentinel in content
            for sentinel in sentinels
            for content in report_output
        )
        if report.when == "call" and report.failed:
            self.failure_longreprs.append(str(report.longrepr))

plugin = Plugin()
result = pytest.main([sys.argv[4], "-q", "-m", "gemini_live", "--showlocals",
                      "-o", "log_cli=true", "-o", "log_cli_level=INFO"],
                     plugins=[plugin])
if plugin.report_leaked:
    sys.exit(86)
if not plugin.restored:
    sys.exit(87)
if path == "clean":
    if plugin.failure_longreprs:
        sys.exit(88)
else:
    if (len(plugin.failure_longreprs) != 1
            or plugin.failure_longreprs[0] not in generic_failures):
        sys.exit(88)
sys.exit(result)
'''


@pytest.mark.parametrize("stage", ["constructor", "document", "query"])
@pytest.mark.parametrize(
    "leak_kind", ["credential", "input", "component", "raw_response"]
)
@pytest.mark.parametrize("path", ["normal", "exception"])
def test_live_check_keeps_each_sensitive_value_out_of_all_failure_output(
    path, leak_kind, stage
):
    result = subprocess.run(
        [sys.executable, "-c", _CHILD, path, leak_kind, stage, _LIVE_TEST],
        cwd=_ROOT, capture_output=True, text=True, timeout=60,
    )
    combined_output = result.stdout + result.stderr
    # Never include child output in a failing assertion: it may contain the leak
    # this regression is intended to detect. All sentinels are synthetic only.
    if any(sentinel in combined_output for sentinel in _SENTINELS):
        pytest.fail("live check exposed synthetic sensitive data in output", pytrace=False)
    if result.returncode == 86:
        pytest.fail("live check exposed synthetic sensitive data in a report", pytrace=False)
    if result.returncode == 87:
        pytest.fail("live check did not restore logging state", pytrace=False)
    if result.returncode == 88:
        pytest.fail("live check did not emit only a generic failure", pytrace=False)
    if result.returncode != 1:
        pytest.fail("live check returned an unexpected subprocess result", pytrace=False)
    if not any(message in combined_output for message in _GENERIC_FAILURES):
        pytest.fail("live check omitted its generic failure message", pytrace=False)


def test_live_check_clean_path_succeeds_without_sensitive_output():
    result = subprocess.run(
        [sys.executable, "-c", _CHILD, "clean", "credential", "unused", _LIVE_TEST],
        cwd=_ROOT, capture_output=True, text=True, timeout=60,
    )
    if any(sentinel in result.stdout + result.stderr for sentinel in _SENTINELS):
        pytest.fail("live check exposed synthetic sensitive data in output", pytrace=False)
    if result.returncode != 0:
        pytest.fail("clean live check returned an unexpected result", pytrace=False)


def test_default_selection_excludes_live_even_with_both_guards_set():
    env = dict(os.environ, TRACERAG_RUN_GEMINI_LIVE="1",
               TRACERAG_GEMINI_API_KEY="synthetic-credential")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", _LIVE_TEST, "--collect-only", "-q"],
        cwd=_ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 5
    assert "1 deselected" in result.stdout
