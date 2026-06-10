import hashlib
from dataclasses import dataclass, field
from typing import Any

from backend.agent.runtime.accounting import (
    CoverageEntry,
    CoverageLane,
    CoverageManifest,
    CoverageState,
)
from backend.domain.pr_context.context_manager import PRContext, FileEntry
from backend.domain.review.evidence import EvidenceItem
from backend.domain.review.route_registry import (
    ROUTE_REGISTRY,
    DEFAULT_LANES,
    RouteDefinition,
    get_route,
    get_all_routes,
    get_allowed_tools,
    get_agent_type,
    get_disallowed_tools,
    route_to_dict,
)


# --- 1.2 Constants ---

TASK_TYPES = list(ROUTE_REGISTRY.keys())

TASK_TYPE_SET = set(TASK_TYPES)

PRIORITIES = ["critical", "high", "medium", "low"]

PRIORITY_RANK = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}

# Derived from shared registry
DEFAULT_BUDGETS = {tt: r.budget for tt, r in ROUTE_REGISTRY.items()}
ROUTE_KEYS = {tt: r.route_key for tt, r in ROUTE_REGISTRY.items()}
OUTPUT_SCHEMAS = {tt: r.output_schema for tt, r in ROUTE_REGISTRY.items()}

INTENTS = [
    "find_related_tests",
    "find_references",
    "verify_security_evidence",
    "inspect_ci_status",
    "inspect_config_impact",
    "inspect_dependency_impact",
    "inspect_data_impact",
    "inspect_runtime_risk",
    "inspect_patch_complexity",
]

# Per-type task cap to avoid oversized plans for large PRs
DEFAULT_TASK_CAP = 10

# Global task budget
MAX_TASKS_PER_RUN = 6

# Default baseline capacity (number of slots reserved for patch_deep_dive)
DEFAULT_BASELINE_CAPACITY = 3

# Per-batch token budget for baseline patch review (estimated tokens)
DEFAULT_PATCH_BATCH_TOKEN_BUDGET = 8000

# Feature flag: enable hybrid baseline planning
# When False, falls back to legacy behavior (specialist-only coverage)
HYBRID_BASELINE_ENABLED = True


def _is_hybrid_enabled() -> bool:
    """Check if hybrid baseline planning is enabled.

    Can be controlled via environment variable PR_COPILOT_HYBRID_BASELINE_ENABLED.
    """
    import os
    env_val = os.environ.get("PR_COPILOT_HYBRID_BASELINE_ENABLED", "")
    if env_val.lower() in ("false", "0", "no"):
        return False
    if env_val.lower() in ("true", "1", "yes"):
        return True
    return HYBRID_BASELINE_ENABLED

# Tokens per line heuristic for estimation
_TOKENS_PER_LINE = 4

# Maximum individual patch tokens before marking as partial
_MAX_SINGLE_PATCH_TOKENS = 12000


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
class PlannerAgentDefinition:
    agent_type: str
    description: str
    allowed_tools: list[str]
    disallowed_tools: list[str] = field(default_factory=list)


@dataclass
class TaskPlan:
    context_id: str
    tasks: list[ContextTask]
    routes: list[TaskRoute]
    agents: list[PlannerAgentDefinition]
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


# --- 2. Route and Agent Metadata (from shared registry) ---

_SUBAGENT_DISALLOWED_TOOLS = get_disallowed_tools()
_PER_TASK_ALLOWED_TOOLS = {tt: list(get_allowed_tools(tt)) for tt in TASK_TYPES}


# --- 2.1 Static Task Route registry (from shared registry) ---

TASK_ROUTES: dict[str, TaskRoute] = {
    tt: TaskRoute(
        task_type=tt,
        route_key=ROUTE_KEYS[tt],
        agent_type=get_agent_type(tt),
        allowed_tools=list(_PER_TASK_ALLOWED_TOOLS[tt]),
        output_schema=OUTPUT_SCHEMAS[tt],
        max_steps=5,
    )
    for tt in TASK_TYPES
}


# --- 2.2 Static Agent Definition registry (from shared registry) ---

