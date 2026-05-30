from backend.domain.pr_context.context_manager import FileEntry

MUST_REVIEW_THRESHOLD = 70
SHOULD_REVIEW_THRESHOLD = 35

LARGE_CHANGE_LINES = 500


def classify_group(score: int) -> str:
    if score >= MUST_REVIEW_THRESHOLD:
        return "must_review"
    if score >= SHOULD_REVIEW_THRESHOLD:
        return "should_review"
    return "skim"


def generate_reasons(file: FileEntry) -> list[str]:
    reasons: list[str] = []

    for hint in file.risk_hints:
        reasons.append(hint)

    if file.is_high_risk_path:
        reasons.append("high_risk_path")
    if file.is_source:
        reasons.append("source_change")
    if file.is_test:
        reasons.append("test_change")
    if file.is_docs:
        reasons.append("docs_change")
    if file.is_config:
        reasons.append("config_change")
    if file.is_generated:
        reasons.append("generated_file")
    if file.is_binary:
        reasons.append("binary_file")
    if not file.patch_available:
        reasons.append("patch_unavailable")
    if file.large_patch:
        reasons.append("large_patch")
    if file.parse_error:
        reasons.append("parse_error")
    if file.status == "added":
        reasons.append("new_file")
    if file.status == "renamed":
        reasons.append("renamed_file")
    if file.status == "removed":
        reasons.append("removed_file")
    if file.additions + file.deletions >= LARGE_CHANGE_LINES:
        reasons.append("large_change")

    seen: set[str] = set()
    deduped: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            deduped.append(r)
    return deduped


def build_file_entry(file: FileEntry) -> dict:
    return {
        "filename": file.filename,
        "status": file.status,
        "additions": file.additions,
        "deletions": file.deletions,
        "language": file.language,
        "language_family": file.language_family,
        "is_test": file.is_test,
        "is_docs": file.is_docs,
        "is_config": file.is_config,
        "is_source": file.is_source,
        "is_binary": file.is_binary,
        "is_generated": file.is_generated,
        "patch_available": file.patch_available,
        "large_patch": file.large_patch,
        "hunk_count": file.hunk_count,
        "added_line_count": file.added_line_count,
        "removed_line_count": file.removed_line_count,
        "priority_score_hint": file.priority_score_hint,
        "reasons": generate_reasons(file),
    }


def sort_files(files: list[FileEntry]) -> list[FileEntry]:
    return sorted(files, key=lambda f: (-f.priority_score_hint, f.filename))


def build_file_priority_view(context_id: str, files: list[FileEntry]) -> dict:
    must_review: list[FileEntry] = []
    should_review: list[FileEntry] = []
    skim: list[FileEntry] = []

    for f in files:
        group = classify_group(f.priority_score_hint)
        if group == "must_review":
            must_review.append(f)
        elif group == "should_review":
            should_review.append(f)
        else:
            skim.append(f)

    return {
        "context_id": context_id,
        "groups": {
            "must_review": [build_file_entry(f) for f in sort_files(must_review)],
            "should_review": [build_file_entry(f) for f in sort_files(should_review)],
            "skim": [build_file_entry(f) for f in sort_files(skim)],
        },
    }
