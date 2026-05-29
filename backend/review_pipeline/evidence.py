import hashlib
import re
from enum import IntEnum
from dataclasses import dataclass, field

from backend.pr_context.context_manager import PRContext, FileEntry


# --- 1.2 Enums and constants ---

class Severity(IntEnum):
    CRITICAL = 0
    WARNING = 1
    INFO = 2


SEVERITY_RANK = {
    "critical": Severity.CRITICAL,
    "warning": Severity.WARNING,
    "info": Severity.INFO,
}

CATEGORIES = {"security", "reliability", "maintainability", "test", "config"}
SOURCES = {"rule_analyzer_v1"}

CONFIDENCE_MIN = 0.0
CONFIDENCE_MAX = 1.0


# --- 1.1 Evidence item data structure ---

@dataclass
class EvidenceItem:
    id: str
    source: str
    rule_id: str
    file: str
    severity: str
    category: str
    message: str
    confidence: float
    tags: list[str] = field(default_factory=list)
    line: int | None = None
    hunk_index: int | None = None
    excerpt: str | None = None


# --- 1.3 ID and dedup helpers ---

def make_evidence_id(source: str, rule_id: str, file: str, line: int | None, excerpt: str | None, message: str) -> str:
    parts = f"{source}:{rule_id}:{file}:{line}:{excerpt}:{message}"
    return hashlib.sha256(parts.encode()).hexdigest()[:16]


def dedup_key(item: EvidenceItem) -> str:
    return f"{item.source}:{item.rule_id}:{item.file}:{item.line}:{item.excerpt}:{item.message}"


# --- 2. Rule Analyzer v1 ---

SENSITIVE_PATTERNS = [
    (re.compile(r"""(?i)(?:secret|token|password|api[_-]?key|private[_-]?key)\s*[:=]\s*['"][^'"]{8,}['"]"""), "hardcoded_secret"),
    (re.compile(r"""(?i)(?:secret|token|password|api[_-]?key|private[_-]?key)\s*[:=]\s*[A-Za-z0-9+/=_-]{16,}"""), "suspicious_assignment"),
]

BARE_EXCEPT_PATTERN = re.compile(r"""^\s*except\s*:""")

DANGEROUS_EXEC_PATTERNS = [
    (re.compile(r"""(?i)\beval\s*\(""") , "eval_call"),
    (re.compile(r"""(?i)\bexec\s*\(""") , "exec_call"),
    (re.compile(r"""(?i)\bos\.system\s*\(""") , "os_system_call"),
    (re.compile(r"""(?i)\bsubprocess\.\w+\s*\(""") , "subprocess_call"),
    (re.compile(r"""(?i)\b__import__\s*\(""") , "dynamic_import"),
]

SQL_CONSTRUCTION_PATTERNS = [
    (re.compile(r"""(?i)(?:SELECT|INSERT|UPDATE|DELETE|DROP|ALTER)\s+.*['"]\s*\+"""), "sql_concat"),
    (re.compile(r"""(?i)(?:SELECT|INSERT|UPDATE|DELETE|DROP|ALTER)\s+.*\{"""), "sql_format"),
    (re.compile(r"""(?i)(?:SELECT|INSERT|UPDATE|DELETE|DROP|ALTER)\s+.*%s"""), "sql_percent"),
    (re.compile(r"""(?i)(?:SELECT|INSERT|UPDATE|DELETE|DROP|ALTER)\s+.*\$\{"""), "sql_template"),
]

LARGE_CHANGE_LINES = 500

_SECRET_REDACT_PATTERN = re.compile(r"""(?i)(secret|token|password|api[_-]?key|private[_-]?key)\s*[:=]\s*(['"]?)[^\s'"]{8,}(['"]?)""")


def _redact_excerpt(text: str) -> str:
    return _SECRET_REDACT_PATTERN.sub(r'\1=<REDACTED>', text)


def _make_item(
    source: str,
    rule_id: str,
    file: str,
    severity: str,
    category: str,
    message: str,
    confidence: float,
    tags: list[str],
    line: int | None = None,
    hunk_index: int | None = None,
    excerpt: str | None = None,
) -> EvidenceItem:
    eid = make_evidence_id(source, rule_id, file, line, excerpt, message)
    return EvidenceItem(
        id=eid,
        source=source,
        rule_id=rule_id,
        file=file,
        severity=severity,
        category=category,
        message=message,
        confidence=confidence,
        tags=tags,
        line=line,
        hunk_index=hunk_index,
        excerpt=excerpt,
    )


def _scan_sensitive_field(file: str, line_no: int, hunk_idx: int, content: str) -> EvidenceItem | None:
    for pattern, tag in SENSITIVE_PATTERNS:
        if pattern.search(content):
            return _make_item(
                source="rule_analyzer_v1",
                rule_id="sensitive_field",
                file=file,
                severity="critical",
                category="security",
                message="Possible hardcoded secret or credential detected",
                confidence=0.8,
                tags=[tag],
                line=line_no,
                hunk_index=hunk_idx,
                excerpt=_redact_excerpt(content.strip()[:120]),
            )
    return None


