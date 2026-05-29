import pytest

from backend.review_pipeline.evidence import (
    make_evidence_id,
    dedup_key,
    deduplicate,
    sort_evidence,
    summarize,
    build_evidence_response,
    analyze,
    EvidenceItem,
)
from backend.pr_context.context_manager import _contexts
from backend.pr_context.hunk_parser import Hunk, HunkLine
from backend.tests.conftest import _make_file, _make_context


def _joined(parts):
    return "".join(parts)


def _fake_assignment(name_parts, value_parts):
    return f'{_joined(name_parts)} = "{_joined(value_parts)}"'


FAKE_CREDENTIAL_VALUE = ["fixture", "-", "credential", "-", "123456"]
FAKE_OPENAI_VALUE = ["s", "k", "-", "X" * 20]


# --- 5.1 Required fields and PR-level shape ---


def test_evidence_item_has_required_fields():
    item = EvidenceItem(
        id="abc123",
        source="rule_analyzer_v1",
        rule_id="test_rule",
        file="src/main.py",
        severity="warning",
        category="security",
        message="Test message",
        confidence=0.8,
        tags=["test"],
    )
    assert item.id == "abc123"
    assert item.source == "rule_analyzer_v1"
    assert item.rule_id == "test_rule"
    assert item.file == "src/main.py"
    assert item.severity == "warning"
    assert item.category == "security"
    assert item.message == "Test message"
    assert item.confidence == 0.8
    assert item.tags == ["test"]


def test_line_evidence_has_location():
    item = EvidenceItem(
        id="abc123",
        source="rule_analyzer_v1",
        rule_id="test_rule",
        file="src/main.py",
        severity="warning",
        category="security",
        message="Test",
        confidence=0.8,
        tags=[],
        line=42,
        hunk_index=0,
        excerpt="some code",
    )
    assert item.line == 42
    assert item.hunk_index == 0
    assert item.excerpt == "some code"


def test_pr_level_evidence_omits_line_metadata():
    ctx = _make_context(
        pr_kwargs={"changed_files": 1, "additions": 10, "deletions": 0},
        derived=None,
    )
    # Force has_source_without_tests by setting derived
    from backend.pr_context.context_manager import DerivedSignals
    ctx.derived = DerivedSignals(
        total_hunks=1,
        source_files_changed=1,
        test_files_changed=0,
        docs_only=False,
        has_source_without_tests=True,
        high_risk_files=[],
    )
    items = analyze(ctx)
    pr_items = [i for i in items if i.rule_id == "source_without_tests"]
    assert len(pr_items) == 1
    assert pr_items[0].line is None
    assert pr_items[0].hunk_index is None
    assert pr_items[0].excerpt is None


# --- 5.2 Added-line scanning, ignored removed/context ---


def test_added_line_scanned():
    fake_line = _fake_assignment(["pass", "word"], FAKE_CREDENTIAL_VALUE)
    hunk = Hunk(
        header="@@ -1,0 +1,1 @@",
        old_start=1, old_lines=0, new_start=1, new_lines=1,
        lines=[HunkLine(type="added", content=fake_line, old_line=None, new_line=1)],
    )
    f = _make_file(hunks=[hunk])
    ctx = _make_context(files=[f])
    items = analyze(ctx)
    line_items = [i for i in items if i.line is not None]
    assert len(line_items) >= 1


def test_removed_line_ignored():
    fake_line = _fake_assignment(["pass", "word"], FAKE_CREDENTIAL_VALUE)
    hunk = Hunk(
        header="@@ -1,1 +0,0 @@",
        old_start=1, old_lines=1, new_start=1, new_lines=0,
        lines=[HunkLine(type="removed", content=fake_line, old_line=1, new_line=None)],
    )
    f = _make_file(hunks=[hunk])
    ctx = _make_context(files=[f])
    items = analyze(ctx)
    line_items = [i for i in items if i.rule_id == "sensitive_field"]
    assert len(line_items) == 0


