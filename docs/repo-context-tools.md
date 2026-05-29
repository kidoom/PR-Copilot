# RepoContext Lite Tools

仓库上下文工具集，为 context SubAgent 提供受控的只读仓库访问能力。

## 架构位置

```
backend/agent_runtime/tool/
├── protocol.py          # Tool ABC
├── registry.py          # ToolRegistry
└── repo_context/        # RepoContext Lite 工具实现
    ├── models.py        # 数据模型
    ├── policy.py        # 安全策略
    ├── tools.py         # 工具函数
    └── registrations.py # Tool 类包装器
```

## 工具列表

| 工具 | 用途 | 需要验证 | 消耗预算 |
|---|---|---|---|
| `verify_repo_context` | 校验仓库 workspace 匹配 PR owner/repo/head_sha | 否 | 否 |
| `read_file_patch` | 读取 PR 中文件的 diff/hunk | 否 | 否 |
| `search_diff` | 在 PR diff 中搜索关键词 | 否 | 否 |
| `search_repo` | 在仓库中搜索关键词 | 是 | 搜索 |
| `read_repo_file` | 读取仓库文件片段 | 是 | 文件 |
| `search_tests_for` | 根据源文件查找候选测试 | 是 | 搜索 |
| `read_repo_manifest` | 读取 README、依赖、CODEOWNERS、CI、规则文件 | 是 | 否 |
| `read_check_summary` | 读取 CI/CD 检查摘要（占位） | 否 | 否 |
| `finish_context_package` | 提交结构化 ContextEvidencePackage | 否 | 否 |
| `todo_write` | 创建/更新任务计划 | 否 | 否 |

## 安全策略

### 验证门控

`search_repo`、`read_repo_file`、`search_tests_for`、`read_repo_manifest` 必须先调用 `verify_repo_context` 才能执行。未验证时返回错误。

`read_file_patch` 和 `search_diff` 不需要验证，因为它们操作的是 PRContext 中已有的 diff 数据。

### 路径安全

- 路径遍历（`../../../etc/passwd`）被拒绝
- 仓库外的绝对路径被拒绝
- 忽略目录（`.git`、`node_modules`、`__pycache__` 等）被跳过

### 敏感文件

以下文件模式被阻止，不返回原始内容：
`.env`、`.env.*`、`private_key`、`id_rsa`、`.pem`、`.key`、`credentials`、`secret`

### 预算跟踪

每个 task 有独立预算：
- `max_searches`: 搜索操作上限（默认 5）
- `max_files`: 文件读取上限（默认 10）
- `max_tokens`: 输出 token 上限（默认 3000）

预算耗尽后，对应操作返回 `budget exhausted` 错误。

## 数据模型

### RepoContextSession

```python
@dataclass
class RepoContextSession:
    context_id: str           # PR 上下文 ID
    task_id: str              # 任务 ID
    repo_root: str            # 仓库根目录
    verification: RepoVerificationState
    budget: TaskBudget
    usage: ToolUsage
    final_package: ContextEvidencePackage | None
    todos: list[dict]
```

### ContextEvidencePackage

SubAgent 必须通过 `finish_context_package` 提交结构化输出：

```python
@dataclass
class ContextEvidencePackage:
    task_id: str
    task_type: str
    status: PackageStatus     # found_context | inconclusive | blocked | error
    findings: list[ContextFinding]
    uncertainties: list[str]
    tool_usage: ToolUsage
```

### ContextFinding

```python
@dataclass
class ContextFinding:
    claim: str                # 发现声明
    confidence: float         # 置信度 0-1
    evidence: list[ContextEvidenceRef]  # 证据引用
```

## Per-Agent 工具白名单

每种 task_type 有独立的工具白名单：

| Agent Type | 允许的工具 |
|---|---|
| test-context-agent | todo_write, verify_repo_context, read_file_patch, search_diff, search_tests_for, read_repo_file, finish_context_package |
| reference-context-agent | + search_repo |
| security-context-agent | + search_repo, read_repo_manifest |
| config-context-agent | search_diff, search_repo, read_repo_file, read_repo_manifest, read_check_summary |
| data-context-agent | + search_repo |
| runtime-context-agent | + search_repo |
| patch-deep-dive-agent | todo_write, read_file_patch, search_diff, read_repo_file, finish_context_package（无 search_repo） |

所有 agent 均禁止 `task_tool` 和 `sub_agent`（防止递归）。

## 使用示例

```python
from backend.agent_runtime.tool.repo_context import (
    RepoContextSession, TaskBudget, create_context_tools,
)
from backend.agent_runtime.tool.registry import ToolRegistry

# 创建 session
session = RepoContextSession(
    context_id="ctx_abc",
    task_id="task_123",
    budget=TaskBudget(max_searches=5, max_files=10),
)

# 创建工具并注册
tools = create_context_tools(session, pr_context)
registry = ToolRegistry()
for tool in tools:
    registry.register(tool)

# agent 先验证仓库
await registry.resolve("verify_repo_context").call({
    "owner": "kidoom", "repo": "PR-Copilot",
    "head_sha": "abc123", "workspace_root": "/path/to/repo",
})

# 然后搜索
result = await registry.resolve("search_repo").call({"query": "authenticate"})
```
