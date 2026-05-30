import re
from dataclasses import dataclass


@dataclass
class ParsedPRUrl:
    owner: str
    repo: str
    pull_number: int


_PR_URL_PATTERN = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)(?:/.*)?$"
)


def parse_pr_url(url: str) -> ParsedPRUrl:
    """Parse a GitHub PR URL and extract owner, repo, pull_number."""
    match = _PR_URL_PATTERN.fullmatch(url.strip())
    if not match:
        raise ValueError(
            f"Invalid GitHub PR URL: {url}. "
            "Expected format: https://github.com/{owner}/{repo}/pull/{number}"
        )

    owner = match.group("owner")
    repo = match.group("repo")
    pull_number = int(match.group("number"))

    if pull_number <= 0:
        raise ValueError(f"Invalid pull number: {pull_number}")

    return ParsedPRUrl(owner=owner, repo=repo, pull_number=pull_number)
