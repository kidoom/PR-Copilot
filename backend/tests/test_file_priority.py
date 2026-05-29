import pytest

from backend.review_pipeline.file_priority import (
    classify_group,
    generate_reasons,
    build_file_priority_view,
    sort_files,
)
from backend.pr_context.context_manager import FileEntry, _contexts
from backend.tests.conftest import _make_file, _make_context


# --- 5.1 Grouping thresholds ---


def test_must_review_high_score():
    assert classify_group(70) == "must_review"
    assert classify_group(100) == "must_review"


def test_should_review_mid_score():
    assert classify_group(35) == "should_review"
    assert classify_group(69) == "should_review"


def test_skim_low_score():
    assert classify_group(0) == "skim"
    assert classify_group(34) == "skim"


# --- 5.2 Deterministic sorting ---


def test_sort_by_descending_score():
    files = [
        _make_file(filename="a.py", priority_score_hint=30),
        _make_file(filename="b.py", priority_score_hint=80),
        _make_file(filename="c.py", priority_score_hint=50),
    ]
    result = sort_files(files)
    assert [f.filename for f in result] == ["b.py", "c.py", "a.py"]


def test_sort_tie_break_by_filename():
    files = [
        _make_file(filename="z.py", priority_score_hint=50),
        _make_file(filename="a.py", priority_score_hint=50),
        _make_file(filename="m.py", priority_score_hint=50),
    ]
    result = sort_files(files)
    assert [f.filename for f in result] == ["a.py", "m.py", "z.py"]


def test_sort_mixed_scores_and_names():
    files = [
        _make_file(filename="b.py", priority_score_hint=70),
        _make_file(filename="a.py", priority_score_hint=70),
        _make_file(filename="c.py", priority_score_hint=35),
    ]
    result = sort_files(files)
    assert [f.filename for f in result] == ["a.py", "b.py", "c.py"]


# --- 5.3 Reason generation ---


def test_reasons_from_risk_hints():
    f = _make_file(risk_hints=["auth_path", "no_test_pair"])
    reasons = generate_reasons(f)
    assert "auth_path" in reasons
    assert "no_test_pair" in reasons


def test_reasons_high_risk_path():
    f = _make_file(is_high_risk_path=True)
    assert "high_risk_path" in generate_reasons(f)


def test_reasons_source_change():
    f = _make_file(is_source=True, is_test=False, is_docs=False, is_config=False)
    assert "source_change" in generate_reasons(f)


def test_reasons_test_change():
    f = _make_file(is_test=True, is_source=False)
    assert "test_change" in generate_reasons(f)


def test_reasons_docs_change():
    f = _make_file(is_docs=True, is_source=False)
    assert "docs_change" in generate_reasons(f)


def test_reasons_config_change():
    f = _make_file(is_config=True, is_source=False)
    assert "config_change" in generate_reasons(f)


def test_reasons_generated_file():
    f = _make_file(is_generated=True)
    assert "generated_file" in generate_reasons(f)


def test_reasons_binary_file():
    f = _make_file(is_binary=True)
    assert "binary_file" in generate_reasons(f)


def test_reasons_patch_unavailable():
    f = _make_file(patch_available=False)
    assert "patch_unavailable" in generate_reasons(f)


def test_reasons_large_patch():
    f = _make_file(large_patch=True)
    assert "large_patch" in generate_reasons(f)


def test_reasons_parse_error():
    f = _make_file(parse_error="unexpected token")
    assert "parse_error" in generate_reasons(f)


def test_reasons_new_file():
    f = _make_file(status="added")
    assert "new_file" in generate_reasons(f)


def test_reasons_renamed_file():
    f = _make_file(status="renamed")
    assert "renamed_file" in generate_reasons(f)


def test_reasons_removed_file():
    f = _make_file(status="removed")
    assert "removed_file" in generate_reasons(f)


def test_reasons_large_change():
    f = _make_file(additions=300, deletions=250)
    assert "large_change" in generate_reasons(f)


def test_reasons_no_duplicates():
    f = _make_file(risk_hints=["auth_path", "auth_path"])
    reasons = generate_reasons(f)
    assert reasons.count("auth_path") == 1


def test_reasons_deterministic_order():
    f = _make_file(
        is_high_risk_path=True,
        is_source=True,
        status="added",
        additions=600,
        deletions=0,
        risk_hints=["auth_path"],
    )
    reasons = generate_reasons(f)
    assert reasons == ["auth_path", "high_risk_path", "source_change", "new_file", "large_change"]


# --- 5.4 API tests ---


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app)


def test_file_priority_success(client):
    ctx = _make_context(
        files=[
            _make_file(filename="src/auth.py", priority_score_hint=80),
            _make_file(filename="src/util.py", priority_score_hint=20),
        ]
    )
    _contexts[ctx.context_id] = ctx

    resp = client.post("/api/review/file-priority", json={"context_id": ctx.context_id})
    assert resp.status_code == 200
    data = resp.json()
    assert data["context_id"] == ctx.context_id
    assert "must_review" in data["groups"]
    assert "should_review" in data["groups"]
    assert "skim" in data["groups"]
    assert len(data["groups"]["must_review"]) == 1
    assert data["groups"]["must_review"][0]["filename"] == "src/auth.py"
    assert len(data["groups"]["skim"]) == 1
    assert data["groups"]["skim"][0]["filename"] == "src/util.py"

    del _contexts[ctx.context_id]


def test_file_priority_missing_context(client):
    resp = client.post("/api/review/file-priority", json={"context_id": "ctx_nonexistent"})
    assert resp.status_code == 404


# --- 5.5 Regression: no patch/hunk data in response ---


def test_file_priority_excludes_patch_data(client):
    from backend.pr_context.hunk_parser import Hunk, HunkLine

    hunk = Hunk(
        header="@@ -1,3 +1,4 @@",
        old_start=1, old_lines=3, new_start=1, new_lines=4,
        lines=[HunkLine(type="added", content="+secret line", old_line=None, new_line=1)],
    )
    f = _make_file(hunks=[hunk], priority_score_hint=90)
    ctx = _make_context(files=[f])
    _contexts[ctx.context_id] = ctx

    resp = client.post("/api/review/file-priority", json={"context_id": ctx.context_id})
    assert resp.status_code == 200
    body = resp.text
    assert "+secret line" not in body
    assert "hunks" not in resp.json()["groups"]["must_review"][0]

    del _contexts[ctx.context_id]
