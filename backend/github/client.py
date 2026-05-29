import httpx


class GitHubAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(message)


class GitHubClient:
    BASE_URL = "https://api.github.com"

    def __init__(self, token: str | None = None):
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

    async def _request(self, method: str, path: str) -> dict:
        resp = await self._client.request(method, path)
        if resp.status_code == 401:
            raise GitHubAPIError(401, "Unauthorized: token is invalid or expired")
        if resp.status_code == 403:
            remaining = resp.headers.get("X-RateLimit-Remaining", "?")
            if remaining == "0":
                raise GitHubAPIError(403, "Rate limited. Please provide a GitHub token")
            raise GitHubAPIError(403, f"Forbidden: {resp.text}")
        if resp.status_code == 404:
            raise GitHubAPIError(404, "PR not found or repository is private")
        resp.raise_for_status()
        return resp.json()

    async def get_pr(self, owner: str, repo: str, pull_number: int) -> dict:
        return await self._request("GET", f"/repos/{owner}/{repo}/pulls/{pull_number}")

    async def get_commits(self, owner: str, repo: str, pull_number: int) -> list[dict]:
        return await self._request("GET", f"/repos/{owner}/{repo}/pulls/{pull_number}/commits")

    async def get_files(self, owner: str, repo: str, pull_number: int) -> list[dict]:
        return await self._request("GET", f"/repos/{owner}/{repo}/pulls/{pull_number}/files")

    async def close(self):
        await self._client.aclose()
