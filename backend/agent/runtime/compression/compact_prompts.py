from __future__ import annotations

from enum import Enum


class CompactProfile(str, Enum):
    """Compact prompt profiles for different agent types."""

    MAIN_AGENT = "main_agent"
    SUBAGENT = "subagent"


# Main agent compact system prompt
MAIN_AGENT_COMPACT_PROMPT = """\
You are a context compaction assistant for a PR review coordinator.

Your task is to create a concise summary of the conversation history that preserves:
1. Current review run identity (run_id, context_id, PR details)
2. User's review objective and requirements
3. Planner state (tasks dispatched, routes, agent assignments)
4. Main agent progress (tasks completed, evidence collected, synthesis state)
5. Subagent results (key findings, evidence packages, status)
6. Pending work (remaining tasks, blockers, next steps)

IMPORTANT:
- Preserve all task IDs, agent types, and their status
- Preserve key evidence claims and findings
- Preserve any user instructions or constraints
- Do NOT include internal tool call details or raw file contents
- Keep the summary under the requested length

Output a structured summary that allows the review to continue from where it left off.
"""

# Subagent compact system prompt
SUBAGENT_COMPACT_PROMPT = """\
You are a context compaction assistant for a read-only PR review subagent.

Your task is to create a concise summary of the conversation history that preserves:
1. Delegated task identity (task_id, task_type, target files)
2. Repository context gathered (files examined, searches performed)
3. Work completed (evidence collected, findings, todo state)
4. Evidence package status (submitted, pending, incomplete)
5. Key findings and claims with their confidence levels
6. Pending work (remaining searches, files to examine, evidence to gather)

IMPORTANT:
- Preserve all file paths and search queries used
- Preserve evidence claims with their supporting references
- Preserve todo items and their status
- Do NOT include raw file contents or search results
- Keep the summary under the requested length

Output a structured summary that allows the subagent to continue its evidence gathering.
"""


def get_compact_profile_prompt(profile: CompactProfile) -> str:
    """Get the system prompt for a compact profile."""
    if profile == CompactProfile.MAIN_AGENT:
        return MAIN_AGENT_COMPACT_PROMPT
    else:
        return SUBAGENT_COMPACT_PROMPT


def select_compact_profile(
    agent_kind: str,
    agent_type: str = "",
) -> CompactProfile:
    """Select a compact profile based on session metadata.

    Args:
        agent_kind: "main" or "subagent"
        agent_type: The specific agent type (e.g., "main-agent", "security-context-agent")

    Returns:
        The appropriate CompactProfile.
    """
    if agent_kind == "main":
        return CompactProfile.MAIN_AGENT
    else:
        return CompactProfile.SUBAGENT


def compact_user_prompt(message_count: int, profile: CompactProfile) -> str:
    """Build a user prompt for compact summarization.

    Args:
        message_count: Number of messages being compacted.
        profile: The compact profile being used.

    Returns:
        A bounded summary request prompt.
    """
    if profile == CompactProfile.MAIN_AGENT:
        return (
            f"Please summarize the following {message_count} messages from a PR review session. "
            f"Preserve the review run state, planner progress, subagent results, and pending work."
        )
    else:
        return (
            f"Please summarize the following {message_count} messages from a PR review subagent. "
            f"Preserve the delegated task, repository context, evidence collected, and pending work."
        )
