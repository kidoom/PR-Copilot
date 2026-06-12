# Repository Provider Abstraction Layer Design

**Date**: 2026-05-30
**Status**: Draft
**Scope**: 重构仓库数据源访问层，支持本地仓库和服务器临时 clone

---

## 1. Problem Statement

### 当前问题

PR-Copilot 的 Agent 工具层直接依赖本地文件系统（`os.walk`、`open()`），导致：

1. **无法远程部署**：必须在有本地仓库的机器上运行
2. **无法服务化**：SaaS 场景下用户不会 clone 仓库到服务器
3. **数据源单一**：没有 fallback 机制，本地没有仓库就无法工作

### 目标

- 支持两种数据源：本地仓库、服务器临时 clone
- 自动选择最优数据源（按能力匹配，非固定顺序）
- 保持现有 Agent 工具接口不变，对上层透明

---

## 2. Architecture

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                    Agent Tools Layer                     │
│  search_repo / read_repo_file / search_tests_for / ...  │
└───────────────────────────┬─────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│                    RepoProvider (接口)                    │
│  read_file(path, ref) / search_code(query, scope)        │
│  list_files(pattern) / get_manifest()                    │
└───────────────────────────┬─────────────────────────────┘
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
        ┌──────────┐               ┌──────────┐
        │  Local   │               │  Temp    │
        │  Repo    │               │  Clone   │
        │ Provider │               │ Provider │
        └──────────┘               └──────────┘
```

### 2.2 数据源选择策略（按能力匹配）

```python
class RepoWorkspaceManager:
    """根据操作类型和上下文选择数据源"""

    def get_provider(self, run_id: str, context_id: str, operation: str) -> RepoProvider:
        workspace = self._workspaces.get(self._key(run_id, context_id))

        # 本地仓库已验证 → 优先使用
        if workspace and workspace.source_type == "local":
            return create_provider_for_workspace(workspace)

        # 需要全文检索（search_repo, search_tests_for）→ temp clone
        if operation in ("search_code", "search_tests", "list_files"):
            if workspace and workspace.source_type == "temp_clone":
                return create_provider_for_workspace(workspace)
            # 需要 clone
            workspace = self._prepare_temp_clone(run_id, context_id)
            return create_provider_for_workspace(workspace)

        # 只读 diff / 少量文件 → 可用 local
        if workspace:
            return create_provider_for_workspace(workspace)

        # 默认：尝试准备 workspace
        workspace = self._prepare_workspace(run_id, context_id)
        return create_provider_for_workspace(workspace)


def create_provider_for_workspace(workspace: RepoWorkspace) -> RepoProvider:
    """根据 workspace 类型创建对应 Provider"""
    if workspace.source_type == WorkspaceSourceType.TEMP_CLONE:
        return TempCloneProvider(workspace)
    return LocalRepoProvider(workspace)
```

---

## 3. Core Design

### 3.1 RepoProvider 接口

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SearchResult:
    file: str
    line: int
    snippet: str


@dataclass
class FileContent:
    path: str
    start_line: int
    end_line: int
    lines: list[dict]  # [{"line": 1, "content": "..."}]
    truncated: bool


class RepoProvider(ABC):
    """仓库数据源接口"""

    @abstractmethod
    def read_file(self, path: str, start_line: int = 1, max_lines: int = 50) -> FileContent:
        """读取文件内容（绑定到 head_sha 版本）"""
        ...

    @abstractmethod
    def search_code(self, query: str, path_scope: str = "", limit: int = 20) -> list[SearchResult]:
        """搜索代码内容"""
        ...

    @abstractmethod
    def list_files(self, pattern: str = "") -> list[str]:
        """列出文件"""
        ...

    @abstractmethod
    def get_manifest(self) -> dict:
        """读取 README、package.json 等清单文件"""
        ...
```

### 3.2 RepoWorkspace

```python
from dataclasses import dataclass
from enum import Enum


class WorkspaceSourceType(str, Enum):
    LOCAL = "local"
    TEMP_CLONE = "temp_clone"


@dataclass
class RepoWorkspace:
    """仓库工作区，绑定到特定 PR 的 head_sha"""
    context_id: str
    run_id: str
    owner: str            # base repo owner
    repo: str             # base repo name
    head_sha: str         # PR head commit SHA
    head_repo: str        # head repo full name (可能是 fork，如 "user/repo")
    pull_number: int      # PR 编号，用于 fetch refs/pull/N/head
    source_type: WorkspaceSourceType
    repo_root: str
```

