import pytest

from backend.review_pipeline.context_task_planner import (
    TaskSource,
    TaskTarget,
    TASK_TYPES,
    TASK_TYPE_SET,
    ROUTE_KEYS,
    TASK_ROUTES,
    AGENT_DEFINITIONS,
    make_task_id,
    deduplicate_tasks,
    sort_tasks,
    summarize_tasks,
    cap_tasks,
    build_context_task_plan,
    route_to_dict,
    agent_to_dict,
    _make_task,
)
from backend.review_pipeline.evidence import EvidenceItem
from backend.pr_context.context_manager import _contexts, DerivedSignals
from backend.pr_context.hunk_parser import Hunk, HunkLine
from backend.tests.conftest import _make_file, _make_context


# --- 6.1 Required ContextTask fields and pending status ---


def test_context_task_has_required_fields():
    task = _make_task(
        task_type="test_context",
        intent="find_related_tests",
        source=TaskSource(evidence_ids=["e1"], rule_ids=["r1"], signals=["s1"]),
        target=TaskTarget(files=["src/main.py"], keywords=["main"]),
        queries=["find tests for main"],
        priority="high",
        expected_output="Test files",
        fallback="No tests found",
        index=0,
        target_file="src/main.py",
    )
    assert task.task_id is not None
    assert task.task_type == "test_context"
    assert task.intent == "find_related_tests"
    assert task.route_key == "route:test_context"
    assert task.source is not None
    assert task.target is not None
    assert task.queries is not None
    assert task.priority == "high"
    assert task.budget is not None
    assert task.expected_output is not None
    assert task.fallback is not None


def test_context_task_starts_pending():
    task = _make_task(
        task_type="test_context",
        intent="find_related_tests",
        source=TaskSource(),
        target=TaskTarget(files=["a.py"]),
        queries=["q"],
        priority="medium",
        expected_output="out",
        fallback="fb",
        index=0,
        target_file="a.py",
    )
    assert task.status == "pending"


def test_task_id_is_stable():
    id1 = make_task_id("test_context", "find_related_tests", "a.py", 0)
    id2 = make_task_id("test_context", "find_related_tests", "a.py", 0)
    assert id1 == id2


def test_task_id_differs_for_different_inputs():
    id1 = make_task_id("test_context", "find_related_tests", "a.py", 0)
    id2 = make_task_id("security_context", "verify_security_evidence", "a.py", 0)
    assert id1 != id2


# --- 6.2 Seven task type categories and route metadata ---


def test_seven_task_types_defined():
    assert len(TASK_TYPES) == 7
    expected = {"test_context", "reference_context", "security_context", "config_context",
                "data_context", "runtime_context", "patch_deep_dive"}
    assert TASK_TYPE_SET == expected


def test_task_route_exists_for_every_type():
    for tt in TASK_TYPES:
        assert tt in TASK_ROUTES
        route = TASK_ROUTES[tt]
        assert route.task_type == tt
        assert route.route_key == ROUTE_KEYS[tt]
        assert route.agent_type.endswith("-agent")


def test_agent_definition_exists_for_every_type():
    expected_agents = {
        "test-context-agent", "reference-context-agent", "security-context-agent",
        "config-context-agent", "data-context-agent", "runtime-context-agent",
        "patch-deep-dive-agent",
    }
    assert set(AGENT_DEFINITIONS.keys()) == expected_agents


def test_route_metadata_uses_read_only_tools():
    for route in TASK_ROUTES.values():
        for tool in route.allowed_tools:
            assert tool in ["search_repo", "read_repo_file", "read_file_patch",
                            "search_diff", "read_check_summary", "read_review_comments_summary"]


def test_agent_metadata_disallows_recursive_tools():
    for agent in AGENT_DEFINITIONS.values():
        assert "task_tool" in agent.disallowed_tools
        assert "sub_agent" in agent.disallowed_tools


def test_route_to_dict_shape():
    route = TASK_ROUTES["test_context"]
    d = route_to_dict(route)
    assert "task_type" in d
    assert "route_key" in d
    assert "agent_type" in d
    assert "allowed_tools" in d
    assert "output_schema" in d
    assert "max_steps" in d


def test_agent_to_dict_shape():
    agent = AGENT_DEFINITIONS["test-context-agent"]
    d = agent_to_dict(agent)
    assert "agent_type" in d
    assert "description" in d
    assert "allowed_tools" in d
    assert "disallowed_tools" in d


# --- 6.3 Source binding from evidence ids, rule ids, signals, and file facts ---


