from .classifier import Classification

# Path risk keywords and their scores
_PATH_RISK_MAP: dict[frozenset[str], int] = {
    frozenset({"auth", "authentication", "authorization", "permission", "permissions", "security", "login", "oauth"}): 90,
    frozenset({"payment", "payments", "billing", "stripe", "checkout", "subscription"}): 90,
    frozenset({"db", "database", "databases", "migration", "migrations", "schema"}): 80,
    frozenset({"config", "configs", "configuration", "deploy", "deployment", "infra", "infrastructure", "ci", ".github"}): 70,
    frozenset({"api", "server", "backend", "routes", "handlers"}): 60,
}

_FILE_STATUS_SCORES: dict[str, int] = {
    "added": 80,
    "renamed": 60,
    "removed": 50,
    "modified": 40,
}

_LANG_FAMILY_RISK: dict[str, int] = {
    "backend": 70,
    "frontend": 50,
    "config": 65,
    "docs": 10,
    "unknown": 40,
}


def compute_path_risk_score(filename: str) -> int:
    """Score 0-100 based on path keywords."""
    parts = {p.lower() for p in filename.split("/")}
    for keywords, score in _PATH_RISK_MAP.items():
        if parts & keywords:
            return score

    # Low score for docs and test paths
    name = filename.rsplit("/", 1)[-1].lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext in ("md", "mdx", "rst", "txt") or "docs" in parts or "doc" in parts:
        return 10
    if (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.js")
        or name.endswith(".test.ts")
        or name.endswith(".test.jsx")
        or name.endswith(".test.tsx")
        or name.endswith(".spec.js")
        or name.endswith(".spec.ts")
        or name.endswith(".spec.jsx")
        or name.endswith(".spec.tsx")
        or "tests" in parts
        or "__tests__" in parts
    ):
        return 15

    return 40  # default for ordinary source paths


def compute_change_size_score(additions: int, deletions: int) -> int:
    """Score 0-100 based on change size, capped at 100."""
    return min(100, (additions + deletions) // 5)


def compute_file_status_score(status: str) -> int:
    """Score 0-100 based on file status."""
    return _FILE_STATUS_SCORES.get(status, 40)


def compute_language_risk_score(language_family: str) -> int:
    """Score 0-100 based on language family."""
    return _LANG_FAMILY_RISK.get(language_family, 40)


def compute_priority_score(
    classification: Classification,
    additions: int,
    deletions: int,
    status: str,
    filename: str = "",
) -> int:
    """Weighted priority score 0-100."""
    path_risk = compute_path_risk_score(filename) if filename else 40
    # Override path risk if high-risk path detected
    if classification.is_high_risk_path:
        path_risk = max(path_risk, 80)
    if classification.risk_hints:
        # Boost based on number of risk hints
        hint_boost = min(20, len(classification.risk_hints) * 10)
        path_risk = min(100, path_risk + hint_boost)

    change_size = compute_change_size_score(additions, deletions)
    file_status = compute_file_status_score(status)
    lang_risk = compute_language_risk_score(classification.language_family)

    # Lower score for non-source files
    if classification.is_docs:
        lang_risk = 10
    elif classification.is_test:
        lang_risk = 20
    elif classification.is_generated:
        lang_risk = 0

    score = (
        path_risk * 0.45
        + change_size * 0.30
        + file_status * 0.15
        + lang_risk * 0.10
    )
    return min(100, max(0, round(score)))