**关键设计**：
- workspace 绑定到 `(run_id, context_id)`，避免并发冲突
- 区分 `owner/repo`（base）和 `head_repo`（可能是 fork）
- 使用 `refs/pull/{pull_number}/head` 获取 PR 代码，确保是 PR 的正确版本

### 3.3 RepoWorkspaceManager

```python
class RepoWorkspaceManager:
    """管理 workspace 的生命周期，生命周期为 review run 级别"""

    def __init__(self, temp_dir: str = "/tmp/pr-copilot-clones"):
        self._temp_dir = temp_dir
        # key 为 (run_id, context_id)，避免并发冲突
        self._workspaces: dict[tuple[str, str], RepoWorkspace] = {}

    def _key(self, run_id: str, context_id: str) -> tuple[str, str]:
        return (run_id, context_id)

    def prepare(
        self,
        context_id: str,
        run_id: str,
        owner: str,
        repo: str,
        head_sha: str,
        head_repo: str = "",
        pull_number: int = 0,
        auth_token: str = "",
        local_repo_root: str = "",
    ) -> RepoWorkspace:
        """准备 workspace：优先本地，fallback 到临时 clone"""

        # 强制要求 head_sha，确保 review 的是 PR 正确版本
        if not head_sha:
            raise ValueError("head_sha is required for workspace preparation")

        # 1. 尝试本地仓库
        if local_repo_root:
            workspace = self._try_local(
                context_id, run_id, owner, repo, head_sha,
                head_repo, pull_number, local_repo_root,
            )
            if workspace:
                self._workspaces[self._key(run_id, context_id)] = workspace
                return workspace

        # 2. 临时 clone（使用 head_repo，可能是 fork）
        workspace = self._try_temp_clone(
            context_id, run_id, owner, repo, head_sha,
            head_repo, pull_number, auth_token,
        )
        if workspace:
            self._workspaces[self._key(run_id, context_id)] = workspace
            return workspace

        raise RuntimeError(f"Failed to prepare workspace for {owner}/{repo}@{head_sha[:8]}")

    def get_workspace(self, run_id: str, context_id: str) -> RepoWorkspace | None:
        return self._workspaces.get(self._key(run_id, context_id))

    def cleanup_run(self, run_id: str) -> None:
        """清理整个 run 的所有 workspace（生命周期为 run 级别）"""
        to_remove = []
        for key, ws in self._workspaces.items():
            if key[0] == run_id:  # key[0] is run_id
                if ws.source_type == WorkspaceSourceType.TEMP_CLONE:
                    self._safe_delete(ws.repo_root)
                to_remove.append(key)
        for key in to_remove:
            del self._workspaces[key]

    def _try_local(
        self, context_id, run_id, owner, repo, head_sha,
        head_repo, pull_number, local_root,
    ) -> RepoWorkspace | None:
        """尝试使用本地仓库"""
        import subprocess
        from pathlib import Path

        if not Path(local_root).is_dir():
            return None

        # 验证 remote URL
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=local_root, capture_output=True, text=True, timeout=5,
            )
            remote = result.stdout.strip() if result.returncode == 0 else ""
            if f"{owner}/{repo}" not in remote.lower():
                return None
        except (OSError, subprocess.TimeoutExpired):
            return None

        # 验证 HEAD SHA（允许不匹配，但记录警告）
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=local_root, capture_output=True, text=True, timeout=5,
            )
            actual_sha = result.stdout.strip() if result.returncode == 0 else ""
            if head_sha and not actual_sha.startswith(head_sha[:12]):
                # SHA 不匹配，可能需要 fetch
                return None
        except (OSError, subprocess.TimeoutExpired):
            return None

        return RepoWorkspace(
            context_id=context_id,
            run_id=run_id,
            owner=owner,
            repo=repo,
            head_sha=head_sha,
            head_repo=head_repo or f"{owner}/{repo}",
            pull_number=pull_number,
            source_type=WorkspaceSourceType.LOCAL,
            repo_root=local_root,
        )

    def _try_temp_clone(
        self, context_id, run_id, owner, repo, head_sha,
        head_repo, pull_number, auth_token,
    ) -> RepoWorkspace | None:
        """Shallow clone 并 checkout 到 PR head_sha

        支持 fork 场景：clone base repo，fetch refs/pull/N/head 获取 PR 代码。
        """
        import os
        import uuid
        import subprocess

        clone_id = uuid.uuid4().hex[:8]
        dir_name = f"{owner}--{repo}--{head_sha[:8]}--{clone_id}"
        clone_path = os.path.join(self._temp_dir, dir_name)

        try:
            os.makedirs(self._temp_dir, exist_ok=True)

            # 始终 clone base repo（fork 的 PR 也能从 base fetch refs/pull/N/head）
            base_url = f"https://github.com/{owner}/{repo}.git"

            # 使用 GIT_ASKPASS 注入 token，避免 token 进入进程参数或 .git/config
            env = os.environ.copy()
            if auth_token:
                askpass_script = self._create_askpass_script(auth_token)
                env["GIT_ASKPASS"] = askpass_script
                env["GIT_TERMINAL_PROMPT"] = "0"

            # Shallow clone base repo
            subprocess.run(
                ["git", "clone", "--depth=1", base_url, clone_path],
                check=True, capture_output=True, timeout=120, env=env,
            )

            # Fetch PR head commit
            if pull_number > 0:
                # 使用 refs/pull/N/head 获取 PR 代码（支持 fork）
                refspec = f"+refs/pull/{pull_number}/head:refs/remotes/origin/pr/{pull_number}"
                subprocess.run(
                    ["git", "fetch", "--depth=1", "origin", refspec],
                    cwd=clone_path, check=True, capture_output=True, timeout=60, env=env,
                )
                # Checkout 到 PR head commit
                subprocess.run(
                    ["git", "checkout", head_sha],
                    cwd=clone_path, check=True, capture_output=True, timeout=30,
                )
            elif head_sha:
                # 没有 pull_number，尝试直接 fetch head_sha
                subprocess.run(
                    ["git", "fetch", "--depth=1", "origin", head_sha],
                    cwd=clone_path, check=True, capture_output=True, timeout=60, env=env,
                )
                subprocess.run(
                    ["git", "checkout", head_sha],
                    cwd=clone_path, check=True, capture_output=True, timeout=30,
                )

            return RepoWorkspace(
                context_id=context_id,
                run_id=run_id,
                owner=owner,
                repo=repo,
                head_sha=head_sha,
                head_repo=head_repo or f"{owner}/{repo}",
                pull_number=pull_number,
                source_type=WorkspaceSourceType.TEMP_CLONE,
                repo_root=clone_path,
            )

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            # clone 失败清理残留
            if os.path.isdir(clone_path):
                import shutil
                shutil.rmtree(clone_path, ignore_errors=True)
            return None
        finally:
            # 清理 askpass 脚本
            if auth_token:
                self._cleanup_askpass_script(env.get("GIT_ASKPASS", ""))

    def _create_askpass_script(self, token: str) -> str:
        """创建 GIT_ASKPASS 脚本，安全注入 token

        GIT_ASKPASS 脚本被 git 调用来获取密码，不会写入 .git/config 或进程参数。
        权限必须 owner-only (0700)，创建失败应让私有 clone 失败。
        """
        import os
        import tempfile
        import stat

        script_content = f'''#!/bin/sh
case "$1" in
    *Username*) echo "x-access-token" ;;
    *Password*) echo "{token}" ;;
esac
'''
        fd, path = tempfile.mkstemp(prefix="git-askpass-", suffix=".sh")
        try:
            os.write(fd, script_content.encode())
            os.close(fd)
            os.chmod(path, stat.S_IRWXU)  # 仅 owner 可执行 (0700)
            return path
        except OSError:
            # 创建失败应让私有 clone 失败，不要静默降级
            try:
                os.close(fd)
            except OSError:
                pass
            raise RuntimeError("Failed to create GIT_ASKPASS script")

    def _cleanup_askpass_script(self, path: str) -> None:
        """清理 GIT_ASKPASS 脚本"""
        if path and os.path.isfile(path):
            try:
                os.unlink(path)
            except OSError:
                pass

    def _safe_delete(self, path: str) -> None:
        """安全删除临时目录"""
        from pathlib import Path
        import shutil

        real_path = Path(path).resolve()
        real_root = Path(self._temp_dir).resolve()

        # 路径必须在临时目录内
        try:
            real_path.relative_to(real_root)
        except ValueError:
            return

        # 禁止删除关键目录
        forbidden = [Path("/"), Path.home(), Path("/tmp")]
        for f in forbidden:
            if real_path == f.resolve():
                return

        shutil.rmtree(path, ignore_errors=True)

    def cleanup_on_startup(self) -> None:
        """启动时清理过期目录"""
        import os
        import time
        import shutil

        if not os.path.isdir(self._temp_dir):
            return

        TTL = 7200  # 2 小时
        for name in os.listdir(self._temp_dir):
            path = os.path.join(self._temp_dir, name)
            if not os.path.isdir(path):
                continue
            if time.time() - os.path.getmtime(path) > TTL:
                try:
                    shutil.rmtree(path, ignore_errors=True)
                except OSError:
                    pass
```

