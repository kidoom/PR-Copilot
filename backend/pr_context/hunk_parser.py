import re
from dataclasses import dataclass, field


@dataclass
class HunkLine:
    type: str  # "context" | "added" | "removed"
    content: str
    old_line: int | None
    new_line: int | None


@dataclass
class Hunk:
    header: str
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: list[HunkLine] = field(default_factory=list)


_HUNK_HEADER_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$"
)


def parse_hunk_header(header: str) -> tuple[int, int, int, int]:
    """Parse @@ -old_start,old_lines +new_start,new_lines @@ header."""
    match = _HUNK_HEADER_RE.match(header.strip())
    if not match:
        raise ValueError(f"Invalid hunk header: {header}")
    old_start = int(match.group(1))
    old_lines = int(match.group(2)) if match.group(2) is not None else 1
    new_start = int(match.group(3))
    new_lines = int(match.group(4)) if match.group(4) is not None else 1
    return old_start, old_lines, new_start, new_lines


def parse_patch(patch: str | None) -> list[Hunk]:
    """Parse a unified diff patch string into structured hunks."""
    if not patch:
        return []

    hunks: list[Hunk] = []
    current_hunk: Hunk | None = None
    old_line = 0
    new_line = 0

    for raw_line in patch.splitlines():
        # Check for hunk header
        if raw_line.startswith("@@"):
            try:
                old_s, old_l, new_s, new_l = parse_hunk_header(raw_line)
            except ValueError:
                # Not a valid hunk header, treat as content if in a hunk
                if current_hunk is not None:
                    current_hunk.lines.append(HunkLine(
                        type="context", content=raw_line,
                        old_line=old_line, new_line=new_line,
                    ))
                    old_line += 1
                    new_line += 1
                continue

            current_hunk = Hunk(
                header=raw_line,
                old_start=old_s, old_lines=old_l,
                new_start=new_s, new_lines=new_l,
            )
            hunks.append(current_hunk)
            old_line = old_s
            new_line = new_s
            continue

        if current_hunk is None:
            # Content before first hunk header — skip
            continue

        if raw_line.startswith("+"):
            current_hunk.lines.append(HunkLine(
                type="added", content=raw_line[1:],
                old_line=None, new_line=new_line,
            ))
            new_line += 1
        elif raw_line.startswith("-"):
            current_hunk.lines.append(HunkLine(
                type="removed", content=raw_line[1:],
                old_line=old_line, new_line=None,
            ))
            old_line += 1
        elif raw_line.startswith(" ") or raw_line == "":
            content = raw_line[1:] if raw_line.startswith(" ") else raw_line
            current_hunk.lines.append(HunkLine(
                type="context", content=content,
                old_line=old_line, new_line=new_line,
            ))
            old_line += 1
            new_line += 1
        # Handle "\ No newline at end of file" and other backslash lines
        elif raw_line.startswith("\\"):
            # Attach to last line as metadata, skip
            continue

    return hunks
