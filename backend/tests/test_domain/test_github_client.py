from __future__ import annotations

import httpx
import pytest

from backend.domain.github.client import GitHubAPIError, GitHubClient


class FailingTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("TLS handshake failed", request=request)


@pytest.mark.asyncio
async def test_request_maps_connection_error_to_github_api_error():
    client = GitHubClient()
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url=client.BASE_URL,
        transport=FailingTransport(),
    )

    try:
        with pytest.raises(GitHubAPIError) as exc_info:
            await client.get_pr("owner", "repo", 1)
    finally:
        await client.close()

    assert exc_info.value.status_code == 502
    assert exc_info.value.error_category == "network"
    assert "TLS handshake failed" in exc_info.value.message