### 3.4 LocalRepoProvider

```python
class LocalRepoProvider(RepoProvider):
    """本地仓库 Provider"""

    def __init__(self, workspace: RepoWorkspace):
        self._root = workspace.repo_root

    def read_file(self, path, start_line=1, max_lines=50):
        # 复用 service.py:read_repo_file 逻辑
        ...

    def search_code(self, query, path_scope="", limit=20):
        # 复用 service.py:search_repo 逻辑
        ...

    def list_files(self, pattern=""):
        # os.walk + fnmatch
        ...

    def get_manifest(self):
        # 复用 service.py:read_repo_manifest 逻辑
        ...
```

### 3.5 TempCloneProvider

```python
class TempCloneProvider(LocalRepoProvider):
    """临时 clone Provider，复用 LocalRepoProvider 逻辑"""

    def __init__(self, workspace: RepoWorkspace):
        super().__init__(workspace)
        # cleanup 由 WorkspaceManager 管理，Provider 不负责删除
```

---

## 4. Integration Plan

### 4.1 Current Decision: run-level workspace/provider, no RepoContextSession extension

`RepoWorkspace` and `RepoProvider` are run-level dependencies. They MUST NOT be
stored on `RepoContextSession`, and subagents MUST NOT maintain a separate
repo-context session for tool state.