def test_context_line_ignored():
    fake_line = _fake_assignment(["pass", "word"], FAKE_CREDENTIAL_VALUE)
    hunk = Hunk(
        header="@@ -1,1 +1,1 @@",
        old_start=1, old_lines=1, new_start=1, new_lines=1,
        lines=[HunkLine(type="context", content=fake_line, old_line=1, new_line=1)],
    )
    f = _make_file(hunks=[hunk])
    ctx = _make_context(files=[f])
    items = analyze(ctx)
    line_items = [i for i in items if i.rule_id == "sensitive_field"]
    assert len(line_items) == 0


# --- 5.3 Each v1 rule category ---


def test_sensitive_field_detection():
    fake_line = _fake_assignment(["API", "_", "KEY"], FAKE_OPENAI_VALUE)
    hunk = Hunk(
        header="@@ -1,0 +1,1 @@",
        old_start=1, old_lines=0, new_start=1, new_lines=1,
        lines=[HunkLine(type="added", content=fake_line, old_line=None, new_line=1)],
    )
    f = _make_file(hunks=[hunk])
    ctx = _make_context(files=[f])
    items = analyze(ctx)
    sec_items = [i for i in items if i.rule_id == "sensitive_field"]
    assert len(sec_items) >= 1
    assert sec_items[0].severity == "critical"
    assert sec_items[0].category == "security"


def test_bare_except_python():
    hunk = Hunk(
        header="@@ -1,0 +1,2 @@",
        old_start=1, old_lines=0, new_start=1, new_lines=2,
        lines=[
            HunkLine(type="added", content="try:", old_line=None, new_line=1),
            HunkLine(type="added", content="    pass", old_line=None, new_line=2),
            HunkLine(type="added", content="except:", old_line=None, new_line=3),
        ],
    )
    f = _make_file(filename="src/app.py", language="python", hunks=[hunk])
    ctx = _make_context(files=[f])
    items = analyze(ctx)
    bare_items = [i for i in items if i.rule_id == "bare_except"]
    assert len(bare_items) == 1
    assert bare_items[0].category == "reliability"


def test_bare_except_ignored_for_non_python():
    hunk = Hunk(
        header="@@ -1,0 +1,1 @@",
        old_start=1, old_lines=0, new_start=1, new_lines=1,
        lines=[HunkLine(type="added", content="except:", old_line=None, new_line=1)],
    )
    f = _make_file(filename="src/app.js", language="javascript", hunks=[hunk])
    ctx = _make_context(files=[f])
    items = analyze(ctx)
    bare_items = [i for i in items if i.rule_id == "bare_except"]
    assert len(bare_items) == 0


def test_dangerous_exec_eval():
    hunk = Hunk(
        header="@@ -1,0 +1,1 @@",
        old_start=1, old_lines=0, new_start=1, new_lines=1,
        lines=[HunkLine(type="added", content='eval(user_input)', old_line=None, new_line=1)],
    )
    f = _make_file(hunks=[hunk])
    ctx = _make_context(files=[f])
    items = analyze(ctx)
    exec_items = [i for i in items if i.rule_id == "dangerous_exec"]
    assert len(exec_items) >= 1


def test_sql_injection_detection():
    hunk = Hunk(
        header="@@ -1,0 +1,1 @@",
        old_start=1, old_lines=0, new_start=1, new_lines=1,
        lines=[HunkLine(type="added", content='query = "SELECT * FROM users WHERE id=" + user_id', old_line=None, new_line=1)],
    )
    f = _make_file(hunks=[hunk])
    ctx = _make_context(files=[f])
    items = analyze(ctx)
    sql_items = [i for i in items if i.rule_id == "sql_injection"]
    assert len(sql_items) >= 1
    assert sql_items[0].category == "security"


def test_high_risk_path_evidence():
    f = _make_file(filename="src/auth/login.py", is_high_risk_path=True, risk_hints=["auth_path"])
    ctx = _make_context(files=[f])
    items = analyze(ctx)
    hr_items = [i for i in items if i.rule_id == "high_risk_path"]
    assert len(hr_items) == 1
    assert hr_items[0].severity == "warning"


def test_patch_unavailable_evidence():
    f = _make_file(patch_available=False)
    ctx = _make_context(files=[f])
    items = analyze(ctx)
    pu_items = [i for i in items if i.rule_id == "patch_unavailable"]
    assert len(pu_items) == 1
    assert pu_items[0].severity == "info"