def test_source_binds_evidence_ids():
    f = _make_file(is_high_risk_path=True, risk_hints=["auth_path"])
    ctx = _make_context(files=[f])
    evidence = [EvidenceItem(
        id="ev_001", source="rule_analyzer_v1", rule_id="high_risk_path",
        file="src/main.py", severity="warning", category="security",
        message="test", confidence=0.7, tags=["auth_path"],
    )]
    result = build_context_task_plan(ctx, evidence)
    sec_tasks = [t for t in result["tasks"] if t["task_type"] == "security_context"]
    assert len(sec_tasks) > 0, "planner should generate security_context tasks for high-risk auth files"
    assert any("ev_001" in t["source"]["evidence_ids"] for t in sec_tasks)


def test_source_binds_rule_ids():
    f = _make_file(is_high_risk_path=True, risk_hints=["auth_path"])
    ctx = _make_context(files=[f])
    evidence = [EvidenceItem(
        id="ev_001", source="rule_analyzer_v1", rule_id="high_risk_path",
        file="src/main.py", severity="warning", category="security",
        message="test", confidence=0.7, tags=["auth_path"],
    )]
    result = build_context_task_plan(ctx, evidence)
    sec_tasks = [t for t in result["tasks"] if t["task_type"] == "security_context"]
    assert len(sec_tasks) > 0, "planner should generate security_context tasks for high-risk auth files"
    assert any("high_risk_path" in t["source"]["rule_ids"] for t in sec_tasks)


def test_source_binds_signals():
    f = _make_file(risk_hints=["db_path"])
    ctx = _make_context(files=[f])
    result = build_context_task_plan(ctx, [])
    data_tasks = [t for t in result["tasks"] if t["task_type"] == "data_context"]
    assert len(data_tasks) > 0, "planner should generate data_context tasks for db_path risk hint"
    assert any("db_path" in t["source"]["signals"] for t in data_tasks)


def test_source_binds_file_facts():
    f = _make_file(filename="src/main.py", is_source=True)
    ctx = _make_context(files=[f])
    result = build_context_task_plan(ctx, [])
    ref_tasks = [t for t in result["tasks"] if t["task_type"] == "reference_context"]
    assert len(ref_tasks) > 0, "planner should generate reference_context tasks for source files"
    assert any("src/main.py" in t["source"]["file_facts"] for t in ref_tasks)


# --- 6.4 Self-contained targets, queries, budgets, expected outputs, fallbacks ---


def test_task_has_target_files():
    f = _make_file(filename="src/main.py", is_source=True)
    ctx = _make_context(files=[f])
    result = build_context_task_plan(ctx, [])
    for t in result["tasks"]:
        assert len(t["target"]["files"]) > 0


def test_task_has_queries():
    f = _make_file(filename="src/main.py", is_source=True)
    ctx = _make_context(files=[f])
    result = build_context_task_plan(ctx, [])
    for t in result["tasks"]:
        assert len(t["queries"]) > 0


def test_task_has_budget():
    f = _make_file(filename="src/main.py", is_source=True)
    ctx = _make_context(files=[f])
    result = build_context_task_plan(ctx, [])
    for t in result["tasks"]:
        b = t["budget"]
        assert b["max_searches"] > 0
        assert b["max_files"] > 0
        assert b["max_tokens"] > 0


def test_task_has_expected_output():
    f = _make_file(filename="src/main.py", is_source=True)
    ctx = _make_context(files=[f])
    result = build_context_task_plan(ctx, [])
    for t in result["tasks"]:
        assert len(t["expected_output"]) > 0


def test_task_has_fallback():
    f = _make_file(filename="src/main.py", is_source=True)
    ctx = _make_context(files=[f])
    result = build_context_task_plan(ctx, [])
    for t in result["tasks"]:
        assert len(t["fallback"]) > 0


# --- 6.5 Deterministic deduplication, sorting, and summary counts ---


def test_dedup_removes_duplicates():
    t1 = _make_task(
        task_type="test_context", intent="find_related_tests",
        source=TaskSource(evidence_ids=["e1"]),
        target=TaskTarget(files=["a.py"]),
        queries=["q1"], priority="high",
        expected_output="out", fallback="fb", index=0, target_file="a.py",
    )
    t2 = _make_task(
        task_type="test_context", intent="find_related_tests",
        source=TaskSource(evidence_ids=["e1"]),
        target=TaskTarget(files=["a.py"]),
        queries=["q1"], priority="high",
        expected_output="out", fallback="fb", index=1, target_file="a.py",
    )
    result = deduplicate_tasks([t1, t2])
    assert len(result) == 1