Current subagent review flow is message-first:

```text
TaskTool
  -> SubAgent(system prompt + task prompt + filtered read-only tools)
  -> tool observations appended as standard tool messages
  -> final structured JSON review result
  -> TaskTool aggregates parsed results for Main Agent
```

Therefore this provider layer integrates as shared read-only runtime deps:

```text
ReviewRun
  -> WorkspaceManager.prepare(run_id, context_id, PR identity)
  -> RepoWorkspace
  -> RepoProvider
  -> child_tool_factory injects repo access into stateless tools
```

Deprecated previous idea: extend `RepoContextSession` with provider/workspace.
That would reintroduce mutable tool-session state and make future read-only tool
parallelism harder.

### 4.2 Workspace 注入点

workspace 在 review run 启动时准备一次，并作为只读依赖注入 child tool factory。它不注入 subagent session，也不挂到 `RepoContextSession`。

```python
# backend/deps.py - build_main_runtime()

def build_main_runtime(self, *, model, task_plan, pr_context, repo_root, ...):
    context_id = task_plan.get("context_id", "")
    run_id = task_plan.get("run_id", "")

    # Prepare workspace once; all subagents share the same read-only provider.
    workspace = workspace_manager.prepare(
        context_id=context_id,
        run_id=run_id,
        owner=pr_context.owner,
        repo=pr_context.repo,
        head_sha=pr_context.commits.head_sha,
        head_repo=pr_context.head_repo,
        pull_number=pr_context.pull_number,
        auth_token=get_auth_token(),
        local_repo_root=repo_root,
    )
    provider = create_provider_for_workspace(workspace)

    def child_tool_factory(child_session_id, task=None):
        # All subagents share the same read-only workspace/provider.
        # The provider is a tool dependency, not subagent session state.
        bundle = build_context_child_tools(
            child_session_id,
            task=task,
            context_id=context_id,
            repo_provider=provider,
            repo_root=workspace.repo_root,
            pr_context=pr_context,
        )
        return bundle
```

