import pytest

from backend.review_pipeline.intake import (
    classify_size,
    classify_change_type,
    compute_language_distribution,
    compute_file_type_distribution,
    compute_top_directories,
    derive_notable_signals,
    build_intake_summary,
)
from backend.pr_context.context_manager import DerivedSignals
from backend.tests.conftest import _make_file, _make_pr, _make_context


# --- 5.1 Size classification ---


def test_small_pr():
    ctx = _make_context(pr_kwargs={"changed_files": 2, "additions": 30, "deletions": 10})
    assert classify_size(ctx) == "small"


def test_medium_pr():
    ctx = _make_context(pr_kwargs={"changed_files": 8, "additions": 200, "deletions": 100})
    assert classify_size(ctx) == "medium"


def test_large_pr_by_file_count():
    ctx = _make_context(pr_kwargs={"changed_files": 15, "additions": 10, "deletions": 5})
    assert classify_size(ctx) == "large"


def test_large_pr_by_line_count():
    ctx = _make_context(pr_kwargs={"changed_files": 5, "additions": 400, "deletions": 200})
    assert classify_size(ctx) == "large"


# --- 5.2 Change type classification ---


def test_docs_only():
    files = [_make_file(is_docs=True, is_source=False, language="markdown") for _ in range(3)]
    assert classify_change_type(files) == "docs"


def test_test_only():
    files = [_make_file(is_test=True, is_source=False, language="python") for _ in range(2)]
    assert classify_change_type(files) == "test"


def test_config_only():
    files = [_make_file(is_config=True, is_source=False, language="yaml") for _ in range(2)]
    assert classify_change_type(files) == "config"


def test_source_dominant():
    files = [
        _make_file(is_source=True, is_test=False, language="python"),
        _make_file(is_source=True, is_test=False, language="python"),
        _make_file(is_source=False, is_test=True, is_docs=False, language="python"),
    ]
    assert classify_change_type(files) == "source"


def test_mixed():
    files = [
        _make_file(is_source=True, is_docs=False, is_test=False, is_config=False),
        _make_file(is_source=False, is_docs=True, is_test=False, is_config=False),
        _make_file(is_source=False, is_docs=False, is_test=False, is_config=True),
    ]
    assert classify_change_type(files) == "mixed"


def test_empty_files():
    assert classify_change_type([]) == "mixed"


# --- 5.3 Distributions ---


def test_language_distribution():
    files = [
        _make_file(language="python"),
        _make_file(language="python"),
        _make_file(language="markdown"),
    ]
    dist = compute_language_distribution(files)
    assert dist == {"markdown": 1, "python": 2}


def test_file_type_distribution():
    files = [
        _make_file(is_source=True, is_test=False, is_docs=False, is_config=False, is_generated=False, is_binary=False),
        _make_file(is_source=False, is_test=True, is_docs=False, is_config=False, is_generated=False, is_binary=False),
        _make_file(is_source=False, is_test=False, is_docs=False, is_config=True, is_generated=False, is_binary=False),
    ]
    dist = compute_file_type_distribution(files)
    assert dist == {"config": 1, "source": 1, "test": 1}


def test_top_directories():
    files = [
        _make_file(filename="src/a.py"),
        _make_file(filename="src/b.py"),
        _make_file(filename="tests/test_a.py"),
    ]
    dirs = compute_top_directories(files)
    assert dirs[0] == {"directory": "src", "file_count": 2}
    assert dirs[1] == {"directory": "tests", "file_count": 1}


def test_top_directories_root_file():
    files = [_make_file(filename="README.md")]
    dirs = compute_top_directories(files)
    assert dirs[0] == {"directory": ".", "file_count": 1}


# --- 5.4 Notable signals ---


def test_notable_signals_docs_only():
    derived = DerivedSignals(
        total_hunks=1, source_files_changed=0, test_files_changed=0,
        docs_only=True, has_source_without_tests=False, high_risk_files=[],
    )
    signals = derive_notable_signals("small", derived)
    assert "docs_only" in signals


def test_notable_signals_source_without_tests():
    derived = DerivedSignals(
        total_hunks=1, source_files_changed=1, test_files_changed=0,
        docs_only=False, has_source_without_tests=True, high_risk_files=[],
    )
    signals = derive_notable_signals("small", derived)
    assert "source_without_tests" in signals


def test_notable_signals_high_risk():
    derived = DerivedSignals(
        total_hunks=1, source_files_changed=1, test_files_changed=0,
        docs_only=False, has_source_without_tests=True,
        high_risk_files=["src/auth.py"],
    )
    signals = derive_notable_signals("small", derived)
    assert "high_risk_paths_changed" in signals


def test_notable_signals_large_pr():
    derived = DerivedSignals(
        total_hunks=1, source_files_changed=1, test_files_changed=0,
        docs_only=False, has_source_without_tests=True, high_risk_files=[],
    )
    signals = derive_notable_signals("large", derived)
    assert "large_pr" in signals


def test_notable_signals_none_derived():
    signals = derive_notable_signals("small", None)
    assert signals == []


# --- 5.5 API tests ---


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app)


def test_intake_summary_success(client):
    from backend.pr_context.context_manager import _contexts
    ctx = _make_context()
    _contexts[ctx.context_id] = ctx

    resp = client.post("/api/review/intake", json={"context_id": ctx.context_id})
    assert resp.status_code == 200
    data = resp.json()
    assert data["context_id"] == ctx.context_id
    assert data["size"] in ("small", "medium", "large")
    assert data["change_type"] in ("docs", "test", "source", "config", "mixed")
    assert "notable_signals" in data

    del _contexts[ctx.context_id]


def test_intake_summary_missing_context(client):
    resp = client.post("/api/review/intake", json={"context_id": "ctx_nonexistent"})
    assert resp.status_code == 404


# --- 5.6 Regression: no patch/hunk data in response ---


def test_intake_excludes_patch_data(client):
    from backend.pr_context.context_manager import _contexts
    from backend.pr_context.hunk_parser import Hunk, HunkLine

    hunk = Hunk(
        header="@@ -1,3 +1,4 @@",
        old_start=1, old_lines=3, new_start=1, new_lines=4,
        lines=[HunkLine(type="added", content="+new line", old_line=None, new_line=1)],
    )
    file_with_hunks = _make_file(hunks=[hunk])
    ctx = _make_context(files=[file_with_hunks])
    _contexts[ctx.context_id] = ctx

    resp = client.post("/api/review/intake", json={"context_id": ctx.context_id})
    assert resp.status_code == 200
    body = resp.text
    assert "@@ -1,3" not in body
    assert "+new line" not in body
    assert "hunks" not in resp.json()

    del _contexts[ctx.context_id]