def test_dedup_keeps_different_tasks():
    t1 = _make_task(
        task_type="test_context", intent="find_related_tests",
        source=TaskSource(evidence_ids=["e1"]),
        target=TaskTarget(files=["a.py"]),
        queries=["q1"], priority="high",
        expected_output="out", fallback="fb", index=0, target_file="a.py",
    )
    t2 = _make_task(
        task_type="security_context", intent="verify_security_evidence",
        source=TaskSource(evidence_ids=["e2"]),
        target=TaskTarget(files=["b.py"]),
        queries=["q2"], priority="high",
        expected_output="out", fallback="fb", index=0, target_file="b.py",
    )
    result = deduplicate_tasks([t1, t2])
    assert len(result) == 2


def test_sort_by_priority():
    t1 = _make_task(
        task_type="test_context", intent="find_related_tests",
        source=TaskSource(), target=TaskTarget(files=["a.py"]),
        queries=["q"], priority="low",
        expected_output="out", fallback="fb", index=0, target_file="a.py",
    )
    t2 = _make_task(
        task_type="test_context", intent="find_related_tests",
        source=TaskSource(), target=TaskTarget(files=["a.py"]),
        queries=["q"], priority="high",
        expected_output="out", fallback="fb", index=1, target_file="a.py",
    )
    result = sort_tasks([t1, t2])
    assert result[0].priority == "high"
    assert result[1].priority == "low"


def test_sort_by_type_within_priority():
    t1 = _make_task(
        task_type="security_context", intent="verify_security_evidence",
        source=TaskSource(), target=TaskTarget(files=["a.py"]),
        queries=["q"], priority="high",
        expected_output="out", fallback="fb", index=0, target_file="a.py",
    )
    t2 = _make_task(
        task_type="test_context", intent="find_related_tests",
        source=TaskSource(), target=TaskTarget(files=["a.py"]),
        queries=["q"], priority="high",
        expected_output="out", fallback="fb", index=1, target_file="a.py",
    )
    result = sort_tasks([t1, t2])
    assert result[0].task_type == "security_context"
    assert result[1].task_type == "test_context"


def test_sort_by_file_within_type_and_priority():
    t1 = _make_task(
        task_type="test_context", intent="find_related_tests",
        source=TaskSource(), target=TaskTarget(files=["b.py"]),
        queries=["q"], priority="high",
        expected_output="out", fallback="fb", index=0, target_file="b.py",
    )
    t2 = _make_task(
        task_type="test_context", intent="find_related_tests",
        source=TaskSource(), target=TaskTarget(files=["a.py"]),
        queries=["q"], priority="high",
        expected_output="out", fallback="fb", index=1, target_file="a.py",
    )
    result = sort_tasks([t1, t2])
    assert result[0].target.files[0] == "a.py"
    assert result[1].target.files[0] == "b.py"


def test_summary_counts_by_type():
    tasks = [
        _make_task(task_type="test_context", intent="i", source=TaskSource(),
                   target=TaskTarget(files=["a.py"]), queries=["q"], priority="high",
                   expected_output="o", fallback="f", index=i, target_file="a.py")
        for i in range(3)
    ]
    tasks.append(_make_task(
        task_type="security_context", intent="i", source=TaskSource(),
        target=TaskTarget(files=["b.py"]), queries=["q"], priority="high",
        expected_output="o", fallback="f", index=10, target_file="b.py",
    ))
    summary = summarize_tasks(tasks)
    assert summary["by_type"]["test_context"] == 3
    assert summary["by_type"]["security_context"] == 1


def test_summary_counts_by_priority():
    tasks = [
        _make_task(task_type="test_context", intent="i", source=TaskSource(),
                   target=TaskTarget(files=["a.py"]), queries=["q"], priority="high",
                   expected_output="o", fallback="f", index=0, target_file="a.py"),
        _make_task(task_type="test_context", intent="i", source=TaskSource(),
                   target=TaskTarget(files=["a.py"]), queries=["q"], priority="medium",
                   expected_output="o", fallback="f", index=1, target_file="a.py"),
    ]
    summary = summarize_tasks(tasks)
    assert summary["by_priority"]["high"] == 1
    assert summary["by_priority"]["medium"] == 1


def test_cap_limits_per_type():
    tasks = [
        _make_task(task_type="test_context", intent="i", source=TaskSource(),
                   target=TaskTarget(files=[f"f{i}.py"]), queries=["q"], priority="high",
                   expected_output="o", fallback="f", index=i, target_file=f"f{i}.py")
        for i in range(15)
    ]
    capped = cap_tasks(tasks, cap=5)
    test_tasks = [t for t in capped if t.task_type == "test_context"]
    assert len(test_tasks) == 5


