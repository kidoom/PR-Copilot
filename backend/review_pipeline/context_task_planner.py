import hashlib
from dataclasses import dataclass, field

from backend.pr_context.context_manager import PRContext, FileEntry
from backend.review_pipeline.evidence import EvidenceItem


# --- 1.2 Constants ---

TASK_TYPES = [
    "test_context",
    "reference_context",
    "security_context",
    "config_context",
    "data_context",
    "runtime_context",
    "patch_deep_dive",
]

TASK_TYPE_SET = set(TASK_TYPES)

PRIORITIES = ["critical", "high", "medium", "low"]

PRIORITY_RANK = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}

DEFAULT_BUDGETS = {
    "test_context": {"max_searches": 5, "max_files": 10, "max_tokens": 3000},
    "reference_context": {"max_searches": 5, "max_files": 10, "max_tokens": 3000},
    "security_context": {"max_searches": 4, "max_files": 8, "max_tokens": 2500},
    "config_context": {"max_searches": 3, "max_files": 6, "max_tokens": 2000},
    "data_context": {"max_searches": 4, "max_files": 8, "max_tokens": 2500},
    "runtime_context": {"max_searches": 4, "max_files": 8, "max_tokens": 2500},
    "patch_deep_dive": {"max_searches": 3, "max_files": 5, "max_tokens": 2000},
}

ROUTE_KEYS = {
    "test_context": "route:test_context",
    "reference_context": "route:reference_context",
    "security_context": "route:security_context",
    "config_context": "route:config_context",
    "data_context": "route:data_context",
    "runtime_context": "route:runtime_context",
    "patch_deep_dive": "route:patch_deep_dive",
}