def _scan_bare_except(file: str, line_no: int, hunk_idx: int, content: str) -> EvidenceItem | None:
    if BARE_EXCEPT_PATTERN.search(content):
        return _make_item(
            source="rule_analyzer_v1",
            rule_id="bare_except",
            file=file,
            severity="warning",
            category="reliability",
            message="Bare except clause catches all exceptions including KeyboardInterrupt and SystemExit",
            confidence=0.9,
            tags=["bare_except"],
            line=line_no,
            hunk_index=hunk_idx,
            excerpt=content.strip()[:120],
        )
    return None


def _scan_dangerous_exec(file: str, line_no: int, hunk_idx: int, content: str) -> EvidenceItem | None:
    for pattern, tag in DANGEROUS_EXEC_PATTERNS:
        if pattern.search(content):
            return _make_item(
                source="rule_analyzer_v1",
                rule_id="dangerous_exec",
                file=file,
                severity="warning",
                category="security",
                message="Dynamic code execution detected",
                confidence=0.7,
                tags=[tag],
                line=line_no,
                hunk_index=hunk_idx,
                excerpt=content.strip()[:120],
            )
    return None


def _scan_sql_construction(file: str, line_no: int, hunk_idx: int, content: str) -> EvidenceItem | None:
    for pattern, tag in SQL_CONSTRUCTION_PATTERNS:
        if pattern.search(content):
            return _make_item(
                source="rule_analyzer_v1",
                rule_id="sql_injection",
                file=file,
                severity="warning",
                category="security",
                message="Possible SQL injection through string construction",
                confidence=0.6,
                tags=[tag],
                line=line_no,
                hunk_index=hunk_idx,
                excerpt=content.strip()[:120],
            )
    return None


def _scan_added_lines(file_entry: FileEntry) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for hunk_idx, hunk in enumerate(file_entry.hunks):
        for line in hunk.lines:
            if line.type != "added" or line.new_line is None:
                continue
            content = line.content
            for scanner in [_scan_sensitive_field, _scan_bare_except, _scan_dangerous_exec, _scan_sql_construction]:
                item = scanner(file_entry.filename, line.new_line, hunk_idx, content)
                if item:
                    # scope bare_except to python files
                    if item.rule_id == "bare_except" and file_entry.language != "python":
                        continue
                    items.append(item)
    return items


def _file_level_evidence(file_entry: FileEntry) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []

    if file_entry.is_high_risk_path:
        items.append(_make_item(
            source="rule_analyzer_v1",
            rule_id="high_risk_path",
            file=file_entry.filename,
            severity="warning",
            category="security",
            message=f"File in high-risk path: {file_entry.filename}",
            confidence=0.7,
            tags=file_entry.risk_hints[:5],
        ))

    if not file_entry.patch_available:
        items.append(_make_item(
            source="rule_analyzer_v1",
            rule_id="patch_unavailable",
            file=file_entry.filename,
            severity="info",
            category="maintainability",
            message="Patch not available for this file; review visibility is limited",
            confidence=1.0,
            tags=["patch_unavailable"],
        ))
    elif file_entry.large_patch:
        items.append(_make_item(
            source="rule_analyzer_v1",
            rule_id="large_patch",
            file=file_entry.filename,
            severity="info",
            category="maintainability",
            message="Patch is large; review may need to be scoped or chunked",
            confidence=1.0,
            tags=["large_patch"],
        ))

    return items


def _pr_level_evidence(ctx: PRContext) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []

    if ctx.derived and ctx.derived.has_source_without_tests:
        items.append(_make_item(
            source="rule_analyzer_v1",
            rule_id="source_without_tests",
            file="",
            severity="warning",
            category="test",
            message="Source files changed without corresponding test changes",
            confidence=0.8,
            tags=["no_test_pair"],
        ))

    return items


def analyze(ctx: PRContext) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for f in ctx.files:
        items.extend(_scan_added_lines(f))
        items.extend(_file_level_evidence(f))
    items.extend(_pr_level_evidence(ctx))
    return items


# --- 3. Evidence Store ---

def deduplicate(items: list[EvidenceItem]) -> list[EvidenceItem]:
    seen: set[str] = set()
    result: list[EvidenceItem] = []
    for item in items:
        key = dedup_key(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def sort_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    return sorted(items, key=lambda e: (
        SEVERITY_RANK.get(e.severity, Severity.INFO),
        -e.confidence,
        e.file,
        e.line or 0,
    ))


def summarize(items: list[EvidenceItem]) -> dict:
    by_severity: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for item in items:
        by_severity[item.severity] = by_severity.get(item.severity, 0) + 1
        by_category[item.category] = by_category.get(item.category, 0) + 1
    return {"by_severity": by_severity, "by_category": by_category}


def item_to_dict(item: EvidenceItem) -> dict:
    d: dict = {
        "id": item.id,
        "source": item.source,
        "rule_id": item.rule_id,
        "file": item.file,
        "severity": item.severity,
        "category": item.category,
        "message": item.message,
        "confidence": item.confidence,
        "tags": item.tags,
    }
    if item.line is not None:
        d["line"] = item.line
    if item.hunk_index is not None:
        d["hunk_index"] = item.hunk_index
    if item.excerpt is not None:
        d["excerpt"] = item.excerpt
    return d


def build_evidence_response(ctx: PRContext) -> dict:
    raw = analyze(ctx)
    items = sort_evidence(deduplicate(raw))
    summary = summarize(items)
    return {
        "context_id": ctx.context_id,
        "items": [item_to_dict(i) for i in items],
        "summary": summary,
    }