```python
# backend/agent/subagents.py - build_context_child_tools()

def build_context_child_tools(
    child_session_id,
    task=None,
    *,
    context_id="",
    repo_provider=None,
    repo_root="",
    pr_context=None,
) -> ChildToolBundle:
    tools = create_stateless_context_tools(
        repo_provider=repo_provider,
        repo_root=repo_root,
        pr_context=pr_context,
    )
    return ChildToolBundle(tools=tools)
```

### 4.3 改造 service.py

```python
# Before: tools read through RepoContextSession/session.repo_root
def search_repo(session, query, path_scope="", limit=20):
    ...

# After: tools read through injected provider
def search_repo(provider, query, path_scope="", limit=20):
    if not provider:
        return {"error": "No provider available"}
    results = provider.search_code(query, path_scope, limit)
    return {"matches": [asdict(r) for r in results], "total": len(results)}
```

Repo tools remain stateless from the orchestration boundary:

- They return observations.
- They do not write `todos`, `usage`, `verification`, or `final_package`.
- Tool observations are stored in the subagent message transcript.
- The final handoff is the subagent's structured JSON review result.

### 4.4 改造 verify_repo_context

```python
def verify_repo_context(workspace, owner, repo, head_sha="", ...):
    # Diagnostic only. Cloning/checking out belongs to WorkspaceManager.prepare().
    if not workspace:
        return {"verified": False, "reason": "No workspace prepared"}

    if workspace.owner != owner or workspace.repo != repo:
        return {"verified": False, "reason": "Owner/repo mismatch"}

    if head_sha and not workspace.head_sha.startswith(head_sha[:12]):
        return {"verified": False, "reason": "HEAD SHA mismatch"}

    return {"verified": True, "source": workspace.source_type.value}
```

---

## 5. Lifecycle

```
POST /api/review/runs
    │
    ▼
WorkspaceManager.prepare(context_id, run_id, owner, repo, head_sha, auth)
    │
    ├─→ 本地仓库匹配？ → LocalRepoProvider
    │
    └─→ 临时 clone → checkout head_sha → TempCloneProvider
            │
            ▼
        Agent 执行（多个 subagent 共享同一 workspace）
            │
            ▼
        run 结束
            │
            ▼
        WorkspaceManager.cleanup_run(run_id)
            │
            └─→ 删除临时目录
```

**关键**：workspace 生命周期为 run 级别，不是单个 provider/subagent 级别。

---

## 6. Testing Strategy

| 场景 | 测试方法 |
|---|---|
| LocalRepoProvider | 使用临时目录创建测试仓库 |
| TempCloneProvider | 使用本地 bare repo 模拟 clone |
| WorkspaceManager | 测试 prepare / cleanup 生命周期 |
| 路径安全 | 测试 _safe_delete 拒绝删除关键目录 |
| Stateless repo tools | 测试 provider/repo_root 只读访问、空 repo_root fail closed、敏感文件过滤 |
| SubAgent integration | 测试 child tool factory 注入 provider，不依赖 `RepoContextSession` |

---

## 7. Migration Path

### Phase 1: 核心抽象
1. 添加 `RepoProvider` 接口
2. 添加 `RepoWorkspace` 和 `RepoWorkspaceManager`
3. 添加 `LocalRepoProvider` 和 `TempCloneProvider`

### Phase 2: 集成
1. 在 review run / main runtime 启动阶段调用 `WorkspaceManager.prepare()`
2. 使用 `create_provider_for_workspace()` 创建 run-level read-only provider
3. 将 provider / repo_root 注入 `child_tool_factory`
4. 改造 stateless repo tools 通过 Provider 读取仓库
5. 改造 `verify_repo_context` 为不写 session 状态的诊断工具

