"""Local GitHub credential resolver.

Resolves GitHub credentials from the developer's machine without browser OAuth.
Precedence: GH_TOKEN > GITHUB_TOKEN > gh auth token > anonymous.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

_GH_TOKEN_TIMEOUT_SECONDS = 5


class CredentialSource(str, Enum):
    """How the credential was resolved."""
    GH_TOKEN = "gh_token"
    GITHUB_TOKEN = "github_token"
    GH_CLI = "gh_cli"
    ANONYMOUS = "anonymous"


@dataclass(frozen=True)
class ResolvedCredential:
    """Result of a credential resolution attempt."""
    token: str
    source: CredentialSource

    @property
    def is_authenticated(self) -> bool:
        return bool(self.token)


def resolve_github_token() -> ResolvedCredential:
    """Resolve a GitHub token from local environment or CLI.

    Precedence:
    1. GH_TOKEN environment variable
    2. GITHUB_TOKEN environment variable
    3. ``gh auth token`` CLI (bounded timeout)
    4. Anonymous (empty token)

    Never logs or exposes the resolved token value.
    """
    # 1. Explicit environment variables
    for env_var in ("GH_TOKEN", "GITHUB_TOKEN"):
        token = os.environ.get(env_var, "").strip()
        if token:
            source = CredentialSource.GH_TOKEN if env_var == "GH_TOKEN" else CredentialSource.GITHUB_TOKEN
            logger.debug("Resolved GitHub credential from %s", env_var)
            return ResolvedCredential(token=token, source=source)

    # 2. GitHub CLI
    gh_result = _try_gh_auth_token()
    if gh_result is not None:
        logger.debug("Resolved GitHub credential from gh CLI")
        return ResolvedCredential(token=gh_result, source=CredentialSource.GH_CLI)

    # 3. Anonymous
    logger.debug("No GitHub credential found; using anonymous access")
    return ResolvedCredential(token="", source=CredentialSource.ANONYMOUS)


def _try_gh_auth_token() -> str | None:
    """Try to get a token from ``gh auth token``.

    Returns the token string, or None if gh is missing, unauthenticated,
    or times out.
    """
    gh_path = shutil.which("gh")
    if gh_path is None:
        return None

    try:
        result = subprocess.run(
            [gh_path, "auth", "token"],
            capture_output=True,
            text=True,
            timeout=_GH_TOKEN_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0:
        return None

    token = result.stdout.strip()
    return token if token else None
