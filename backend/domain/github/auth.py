from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)


class GitHubAuthError(Exception):
    pass


@dataclass(frozen=True)
class GitHubAppAuthConfig:
    client_id: str
    client_secret: str
    callback_url: str
    frontend_url: str
    app_slug: str = ""
    cookie_name: str = "pr_copilot_session"
    cookie_secure: bool = False
    state_ttl_seconds: int = 600
    session_ttl_seconds: int = 60 * 60 * 24 * 30

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.callback_url)

    @classmethod
    def from_env(cls) -> GitHubAppAuthConfig:
        return cls(
            client_id=os.environ.get("GITHUB_APP_CLIENT_ID", ""),
            client_secret=os.environ.get("GITHUB_APP_CLIENT_SECRET", ""),
            callback_url=os.environ.get(
                "GITHUB_APP_CALLBACK_URL",
                "http://localhost:8000/api/auth/github/callback",
            ),
            frontend_url=os.environ.get(
                "PR_COPILOT_FRONTEND_URL",
                "http://localhost:5173/",
            ),
            app_slug=os.environ.get("GITHUB_APP_SLUG", ""),
            cookie_name=os.environ.get(
                "PR_COPILOT_SESSION_COOKIE_NAME",
                "pr_copilot_session",
            ),
            cookie_secure=_env_bool("PR_COPILOT_COOKIE_SECURE", False),
        )


@dataclass
class PendingAuthorization:
    code_verifier: str
    expires_at: float
    post_auth_redirect: str = ""


@dataclass
class GitHubUserSession:
    session_id: str
    access_token: str
    user: dict[str, Any]
    expires_at: float
    refresh_token: str = ""
    refresh_token_expires_at: float = 0
    post_auth_redirect: str = ""


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


