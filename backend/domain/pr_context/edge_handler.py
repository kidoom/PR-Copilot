from dataclasses import dataclass, field

from .fetcher import ChangedFile
from .hunk_parser import Hunk, parse_patch

LARGE_PATCH_THRESHOLD = 50 * 1024  # 50KB


@dataclass
class ProcessedFile:
    filename: str
    previous_filename: str | None
    status: str
    additions: int
    deletions: int
    changes: int
    blob_url: str
    raw_url: str
    contents_url: str
    is_binary: bool
    patch_available: bool
    large_patch: bool
    parse_error: str | None
    hunks: list[Hunk] = field(default_factory=list)


def process_file(file: ChangedFile) -> ProcessedFile:
    """Process a single file, handling edge cases in patch parsing."""
    result = ProcessedFile(
        filename=file.filename,
        previous_filename=file.previous_filename,
        status=file.status,
        additions=file.additions,
        deletions=file.deletions,
        changes=file.changes,
        blob_url=file.blob_url,
        raw_url=file.raw_url,
        contents_url=file.contents_url,
        is_binary=False,
        patch_available=True,
        large_patch=False,
        parse_error=None,
        hunks=[],
    )

    # Binary file: no patch from API
    if file.patch is None:
        if file.status in ("added", "removed") and file.additions == 0 and file.deletions == 0:
            result.is_binary = True
        result.patch_available = False
        return result

    # Empty patch
    if not file.patch.strip():
        result.patch_available = False
        return result

    # Large patch detection
    if len(file.patch) > LARGE_PATCH_THRESHOLD:
        result.large_patch = True

    # Parse hunks with error isolation
    try:
        result.hunks = parse_patch(file.patch)
    except Exception as e:
        result.parse_error = str(e)
        result.hunks = []

    return result
