IGNORED_DIRECTORIES: frozenset[str] = frozenset({
    ".git",
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".eggs",
    "*.egg-info",
    ".next",
    ".nuxt",
    "vendor",
    "target",
    "bin",
    "obj",
})

SENSITIVE_PATH_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    ".pem",
    ".key",
    "id_rsa",
    "id_ed25519",
    "credentials",
    "credentials.json",
    "service_account.json",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "secrets.yml",
    "secrets.yaml",
    "secret_key.py",
)

MAX_SEARCH_RESULTS: int = 30
MAX_SNIPPET_LINES: int = 50
MAX_SNIPPET_BYTES: int = 10_000
