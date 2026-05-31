from __future__ import annotations

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket
from fastapi.responses import RedirectResponse

from backend.domain.github.auth import GitHubAuthError, get_github_auth_service

router = APIRouter(prefix="/api/auth", tags=["github-auth"])
logger = logging.getLogger(__name__)


def _session_cookie(request: Request | WebSocket) -> str | None:
    service = get_github_auth_service()
    return request.cookies.get(service.config.cookie_name)


async def get_authenticated_github_session(request: Request | WebSocket):
    service = get_github_auth_service()
    return await service.get_session(_session_cookie(request))


async def get_authenticated_github_token(request: Request | WebSocket) -> str | None:
    session = await get_authenticated_github_session(request)
    return session.access_token if session is not None else None


@router.get("/github/login")
async def github_login(
    next_step: str = Query("", alias="next"),
) -> RedirectResponse:
    service = get_github_auth_service()
    post_auth_redirect = "install" if next_step == "install" else ""
    try:
        authorization_url = service.begin_authorization(
            post_auth_redirect=post_auth_redirect,
        )
    except GitHubAuthError as exc:
        logger.error("GitHub App login is unavailable: %s", exc)
        return _frontend_redirect(service.config.frontend_url, github_auth="unavailable")
    return RedirectResponse(authorization_url, status_code=302)


@router.get("/github/install")
async def github_install(request: Request) -> RedirectResponse:
    service = get_github_auth_service()
    try:
        installation_url = service.installation_url()
    except GitHubAuthError as exc:
        logger.error("GitHub App installation is unavailable: %s", exc)
        return _frontend_redirect(service.config.frontend_url, github_install="unavailable")
    if await service.get_session(_session_cookie(request)) is None:
        return RedirectResponse("/api/auth/github/login?next=install", status_code=302)
    return RedirectResponse(installation_url, status_code=302)


@router.get("/github/setup")
async def github_setup() -> RedirectResponse:
    service = get_github_auth_service()
    return _frontend_redirect(service.config.frontend_url, github_install="success")


@router.get("/github/callback")
async def github_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
) -> RedirectResponse:
    service = get_github_auth_service()
    if error:
        if not state:
            return _frontend_redirect(service.config.frontend_url, github_install="denied")
        return _frontend_redirect(service.config.frontend_url, github_auth="denied")
    if code and not state:
        if await service.get_session(_session_cookie(request)) is None:
            return RedirectResponse("/api/auth/github/login", status_code=302)
        return _frontend_redirect(service.config.frontend_url, github_install="success")
    if not code:
        raise HTTPException(status_code=400, detail="Missing GitHub authorization code or state")

    try:
        session = await service.complete_authorization(code=code, state=state)
    except GitHubAuthError as exc:
        logger.warning("GitHub App callback failed: %s", exc)
        return _frontend_redirect(service.config.frontend_url, github_auth="failed")

    if session.post_auth_redirect == "install":
        response = RedirectResponse("/api/auth/github/install", status_code=302)
    else:
        response = _frontend_redirect(service.config.frontend_url, github_auth="success")
    response.set_cookie(
        key=service.config.cookie_name,
        value=session.session_id,
        max_age=service.config.session_ttl_seconds,
        httponly=True,
        secure=service.config.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/github/repositories/status")
async def github_repository_status(
    request: Request,
    owner: str,
    repo: str,
) -> dict:
    service = get_github_auth_service()
    session_id = _session_cookie(request)
    if await service.get_session(session_id) is None:
        raise HTTPException(status_code=401, detail="GitHub login required")
    try:
        status = await service.repository_access(
            session_id,
            owner=owner,
            repo=repo,
        )
    except GitHubAuthError as exc:
        logger.warning("GitHub App repository status lookup failed: %s", exc)
        raise HTTPException(status_code=502, detail="Unable to verify GitHub repository access") from exc
    return {
        "repository": f"{owner}/{repo}",
        "authorized": status["authorized"],
        "installation_id": status["installation_id"],
        "install_url": "/api/auth/github/install",
    }


@router.get("/github/installations/status")
async def github_installations_status(request: Request) -> dict:
    service = get_github_auth_service()
    session_id = _session_cookie(request)
    if await service.get_session(session_id) is None:
        raise HTTPException(status_code=401, detail="GitHub login required")
    try:
        status = await service.has_installations(session_id)
    except GitHubAuthError as exc:
        logger.warning("GitHub App installations status lookup failed: %s", exc)
        raise HTTPException(status_code=502, detail="Unable to check GitHub App installations") from exc
    return status


@router.get("/session")
async def auth_session(request: Request) -> dict:
    service = get_github_auth_service()
    session = await service.get_session(_session_cookie(request))
    return {
        "authenticated": session is not None,
        "user": session.user if session is not None else None,
    }


@router.post("/logout")
async def logout(request: Request) -> dict:
    service = get_github_auth_service()
    service.delete_session(_session_cookie(request))
    response = {"authenticated": False}
    from fastapi.responses import JSONResponse
    result = JSONResponse(response)
    result.delete_cookie(
        key=service.config.cookie_name,
        path="/",
        httponly=True,
        secure=service.config.cookie_secure,
        samesite="lax",
    )
    return result


def _frontend_redirect(frontend_url: str, **query: str) -> RedirectResponse:
    separator = "&" if "?" in frontend_url else "?"
    return RedirectResponse(
        frontend_url + separator + urlencode(query),
        status_code=302,
    )
