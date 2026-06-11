from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from backend.agent.tools.repo_context.provider.helpers import normalize_repo_root
from backend.agent.tools.repo_context.provider.local import LocalRepoProvider
from backend.agent.tools.repo_context.provider.models import (
    PRIdentity,
    PreparationError,
    PreparationErrorKind,
    PreparationResult,
    RepoWorkspace,
    WorkspaceSource,
)

logger = logging.getLogger(__name__)


def _run_git(
    args: list[str],
    *,
    cwd: str,
    env: dict[str, str] | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    base_env = os.environ.copy()
    if env:
        base_env.update(env)
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=base_env,
    )


def _verify_local_identity(
    repo_root: str,
    identity: PRIdentity,
) -> tuple[bool, str]:
    try:
        result = _run_git(["remote", "get-url", "origin"], cwd=repo_root, timeout=5)
        remote = result.stdout.strip() if result.returncode == 0 else ""
        if f"{identity.owner}/{identity.repo}".lower() not in remote.lower():
            return False, f"Remote origin does not match {identity.owner}/{identity.repo}"
    except (OSError, subprocess.TimeoutExpired):
        return False, "Cannot read git remote"

    try:
        result = _run_git(["rev-parse", "HEAD"], cwd=repo_root, timeout=5)
        actual_sha = result.stdout.strip() if result.returncode == 0 else ""
        if not actual_sha.startswith(identity.head_sha[:12]):
            return False, f"HEAD mismatch: expected {identity.head_sha[:12]}, got {actual_sha[:12]}"
    except (OSError, subprocess.TimeoutExpired):
        return False, "Cannot read HEAD SHA"

    return True, ""


def _create_askpass_script(token: str) -> tuple[str, str]:
    tmpdir = tempfile.mkdtemp(prefix="askpass_")
    if os.name == "nt":
        script_path = os.path.join(tmpdir, "askpass.bat")
        with open(script_path, "w") as f:
            f.write(f'@echo off\necho {token}\n')
    else:
        script_path = os.path.join(tmpdir, "askpass.sh")
        with open(script_path, "w") as f:
            f.write(f"#!/bin/sh\necho '{token}'\n")
        os.chmod(script_path, 0o700)
    return tmpdir, script_path


def _cleanup_askpass(askpass_dir: str) -> None:
    try:
        shutil.rmtree(askpass_dir, ignore_errors=True)
    except OSError:
        pass


def _sanitize_dir_name(name: str) -> str:
    """Remove characters that are invalid in Windows directory names."""
    # Windows forbidden: < > : " / \ | ? *
    invalid = '<>:"/\\|?*'
    for ch in invalid:
        name = name.replace(ch, "_")
    # Strip trailing dots/spaces (Windows silently strips them, causing confusion)
    name = name.rstrip(". ")
    return name or "repo"


def _prepare_temp_clone(
    identity: PRIdentity,
    temp_root: str,
    token: str | None = None,
) -> tuple[str | None, str | None]:
    os.makedirs(temp_root, exist_ok=True)
    # Sanitize each component and keep the name short to avoid MAX_PATH issues on Windows
    owner = _sanitize_dir_name(identity.owner)[:40]
    repo = _sanitize_dir_name(identity.repo)[:40]
    dir_name = f"{owner}__{repo}__{identity.head_sha[:8]}__{uuid.uuid4().hex[:6]}"
    clone_dir = os.path.join(temp_root, dir_name)

    askpass_dir: str | None = None
    try:
        env: dict[str, str] | None = None
        if token:
            askpass_dir, askpass_script = _create_askpass_script(token)
            env = {
                "GIT_ASKPASS": askpass_script,
                "GIT_TERMINAL_PROMPT": "0",
            }

        clone_url = f"https://github.com/{identity.owner}/{identity.repo}.git"
        result = _run_git(
            ["clone", "--depth=1", "--no-checkout", clone_url, clone_dir],
            cwd=temp_root,
            env=env,
            timeout=120,
        )
        if result.returncode != 0:
            _cleanup_dir(clone_dir)
            return None, f"Clone failed: {result.stderr.strip()}"

        if identity.pull_number is not None:
            ref = f"refs/pull/{identity.pull_number}/head"
            result = _run_git(
                ["fetch", "origin", ref],
                cwd=clone_dir,
                env=env,
                timeout=60,
            )
            if result.returncode != 0:
                _cleanup_dir(clone_dir)
                return None, f"Fetch PR ref failed: {result.stderr.strip()}"

        result = _run_git(
            ["checkout", identity.head_sha],
            cwd=clone_dir,
            timeout=30,
        )
        if result.returncode != 0:
            _cleanup_dir(clone_dir)
            return None, f"Checkout failed: {result.stderr.strip()}"

        return clone_dir, None

    except (OSError, subprocess.TimeoutExpired) as e:
        _cleanup_dir(clone_dir)
        return None, f"Git operation failed: {e}"
    finally:
        if askpass_dir:
            _cleanup_askpass(askpass_dir)