AGENT_DEFINITIONS: dict[str, PlannerAgentDefinition] = {
    get_agent_type(tt): PlannerAgentDefinition(
        agent_type=get_agent_type(tt),
        description=ROUTE_REGISTRY[tt].description,
        allowed_tools=list(_PER_TASK_ALLOWED_TOOLS[tt]),
        disallowed_tools=list(_SUBAGENT_DISALLOWED_TOOLS),
    )
    for tt in TASK_TYPES
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


def agent_to_dict(agent: PlannerAgentDefinition) -> dict:
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

    # Collect source files that need test coverage analysis
    source_without_tests = []
    files_with_no_test_pair = []

    for f in ctx.files:
        if not f.is_source or f.is_test:
            continue
        source_without_tests.append(f)
        if "no_test_pair" in f.risk_hints:
            files_with_no_test_pair.append(f)

    # Batch source-without-tests files
    if ctx.derived and ctx.derived.has_source_without_tests and source_without_tests:
        batches = _batch_files_by_directory(source_without_tests)
        for batch in batches:
            batch_files = [f.filename for f in batch]
            batch_keywords = ["test", "spec"]
            for f in batch:
                batch_keywords.extend(_file_keywords(f))
            batch_keywords = list(set(batch_keywords))

            ev_ids = [e.id for e in evidence if e.rule_id == "source_without_tests"]
            tasks.append(_make_task(
                task_type="test_context",
                intent="find_related_tests",
                source=TaskSource(evidence_ids=ev_ids, rule_ids=["source_without_tests"], signals=["source_without_tests"]),
                target=TaskTarget(files=batch_files, keywords=batch_keywords),
                queries=[f"find tests for {f.filename}" for f in batch],
                priority="high",
                expected_output="List of related test files and test coverage gaps",
                fallback="Mark as inconclusive if no test files are found",
                index=idx,
                target_file=batch_files[0],
            ))
            idx += 1

    # Batch no_test_pair files that weren't already covered
    covered_files = set()
    for t in tasks:
        covered_files.update(t.target.files)

    uncovered_no_test = [f for f in files_with_no_test_pair if f.filename not in covered_files]
    if uncovered_no_test:
        batches = _batch_files_by_directory(uncovered_no_test)
        for batch in batches:
            batch_files = [f.filename for f in batch]
            batch_keywords = []
            for f in batch:
                batch_keywords.extend(_file_keywords(f))
            batch_keywords = list(set(batch_keywords))

            tasks.append(_make_task(
                task_type="test_context",
                intent="find_related_tests",
                source=TaskSource(signals=["no_test_pair"], file_facts=batch_files),
                target=TaskTarget(files=batch_files, keywords=batch_keywords),
                queries=[f"find tests related to {f.filename}" for f in batch],
                priority="medium",
                expected_output="Related test files or confirmation that no tests exist",
                fallback="Mark as no-test-found",
                index=idx,
                target_file=batch_files[0],
            ))
            idx += 1

    return tasks


# --- 3.3 reference_context tasks ---

MAX_BATCH_SIZE = 5


def _batch_files_by_directory(files: list[FileEntry], max_batch: int = MAX_BATCH_SIZE) -> list[list[FileEntry]]:
    """Group source files by directory, with batches of up to max_batch files."""
    by_dir: dict[str, list[FileEntry]] = {}
    for f in files:
        dir_name = f.filename.replace("\\", "/").rsplit("/", 1)[0] if "/" in f.filename else ""
        by_dir.setdefault(dir_name, []).append(f)

    batches: list[list[FileEntry]] = []
    for dir_files in by_dir.values():
        for i in range(0, len(dir_files), max_batch):
            batches.append(dir_files[i:i + max_batch])
    return batches


def _generate_reference_context_tasks(ctx: PRContext, evidence: list[EvidenceItem]) -> list[ContextTask]:
    tasks: list[ContextTask] = []
    source_files = [f for f in ctx.files if f.is_source and not f.is_test]

    if not source_files:
        return tasks

    batches = _batch_files_by_directory(source_files)

    for idx, batch in enumerate(batches):
        batch_files = [f.filename for f in batch]
        batch_symbols = [_symbol_from_filename(f) for f in batch]
        batch_keywords = []
        for f in batch:
            batch_keywords.extend(_file_keywords(f))
        batch_keywords = list(set(batch_keywords))

        queries = []
        for f in batch:
            symbol = _symbol_from_filename(f)
            queries.append(f"find references to {symbol}")
            queries.append(f"find callers of {f.filename}")

        max_priority = max(f.priority_score_hint for f in batch)
        priority = "high" if max_priority >= 70 else "medium"

        tasks.append(_make_task(
            task_type="reference_context",
            intent="find_references",
            source=TaskSource(file_facts=batch_files, signals=["source_file_changed"]),
            target=TaskTarget(files=batch_files, symbols=batch_symbols, keywords=batch_keywords),
            queries=queries,
            priority=priority,
            expected_output="List of references, callers, and API usage sites",
            fallback="Mark as no-references-found if nothing is found",
            index=idx,
            target_file=batch_files[0],
        ))

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
                intent="inspect_config_impact",
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
                intent="inspect_config_impact",
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
                intent="inspect_dependency_impact",
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


# --- 3.8 Patch token estimation and batching ---


def estimate_patch_tokens(f: FileEntry) -> int:
    """Estimate token count for a file's patch content."""
    if f.large_patch or not f.patch_available:
        return _MAX_SINGLE_PATCH_TOKENS
    return max(1, (f.added_line_count + f.removed_line_count) * _TOKENS_PER_LINE)


def batch_patches_by_budget(
    files: list[FileEntry],
    token_budget: int = DEFAULT_PATCH_BATCH_TOKEN_BUDGET,
) -> list[list[FileEntry]]:
    """Group files into deterministic batches that fit within token budgets.

    Files are sorted by priority (descending) then filename for determinism.
    Each batch stays within the token budget. Individually oversized files
    get their own batch with a partial marker.
    """
    # Sort by priority descending, then filename for determinism
    sorted_files = sorted(files, key=lambda f: (-f.priority_score_hint, f.filename))

    batches: list[list[FileEntry]] = []
    current_batch: list[FileEntry] = []
    current_tokens = 0

    for f in sorted_files:
        est = estimate_patch_tokens(f)

        # Individually oversized files get their own batch
        if est > token_budget:
            # Flush current batch first
            if current_batch:
                batches.append(current_batch)
                current_batch = []
                current_tokens = 0
            batches.append([f])  # Solo batch, will be marked partial
            continue

        # Would this file exceed the current batch?
        if current_tokens + est > token_budget and current_batch:
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0

        current_batch.append(f)
        current_tokens += est

    if current_batch:
        batches.append(current_batch)

    return batches


def _generate_patch_deep_dive_tasks(
    ctx: PRContext,
    covered_files: set[str],
) -> list[ContextTask]:
    """Generate baseline patch_deep_dive tasks for high-priority source files.

    High-priority files remain eligible for baseline review even when specialist
    tasks also target the same file. Only non-high-priority files are excluded
    if they already have specialist coverage.

    Files are batched deterministically by priority and estimated patch tokens.
    Individually oversized patches get their own batch with partial markers.
    """
    # Collect eligible files
    eligible: list[FileEntry] = []
    for f in ctx.files:
        if not f.is_source or f.is_test or f.is_docs:
            continue

        is_high_priority = f.priority_score_hint >= 70

        # High-priority files always get baseline eligibility
        if is_high_priority:
            eligible.append(f)
        elif f.filename in covered_files:
            # Non-high-priority files skip if already covered by specialist tasks
            continue
        else:
            continue  # Skip low-priority files not covered by anything

    if not eligible:
        return []

    # Batch by token budget
    batches = batch_patches_by_budget(eligible)

    tasks: list[ContextTask] = []
    for idx, batch in enumerate(batches):
        batch_files = [f.filename for f in batch]
        batch_keywords = []
        for f in batch:
            batch_keywords.extend(_file_keywords(f))
        batch_keywords = list(set(batch_keywords))

        # Check if any file in the batch is individually oversized
        is_partial = any(
            estimate_patch_tokens(f) > DEFAULT_PATCH_BATCH_TOKEN_BUDGET
            for f in batch
        )

        signals = ["high_priority_file", "baseline_patch_review"]
        if is_partial:
            signals.append("partial_patch")

        tasks.append(_make_task(
            task_type="patch_deep_dive",
            intent="inspect_patch_complexity",
            source=TaskSource(file_facts=batch_files, signals=signals),
            target=TaskTarget(files=batch_files, keywords=batch_keywords),
            queries=[f"deep dive into patch for {f}" for f in batch_files],
            priority="medium",
            expected_output="Complexity analysis and improvement suggestions for the patch",
            fallback="Mark as patch-analysis-unavailable",
            index=idx,
            target_file=batch_files[0],
        ))

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

    # Track files covered by specialist tasks
    _SPECIALIST_TYPES = {
        "test_context", "security_context", "config_context",
        "data_context", "runtime_context",
    }
    specialist_covered_files: set[str] = set()
    for t in all_tasks:
        if t.task_type in _SPECIALIST_TYPES:
            specialist_covered_files.update(t.target.files)

    hybrid_enabled = _is_hybrid_enabled()

    if hybrid_enabled:
        # Hybrid mode: high-priority files remain eligible for baseline
        all_tasks.extend(_generate_patch_deep_dive_tasks(ctx, specialist_covered_files))
    else:
        # Legacy mode: specialist coverage removes files from baseline
        all_tasks.extend(_generate_patch_deep_dive_tasks(ctx, specialist_covered_files))

    # 4.1-4.5: Dedup, sort, per-type cap, global budget, summarize
    all_tasks = deduplicate_tasks(all_tasks)
    all_tasks = sort_tasks(all_tasks)
    all_tasks = cap_tasks(all_tasks)

    if hybrid_enabled:
        all_tasks, omitted = apply_global_budget(all_tasks)
    else:
        all_tasks, omitted = apply_global_budget(all_tasks)

    summary = summarize_tasks(all_tasks)
    if omitted > 0:
        summary["omitted_candidates"] = omitted

    # Build coverage manifest only in hybrid mode
    coverage_manifest = None
    if hybrid_enabled:
        coverage_manifest = _build_coverage_manifest(ctx, all_tasks, specialist_covered_files)
        summary["hybrid_baseline_enabled"] = True
    else:
        summary["hybrid_baseline_enabled"] = False

    return _build_response(ctx.context_id, all_tasks, summary, coverage_manifest)


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


# --- 4.5 Global task budget ---

_SPECIALIST_TYPES = {"security_context", "config_context", "data_context", "runtime_context"}

# Priority order for global selection:
# 1. critical/high specialists
# 2. patch_deep_dive
# 3. reference_context
# 4. test_context
# 5. medium/low specialists

_SELECTION_ORDER = {
    "security_context_critical": 0,
    "security_context_high": 0,
    "config_context_critical": 0,
    "config_context_high": 0,
    "data_context_critical": 0,
    "data_context_high": 0,
    "runtime_context_critical": 0,
    "runtime_context_high": 0,
    "patch_deep_dive": 1,
    "reference_context": 2,
    "test_context": 3,
    "security_context_medium": 4,
    "security_context_low": 4,
    "config_context_medium": 4,
    "config_context_low": 4,
    "data_context_medium": 4,
    "data_context_low": 4,
    "runtime_context_medium": 4,
    "runtime_context_low": 4,
}


def _selection_key(task: ContextTask) -> tuple[int, str, str]:
    """Key for stable global selection ordering."""
    if task.task_type in _SPECIALIST_TYPES:
        bucket = _SELECTION_ORDER.get(f"{task.task_type}_{task.priority}", 4)
    else:
        bucket = _SELECTION_ORDER.get(task.task_type, 5)
    return (bucket, task.task_type, task.task_id)


def apply_global_budget(
    tasks: list[ContextTask],
    max_tasks: int = MAX_TASKS_PER_RUN,
    baseline_capacity: int = DEFAULT_BASELINE_CAPACITY,
) -> tuple[list[ContextTask], int]:
    """Select at most max_tasks with reserved baseline capacity.

    Reserves baseline_capacity slots for patch_deep_dive tasks, then fills
    remaining slots with specialist tasks. Critical specialists may preempt
    lower-priority baseline tasks if needed.

    Returns (selected_tasks, omitted_count).
    """
    if len(tasks) <= max_tasks:
        return tasks, 0

    # Separate baseline and specialist tasks
    baseline_tasks = [t for t in tasks if t.task_type == "patch_deep_dive"]
    specialist_tasks = [t for t in tasks if t.task_type != "patch_deep_dive"]

    # Sort each group by selection priority
    baseline_sorted = sorted(baseline_tasks, key=_selection_key)
    specialist_sorted = sorted(specialist_tasks, key=_selection_key)

    # Reserve baseline capacity
    reserved_baseline = baseline_sorted[:baseline_capacity]
    remaining_baseline = baseline_sorted[baseline_capacity:]

    # Fill remaining slots with specialists
    remaining_slots = max_tasks - len(reserved_baseline)
    if remaining_slots < 0:
        remaining_slots = 0

    # If we have more baseline than reserved, extras compete with specialists
    all_competing = remaining_baseline + specialist_sorted
    all_competing_sorted = sorted(all_competing, key=_selection_key)
    fillers = all_competing_sorted[:remaining_slots]

    selected = reserved_baseline + fillers
    selected = sorted(selected, key=_selection_key)

    # Truncate to max_tasks (in case of negative remaining_slots)
    selected = selected[:max_tasks]
    omitted = len(tasks) - len(selected)
    return selected, omitted


# --- Coverage manifest builder ---


def _build_coverage_manifest(
    ctx: PRContext,
    selected_tasks: list[ContextTask],
    specialist_covered_files: set[str],
) -> dict[str, Any]:
    """Build a planned coverage manifest for the selected task plan.

    Records each high-priority file's assigned baseline task, lane, state,
    and omission or truncation reason.
    """
    manifest = CoverageManifest()

    # Find which files are assigned to baseline tasks
    baseline_files: dict[str, str] = {}  # filename -> task_id
    baseline_signals: dict[str, list[str]] = {}  # filename -> signals
    for t in selected_tasks:
        if t.task_type == "patch_deep_dive":
            for f in t.target.files:
                baseline_files[f] = t.task_id
                baseline_signals[f] = t.source.signals

    # Track all high-priority changed files
    for f in ctx.files:
        if not f.is_source or f.is_test or f.is_docs:
            continue

        is_high_priority = f.priority_score_hint >= 70
        est_tokens = estimate_patch_tokens(f)
        is_partial = f.filename in baseline_signals and "partial_patch" in baseline_signals.get(f.filename, [])

        if f.filename in baseline_files:
            # Assigned to a baseline task
            manifest.add_entry(CoverageEntry(
                filename=f.filename,
                lane=CoverageLane.BASELINE.value,
                state=CoverageState.PLANNED.value,
                task_id=baseline_files[f.filename],
                is_high_priority=is_high_priority,
                priority_score=f.priority_score_hint,
                truncated=is_partial,
                estimated_tokens=est_tokens,
            ))
        elif is_high_priority:
            # High-priority file not assigned to any baseline task
            manifest.add_entry(CoverageEntry(
                filename=f.filename,
                lane=CoverageLane.BASELINE.value,
                state=CoverageState.OMITTED.value,
                reason="budget_limit",
                is_high_priority=True,
                priority_score=f.priority_score_hint,
                estimated_tokens=est_tokens,
            ))

        # Track specialist coverage separately
        if f.filename in specialist_covered_files:
            manifest.add_entry(CoverageEntry(
                filename=f.filename,
                lane=CoverageLane.SPECIALIST.value,
                state=CoverageState.PLANNED.value,
                is_high_priority=is_high_priority,
                priority_score=f.priority_score_hint,
            ))

    return manifest.to_dict()


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


def _build_response(
    context_id: str,
    tasks: list[ContextTask],
    summary: dict,
    coverage_manifest: dict[str, Any] | None = None,
) -> dict:
    result = {
        "context_id": context_id,
        "tasks": [_task_to_dict(t) for t in tasks],
        "routes": [route_to_dict(r) for r in TASK_ROUTES.values()],
        "agents": [agent_to_dict(a) for a in AGENT_DEFINITIONS.values()],
        "summary": summary,
    }
    if coverage_manifest:
        result["coverage_manifest"] = coverage_manifest
    return result