def test_large_patch_evidence():
    f = _make_file(patch_available=True, large_patch=True)
    ctx = _make_context(files=[f])
    items = analyze(ctx)
    lp_items = [i for i in items if i.rule_id == "large_patch"]
    assert len(lp_items) == 1


def test_source_without_tests_evidence():
    from backend.pr_context.context_manager import DerivedSignals
    ctx = _make_context()
    ctx.derived = DerivedSignals(
        total_hunks=1,
        source_files_changed=1,
        test_files_changed=0,
        docs_only=False,
        has_source_without_tests=True,
        high_risk_files=[],
    )
    items = analyze(ctx)
    swt_items = [i for i in items if i.rule_id == "source_without_tests"]
    assert len(swt_items) == 1
    assert swt_items[0].category == "test"


# --- 5.4 Deduplication and deterministic sorting ---


def test_dedup_removes_duplicates():
    item = EvidenceItem(
        id="abc", source="rule_analyzer_v1", rule_id="test", file="a.py",
        severity="warning", category="security", message="msg",
        confidence=0.8, tags=[], line=1, excerpt="code",
    )
    items = [item, item, item]
    deduped = deduplicate(items)
    assert len(deduped) == 1


def test_sort_by_severity():
    items = [
        EvidenceItem(id="1", source="r", rule_id="r", file="a.py", severity="info", category="c", message="m", confidence=0.9, tags=[]),
        EvidenceItem(id="2", source="r", rule_id="r", file="a.py", severity="critical", category="c", message="m", confidence=0.9, tags=[]),
        EvidenceItem(id="3", source="r", rule_id="r", file="a.py", severity="warning", category="c", message="m", confidence=0.9, tags=[]),
    ]
    sorted_items = sort_evidence(items)
    assert [i.severity for i in sorted_items] == ["critical", "warning", "info"]


def test_sort_by_confidence_within_severity():
    items = [
        EvidenceItem(id="1", source="r", rule_id="r", file="a.py", severity="warning", category="c", message="m", confidence=0.5, tags=[]),
        EvidenceItem(id="2", source="r", rule_id="r", file="a.py", severity="warning", category="c", message="m", confidence=0.9, tags=[]),
    ]
    sorted_items = sort_evidence(items)
    assert sorted_items[0].confidence == 0.9


def test_sort_by_file_and_line():
    items = [
        EvidenceItem(id="1", source="r", rule_id="r", file="b.py", severity="warning", category="c", message="m", confidence=0.9, tags=[], line=10),
        EvidenceItem(id="2", source="r", rule_id="r", file="a.py", severity="warning", category="c", message="m", confidence=0.9, tags=[], line=5),
    ]
    sorted_items = sort_evidence(items)
    assert sorted_items[0].file == "a.py"
    assert sorted_items[1].file == "b.py"


def test_summary_counts():
    items = [
        EvidenceItem(id="1", source="r", rule_id="r", file="a.py", severity="critical", category="security", message="m", confidence=0.9, tags=[]),
        EvidenceItem(id="2", source="r", rule_id="r", file="a.py", severity="warning", category="security", message="m", confidence=0.8, tags=[]),
        EvidenceItem(id="3", source="r", rule_id="r", file="a.py", severity="warning", category="test", message="m", confidence=0.7, tags=[]),
    ]
    s = summarize(items)
    assert s["by_severity"] == {"critical": 1, "warning": 2}
    assert s["by_category"] == {"security": 2, "test": 1}


# --- 5.5 API tests ---


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app)


def test_evidence_success(client):
    ctx = _make_context()
    _contexts[ctx.context_id] = ctx

    resp = client.post("/api/review/evidence", json={"context_id": ctx.context_id})
    assert resp.status_code == 200
    data = resp.json()
    assert data["context_id"] == ctx.context_id
    assert "items" in data
    assert "summary" in data
    assert "by_severity" in data["summary"]
    assert "by_category" in data["summary"]

    del _contexts[ctx.context_id]


def test_evidence_missing_context(client):
    resp = client.post("/api/review/evidence", json={"context_id": "ctx_nonexistent"})
    assert resp.status_code == 404


# --- 5.6 Regression: no tokens or raw patches in response ---