def _cleanup_dir(path: str) -> None:
    try:
        if os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


class RepoWorkspaceManager:
    def __init__(self, temp_root: str | None = None) -> None:
        self._temp_root = temp_root
        self._workspaces: dict[tuple[str, str], RepoWorkspace] = {}

    def prepare_workspace(
        self,
        run_id: str,
        context_id: str,
        pr_identity: PRIdentity,
        local_repo_root: str | None = None,
        token: str | None = None,
    ) -> PreparationResult:
        key = (run_id, context_id)
        if key in self._workspaces:
            return PreparationResult.success(self._workspaces[key])

        if not pr_identity.owner or not pr_identity.repo or not pr_identity.head_sha:
            return PreparationResult.failure(PreparationError(
                kind=PreparationErrorKind.MISSING_PR_IDENTITY,
                message="owner, repo, and head_sha are required",
            ))

        if local_repo_root:
            root = normalize_repo_root(local_repo_root)
            if os.path.isdir(root):
                ok, reason = _verify_local_identity(root, pr_identity)
                if ok:
                    workspace = RepoWorkspace(
                        run_id=run_id,
                        context_id=context_id,
                        repo_root=root,
                        source=WorkspaceSource.LOCAL,
                        pr_identity=pr_identity,
                        is_temp=False,
                    )
                    self._workspaces[key] = workspace
                    return PreparationResult.success(workspace)
                logger.info("Local repo mismatch: %s", reason)

        if not self._temp_root:
            return PreparationResult.failure(PreparationError(
                kind=PreparationErrorKind.LOCAL_MISMATCH,
                message="Local repo mismatch and no temp_root configured",
            ))

        clone_dir, error = _prepare_temp_clone(pr_identity, self._temp_root, token)
        if clone_dir is None:
            return PreparationResult.failure(PreparationError(
                kind=PreparationErrorKind.CLONE_FAILED,
                message=error or "Clone failed",
            ))

        workspace = RepoWorkspace(
            run_id=run_id,
            context_id=context_id,
            repo_root=clone_dir,
            source=WorkspaceSource.TEMP_CLONE,
            pr_identity=pr_identity,
            is_temp=True,
        )
        self._workspaces[key] = workspace
        return PreparationResult.success(workspace)

    def get_workspace(self, run_id: str, context_id: str) -> RepoWorkspace | None:
        return self._workspaces.get((run_id, context_id))

    def get_provider(self, run_id: str, context_id: str) -> LocalRepoProvider | None:
        workspace = self.get_workspace(run_id, context_id)
        if workspace is None:
            return None
        return LocalRepoProvider(workspace.repo_root)

    def cleanup_workspace(self, run_id: str, context_id: str) -> bool:
        key = (run_id, context_id)
        workspace = self._workspaces.pop(key, None)
        if workspace is None:
            return False
        if workspace.is_temp:
            self._safe_delete(workspace.repo_root)
        return True

    def cleanup_run(self, run_id: str) -> int:
        keys = [k for k in self._workspaces if k[0] == run_id]
        count = 0
        for key in keys:
            workspace = self._workspaces.pop(key)
            if workspace.is_temp:
                self._safe_delete(workspace.repo_root)
            count += 1
        return count

    def cleanup_all(self) -> int:
        count = 0
        for workspace in list(self._workspaces.values()):
            if workspace.is_temp:
                self._safe_delete(workspace.repo_root)
            count += 1
        self._workspaces.clear()
        return count

    def _safe_delete(self, path: str) -> None:
        if not self._temp_root:
            logger.warning("Cannot delete %s: no temp_root configured", path)
            return
        resolved = Path(path).resolve()
        temp_resolved = Path(self._temp_root).resolve()
        try:
            resolved.relative_to(temp_resolved)
        except ValueError:
            logger.warning("Refusing to delete %s: outside temp root %s", path, self._temp_root)
            return
        _cleanup_dir(str(resolved))
