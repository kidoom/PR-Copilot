from collections import Counter

from backend.domain.pr_context.context_manager import PRContext, FileEntry


def classify_size(pr: PRContext) -> str:
    changed = pr.pr.changed_files
    total_lines = pr.pr.additions + pr.pr.deletions

    if changed <= 3 and total_lines <= 100:
        return "small"
    if changed <= 10 and total_lines <= 500:
        return "medium"
    return "large"


def classify_change_type(files: list[FileEntry]) -> str:
    if not files:
        return "mixed"

    total = len(files)
    docs_count = sum(1 for f in files if f.is_docs)
    test_count = sum(1 for f in files if f.is_test)
    config_count = sum(1 for f in files if f.is_config)
    source_count = sum(1 for f in files if f.is_source)

    if docs_count == total:
        return "docs"
    if test_count == total:
        return "test"
    if config_count == total:
        return "config"
    if source_count > 0 and source_count > max(test_count, config_count, docs_count):
        return "source"
    return "mixed"


def compute_language_distribution(files: list[FileEntry]) -> dict[str, int]:
    dist: Counter[str] = Counter()
    for f in files:
        lang = f.language or "unknown"
        dist[lang] += 1
    return dict(sorted(dist.items()))


def compute_file_type_distribution(files: list[FileEntry]) -> dict[str, int]:
    dist: Counter[str] = Counter()
    for f in files:
        if f.is_source:
            dist["source"] += 1
        elif f.is_test:
            dist["test"] += 1
        elif f.is_docs:
            dist["docs"] += 1
        elif f.is_config:
            dist["config"] += 1
        elif f.is_generated:
            dist["generated"] += 1
        elif f.is_binary:
            dist["binary"] += 1
        else:
            dist["unknown"] += 1
    return dict(sorted(dist.items()))


def compute_top_directories(files: list[FileEntry], limit: int = 10) -> list[dict]:
    dir_counts: Counter[str] = Counter()
    for f in files:
        parts = f.filename.rsplit("/", 1)
        directory = parts[0] if len(parts) > 1 else "."
        dir_counts[directory] += 1

    sorted_dirs = sorted(dir_counts.items(), key=lambda x: (-x[1], x[0]))
    return [{"directory": d, "file_count": c} for d, c in sorted_dirs[:limit]]


def derive_notable_signals(
    size: str,
    derived,
) -> list[str]:
    signals: list[str] = []

    if derived and derived.docs_only:
        signals.append("docs_only")
    if derived and derived.has_source_without_tests:
        signals.append("source_without_tests")
    if derived and derived.high_risk_files:
        signals.append("high_risk_paths_changed")
    if size == "large":
        signals.append("large_pr")

    return signals


def build_intake_summary(ctx: PRContext) -> dict:
    size = classify_size(ctx)
    change_type = classify_change_type(ctx.files)
    notable_signals = derive_notable_signals(size, ctx.derived)

    return {
        "context_id": ctx.context_id,
        "size": size,
        "change_type": change_type,
        "docs_only": ctx.derived.docs_only if ctx.derived else False,
        "source_without_tests": ctx.derived.has_source_without_tests if ctx.derived else False,
        "has_high_risk_paths": bool(ctx.derived.high_risk_files) if ctx.derived else False,
        "language_distribution": compute_language_distribution(ctx.files),
        "file_type_distribution": compute_file_type_distribution(ctx.files),
        "top_directories": compute_top_directories(ctx.files),
        "notable_signals": notable_signals,
    }