def test_cap_preserves_order():
    tasks = [
        _make_task(task_type="test_context", intent="i", source=TaskSource(),
                   target=TaskTarget(files=[f"f{i}.py"]), queries=["q"], priority="high",
                   expected_output="o", fallback="f", index=i, target_file=f"f{i}.py")
        for i in range(3)
    ]
    capped = cap_tasks(tasks, cap=2)
    assert capped[0].target.files[0] == "f0.py"
    assert capped[1].target.files[0] == "f1.py"


# --- 6.6 API tests ---


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app)


def test_context_tasks_success(client):
    f = _make_file(filename="src/main.py", is_source=True, priority_score_hint=80)
    ctx = _make_context(files=[f])
    _contexts[ctx.context_id] = ctx

    resp = client.post("/api/review/context-tasks", json={"context_id": ctx.context_id})
    assert resp.status_code == 200
    data = resp.json()
    assert data["context_id"] == ctx.context_id
    assert "tasks" in data
    assert "routes" in data
    assert "agents" in data
    assert "summary" in data
    assert "by_type" in data["summary"]
    assert "by_priority" in data["summary"]

    del _contexts[ctx.context_id]


def test_context_tasks_missing_context(client):
    resp = client.post("/api/review/context-tasks", json={"context_id": "ctx_nonexistent"})
    assert resp.status_code == 404


def test_context_tasks_response_has_all_routes(client):
    f = _make_file()
    ctx = _make_context(files=[f])
    _contexts[ctx.context_id] = ctx

    resp = client.post("/api/review/context-tasks", json={"context_id": ctx.context_id})
    assert resp.status_code == 200
    data = resp.json()
    route_types = {r["task_type"] for r in data["routes"]}
    assert route_types == TASK_TYPE_SET

    del _contexts[ctx.context_id]


def test_context_tasks_response_has_all_agents(client):
    f = _make_file()
    ctx = _make_context(files=[f])
    _contexts[ctx.context_id] = ctx

    resp = client.post("/api/review/context-tasks", json={"context_id": ctx.context_id})
    assert resp.status_code == 200
    data = resp.json()
    agent_types = {a["agent_type"] for a in data["agents"]}
    expected = {"test-context-agent", "reference-context-agent", "security-context-agent",
                "config-context-agent", "data-context-agent", "runtime-context-agent",
                "patch-deep-dive-agent"}
    assert agent_types == expected

    del _contexts[ctx.context_id]


def test_context_tasks_with_evidence(client):
    """Security evidence generates security_context tasks."""
    hunk = Hunk(
        header="@@ -1,0 +1,1 @@",
        old_start=1, old_lines=0, new_start=1, new_lines=1,
        lines=[HunkLine(type="added", content='eval(user_input)', old_line=None, new_line=1)],
    )
    f = _make_file(filename="src/main.py", hunks=[hunk])
    ctx = _make_context(files=[f])
    _contexts[ctx.context_id] = ctx

    resp = client.post("/api/review/context-tasks", json={"context_id": ctx.context_id})
    assert resp.status_code == 200
    data = resp.json()
    task_types = {t["task_type"] for t in data["tasks"]}
    assert "security_context" in task_types

    del _contexts[ctx.context_id]


def test_context_tasks_source_without_tests(client):
    """Source without tests generates test_context tasks."""
    f = _make_file(filename="src/main.py", is_source=True)
    ctx = _make_context(files=[f])
    ctx.derived = DerivedSignals(
        total_hunks=1, source_files_changed=1, test_files_changed=0,
        docs_only=False, has_source_without_tests=True, high_risk_files=[],
    )
    _contexts[ctx.context_id] = ctx

    resp = client.post("/api/review/context-tasks", json={"context_id": ctx.context_id})
    assert resp.status_code == 200
    data = resp.json()
    test_tasks = [t for t in data["tasks"] if t["task_type"] == "test_context"]
    assert len(test_tasks) >= 1

    del _contexts[ctx.context_id]


# --- Regression: no execution side effects ---


def test_context_tasks_no_hunks_in_response(client):
    hunk = Hunk(
        header="@@ -1,0 +1,1 @@",
        old_start=1, old_lines=0, new_start=1, new_lines=1,
        lines=[HunkLine(type="added", content="x = 1", old_line=None, new_line=1)],
    )
    f = _make_file(hunks=[hunk])
    ctx = _make_context(files=[f])
    _contexts[ctx.context_id] = ctx

    resp = client.post("/api/review/context-tasks", json={"context_id": ctx.context_id})
    assert resp.status_code == 200
    assert "hunks" not in resp.json()

    del _contexts[ctx.context_id]
