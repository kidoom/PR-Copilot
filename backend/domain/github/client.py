import httpx


class GitHubAPIError(Exception):
    def __init__(self, status_code: int, message: str, error_category: str = "unknown"):
        self.status_code = status_code
        self.message = message
        self.error_category = error_category  # auth, permission, rate_limit, not_found, upstream
        super().__init__(message)


class GitHubClient:
    BASE_URL = "https://api.github.com"

    def __init__(self, token: str | None = None, cancellation_probe=None):
        from backend.agent.runtime.cancellation import CancellationProbe
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers=headers,
            timeout=30.0,
        )
        self._cancellation_probe = cancellation_probe

    def _check_cancellation(self) -> None:
        """Preserve cancellation instead of converting to generic error (task 4.8)."""
        if self._cancellation_probe is not None:
            self._cancellation_probe.check()

    async def _send(self, method: str, path: str) -> httpx.Response:
        try:
            return await self._client.request(method, path)
        except httpx.TimeoutException as exc:
            raise GitHubAPIError(
                504,
                "GitHub API request timed out",
                "timeout",
            ) from exc
        except httpx.RequestError as exc:
            raise GitHubAPIError(
                502,
                f"Unable to connect to GitHub API: {exc}",
                "network",
            ) from exc

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code >= 500:
            raise GitHubAPIError(
                502,
                f"GitHub API returned HTTP {resp.status_code}",
                "upstream",
            )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise GitHubAPIError(
                502,
                f"Unexpected GitHub API response: HTTP {resp.status_code}",
                "upstream",
            ) from exc

    async def _request(self, method: str, path: str) -> dict | list:
        self._check_cancellation()
        resp = await self._send(method, path)
        if resp.status_code == 401:
            raise GitHubAPIError(401, "Unauthorized: token is invalid or expired", "auth")
        if resp.status_code == 403:
            remaining = resp.headers.get("X-RateLimit-Remaining", "?")
            if remaining == "0":
                raise GitHubAPIError(403, "Rate limited. Please provide a GitHub token", "rate_limit")
            raise GitHubAPIError(403, f"Forbidden: {resp.text}", "permission")
        if resp.status_code == 404:
            raise GitHubAPIError(404, "PR not found or repository is private", "not_found")
        self._raise_for_status(resp)
        return resp.json()

    def _parse_next_link(self, link_header: str | None) -> str | None:
        """Extract the next page URL from a GitHub Link header."""
        if not link_header:
            return None
        for part in link_header.split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")
                return url
        return None

    async def _paginated_request(self, path: str, per_page: int = 100) -> list:
        """Fetch all pages for a paginated GitHub REST endpoint."""
        results: list = []
        url = f"{path}?per_page={per_page}"
        while url:
            self._check_cancellation()
            resp = await self._send("GET", url)
            if resp.status_code == 401:
                raise GitHubAPIError(401, "Unauthorized: token is invalid or expired", "auth")
            if resp.status_code == 403:
                remaining = resp.headers.get("X-RateLimit-Remaining", "?")
                if remaining == "0":
                    raise GitHubAPIError(403, "Rate limited. Please provide a GitHub token", "rate_limit")
                raise GitHubAPIError(403, f"Forbidden: {resp.text}", "permission")
            if resp.status_code == 404:
                raise GitHubAPIError(404, "PR not found or repository is private", "not_found")
            self._raise_for_status(resp)
            data = resp.json()
            if isinstance(data, list):
                results.extend(data)
            else:
                results.append(data)
            url = self._parse_next_link(resp.headers.get("link"))
        return results

    async def get_pr(self, owner: str, repo: str, pull_number: int) -> dict:
        return await self._request("GET", f"/repos/{owner}/{repo}/pulls/{pull_number}")

    async def get_commits(self, owner: str, repo: str, pull_number: int) -> list[dict]:
        return await self._paginated_request(f"/repos/{owner}/{repo}/pulls/{pull_number}/commits")

    async def get_files(self, owner: str, repo: str, pull_number: int) -> list[dict]:
        return await self._paginated_request(f"/repos/{owner}/{repo}/pulls/{pull_number}/files")

    async def get_check_runs(self, owner: str, repo: str, ref: str) -> dict:
        """Retrieve check runs for a repository commit SHA (task 6.1).

        Returns the raw GitHub API response with total_count and check_runs array.
        """
        return await self._request("GET", f"/repos/{owner}/{repo}/commits/{ref}/check-runs")

    async def get_combined_status(self, owner: str, repo: str, ref: str) -> dict:
        """Retrieve combined legacy commit status contexts for a SHA (task 6.2).

        Returns the raw GitHub API response with state, statuses, etc.
        """
        return await self._request("GET", f"/repos/{owner}/{repo}/commits/{ref}/status")

    async def close(self):
        await self._client.aclose()