class GitHubAppOAuthClient:
    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client or httpx.AsyncClient(timeout=30.0)
        self._owns_client = http_client is None

    async def exchange_code(
        self,
        config: GitHubAppAuthConfig,
        *,
        code: str,
        code_verifier: str,
    ) -> dict[str, Any]:
        return await self._exchange({
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "code": code,
            "redirect_uri": config.callback_url,
            "code_verifier": code_verifier,
        })

    async def refresh_access_token(
        self,
        config: GitHubAppAuthConfig,
        *,
        refresh_token: str,
    ) -> dict[str, Any]:
        return await self._exchange({
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        })

    async def get_user(self, access_token: str) -> dict[str, Any]:
        return await self._get_json("https://api.github.com/user", access_token)

    async def repository_access(
        self,
        access_token: str,
        *,
        owner: str,
        repo: str,
    ) -> dict[str, Any]:
        full_name = f"{owner}/{repo}".casefold()
        installations = await self._get_paginated_items(
            "https://api.github.com/user/installations",
            access_token,
            item_key="installations",
        )
        for installation in installations:
            installation_id = installation.get("id")
            if not installation_id:
                continue
            repositories = await self._get_paginated_items(
                f"https://api.github.com/user/installations/{installation_id}/repositories",
                access_token,
                item_key="repositories",
            )
            if any(str(item.get("full_name", "")).casefold() == full_name for item in repositories):
                return {
                    "authorized": True,
                    "installation_id": installation_id,
                }
        return {
            "authorized": False,
            "installation_id": None,
        }

    async def _get_json(self, url: str, access_token: str) -> dict[str, Any]:
        try:
            response = await self._client.get(
                url,
                headers=self._auth_headers(access_token),
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise GitHubAuthError("Failed to load GitHub App authorization data") from exc

    async def _get_paginated_items(
        self,
        url: str,
        access_token: str,
        *,
        item_key: str,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_url: str | None = f"{url}?per_page=100"
        while next_url:
            try:
                response = await self._client.get(
                    next_url,
                    headers=self._auth_headers(access_token),
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise GitHubAuthError("Failed to load GitHub App repository access") from exc
            data = response.json()
            raw_items = data.get(item_key, [])
            if isinstance(raw_items, list):
                items.extend(item for item in raw_items if isinstance(item, dict))
            next_url = self._next_link(response.headers.get("link"))
        return items

    def _auth_headers(self, access_token: str) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {access_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _next_link(self, link_header: str | None) -> str | None:
        if not link_header:
            return None
        for part in link_header.split(","):
            if 'rel="next"' in part:
                return part.split(";", 1)[0].strip().strip("<>")
        return None

    async def has_installations(self, access_token: str) -> dict[str, Any]:
        installations = await self._get_paginated_items(
            "https://api.github.com/user/installations",
            access_token,
            item_key="installations",
        )
        return {
            "installed": len(installations) > 0,
            "count": len(installations),
        }

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _exchange(self, payload: dict[str, str]) -> dict[str, Any]:
        try:
            response = await self._client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data=payload,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise GitHubAuthError("GitHub token exchange failed") from exc
        data = response.json()
        if data.get("error"):
            description = data.get("error_description") or data["error"]
            raise GitHubAuthError(f"GitHub authorization failed: {description}")
        if not data.get("access_token"):
            raise GitHubAuthError("GitHub authorization did not return an access token")
        return data


class GitHubAuthService:
    def __init__(
        self,
        config: GitHubAppAuthConfig,
        oauth_client: GitHubAppOAuthClient | None = None,
        storage_path: Path | str | None = None,
    ) -> None:
        self.config = config
        self._oauth_client = oauth_client or GitHubAppOAuthClient()
        self._pending: dict[str, PendingAuthorization] = {}
        self._sessions: dict[str, GitHubUserSession] = {}
        self._storage_path: Path | None = Path(storage_path) if storage_path else None
        self._load_sessions()

    def begin_authorization(self, *, post_auth_redirect: str = "") -> str:
        if not self.config.configured:
            raise GitHubAuthError("GitHub App login is not configured")

        self._cleanup()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        self._pending[state] = PendingAuthorization(
            code_verifier=verifier,
            expires_at=time.time() + self.config.state_ttl_seconds,
            post_auth_redirect=post_auth_redirect,
        )
        return "https://github.com/login/oauth/authorize?" + urlencode({
            "client_id": self.config.client_id,
            "redirect_uri": self.config.callback_url,
            "state": state,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
        })

    def installation_url(self) -> str:
        slug = self.config.app_slug.strip()
        if not slug or re.fullmatch(r"[A-Za-z0-9-]+", slug) is None:
            raise GitHubAuthError("GitHub App installation is not configured")
        return f"https://github.com/apps/{slug}/installations/new"

    async def complete_authorization(self, *, code: str, state: str) -> GitHubUserSession:
        self._cleanup()
        pending = self._pending.pop(state, None)
        if pending is None or pending.expires_at <= time.time():
            raise GitHubAuthError("GitHub authorization state is invalid or expired")

        token_data = await self._oauth_client.exchange_code(
            self.config,
            code=code,
            code_verifier=pending.code_verifier,
        )
        access_token = str(token_data["access_token"])
        user = await self._oauth_client.get_user(access_token)
        session = self._build_session(access_token=access_token, token_data=token_data, user=user)
        session.post_auth_redirect = pending.post_auth_redirect
        self._sessions[session.session_id] = session
        self._save_sessions()
        return session

    async def repository_access(
        self,
        session_id: str | None,
        *,
        owner: str,
        repo: str,
    ) -> dict[str, Any]:
        session = await self.get_session(session_id)
        if session is None:
            raise GitHubAuthError("GitHub login required")
        return await self._oauth_client.repository_access(
            session.access_token,
            owner=owner,
            repo=repo,
        )

    async def has_installations(self, session_id: str | None) -> dict[str, Any]:
        session = await self.get_session(session_id)
        if session is None:
            raise GitHubAuthError("GitHub login required")
        return await self._oauth_client.has_installations(session.access_token)

    async def get_session(self, session_id: str | None) -> GitHubUserSession | None:
        if not session_id:
            return None
        self._cleanup()
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.expires_at > time.time() + 60:
            return session
        if not session.refresh_token or session.refresh_token_expires_at <= time.time():
            self._sessions.pop(session_id, None)
            return None

        try:
            token_data = await self._oauth_client.refresh_access_token(
                self.config,
                refresh_token=session.refresh_token,
            )
        except Exception:
            self._sessions.pop(session_id, None)
            return None
        session.access_token = str(token_data["access_token"])
        session.expires_at = self._access_token_expiry(token_data)
        session.refresh_token = str(token_data.get("refresh_token", session.refresh_token))
        session.refresh_token_expires_at = self._refresh_token_expiry(token_data)
        self._save_sessions()
        return session

    async def get_access_token(self, session_id: str | None) -> str | None:
        session = await self.get_session(session_id)
        return session.access_token if session is not None else None

    def delete_session(self, session_id: str | None) -> None:
        if session_id:
            self._sessions.pop(session_id, None)
            self._save_sessions()

    async def close(self) -> None:
        await self._oauth_client.close()

    def _build_session(
        self,
        *,
        access_token: str,
        token_data: dict[str, Any],
        user: dict[str, Any],
    ) -> GitHubUserSession:
        return GitHubUserSession(
            session_id=secrets.token_urlsafe(32),
            access_token=access_token,
            expires_at=self._access_token_expiry(token_data),
            refresh_token=str(token_data.get("refresh_token", "")),
            refresh_token_expires_at=self._refresh_token_expiry(token_data),
            user={
                "login": str(user.get("login", "")),
                "name": str(user.get("name") or ""),
                "avatar_url": str(user.get("avatar_url", "")),
                "html_url": str(user.get("html_url", "")),
            },
        )

    def _access_token_expiry(self, token_data: dict[str, Any]) -> float:
        ttl = int(token_data.get("expires_in", self.config.session_ttl_seconds))
        return time.time() + min(ttl, self.config.session_ttl_seconds)

    def _refresh_token_expiry(self, token_data: dict[str, Any]) -> float:
        ttl = int(token_data.get("refresh_token_expires_in", 0))
        return time.time() + ttl if ttl else 0

    def _load_sessions(self) -> None:
        if self._storage_path is None or not self._storage_path.exists():
            return
        try:
            raw = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load GitHub sessions from %s: %s", self._storage_path, exc)
            return
        if not isinstance(raw, list):
            return
        now = time.time()
        for item in raw:
            if not isinstance(item, dict):
                continue
            sid = item.get("session_id")
            if not sid or not isinstance(sid, str):
                continue
            expires_at = float(item.get("expires_at", 0))
            refresh_expires = float(item.get("refresh_token_expires_at", 0))
            if expires_at <= now and refresh_expires <= now:
                continue
            self._sessions[sid] = GitHubUserSession(
                session_id=sid,
                access_token=str(item.get("access_token", "")),
                user=item.get("user") if isinstance(item.get("user"), dict) else {},
                expires_at=expires_at,
                refresh_token=str(item.get("refresh_token", "")),
                refresh_token_expires_at=refresh_expires,
                post_auth_redirect=str(item.get("post_auth_redirect", "")),
            )

    def _save_sessions(self) -> None:
        if self._storage_path is None:
            return
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            data = [
                {
                    "session_id": s.session_id,
                    "access_token": s.access_token,
                    "user": s.user,
                    "expires_at": s.expires_at,
                    "refresh_token": s.refresh_token,
                    "refresh_token_expires_at": s.refresh_token_expires_at,
                    "post_auth_redirect": s.post_auth_redirect,
                }
                for s in self._sessions.values()
            ]
            self._storage_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Failed to save GitHub sessions to %s: %s", self._storage_path, exc)

    def _cleanup(self) -> None:
        now = time.time()
        self._pending = {
            state: pending
            for state, pending in self._pending.items()
            if pending.expires_at > now
        }
        before = len(self._sessions)
        self._sessions = {
            session_id: session
            for session_id, session in self._sessions.items()
            if session.expires_at > now or session.refresh_token_expires_at > now
        }
        if len(self._sessions) != before:
            self._save_sessions()


_GITHUB_AUTH_SERVICE: GitHubAuthService | None = None


def _default_session_storage_path() -> Path:
    from backend.agent.runtime.memory.config import get_storage_dir
    return get_storage_dir() / "github_sessions.json"


def get_github_auth_service() -> GitHubAuthService:
    global _GITHUB_AUTH_SERVICE
    if _GITHUB_AUTH_SERVICE is None:
        _GITHUB_AUTH_SERVICE = GitHubAuthService(
            GitHubAppAuthConfig.from_env(),
            storage_path=_default_session_storage_path(),
        )
    return _GITHUB_AUTH_SERVICE


def set_github_auth_service(service: GitHubAuthService | None) -> None:
    global _GITHUB_AUTH_SERVICE
    _GITHUB_AUTH_SERVICE = service
