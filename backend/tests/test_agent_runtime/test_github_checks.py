from __future__ import annotations

import json
import pytest
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx

from backend.domain.github.client import GitHubClient, GitHubAPIError
from backend.domain.github.checks_provider import (
    ChecksSummaryProvider,
    ChecksProviderResult,
    ChecksSummary,
)


def _make_mock_response(data: dict, status_code: int = 200, headers: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = json.dumps(data)
    resp_headers = {}
    if headers:
        resp_headers.update(headers)
    resp.headers = resp_headers
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp,
        )
    return resp


def _make_client(*responses):
    client = GitHubClient.__new__(GitHubClient)
    client._cancellation_probe = None
    mock_http = AsyncMock()
    mock_http.request = AsyncMock(side_effect=list(responses))
    client._client = mock_http
    return client


CHECK_RUNS_DATA = {
    "total_count": 2,
    "check_runs": [
        {
            "name": "build",
            "status": "completed",
            "conclusion": "success",
            "details_url": "https://example.com/build/1",
            "started_at": "2025-01-01T00:00:00Z",
            "completed_at": "2025-01-01T00:01:00Z",
        },
        {
            "name": "lint",
            "status": "completed",
            "conclusion": "failure",
            "details_url": "https://example.com/lint/1",
            "started_at": "2025-01-01T00:00:00Z",
            "completed_at": "2025-01-01T00:00:30Z",
        },
    ],
}

STATUS_DATA = {
    "state": "failure",
    "statuses": [
        {
            "context": "ci/travis",
            "state": "failure",
            "target_url": "https://travis-ci.org/build/1",
            "description": "Build failed",
        },
    ],
}

EMPTY_CHECKS = {"total_count": 0, "check_runs": []}
EMPTY_STATUS = {"state": "success", "statuses": []}


@pytest.mark.asyncio
async def test_checks_provider_empty_checks():
    gh = _make_client(
        _make_mock_response(EMPTY_CHECKS),
        _make_mock_response(EMPTY_STATUS),
    )
    provider = ChecksSummaryProvider(
        owner="owner", repo="repo", head_sha="abc123", github_client=gh,
    )
    result = await provider.get_summary()
    assert result.status == "ok"
    assert result.summary.overall_status == "success"
    assert result.summary.total_check_runs == 0


@pytest.mark.asyncio
async def test_checks_provider_populated_checks():
    gh = _make_client(
        _make_mock_response(CHECK_RUNS_DATA),
        _make_mock_response(STATUS_DATA),
    )
    provider = ChecksSummaryProvider(
        owner="owner", repo="repo", head_sha="abc123", github_client=gh,
    )
    result = await provider.get_summary()
    assert result.status == "ok"
    assert result.summary.overall_status == "failure"
    assert result.summary.failure_count >= 1
    assert len(result.summary.check_runs) == 2


@pytest.mark.asyncio
async def test_checks_provider_caches_result():
    gh = _make_client(
        _make_mock_response(EMPTY_CHECKS),
        _make_mock_response(EMPTY_STATUS),
    )
    provider = ChecksSummaryProvider(
        owner="owner", repo="repo", head_sha="abc123", github_client=gh,
    )
    r1 = await provider.get_summary()
    r2 = await provider.get_summary()
    assert r1 is r2


@pytest.mark.asyncio
async def test_checks_provider_missing_identity():
    gh = _make_client()
    provider = ChecksSummaryProvider(
        owner="", repo="repo", head_sha="abc123", github_client=gh,
    )
    result = await provider.get_summary()
    assert result.status == "unavailable"
    assert "Missing" in result.reason


@pytest.mark.asyncio
async def test_checks_provider_auth_failure():
    gh = _make_client(_make_mock_response({"message": "Bad credentials"}, 401))
    provider = ChecksSummaryProvider(
        owner="owner", repo="repo", head_sha="abc123", github_client=gh,
    )
    result = await provider.get_summary()
    assert result.status == "unavailable"


@pytest.mark.asyncio
async def test_checks_provider_rate_limit():
    gh = _make_client(_make_mock_response(
        {"message": "rate limited"}, 403, {"X-RateLimit-Remaining": "0"},
    ))
    provider = ChecksSummaryProvider(
        owner="owner", repo="repo", head_sha="abc123", github_client=gh,
    )
    result = await provider.get_summary()
    assert result.status == "error"
    assert "rate" in result.reason.lower()


@pytest.mark.asyncio
async def test_checks_provider_upstream_error():
    gh = _make_client(_make_mock_response({"message": "server error"}, 500))
    provider = ChecksSummaryProvider(
        owner="owner", repo="repo", head_sha="abc123", github_client=gh,
    )
    result = await provider.get_summary()
    assert result.status == "error"


@pytest.mark.asyncio
async def test_checks_provider_to_dict():
    gh = _make_client(
        _make_mock_response(CHECK_RUNS_DATA),
        _make_mock_response(STATUS_DATA),
    )
    provider = ChecksSummaryProvider(
        owner="owner", repo="repo", head_sha="abc123", github_client=gh,
    )
    result = await provider.get_summary()
    d = result.to_dict()
    assert "overall_status" in d
    assert "check_runs" in d
    assert "status_contexts" in d
    assert "summary" in d


@pytest.mark.asyncio
async def test_checks_provider_truncation():
    many_runs = {
        "total_count": 50,
        "check_runs": [
            {"name": f"check-{i}", "status": "completed", "conclusion": "success"}
            for i in range(50)
        ],
    }
    gh = _make_client(
        _make_mock_response(many_runs),
        _make_mock_response(EMPTY_STATUS),
    )
    provider = ChecksSummaryProvider(
        owner="owner", repo="repo", head_sha="abc123", github_client=gh,
    )
    result = await provider.get_summary()
    assert result.status == "ok"
    assert result.summary.truncated is True
    assert len(result.summary.check_runs) == 30
