from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
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

def _detect_system_proxy() -> str | None:
    """Detect HTTP proxy from environment variables or Windows system settings."""
    import socket

    # 1. Explicit env vars (highest priority)
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        val = os.environ.get(key, "").strip()
        if val:
            return val

    # 2. Windows system proxy from registry
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            ) as reg_key:
                server, _ = winreg.QueryValueEx(reg_key, "ProxyServer")
                if server and isinstance(server, str) and server.strip():
                    proxy_url = f"http://{server.strip()}"
                    # Verify the proxy port is actually reachable
                    host, _, port = server.strip().partition(":")
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(1)
                        sock.connect((host, int(port)))
                        sock.close()
                        return proxy_url
                    except (OSError, ValueError):
                        pass
        except (OSError, ImportError, ValueError):
            pass

    return None


_GIT_NETWORK_ERROR_MARKERS = (
    "connection was reset",
    "connection reset",
    "recv failure",
    "failed to connect",
    "could not resolve host",
    "connection timed out",
    "operation timed out",
    "network is unreachable",
    "network unavailable",
    "early eof",
    "remote end hung up unexpectedly",
    "tls",
    "ssl",
    "schannel",
    "http/2 stream",
)
_GIT_RETRY_DELAYS_SECONDS = (0.25, 0.75)


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


def _git_failure(operation: str, result: subprocess.CompletedProcess[str]) -> str:
    detail = result.stderr.strip() or result.stdout.strip() or "Git produced no output"
    return f"{operation} failed (exit {result.returncode}): {detail}"


def _is_retryable_git_failure(result: subprocess.CompletedProcess[str]) -> bool:
    if result.returncode == 0:
        return False
    output = f"{result.stderr}\n{result.stdout}".lower()
    return any(marker in output for marker in _GIT_NETWORK_ERROR_MARKERS)


def _run_git_with_network_retry(
    args: list[str],
    *,
    cwd: str,
    operation: str,
    env: dict[str, str] | None = None,
    timeout: int = 60,
    cleanup_before_retry: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = _run_git(args, cwd=cwd, env=env, timeout=timeout)
    for attempt, delay in enumerate(_GIT_RETRY_DELAYS_SECONDS, start=2):
        if not _is_retryable_git_failure(result):
            break
        logger.warning(
            "%s hit a transient Git network error; retrying attempt %d/%d in %.2fs",
            operation,
            attempt,
            len(_GIT_RETRY_DELAYS_SECONDS) + 1,
            delay,
        )
        if cleanup_before_retry:
            _cleanup_dir(cleanup_before_retry)
        time.sleep(delay)
        result = _run_git(args, cwd=cwd, env=env, timeout=timeout)
    return result


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
    """Remove characters that are invalid in Windows directory names.

    Windows forbids: < > : " / \\ | ? *
    Also strips control characters (0x00-0x1F) and characters above U+FFFF
    that can cause issues with some filesystem drivers.
    """
    # Windows forbidden: < > : " / \ | ? *
    invalid = '<>:"/\\|?*'
    for ch in invalid:
        name = name.replace(ch, "_")
    # Strip control characters (0x00-0x1F) and DEL (0x7F)
    name = "".join(ch for ch in name if ord(ch) >= 32 and ord(ch) != 127)
    # Strip trailing dots/spaces (Windows silently strips them, causing confusion)
    name = name.rstrip(". ")
    return name or "repo"


def _prepare_temp_clone(
    identity: PRIdentity,
    temp_root: str,
    token: str | None = None,
) -> tuple[str | None, str | None]:
    temp_root = os.path.abspath(temp_root)
    os.makedirs(temp_root, exist_ok=True)
    # Sanitize each component and keep the name short to avoid MAX_PATH issues on Windows
    owner = _sanitize_dir_name(identity.owner)[:40]
    repo = _sanitize_dir_name(identity.repo)[:40]
    dir_name = f"{owner}__{repo}__{identity.head_sha[:8]}__{uuid.uuid4().hex[:6]}"
    clone_dir = os.path.join(temp_root, dir_name)

    # Guard: ensure the clone directory path is valid before proceeding
    if len(clone_dir) > 240:
        return None, f"Clone path too long ({len(clone_dir)} chars): {clone_dir[:100]}..."

    askpass_dir: str | None = None
    try:
        env: dict[str, str] = {
            "GIT_TERMINAL_PROMPT": "0",
            # Keep workspace preparation independent from stale machine-level
            # credential helpers. Private repositories use the explicit token.
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "",
        }
        # Forward proxy settings so git can reach GitHub through corporate/personal proxies
        proxy = _detect_system_proxy()
        if proxy:
            env.setdefault("HTTP_PROXY", proxy)
            env.setdefault("HTTPS_PROXY", proxy)
            env.setdefault("http_proxy", proxy)
            env.setdefault("https_proxy", proxy)
        if token:
            askpass_dir, askpass_script = _create_askpass_script(token)
            env["GIT_ASKPASS"] = askpass_script

        clone_url = f"https://github.com/{identity.owner}/{identity.repo}.git"
        logger.info("Cloning %s into %s", clone_url, clone_dir)
        result = _run_git_with_network_retry(
            ["clone", "--depth=1", "--no-checkout", clone_url, clone_dir],
            cwd=temp_root,
            operation="Clone",
            env=env,
            timeout=120,
            cleanup_before_retry=clone_dir,
        )
        if result.returncode != 0:
            _cleanup_dir(clone_dir)
            return None, _git_failure("Clone", result)

        if identity.pull_number is not None:
            ref = f"refs/pull/{identity.pull_number}/head"
            result = _run_git_with_network_retry(
                ["fetch", "origin", ref],
                cwd=clone_dir,
                operation="Fetch PR ref",
                env=env,
                timeout=60,
            )
            if result.returncode != 0:
                _cleanup_dir(clone_dir)
                return None, _git_failure("Fetch PR ref", result)

        result = _run_git(
            ["checkout", identity.head_sha],
            cwd=clone_dir,
            timeout=30,
        )
        if result.returncode != 0:
            _cleanup_dir(clone_dir)
            return None, _git_failure("Checkout", result)

        return clone_dir, None

    except (OSError, subprocess.TimeoutExpired) as e:
        _cleanup_dir(clone_dir)
        return None, f"Git operation failed for '{clone_dir}': {e}"
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
        self._temp_root = os.path.abspath(temp_root) if temp_root else None
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