def test_evidence_excludes_tokens_and_patches(client):
    FAKE_GHP = "ghp_" + "X" * 36
    hunk = Hunk(
        header="@@ -1,0 +1,1 @@",
        old_start=1, old_lines=0, new_start=1, new_lines=1,
        lines=[HunkLine(type="added", content=f'token = "{FAKE_GHP}"', old_line=None, new_line=1)],
    )
    f = _make_file(hunks=[hunk])
    ctx = _make_context(files=[f])
    _contexts[ctx.context_id] = ctx

    resp = client.post("/api/review/evidence", json={"context_id": ctx.context_id})
    assert resp.status_code == 200
    body = resp.text
    assert FAKE_GHP not in body
    assert "REDACTED" in body
    assert "hunks" not in resp.json()

    del _contexts[ctx.context_id]


# --- P2: Token value pattern detection ---


def test_bearer_token_redacted():
    from backend.review_pipeline.evidence import sanitize_excerpt
    FAKE_JWT = "Bearer " + "X" * 40
    result = sanitize_excerpt(f"Authorization: {FAKE_JWT}")
    assert FAKE_JWT not in result
    assert "REDACTED" in result


def test_github_pat_redacted():
    from backend.review_pipeline.evidence import sanitize_excerpt
    FAKE_PAT = "github_pat_" + "X" * 30
    result = sanitize_excerpt(f"using {FAKE_PAT}")
    assert FAKE_PAT not in result
    assert "REDACTED" in result


def test_aws_key_redacted():
    from backend.review_pipeline.evidence import sanitize_excerpt
    FAKE_AWS = "AKIA" + "X" * 16
    result = sanitize_excerpt(f"aws_key = {FAKE_AWS}")
    assert FAKE_AWS not in result
    assert "REDACTED" in result


def test_slack_token_redacted():
    from backend.review_pipeline.evidence import sanitize_excerpt
    FAKE_SLACK = "xoxb-" + "X" * 10
    result = sanitize_excerpt(f"token = {FAKE_SLACK}")
    assert FAKE_SLACK not in result
    assert "REDACTED" in result


def test_all_rules_sanitize_excerpt(client):
    """All evidence items with excerpts should have secrets redacted."""
    FAKE_GHP = "ghp_" + "A" * 36
    hunk = Hunk(
        header="@@ -1,0 +1,1 @@",
        old_start=1, old_lines=0, new_start=1, new_lines=1,
        lines=[HunkLine(type="added", content=f'eval(request.headers["Authorization"] + " {FAKE_GHP}")', old_line=None, new_line=1)],
    )
    f = _make_file(hunks=[hunk])
    ctx = _make_context(files=[f])
    _contexts[ctx.context_id] = ctx

    resp = client.post("/api/review/evidence", json={"context_id": ctx.context_id})
    assert resp.status_code == 200
    body = resp.text
    assert FAKE_GHP not in body
    assert "REDACTED" in body

    del _contexts[ctx.context_id]


# --- P3: High-risk path category mapping ---


def test_high_risk_path_auth_is_security():
    f = _make_file(filename="src/auth/login.py", is_high_risk_path=True, risk_hints=["auth_path"])
    ctx = _make_context(files=[f])
    items = analyze(ctx)
    hr = [i for i in items if i.rule_id == "high_risk_path"]
    assert len(hr) == 1
    assert hr[0].category == "security"


def test_high_risk_path_payment_is_security():
    f = _make_file(filename="src/billing/stripe.py", is_high_risk_path=True, risk_hints=["payment_path"])
    ctx = _make_context(files=[f])
    items = analyze(ctx)
    hr = [i for i in items if i.rule_id == "high_risk_path"]
    assert hr[0].category == "security"


def test_high_risk_path_config_is_config():
    f = _make_file(filename="config/settings.py", is_high_risk_path=True, risk_hints=["config_path"])
    ctx = _make_context(files=[f])
    items = analyze(ctx)
    hr = [i for i in items if i.rule_id == "high_risk_path"]
    assert hr[0].category == "config"


def test_high_risk_path_db_is_maintainability():
    f = _make_file(filename="db/migration/001.py", is_high_risk_path=True, risk_hints=["db_path"])
    ctx = _make_context(files=[f])
    items = analyze(ctx)
    hr = [i for i in items if i.rule_id == "high_risk_path"]
    assert hr[0].category == "maintainability"
