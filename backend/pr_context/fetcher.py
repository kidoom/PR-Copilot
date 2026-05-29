from dataclasses import dataclass, field


@dataclass
class PRMetadata:
    title: str
    body: str
    author: str
    url: str
    state: str
    merged: bool
    base_branch: str
    head_branch: str
    created_at: str
    updated_at: str
    additions: int
    deletions: int
    changed_files: int
    labels: list[str] = field(default_factory=list)
    assignees: list[str] = field(default_factory=list)
    requested_reviewers: list[str] = field(default_factory=list)


@dataclass
class CommitInfo:
    sha: str
    message: str
    author: str
    date: str


@dataclass
class CommitsData:
    head_sha: str
    commits: list[CommitInfo]


@dataclass
class ChangedFile:
    filename: str
    previous_filename: str | None
    status: str
    additions: int
    deletions: int
    changes: int
    blob_url: str
    raw_url: str
    contents_url: str
    patch: str | None


def fetch_pr_metadata(raw: dict) -> PRMetadata:
    """Normalize raw GitHub API PR response into PRMetadata."""
    return PRMetadata(
        title=raw["title"] or "",
        body=raw.get("body") or "",
        author=raw["user"]["login"],
        url=raw["html_url"],
        state=raw["state"],
        merged=raw.get("merged", False),
        base_branch=raw["base"]["ref"],
        head_branch=raw["head"]["ref"],
        created_at=raw["created_at"],
        updated_at=raw["updated_at"],
        additions=raw.get("additions", 0),
        deletions=raw.get("deletions", 0),
        changed_files=raw.get("changed_files", 0),
        labels=[label["name"] for label in raw.get("labels", [])],
        assignees=[user["login"] for user in raw.get("assignees", [])],
        requested_reviewers=[user["login"] for user in raw.get("requested_reviewers", [])],
    )


def fetch_commits(raw_list: list[dict]) -> CommitsData:
    """Normalize raw GitHub API commits response."""
    commits = []
    for c in raw_list:
        commit_data = c.get("commit", {})
        author_info = commit_data.get("author", {})
        commits.append(CommitInfo(
            sha=c["sha"],
            message=commit_data.get("message", ""),
            author=author_info.get("name", ""),
            date=author_info.get("date", ""),
        ))
    head_sha = commits[-1].sha if commits else ""
    return CommitsData(head_sha=head_sha, commits=commits)


def fetch_changed_files(raw_list: list[dict]) -> list[ChangedFile]:
    """Normalize raw GitHub API files response."""
    files = []
    for f in raw_list:
        files.append(ChangedFile(
            filename=f["filename"],
            previous_filename=f.get("previous_filename"),
            status=f.get("status", "modified"),
            additions=f.get("additions", 0),
            deletions=f.get("deletions", 0),
            changes=f.get("changes", 0),
            blob_url=f.get("blob_url", ""),
            raw_url=f.get("raw_url", ""),
            contents_url=f.get("contents_url", ""),
            patch=f.get("patch"),
        ))
    return files
