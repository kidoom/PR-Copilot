from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .fetcher import ChangedFile

# --- Language identification ---

_EXT_TO_LANGUAGE: dict[str, tuple[str, str, str]] = {
    # (language, language_family, rule_profile)
    ".py": ("python", "backend", "python"),
    ".js": ("javascript", "frontend", "javascript"),
    ".jsx": ("javascript", "frontend", "javascript"),
    ".ts": ("typescript", "frontend", "typescript"),
    ".tsx": ("typescript", "frontend", "typescript"),
    ".java": ("java", "backend", "java"),
    ".go": ("go", "backend", "go"),
    ".rs": ("rust", "backend", "rust"),
    ".rb": ("ruby", "backend", "ruby"),
    ".php": ("php", "backend", "php"),
    ".cs": ("csharp", "backend", "csharp"),
    ".yml": ("yaml", "config", "config"),
    ".yaml": ("yaml", "config", "config"),
    ".json": ("json", "config", "config"),
    ".toml": ("toml", "config", "config"),
    ".ini": ("ini", "config", "config"),
    ".cfg": ("cfg", "config", "config"),
    ".md": ("markdown", "docs", "docs"),
    ".mdx": ("markdown", "docs", "docs"),
    ".rst": ("rst", "docs", "docs"),
    ".txt": ("text", "docs", "docs"),
    ".html": ("html", "frontend", "frontend"),
    ".css": ("css", "frontend", "frontend"),
    ".scss": ("scss", "frontend", "frontend"),
    ".sql": ("sql", "backend", "sql"),
    ".sh": ("shell", "config", "shell"),
    ".bash": ("shell", "config", "shell"),
    ".dockerfile": ("dockerfile", "config", "config"),
}


@dataclass
class Classification:
    language: str
    language_family: str
    rule_profile: str
    is_test: bool
    is_docs: bool
    is_config: bool
    is_source: bool
    is_generated: bool
    is_high_risk_path: bool
    risk_hints: list[str] = field(default_factory=list)


def identify_language(filename: str) -> tuple[str, str, str]:
    """Return (language, language_family, rule_profile) from filename."""
    path = PurePosixPath(filename)
    ext = path.suffix.lower()
    name = path.name.lower()

    # Special filenames
    if name == "dockerfile":
        return ("dockerfile", "config", "config")
    if name in ("makefile", "procfile"):
        return _EXT_TO_LANGUAGE.get(".cfg", ("unknown", "unknown", "unknown"))

    return _EXT_TO_LANGUAGE.get(ext, ("unknown", "unknown", "unknown"))


def identify_file_type(filename: str) -> tuple[bool, bool, bool, bool, bool]:
    """Return (is_test, is_docs, is_config, is_source, is_generated)."""
    path = PurePosixPath(filename)
    name = path.name.lower()
    parts = [p.lower() for p in path.parts]
    ext = path.suffix.lower()

    # Test detection
    is_test = (
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
        or "test" in parts
    )

    # Docs detection
    is_docs = (
        ext in (".md", ".mdx", ".rst", ".txt")
        or "docs" in parts
        or "doc" in parts
    )

    # Config detection
    is_config = (
        ext in (".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".env")
        or name in ("dockerfile", "makefile", ".gitignore", ".dockerignore")
        or "config" in parts
        or "deploy" in parts
        or ".github" in parts
    )

    # Generated detection
    is_generated = (
        ".generated." in name
        or name.endswith(".pb.go")
        or "_generated" in name
        or "auto-generated" in name
    )

    # Source = not any of the above
    is_source = not is_test and not is_docs and not is_config and not is_generated

    return is_test, is_docs, is_config, is_source, is_generated


_HIGH_RISK_SEGMENTS = {
    "auth_path": {"auth", "authentication", "authorization", "permission", "permissions", "security", "login", "oauth"},
    "payment_path": {"payment", "payments", "billing", "stripe", "checkout", "subscription"},
    "db_path": {"db", "database", "databases", "migration", "migrations", "schema", "models"},
    "config_path": {"config", "configs", "configuration", "deploy", "deployment", "infra", "infrastructure", "ci", ".github"},
}


def derive_risk_hints(filename: str) -> tuple[bool, list[str]]:
    """Analyze path and return (is_high_risk_path, risk_hints)."""
    path = PurePosixPath(filename)
    parts = {p.lower() for p in path.parts}

    hints: list[str] = []
    for hint_name, segments in _HIGH_RISK_SEGMENTS.items():
        if parts & segments:
            hints.append(hint_name)

    return len(hints) > 0, hints


def detect_no_test_pair(filename: str, all_filenames: set[str]) -> bool:
    """Check if a source file has no corresponding test file in the changeset."""
    path = PurePosixPath(filename)
    ext = path.suffix.lower()

    # Only applies to source code files
    if ext not in (".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".java", ".rs", ".rb", ".php"):
        return False

    # Skip if this is itself a test file
    is_test, _, _, _, _ = identify_file_type(filename)
    if is_test:
        return False

    stem = path.stem
    parent = path.parent

    # Common test file patterns to check
    test_patterns = [
        f"{parent}/test_{stem}{ext}",
        f"{parent}/{stem}_test{ext}",
        f"{parent}/{stem}.test{ext}",
        f"{parent}/{stem}.spec{ext}",
        f"tests/{stem}{ext}",
        f"test/{stem}{ext}",
        f"__tests__/{stem}{ext}",
    ]

    for pattern in test_patterns:
        if pattern in all_filenames:
            return False

    return True


def classify_file(file: ChangedFile, all_filenames: set[str]) -> Classification:
    """Run all classifiers on a single file."""
    lang, lang_family, rule_profile = identify_language(file.filename)
    is_test, is_docs, is_config, is_source, is_generated = identify_file_type(file.filename)
    is_high_risk, hints = derive_risk_hints(file.filename)

    if is_source and not is_test:
        if detect_no_test_pair(file.filename, all_filenames):
            hints.append("no_test_pair")

    return Classification(
        language=lang,
        language_family=lang_family,
        rule_profile=rule_profile,
        is_test=is_test,
        is_docs=is_docs,
        is_config=is_config,
        is_source=is_source,
        is_generated=is_generated,
        is_high_risk_path=is_high_risk,
        risk_hints=hints,
    )