OUTPUT_SCHEMAS = {
    "test_context": {
        "type": "object",
        "properties": {
            "related_tests": {"type": "array", "items": {"type": "string"}},
            "test_gaps": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
    },
    "reference_context": {
        "type": "object",
        "properties": {
            "references": {"type": "array", "items": {"type": "string"}},
            "callers": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
    },
    "security_context": {
        "type": "object",
        "properties": {
            "findings": {"type": "array", "items": {"type": "string"}},
            "risk_level": {"type": "string"},
            "summary": {"type": "string"},
        },
    },
    "config_context": {
        "type": "object",
        "properties": {
            "config_files": {"type": "array", "items": {"type": "string"}},
            "ci_status": {"type": "string"},
            "summary": {"type": "string"},
        },
    },
    "data_context": {
        "type": "object",
        "properties": {
            "models": {"type": "array", "items": {"type": "string"}},
            "migrations": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
    },
    "runtime_context": {
        "type": "object",
        "properties": {
            "risks": {"type": "array", "items": {"type": "string"}},
            "patterns": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
    },
    "patch_deep_dive": {
        "type": "object",
        "properties": {
            "complexity_notes": {"type": "array", "items": {"type": "string"}},
            "suggestions": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
    },
}

INTENTS = [
    "find_related_tests",
    "find_references",
    "verify_security_evidence",
    "inspect_ci_status",
    "inspect_data_impact",
    "inspect_runtime_risk",
    "inspect_patch_complexity",
]

# Per-type task cap to avoid oversized plans for large PRs
DEFAULT_TASK_CAP = 10


# --- 1.1 Data structures ---

@dataclass
class TaskSource:
    evidence_ids: list[str] = field(default_factory=list)
    rule_ids: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    file_facts: list[str] = field(default_factory=list)


@dataclass
class TaskTarget:
    files: list[str] = field(default_factory=list)
    directories: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)


@dataclass
class TaskBudget:
    max_searches: int = 5
    max_files: int = 10
    max_tokens: int = 3000


@dataclass
class ContextTask:
    task_id: str
    task_type: str
    intent: str
    route_key: str
    source: TaskSource
    target: TaskTarget
    queries: list[str]
    priority: str
    budget: TaskBudget
    expected_output: str
    fallback: str
    status: str = "pending"


@dataclass
class TaskRoute:
    task_type: str
    route_key: str
    agent_type: str
    allowed_tools: list[str]
    output_schema: dict
    max_steps: int = 5


@dataclass
class AgentDefinition:
    agent_type: str
    description: str
    allowed_tools: list[str]
    disallowed_tools: list[str] = field(default_factory=list)


@dataclass
class TaskPlan:
    context_id: str
    tasks: list[ContextTask]
    routes: list[TaskRoute]
    agents: list[AgentDefinition]
    summary: dict


# --- 1.3 Stable task ID and deduplication helpers ---

def make_task_id(task_type: str, intent: str, target_file: str, index: int) -> str:
    parts = f"{task_type}:{intent}:{target_file}:{index}"
    return f"task_{hashlib.sha256(parts.encode()).hexdigest()[:12]}"


def task_identity(task: ContextTask) -> str:
    target_key = "|".join(sorted(task.target.files))
    query_key = "|".join(sorted(task.queries))
    source_key = "|".join(sorted(task.source.evidence_ids))
    return f"{task.task_type}:{task.intent}:{source_key}:{target_key}:{query_key}"


# --- 2. Route and Agent Metadata ---

_READ_ONLY_TOOLS = [
    "search_repo",
    "read_repo_file",
    "read_file_patch",
    "search_diff",
    "read_check_summary",
    "read_review_comments_summary",
]

# SubAgents must not recursively spawn more tasks
_SUBAGENT_DISALLOWED_TOOLS = ["task_tool", "sub_agent"]


# --- 2.1 Static Task Route registry ---

TASK_ROUTES: dict[str, TaskRoute] = {
    tt: TaskRoute(
        task_type=tt,
        route_key=ROUTE_KEYS[tt],
        agent_type=tt.replace("_", "-") + "-agent",
        allowed_tools=list(_READ_ONLY_TOOLS),
        output_schema=OUTPUT_SCHEMAS[tt],
        max_steps=5,
    )
    for tt in TASK_TYPES
}


# --- 2.2 Static Agent Definition registry ---

AGENT_DEFINITIONS: dict[str, AgentDefinition] = {
    "test-context-agent": AgentDefinition(
        agent_type="test-context-agent",
        description="Finds related tests, test gaps, and test coverage signals for changed source files",
        allowed_tools=list(_READ_ONLY_TOOLS),
        disallowed_tools=list(_SUBAGENT_DISALLOWED_TOOLS),
    ),
    "reference-context-agent": AgentDefinition(
        agent_type="reference-context-agent",
        description="Finds references, callers, API usage, and symbol impact for changed files",
        allowed_tools=list(_READ_ONLY_TOOLS),
        disallowed_tools=list(_SUBAGENT_DISALLOWED_TOOLS),
    ),
    "security-context-agent": AgentDefinition(
        agent_type="security-context-agent",
        description="Inspects authentication, authorization, secrets, SQL risk, and input validation context",
        allowed_tools=list(_READ_ONLY_TOOLS),
        disallowed_tools=list(_SUBAGENT_DISALLOWED_TOOLS),
    ),
    "config-context-agent": AgentDefinition(
        agent_type="config-context-agent",
        description="Inspects configuration, environment variables, dependency files, CI/checks, and deployment context",
        allowed_tools=list(_READ_ONLY_TOOLS),
        disallowed_tools=list(_SUBAGENT_DISALLOWED_TOOLS),
    ),
    "data-context-agent": AgentDefinition(
        agent_type="data-context-agent",
        description="Inspects database, schema, migration, cache, model, and data access context",
        allowed_tools=list(_READ_ONLY_TOOLS),
        disallowed_tools=list(_SUBAGENT_DISALLOWED_TOOLS),
    ),
    "runtime-context-agent": AgentDefinition(
        agent_type="runtime-context-agent",
        description="Inspects exception handling, async behavior, concurrency, timeouts, retries, and resource lifecycle",
        allowed_tools=list(_READ_ONLY_TOOLS),
        disallowed_tools=list(_SUBAGENT_DISALLOWED_TOOLS),
    ),
    "patch-deep-dive-agent": AgentDefinition(
        agent_type="patch-deep-dive-agent",
        description="Performs deep local inspection of high-priority or complex patches",
        allowed_tools=list(_READ_ONLY_TOOLS),
        disallowed_tools=list(_SUBAGENT_DISALLOWED_TOOLS),
    ),
}


def route_to_dict(route: TaskRoute) -> dict:
    return {
        "task_type": route.task_type,
        "route_key": route.route_key,
        "agent_type": route.agent_type,
        "allowed_tools": route.allowed_tools,
        "output_schema": route.output_schema,
        "max_steps": route.max_steps,
    }


def agent_to_dict(agent: AgentDefinition) -> dict:
    return {
        "agent_type": agent.agent_type,
        "description": agent.description,
        "allowed_tools": agent.allowed_tools,
        "disallowed_tools": agent.disallowed_tools,
    }


# --- 3. Planner helpers ---

def _file_keywords(f: FileEntry) -> list[str]:
    """Extract useful search keywords from a file entry."""
    parts = f.filename.replace("\\", "/").split("/")
    name = parts[-1] if parts else f.filename
    base = name.rsplit(".", 1)[0] if "." in name else name
    keywords = [base]
    if f.keywords:
        keywords.extend(f.keywords[:5])
    return keywords


def _symbol_from_filename(f: FileEntry) -> str:
    """Derive a likely symbol/class name from filename."""
    name = f.filename.replace("\\", "/").split("/")[-1]
    base = name.rsplit(".", 1)[0] if "." in name else name
    # Convert snake_case to CamelCase as a guess
    return "".join(w.capitalize() for w in base.split("_"))


def _make_task(
    task_type: str,
    intent: str,
    source: TaskSource,
    target: TaskTarget,
    queries: list[str],
    priority: str,
    expected_output: str,
    fallback: str,
    index: int,
    target_file: str = "",
) -> ContextTask:
    budget = TaskBudget(**DEFAULT_BUDGETS[task_type])
    tid = make_task_id(task_type, intent, target_file, index)
    return ContextTask(
        task_id=tid,
        task_type=task_type,
        intent=intent,
        route_key=ROUTE_KEYS[task_type],
        source=source,
        target=target,
        queries=queries,
        priority=priority,
        budget=budget,
        expected_output=expected_output,
        fallback=fallback,
    )


# --- 3.2 test_context tasks ---

def _generate_test_context_tasks(ctx: PRContext, evidence: list[EvidenceItem]) -> list[ContextTask]:
    tasks: list[ContextTask] = []
    idx = 0

    # Source files without tests
    if ctx.derived and ctx.derived.has_source_without_tests:
        source_files = [f.filename for f in ctx.files if f.is_source and not f.is_test]
        if source_files:
            ev_ids = [e.id for e in evidence if e.rule_id == "source_without_tests"]
            tasks.append(_make_task(
                task_type="test_context",
                intent="find_related_tests",
                source=TaskSource(evidence_ids=ev_ids, rule_ids=["source_without_tests"], signals=["source_without_tests"]),
                target=TaskTarget(files=source_files[:5], keywords=["test", "spec"]),
                queries=[f"find tests for {f}" for f in source_files[:3]],
                priority="high",
                expected_output="List of related test files and test coverage gaps",
                fallback="Mark as inconclusive if no test files are found",
                index=idx,
                target_file=source_files[0],
            ))
            idx += 1

    # Per-source-file test lookup for files with no_test_pair hint
    for f in ctx.files:
        if f.is_source and not f.is_test and "no_test_pair" in f.risk_hints:
            kw = _file_keywords(f)
            tasks.append(_make_task(
                task_type="test_context",
                intent="find_related_tests",
                source=TaskSource(signals=["no_test_pair"], file_facts=[f.filename]),
                target=TaskTarget(files=[f.filename], keywords=kw),
                queries=[f"find tests related to {f.filename}"],
                priority="medium",
                expected_output="Related test files or confirmation that no tests exist",
                fallback="Mark as no-test-found",
                index=idx,
                target_file=f.filename,
            ))
            idx += 1

    return tasks


# --- 3.3 reference_context tasks ---

def _generate_reference_context_tasks(ctx: PRContext, evidence: list[EvidenceItem]) -> list[ContextTask]:
    tasks: list[ContextTask] = []
    idx = 0

    for f in ctx.files:
        if not f.is_source or f.is_test:
            continue
        kw = _file_keywords(f)
        symbol = _symbol_from_filename(f)
        queries = [f"find references to {symbol}", f"find callers of {f.filename}"]
        priority = "high" if f.priority_score_hint >= 70 else "medium"
        tasks.append(_make_task(
            task_type="reference_context",
            intent="find_references",
            source=TaskSource(file_facts=[f.filename], signals=["source_file_changed"]),
            target=TaskTarget(files=[f.filename], symbols=[symbol], keywords=kw),
            queries=queries,
            priority=priority,
            expected_output="List of references, callers, and API usage sites",
            fallback="Mark as no-references-found if nothing is found",
            index=idx,
            target_file=f.filename,
        ))
        idx += 1

    return tasks


# --- 3.4 security_context tasks ---

_SECURITY_RULE_IDS = {"sensitive_field", "dangerous_exec", "sql_injection", "high_risk_path"}
_SECURITY_RISK_HINTS = {"auth_path", "payment_path"}


def _generate_security_context_tasks(ctx: PRContext, evidence: list[EvidenceItem]) -> list[ContextTask]:
    tasks: list[ContextTask] = []
    idx = 0
    covered_files: set[str] = set()

    # Tasks from security evidence
    for e in evidence:
        if e.rule_id in _SECURITY_RULE_IDS and e.file:
            if e.file in covered_files:
                continue
            covered_files.add(e.file)
            ev_ids = [x.id for x in evidence if x.file == e.file and x.rule_id in _SECURITY_RULE_IDS]
            tasks.append(_make_task(
                task_type="security_context",
                intent="verify_security_evidence",
                source=TaskSource(evidence_ids=ev_ids, rule_ids=[e.rule_id], signals=[e.category]),
                target=TaskTarget(files=[e.file], keywords=_file_keywords(_find_file(ctx, e.file) or ctx.files[0])),
                queries=[f"inspect security context for {e.file}"],
                priority="critical" if e.severity == "critical" else "high",
                expected_output="Security findings, risk assessment, and related patterns",
                fallback="Mark as no-security-context-found",
                index=idx,
                target_file=e.file,
            ))
            idx += 1

    # Tasks from risk hints
    for f in ctx.files:
        if f.filename in covered_files:
            continue
        if any(h in _SECURITY_RISK_HINTS for h in f.risk_hints):
            covered_files.add(f.filename)
            tasks.append(_make_task(
                task_type="security_context",
                intent="verify_security_evidence",
                source=TaskSource(signals=f.risk_hints, file_facts=[f.filename]),
                target=TaskTarget(files=[f.filename], keywords=_file_keywords(f)),
                queries=[f"inspect security context for {f.filename}"],
                priority="high",
                expected_output="Security findings and risk assessment for high-risk path",
                fallback="Mark as no-security-context-found",
                index=idx,
                target_file=f.filename,
            ))
            idx += 1

    return tasks


def _find_file(ctx: PRContext, filename: str) -> FileEntry | None:
    for f in ctx.files:
        if f.filename == filename:
            return f
    return None


# --- 3.5 config_context tasks ---

_CONFIG_RULE_IDS = {"high_risk_path"}
_CONFIG_RISK_HINTS = {"config_path"}


def _generate_config_context_tasks(ctx: PRContext, evidence: list[EvidenceItem]) -> list[ContextTask]:
    tasks: list[ContextTask] = []
    idx = 0
    covered_files: set[str] = set()

    # Config-classified files
    for f in ctx.files:
        if f.is_config:
            covered_files.add(f.filename)
            tasks.append(_make_task(
                task_type="config_context",
                intent="inspect_ci_status",
                source=TaskSource(file_facts=[f.filename], signals=["config_file_changed"]),
                target=TaskTarget(files=[f.filename], keywords=_file_keywords(f)),
                queries=[f"inspect config file {f.filename}", "check CI status and workflow impact"],
                priority="medium",
                expected_output="Config file analysis and CI/checks status",
                fallback="Mark as config-check-unavailable",
                index=idx,
                target_file=f.filename,
            ))
            idx += 1

    # Files with config_path risk hint
    for f in ctx.files:
        if f.filename in covered_files:
            continue
        if any(h in _CONFIG_RISK_HINTS for h in f.risk_hints):
            covered_files.add(f.filename)
            tasks.append(_make_task(
                task_type="config_context",
                intent="inspect_ci_status",
                source=TaskSource(signals=["config_path"], file_facts=[f.filename]),
                target=TaskTarget(files=[f.filename], keywords=_file_keywords(f)),
                queries=[f"inspect config impact of {f.filename}"],
                priority="medium",
                expected_output="Configuration impact analysis",
                fallback="Mark as no-config-impact",
                index=idx,
                target_file=f.filename,
            ))
            idx += 1

    # Dependency files
    dep_patterns = {"package.json", "requirements.txt", "Pipfile", "pyproject.toml", "Cargo.toml", "go.mod", "Gemfile", "pom.xml", "build.gradle"}
    for f in ctx.files:
        name = f.filename.replace("\\", "/").split("/")[-1]
        if f.filename in covered_files:
            continue
        if name in dep_patterns:
            covered_files.add(f.filename)
            tasks.append(_make_task(
                task_type="config_context",
                intent="inspect_ci_status",
                source=TaskSource(file_facts=[f.filename], signals=["dependency_file_changed"]),
                target=TaskTarget(files=[f.filename], keywords=_file_keywords(f)),
                queries=[f"inspect dependency changes in {f.filename}"],
                priority="medium",
                expected_output="Dependency change impact analysis",
                fallback="Mark as no-dependency-impact",
                index=idx,
                target_file=f.filename,
            ))
            idx += 1

    return tasks


# --- 3.6 data_context tasks ---

_DATA_RULE_IDS: set[str] = set()
_DATA_RISK_HINTS = {"db_path"}


def _generate_data_context_tasks(ctx: PRContext, evidence: list[EvidenceItem]) -> list[ContextTask]:
    tasks: list[ContextTask] = []
    idx = 0
    covered_files: set[str] = set()

    # Files with db_path risk hint
    for f in ctx.files:
        if any(h in _DATA_RISK_HINTS for h in f.risk_hints):
            covered_files.add(f.filename)
            tasks.append(_make_task(
                task_type="data_context",
                intent="inspect_data_impact",
                source=TaskSource(signals=["db_path"], file_facts=[f.filename]),
                target=TaskTarget(files=[f.filename], keywords=_file_keywords(f)),
                queries=[f"inspect data impact of {f.filename}", "find related migrations and schema changes"],
                priority="high",
                expected_output="Data access patterns, migration impact, and schema analysis",
                fallback="Mark as no-data-context-found",
                index=idx,
                target_file=f.filename,
            ))
            idx += 1

    # Files with migration/schema/model signals
    data_keywords = {"migration", "schema", "model", "database", "db"}
    for f in ctx.files:
        if f.filename in covered_files:
            continue
        name_lower = f.filename.lower()
        if any(kw in name_lower for kw in ["migration", "schema", "model", "database", "db/"]):
            covered_files.add(f.filename)
            tasks.append(_make_task(
                task_type="data_context",
                intent="inspect_data_impact",
                source=TaskSource(signals=["data_file_changed"], file_facts=[f.filename]),
                target=TaskTarget(files=[f.filename], keywords=_file_keywords(f)),
                queries=[f"inspect data context for {f.filename}"],
                priority="medium",
                expected_output="Data model and schema analysis",
                fallback="Mark as no-data-context-found",
                index=idx,
                target_file=f.filename,
            ))
            idx += 1

    # Files with SQL-related evidence
    for e in evidence:
        if e.rule_id == "sql_injection" and e.file and e.file not in covered_files:
            covered_files.add(e.file)
            tasks.append(_make_task(
                task_type="data_context",
                intent="inspect_data_impact",
                source=TaskSource(evidence_ids=[e.id], rule_ids=["sql_injection"], signals=["security"]),
                target=TaskTarget(files=[e.file], keywords=_file_keywords(_find_file(ctx, e.file) or ctx.files[0])),
                queries=[f"inspect SQL usage and data access in {e.file}"],
                priority="high",
                expected_output="SQL construction patterns and data access analysis",
                fallback="Mark as no-sql-context-found",
                index=idx,
                target_file=e.file,
            ))
            idx += 1

    return tasks


# --- 3.7 runtime_context tasks ---

_RUNTIME_RULE_IDS = {"bare_except"}
_RUNTIME_SIGNAL_KEYWORDS = {
    "async", "await", "timeout", "retry", "exception", "error_handling",
    "concurrency", "threading", "multiprocessing", "resource", "lifecycle",
    "cleanup", "finally", "context_manager",
}


def _generate_runtime_context_tasks(ctx: PRContext, evidence: list[EvidenceItem]) -> list[ContextTask]:
    tasks: list[ContextTask] = []
    idx = 0
    covered_files: set[str] = set()

    # Tasks from runtime evidence (bare_except)
    for e in evidence:
        if e.rule_id in _RUNTIME_RULE_IDS and e.file:
            if e.file in covered_files:
                continue
            covered_files.add(e.file)
            tasks.append(_make_task(
                task_type="runtime_context",
                intent="inspect_runtime_risk",
                source=TaskSource(evidence_ids=[e.id], rule_ids=[e.rule_id], signals=[e.category]),
                target=TaskTarget(files=[e.file], keywords=_file_keywords(_find_file(ctx, e.file) or ctx.files[0])),
                queries=[f"inspect runtime behavior in {e.file}"],
                priority="high",
                expected_output="Exception handling patterns and runtime risk analysis",
                fallback="Mark as no-runtime-context-found",
                index=idx,
                target_file=e.file,
            ))
            idx += 1

    # Files with runtime-related keywords
    for f in ctx.files:
        if f.filename in covered_files or not f.is_source:
            continue
        file_kws = set(k.lower() for k in f.keywords)
        matching = file_kws & _RUNTIME_SIGNAL_KEYWORDS
        if matching:
            covered_files.add(f.filename)
            tasks.append(_make_task(
                task_type="runtime_context",
                intent="inspect_runtime_risk",
                source=TaskSource(signals=list(matching), file_facts=[f.filename]),
                target=TaskTarget(files=[f.filename], keywords=_file_keywords(f)),
                queries=[f"inspect runtime patterns in {f.filename}"],
                priority="medium",
                expected_output="Async, concurrency, timeout, and resource lifecycle analysis",
                fallback="Mark as no-runtime-context-found",
                index=idx,
                target_file=f.filename,
            ))
            idx += 1

    return tasks


# --- 3.8 patch_deep_dive tasks ---

def _generate_patch_deep_dive_tasks(
    ctx: PRContext,
    covered_files: set[str],
) -> list[ContextTask]:
    tasks: list[ContextTask] = []
    idx = 0

    # High-priority source files not already covered by more specific tasks
    for f in ctx.files:
        if f.filename in covered_files:
            continue
        if not f.is_source or f.is_test or f.is_docs:
            continue
        if f.priority_score_hint < 70:
            continue
        tasks.append(_make_task(
            task_type="patch_deep_dive",
            intent="inspect_patch_complexity",
            source=TaskSource(file_facts=[f.filename], signals=["high_priority_file"]),
            target=TaskTarget(files=[f.filename], keywords=_file_keywords(f)),
            queries=[f"deep dive into patch for {f.filename}"],
            priority="medium",
            expected_output="Complexity analysis and improvement suggestions for the patch",
            fallback="Mark as patch-analysis-unavailable",
            index=idx,
            target_file=f.filename,
        ))
        idx += 1

    return tasks


# --- 3.1 Planner entrypoint ---

def build_context_task_plan(ctx: PRContext, evidence: list[EvidenceItem] | None = None) -> dict:
    if evidence is None:
        evidence = []

    all_tasks: list[ContextTask] = []

    # 3.2-3.8: Generate tasks by category
    all_tasks.extend(_generate_test_context_tasks(ctx, evidence))
    all_tasks.extend(_generate_reference_context_tasks(ctx, evidence))
    all_tasks.extend(_generate_security_context_tasks(ctx, evidence))
    all_tasks.extend(_generate_config_context_tasks(ctx, evidence))
    all_tasks.extend(_generate_data_context_tasks(ctx, evidence))
    all_tasks.extend(_generate_runtime_context_tasks(ctx, evidence))

    # Track files covered by specific tasks for patch_deep_dive
    covered_files: set[str] = set()
    for t in all_tasks:
        covered_files.update(t.target.files)

    all_tasks.extend(_generate_patch_deep_dive_tasks(ctx, covered_files))

    # 4.1-4.4: Dedup, sort, summarize, cap
    all_tasks = deduplicate_tasks(all_tasks)
    all_tasks = sort_tasks(all_tasks)
    all_tasks = cap_tasks(all_tasks)
    summary = summarize_tasks(all_tasks)

    return _build_response(ctx.context_id, all_tasks, summary)


# --- 4. Store Behavior ---

# --- 4.1 Deterministic deduplication ---

def deduplicate_tasks(tasks: list[ContextTask]) -> list[ContextTask]:
    seen: set[str] = set()
    result: list[ContextTask] = []
    for t in tasks:
        key = task_identity(t)
        if key not in seen:
            seen.add(key)
            result.append(t)
    return result


# --- 4.2 Deterministic sorting ---

def sort_tasks(tasks: list[ContextTask]) -> list[ContextTask]:
    return sorted(tasks, key=lambda t: (
        PRIORITY_RANK.get(t.priority, 99),
        t.task_type,
        t.target.files[0] if t.target.files else "",
        t.task_id,
    ))


# --- 4.3 Summary counts ---

def summarize_tasks(tasks: list[ContextTask]) -> dict:
    by_type: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    for t in tasks:
        by_type[t.task_type] = by_type.get(t.task_type, 0) + 1
        by_priority[t.priority] = by_priority.get(t.priority, 0) + 1
    return {"by_type": by_type, "by_priority": by_priority}


# --- 4.4 Per-type caps ---

def cap_tasks(tasks: list[ContextTask], cap: int = DEFAULT_TASK_CAP) -> list[ContextTask]:
    counts: dict[str, int] = {}
    result: list[ContextTask] = []
    for t in tasks:
        count = counts.get(t.task_type, 0)
        if count < cap:
            result.append(t)
            counts[t.task_type] = count + 1
    return result


# --- Response builder ---

def _task_to_dict(t: ContextTask) -> dict:
    return {
        "task_id": t.task_id,
        "task_type": t.task_type,
        "intent": t.intent,
        "route_key": t.route_key,
        "source": {
            "evidence_ids": t.source.evidence_ids,
            "rule_ids": t.source.rule_ids,
            "signals": t.source.signals,
            "file_facts": t.source.file_facts,
        },
        "target": {
            "files": t.target.files,
            "directories": t.target.directories,
            "symbols": t.target.symbols,
            "keywords": t.target.keywords,
        },
        "queries": t.queries,
        "priority": t.priority,
        "budget": {
            "max_searches": t.budget.max_searches,
            "max_files": t.budget.max_files,
            "max_tokens": t.budget.max_tokens,
        },
        "expected_output": t.expected_output,
        "fallback": t.fallback,
        "status": t.status,
    }


def _build_response(context_id: str, tasks: list[ContextTask], summary: dict) -> dict:
    return {
        "context_id": context_id,
        "tasks": [_task_to_dict(t) for t in tasks],
        "routes": [route_to_dict(r) for r in TASK_ROUTES.values()],
        "agents": [agent_to_dict(a) for a in AGENT_DEFINITIONS.values()],
        "summary": summary,
    }