### Phase 3: 调用链
1. 在 `POST /api/review/runs` 中调用 `WorkspaceManager.prepare()`
2. main runner 将 planner task plan append 到 main agent messages
3. main agent 通过 `TaskTool` 分发 subagent
4. subagent 使用共享只读 provider 和 PR diff 工具检索上下文
5. subagent 返回结构化 JSON review result
6. TaskTool 聚合结果交回 main agent
7. 在 run 结束时调用 `WorkspaceManager.cleanup_run()`

---

## 8. Decisions

| 问题 | 决定 |
|---|---|
| **认证方式** | 短期 PAT，长期 GitHub App；使用 GIT_ASKPASS 注入 token |
| **数据源** | 先做 Local + TempClone，跳过 GitHubApiProvider |
| **选择策略** | 按能力匹配（search 需要 clone，read 可用 local） |
| **生命周期** | workspace 绑定 (run_id, context_id)，由 WorkspaceManager 管理 |
| **head_sha** | clone 后 fetch + checkout 到 PR head_sha；prepare() 强制要求 head_sha |
| **Fork 支持** | clone base repo，fetch refs/pull/N/head 获取 PR 代码 |
| **workspace key** | (run_id, context_id) 避免并发冲突 |
| **Provider 创建** | 使用 factory 函数 create_provider_for_workspace()，根据 source_type 创建 |
| **SubAgent 工具状态** | 不扩展 `RepoContextSession`；repo tools 通过 run-level provider 只读访问，最终结果来自 subagent structured JSON |

---

## 9. Security Constraints

| 约束 | 实现 |
|---|---|
| 临时目录硬编码 | `ALLOWED_TEMP_ROOT = "/tmp/pr-copilot-clones"` |
| 路径遍历防护 | `Path.resolve()` + `relative_to()` 校验 |
| 禁止删除关键目录 | 白名单检查：`/`、`/home`、`/tmp` |
| **Token 安全** | 使用 GIT_ASKPASS 脚本，token 不进入进程参数、.git/config、日志 |
| SHA 绑定 | clone 后 fetch refs/pull/N/head + checkout 到 head_sha |
| Fork 支持 | 从 base repo fetch PR ref，不需要 clone fork |

### Token 安全细节

```python
# 不安全：token 进入进程参数（ps 可见）和 .git/config
clone_url = f"https://x-access-token:{token}@github.com/..."
subprocess.run(["git", "clone", clone_url, ...])

# 安全：使用 GIT_ASKPASS
env["GIT_ASKPASS"] = askpass_script_path  # 脚本输出 token
env["GIT_TERMINAL_PROMPT"] = "0"
subprocess.run(["git", "clone", base_url, ...], env=env)
```

---

## 10. Success Criteria

- [ ] prepare() 强制要求 head_sha，为空时抛出 ValueError
- [ ] 本地有仓库且 SHA 匹配时，自动使用 LocalRepoProvider
- [ ] 本地无仓库时，自动 shallow clone 并 checkout 到 head_sha
- [ ] 支持 fork PR：从 base repo fetch refs/pull/N/head
- [ ] 私有仓库通过 GIT_ASKPASS 安全注入 token
- [ ] token 不进入进程参数、.git/config、日志
- [ ] GIT_ASKPASS 创建失败时 clone 失败（不静默降级）
- [ ] workspace 使用 (run_id, context_id) 作为 key，避免并发冲突
- [ ] Provider 使用 factory 函数创建，根据 workspace.source_type 返回对应类型
- [ ] workspace 生命周期绑定到 review run
- [ ] run 结束后自动清理临时目录
- [ ] 路径安全校验通过（无法误删关键目录）
- [ ] fetch/checkout 失败时 workspace prepare 返回失败
- [ ] 现有 Agent 工具接口不变，对上层透明
- [ ] `RepoProvider` / `RepoWorkspace` 不挂到 `RepoContextSession`
- [ ] 所有 subagent repo tools 通过 run-level provider 或 repo_root 只读访问仓库
- [ ] subagent 最终输出通过 structured JSON review result 返回给 TaskTool
- [ ] `TaskTool` 聚合 parsed subagent results 后交给 main agent synthesis
- [ ] 所有现有测试通过
