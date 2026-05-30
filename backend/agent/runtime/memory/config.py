from __future__ import annotations

import os
from pathlib import Path


# Default storage directory
DEFAULT_STORAGE_DIR = os.path.join(os.path.expanduser("~"), ".pr-copilot")

# Environment variable for storage directory
STORAGE_DIR_ENV = "PR_COPILOT_STORAGE_DIR"


def get_storage_dir() -> Path:
    """Get the configured storage directory.

    Priority:
    1. PR_COPILOT_STORAGE_DIR environment variable
    2. Default: ~/.pr-copilot
    """
    env_dir = os.environ.get(STORAGE_DIR_ENV)
    if env_dir:
        return Path(env_dir)
    return Path(DEFAULT_STORAGE_DIR)
