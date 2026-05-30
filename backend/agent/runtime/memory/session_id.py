from __future__ import annotations

import re
import uuid

# Filesystem-safe session id pattern: alphanumeric, hyphens, underscores
_SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

# Maximum session id length
MAX_SESSION_ID_LENGTH = 128


def validate_session_id(session_id: str) -> bool:
    """Check if a session id is filesystem-safe.

    Allowed characters: alphanumeric, hyphens, underscores.
    Maximum length: 128 characters.
    """
    if not session_id:
        return False
    if len(session_id) > MAX_SESSION_ID_LENGTH:
        return False
    return bool(_SESSION_ID_PATTERN.match(session_id))


def normalize_agent_type(agent_type: str) -> str:
    """Normalize agent type for use in session ids.

    Replaces dots and spaces with hyphens, converts to lowercase.
    """
    return agent_type.lower().replace(".", "-").replace(" ", "-")


def build_main_session_id(run_id: str, context_id: str) -> str:
    """Build a session id for a main agent session.

    Format: main-{run_id_short}-{context_id_short}-{uuid4_short}
    """
    run_short = run_id[:8] if run_id else "no-run"
    ctx_short = context_id[:8] if context_id else "no-ctx"
    uid = uuid.uuid4().hex[:8]
    session_id = f"main-{run_short}-{ctx_short}-{uid}"

    # Ensure it's valid
    if not validate_session_id(session_id):
        raise ValueError(f"Generated invalid session id: {session_id}")

    return session_id


def build_subagent_session_id(
    agent_type: str,
    run_id: str,
    context_id: str,
    task_id: str = "",
) -> str:
    """Build a session id for a subagent session.

    Format: sub-{agent_type}-{run_id_short}-{task_id_short}-{uuid4_short}
    """
    normalized_type = normalize_agent_type(agent_type)
    run_short = run_id[:8] if run_id else "no-run"
    task_short = task_id[:8] if task_id else "no-task"
    uid = uuid.uuid4().hex[:8]
    session_id = f"sub-{normalized_type}-{run_short}-{task_short}-{uid}"

    # Ensure it's valid
    if not validate_session_id(session_id):
        raise ValueError(f"Generated invalid session id: {session_id}")

    return session_id
