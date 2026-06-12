# PR Copilot API 文档

本文档描述 PR Copilot 后端当前对前端开放的 HTTP 和 WebSocket 契约，适用于本地联调和前端实现。

## 1. 基础信息

- HTTP Base URL：`http://127.0.0.1:8000`
- WebSocket Base URL：`ws://127.0.0.1:8000`
- Swagger UI：`http://127.0.0.1:8000/docs`
- 服务认证：支持 GitHub App web application flow 登录。浏览器只保存 HttpOnly 会话 cookie，不接触 GitHub user access token。
- GitHub 认证：PR Context 和异步 AI Review Run 优先复用当前 GitHub 登录态。本地联调仍可使用 `PR_COPILOT_GITHUB_TOKEN` 或 `GITHUB_TOKEN`。
- 登录保护：除 `/api/auth/*` 和 `/api/health` 外，`/api/pr/*`、`/api/review/*` 与 Review WebSocket 都要求有效 GitHub 登录态。
- 状态存储：`context_id` 和 `run_id` 当前都保存在后端内存中。后端重启后需要重新创建 Context 和 Run。

## 2. 当前能力

| 能力 | 状态 | 前端用途 |
| --- | --- | --- |
| 创建 PR Context | 已实现 | 从 GitHub PR URL 获取 PR 概览和文件列表 |
| Intake 摘要 | 已实现 | 展示 PR 大小、类型、语言分布和风险信号 |
| 文件优先级 | 已实现 | 构建 `must_review`、`should_review` 和 `skim` 文件视图 |
| 规则 Evidence | 已实现 | 展示确定性规则命中和证据 |
| Context Task Plan | 已实现 | 展示后续 Agent 调查任务 |
| 异步 AI Review Run | 已实现 | 启动主 Agent 和专用 SubAgent 编排 |
| Run 状态查询 | 已实现 | 页面刷新和断线后恢复状态 |
| WebSocket 事件流 | 已实现 | 展示流式文本、工具调用和 SubAgent 进度 |
| Run 取消 | 已实现 | 支持幂等取消和终端 `cancelled` 状态 |
| 顶层结构化 Findings | 已实现 | `final_result.findings[]` 可直接渲染 |
| GitHub Checks 摘要 | 已实现 | Config SubAgent 可读取 CI check-runs 和 legacy statuses |
| GitHub App 授权登录 | 已实现 | 使用 PKCE 和一次性 state 建立服务端 GitHub 用户会话 |

## 3. 推荐联调流程

```text
用户点击 Sign in with GitHub
  -> GET /api/auth/github/login
  -> GitHub 授权页面
  -> GET /api/auth/github/callback
  -> HttpOnly 会话 cookie
  -> GET /api/auth/session
  -> 用户输入 GitHub PR URL
  -> 如果私有仓库尚未授权，前端显示 Connect repositories
  -> GET /api/auth/github/install
  -> GitHub App 安装页选择账号、组织和仓库
  -> GET /api/auth/github/callback?code=...
  -> 前端提示仓库授权已更新
  -> POST /api/pr/context
  -> 并行请求：
       POST /api/review/intake
       POST /api/review/file-priority
       POST /api/review/evidence
       POST /api/review/context-tasks
       GET  /api/pr/context/{context_id}/patch-index
  -> 用户点击文件时按需请求：
       GET /api/pr/context/{context_id}/files/{filename}/patch
  -> 用户启动 AI Review：
       POST /api/review/runs
  -> 立即连接：
       WS /ws/review-runs/{run_id}
  -> 使用 sequence 对事件排序和去重
  -> WebSocket 结束后再次请求：
       GET /api/review/runs/{run_id}
  -> 使用 final_result.findings[] 渲染最终建议
```

## 4. 通用错误格式

FastAPI 错误响应：

```json
{
  "detail": "Context not found: ctx_xxx"
}
```

常见状态码：

| 状态码 | 含义 |
| --- | --- |
| `400` | URL、文件、hunk 参数不合法 |
| `401` | GitHub token 无效或过期 |
| `404` | Context、Run、PR 或文件不存在 |
| `422` | 请求体字段缺失或类型错误 |
| `429` | GitHub API 限流，或 GitHub 返回 `403` |
| `502` | GitHub 上游错误 |

## 5. 健康检查

### `GET /api/health`

```json
{
  "status": "ok"
}
```

## 6. GitHub App 授权登录

### `GET /api/auth/github/login`

跳转到 GitHub App 用户授权页面。后端自动生成一次性 `state` 和 PKCE `code_challenge`。

### `GET /api/auth/github/callback`

GitHub 授权后的回调地址。后端校验 `state`，使用 `code` 和 PKCE `code_verifier` 换取 user access token，并设置 HttpOnly 会话 cookie。成功后跳转：

```text
http://127.0.0.1:5173/?github_auth=success
```

安装 GitHub App 后，GitHub 也会回调此接口。安装回调没有 `state`，后端复用已有登录会话并跳转：

```text
http://127.0.0.1:5173/?github_install=success
```

### `GET /api/auth/github/install`

跳转到 GitHub App 安装或仓库范围管理页面。未登录时，先跳转到 `/api/auth/github/login?next=install`，完成 OAuth 登录后继续安装流程。

### `GET /api/auth/github/repositories/status`

检查当前登录用户是否已经授权 GitHub App 访问指定仓库。

查询参数：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `owner` | `string` | 是 | GitHub 仓库 owner |
| `repo` | `string` | 是 | GitHub 仓库名 |

响应：

```json
{
  "repository": "org/private-repo",
  "authorized": true,
  "installation_id": 123456,
  "install_url": "/api/auth/github/install"
}
```

### `GET /api/auth/session`

```json
{
  "authenticated": true,
  "user": {
    "login": "octocat",
    "name": "The Octocat",
    "avatar_url": "https://avatars.githubusercontent.com/...",
    "html_url": "https://github.com/octocat"
  }
}
```

### `POST /api/auth/logout`

清理服务端会话并删除浏览器 cookie。

### 登录保护 Hook

未登录调用业务 API：

```json
{
  "detail": "GitHub login required"
}
```

HTTP 状态码为 `401`。Review WebSocket 会使用关闭码 `4401` 拒绝匿名连接。

## 7. PR Context

### `POST /api/pr/context`

根据 GitHub PR URL 创建内存中的 PR Context。

请求：

```json
{
  "pr_url": "https://github.com/open-multi-agent/open-multi-agent/pull/123",
  "github_token": null
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `pr_url` | `string` | 是 | GitHub Pull Request URL |
| `github_token` | `string \| null` | 否 | 私有仓库或提高 GitHub API 限额时使用 |

浏览器产品流程不需要提交 `github_token`。后端优先使用当前登录会话中的 GitHub App user access token。`github_token` 仅保留给受控的本地联调调用。

私有仓库尚未连接 GitHub App 时，返回 HTTP `403`：

```json
{
  "detail": {
    "code": "github_app_repository_access_required",
    "message": "Connect this repository to the GitHub App, then try the PR analysis again.",
    "install_url": "/api/auth/github/install"
  }
}
```

响应：

```json
{
  "context_id": "ctx_123456789abc",
  "pr": {
    "title": "Improve review pipeline",
    "author": "octocat",
    "url": "https://github.com/org/repo/pull/123",
    "base_branch": "main",
    "head_branch": "feature/review",
    "additions": 120,
    "deletions": 35,
    "changed_files": 4,
    "head_sha": "abc123..."
  },
  "files": [
    {
      "filename": "backend/api/routes/review.py",
      "status": "modified",
      "additions": 20,
      "deletions": 3,
      "language": "python",
      "language_family": "python",
      "is_test": false,
      "is_docs": false,
      "is_config": false,
      "is_source": true,
      "is_binary": false,
      "is_high_risk_path": false,
      "risk_hints": [],
      "priority_score_hint": 40
    }
  ],
  "derived": {
    "docs_only": false,
    "has_source_without_tests": true,
    "high_risk_files": []
  }
}
```

说明：

- 响应是概览视图，不包含完整 patch。
- `priority_score_hint` 是审查排序提示，不是最终风险结论。
- 前端需要保留 `context_id`，后续分析接口都依赖它。

### `GET /api/pr/context/{context_id}/patch-index`

返回按风险提示排序的 patch 索引。

```json
{
  "context_id": "ctx_123456789abc",
  "files": [
    {
      "filename": "backend/api/routes/review.py",
      "hunk_count": 2,
      "added_line_count": 20,
      "removed_line_count": 3,
      "keywords": ["auth"],
      "risk_score_hint": 40
    }
  ]
}
```

### `GET /api/pr/context/{context_id}/files/{filename:path}/patch`

按需返回单个文件的 patch。前端需要对 `filename` 做 URL 编码。

| 查询参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `hunk_index` | `number \| null` | `null` | 只返回指定 hunk |
| `max_lines` | `number` | `500` | 最大返回行数 |

```json
{
  "context_id": "ctx_123456789abc",
  "filename": "backend/api/routes/review.py",
  "patch_available": true,
  "is_binary": false,
  "parse_error": null,
  "truncated": false,
  "hunks": [
    {
      "header": "@@ -1,3 +1,4 @@",
      "old_start": 1,
      "old_lines": 3,
      "new_start": 1,
      "new_lines": 4,
      "lines": [
        {
          "type": "added",
          "content": "new line",
          "old_line": null,
          "new_line": 2
        }
      ]
    }
  ]
}
```

Patch 建议按需加载，不要在首屏请求全部 diff。

## 8. 静态 Review Pipeline

以下接口请求体相同：

```json
{
  "context_id": "ctx_123456789abc"
}
```

### `POST /api/review/intake`

返回 PR 摘要信号。

```json
{
  "context_id": "ctx_123456789abc",
  "size": "medium",
  "change_type": "source",
  "docs_only": false,
  "source_without_tests": true,
  "has_high_risk_paths": false,
  "language_distribution": {"python": 4},
  "file_type_distribution": {"source": 3, "test": 1},
  "top_directories": [{"directory": "backend/api", "file_count": 2}],
  "notable_signals": ["source_without_tests"]
}
```

- `size`：`small | medium | large`
- `change_type`：`docs | test | config | source | mixed`

### `POST /api/review/file-priority`

返回文件审查优先级分组。

```json
{
  "context_id": "ctx_123456789abc",
  "groups": {
    "must_review": [],
    "should_review": [
      {
        "filename": "backend/api/routes/review.py",
        "status": "modified",
        "additions": 20,
        "deletions": 3,
        "language": "python",
        "language_family": "python",
        "is_test": false,
        "is_docs": false,
        "is_config": false,
        "is_source": true,
        "is_binary": false,
        "is_generated": false,
        "patch_available": true,
        "large_patch": false,
        "hunk_count": 2,
        "added_line_count": 20,
        "removed_line_count": 3,
        "priority_score_hint": 40,
        "reasons": ["source_change"]
      }
    ],
    "skim": []
  }
}
```

### `POST /api/review/evidence`

返回规则分析 Evidence。Evidence 是确定性输入信号，不等于最终 AI Review finding。

```json
{
  "context_id": "ctx_123456789abc",
  "evidence": [
    {
      "id": "53d52c53bd87f310",
      "source": "rule_analyzer_v1",
      "rule_id": "bare_except",
      "file": "backend/service.py",
      "severity": "warning",
      "category": "reliability",
      "message": "Bare except clause catches all exceptions including KeyboardInterrupt and SystemExit",
      "confidence": 0.9,
      "tags": ["bare_except"],
      "line": 42,
      "hunk_index": 0,
      "excerpt": "except:"
    }
  ],
  "summary": {
    "total": 1,
    "by_severity": {"warning": 1},
    "by_category": {"reliability": 1}
  }
}
```

常见 `rule_id`：

| `rule_id` | 含义 |
| --- | --- |
| `sensitive_field` | 可能存在硬编码 secret、token、password 或 API key |
| `bare_except` | Python `except:` |
| `dangerous_exec` | 动态执行或 shell 调用 |
| `sql_injection` | SQL 字符串构造风险 |
| `high_risk_path` | 高风险路径发生变更 |
| `patch_unavailable` | Patch 不可用 |
| `large_patch` | Patch 过大 |
| `source_without_tests` | 源码变更但没有测试变更 |

`excerpt` 会脱敏。PR 级 Evidence 的 `file` 可以为空字符串。

### `POST /api/review/context-tasks`

返回 Agent 编排计划。该接口只做规划，不执行模型调用。

```json
{
  "context_id": "ctx_123456789abc",
  "tasks": [
    {
      "task_id": "ctx_task_...",
      "task_type": "test_context",
      "intent": "Find related tests and gaps",
      "route_key": "test_context",
      "source": {
        "evidence_ids": [],
        "rule_ids": [],
        "signals": ["source_without_tests"],
        "file_facts": []
      },
      "target": {
        "files": ["backend/service.py"],
        "directories": [],
        "symbols": [],
        "keywords": []
      },
      "queries": ["Find tests for backend/service.py"],
      "priority": "high",
      "budget": {
        "max_searches": 4,
        "max_files": 6,
        "max_tokens": 5000
      },
      "expected_output": "Related tests and coverage gaps",
      "fallback": "Report uncertainty",
      "status": "pending"
    }
  ],
  "routes": [],
  "agents": [],
  "summary": {}
}
```

## 9. 异步 AI Review Run

### `POST /api/review/runs`

创建异步 AI Review Run。接口立即返回，不等待 Agent 完成。

```json
{
  "context_id": "ctx_123456789abc",
  "local_repo_root": null
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `context_id` | `string` | 是 | 已创建的 PR Context ID |
| `local_repo_root` | `string \| null` | 否 | 仅用于本地开发调试。生产环境应固定配置或增加权限校验 |

响应：

```json
{
  "run_id": "run_123456789abc",
  "status": "queued"
}
```

说明：

- 前端拿到 `run_id` 后应立即连接 WebSocket。
- 未提交 `local_repo_root` 时，后端读取 `PR_COPILOT_LOCAL_REPO_ROOT`。
- 私有仓库 workspace 和 GitHub Checks 使用服务端 `PR_COPILOT_GITHUB_TOKEN` 或 `GITHUB_TOKEN`。

### `GET /api/review/runs/{run_id}`

查询 Run 当前状态。页面刷新、WebSocket 断线和 Run 完成后都应调用。

运行中：

```json
{
  "run_id": "run_123456789abc",
  "context_id": "ctx_123456789abc",
  "status": "running"
}
```

完成：

```json
{
  "run_id": "run_123456789abc",
  "context_id": "ctx_123456789abc",
  "status": "completed",
  "final_result": {
    "status": "completed",
    "summary": "Review completed",
    "findings": [
      {
        "claim": "User-controlled input reaches a shell command",
        "confidence": 0.92,
        "severity": "high",
        "evidence": [
          {
            "file": "backend/service.py",
            "line": 42,
            "snippet": "subprocess.run(user_input, shell=True)",
            "source": "diff"
          }
        ],
        "fingerprint": "89dd9b1922a8f850"
      }
    ],
    "uncertainties": [],
    "notes": [],
    "task_summaries": [
      {
        "task_id": "ctx_task_...",
        "task_type": "security_context",
        "agent_type": "security-context-agent",
        "child_session_id": "run_....child_...",
        "execution_status": "ok",
        "parse_status": "valid",
        "validation_errors": []
      }
    ],
    "raw_output": "Review completed",
    "steps": 2,
    "stopped_by_max_steps": false,
    "token_usage": {
      "input_tokens": 1200,
      "output_tokens": 260
    }
  }
}
```

失败：

```json
{
  "run_id": "run_123456789abc",
  "context_id": "ctx_123456789abc",
  "status": "failed",
  "error_summary": "RuntimeError: ..."
}
```

状态：

| 状态 | 含义 |
| --- | --- |
| `queued` | 已创建，等待后台任务启动 |
| `running` | Agent 正在执行 |
| `cancelling` | 已收到取消请求，等待运行循环终止 |
| `cancelled` | 已取消，终端状态 |
| `completed` | 已完成，包含 `final_result` |
| `failed` | 执行失败，包含 `error_summary` |

### `POST /api/review/runs/{run_id}/cancel`

请求取消 Run。

```json
{
  "run_id": "run_123456789abc",
  "status": "cancelling"
}
```

语义：

- 取消 queued Run 时，状态直接进入 `cancelled`。
- 取消 running Run 时，状态先进入 `cancelling`，运行循环观察到取消后进入 `cancelled`。
- 重复取消是幂等操作。
- `completed`、`failed` 和 `cancelled` 不会被后续取消覆盖。

## 10. WebSocket Review 事件

### `WS /ws/review-runs/{run_id}`

连接：

```text
ws://127.0.0.1:8000/ws/review-runs/run_123456789abc
```

行为：

- `run_id` 不存在时，服务端关闭连接，close code 为 `4004`。
- 连接后先回放 retained events，再推送实时事件。
- retained events 最多保留最近 `100` 条。
- Run 已结束时，回放 retained events 后关闭连接。
- 收到终端事件后，服务端关闭连接，close code 为 `1000`。
- 前端使用 `sequence` 排序和去重。

统一事件：

```json
{
  "event_id": "f87e3a7bd1244784",
  "run_id": "run_123456789abc",
  "type": "message.delta",
  "sequence": 3,
  "created_at": "2026-05-31T02:00:00+00:00",
  "payload": {
    "text": "Reviewing high-risk files...",
    "agent_type": "main-agent"
  }
}
```

事件类型：

| `type` | 用途 |
| --- | --- |
| `run.started` | 主 Agent 开始执行 |
| `message.delta` | 主 Agent 可见文本增量 |
| `tool.call` | Agent 准备调用工具 |
| `tool.result` | 工具调用结束 |
| `subagent.started` | 专用 SubAgent 开始执行 |
| `subagent.completed` | 专用 SubAgent 执行结束 |
| `run.completed` | `payload` 与状态接口中的 `final_result` 相同 |
| `run.failed` | Run 执行失败 |
| `run.cancelled` | Run 已取消 |

### `message.delta`

```json
{
  "text": "Reviewing high-risk files...",
  "agent_type": "main-agent"
}
```

- `message.delta` 已主动推送。
- 单个 `text` payload 最长 `1000` 字符。
- 只推送可见文本，不推送模型 reasoning 字段。
- 最终展示仍以 `final_result` 为准。

### `tool.call`

```json
{
  "agent_kind": "subagent",
  "agent_type": "security-context-agent",
  "task_id": "ctx_task_...",
  "child_session_id": "run_....child_...",
  "tool_name": "search_repo",
  "tool_use_id": "call_...",
  "input_summary": {"query": "subprocess"}
}
```

### `tool.result`

```json
{
  "agent_kind": "subagent",
  "agent_type": "security-context-agent",
  "task_id": "ctx_task_...",
  "child_session_id": "run_....child_...",
  "tool_name": "search_repo",
  "tool_use_id": "call_...",
  "output_summary": {"total": 2},
  "is_error": false
}
```

工具事件只发送有界摘要。不要依赖它获取完整文件内容。

### `subagent.started`

```json
{
  "task_id": "ctx_task_...",
  "task_type": "security_context",
  "agent_type": "security-context-agent",
  "child_session_id": "run_....child_..."
}
```

### `subagent.completed`

```json
{
  "task_id": "ctx_task_...",
  "task_type": "security_context",
  "agent_type": "security-context-agent",
  "child_session_id": "run_....child_...",
  "memory_session_id": "subagent_...",
  "status": "valid",
  "stopped_by_max_steps": false,
  "validation_errors": []
}
```

`status` 常见值：`valid | invalid | max_steps | error`。

终端事件说明：

- `run.completed.payload` 与状态接口中的 `final_result` 结构相同。
- `run.failed.payload` 包含 `error`。
- `run.cancelled.payload` 当前为空对象。

## 11. Findings 与证据约束

顶层 `final_result.findings[]` 是前端渲染最终 Review 卡片的稳定入口。

Finding：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `claim` | `string` | Review 结论 |
| `confidence` | `number` | `0` 到 `1` |
| `severity` | `low \| medium \| high \| critical` | 风险级别 |
| `evidence` | `EvidenceRef[]` | 至少一条证据 |
| `fingerprint` | `string` | 去重后的稳定标识 |

EvidenceRef：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `file` | `string` | 必填文件路径 |
| `line` | `number \| null` | 可选行号 |
| `snippet` | `string` | 最长 `500` 字符 |
| `source` | `string` | 例如 `diff`、`file`、`search` |

后端只提升经过校验且有证据的 Findings。无证据 Finding 不会进入顶层结果。

## 12. GitHub Checks 内部契约

`read_check_summary` 是 Config SubAgent 的内部只读工具，不是公开 HTTP 接口。

成功结果：

```json
{
  "status": "ok",
  "overall_status": "failure",
  "check_runs": [
    {
      "name": "unit-tests",
      "status": "completed",
      "conclusion": "failure",
      "details_url": "https://github.com/...",
      "started_at": "2026-05-31T01:00:00Z",
      "completed_at": "2026-05-31T01:02:00Z"
    }
  ],
  "status_contexts": [
    {
      "context": "ci/legacy",
      "state": "success",
      "target_url": "https://github.com/...",
      "description": "Passed"
    }
  ],
  "total_check_runs": 1,
  "total_status_contexts": 1,
  "truncated": false,
  "summary": {
    "success": 1,
    "failure": 1,
    "pending": 0,
    "neutral": 0
  }
}
```

约束：

- `overall_status`：`success | failure | pending`
- `check_runs` 最多返回 `30` 条。
- `status_contexts` 最多返回 `30` 条。
- 无服务端 GitHub token、权限不足、限流或网络错误时，工具返回 `unavailable` 或 `error`，不会让整个 Review Run 失败。

## 13. TypeScript 契约参考

```ts
export type ReviewRunStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "cancelled"
  | "completed"
  | "failed"

export type ReviewSeverity = "low" | "medium" | "high" | "critical"

export interface EvidenceRef {
  file: string
  line: number | null
  snippet: string
  source: string
}

export interface ReviewFinding {
  claim: string
  confidence: number
  severity: ReviewSeverity
  evidence: EvidenceRef[]
  fingerprint: string
}

export interface ReviewTaskSummary {
  task_id: string
  task_type: string
  agent_type: string
  child_session_id: string
  execution_status: string
  parse_status: string
  validation_errors: string[]
}

export interface ReviewRunFinalResult {
  status: "completed"
  summary: string
  findings: ReviewFinding[]
  uncertainties: string[]
  notes: string[]
  task_summaries: ReviewTaskSummary[]
  raw_output: string
  steps: number
  stopped_by_max_steps: boolean
  token_usage: {
    input_tokens: number
    output_tokens: number
  }
}

export interface ReviewRunState {
  run_id: string
  context_id: string
  status: ReviewRunStatus
  final_result?: ReviewRunFinalResult
  error_summary?: string
}

export type ReviewRunEventType =
  | "run.started"
  | "message.delta"
  | "tool.call"
  | "tool.result"
  | "subagent.started"
  | "subagent.completed"
  | "run.completed"
  | "run.failed"
  | "run.cancelled"

export interface ReviewRunEvent<TPayload = Record<string, unknown>> {
  event_id: string
  run_id: string
  type: ReviewRunEventType
  sequence: number
  created_at: string
  payload: TPayload
}
```

WebSocket：

```ts
export function connectReviewRun(runId: string) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
  return new WebSocket(`${protocol}//127.0.0.1:8000/ws/review-runs/${runId}`)
}
```

## 14. 环境变量

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 空字符串 | 模型 API Key |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible API 地址 |
| `OPENAI_MODEL` | `gpt-4o` | 模型名称 |
| `GITHUB_APP_CLIENT_ID` | 空字符串 | GitHub App Client ID，不是 App ID |
| `GITHUB_APP_CLIENT_SECRET` | 空字符串 | GitHub App Client Secret，仅保存在后端 |
| `GITHUB_APP_SLUG` | 空字符串 | GitHub App slug，用于生成安装管理页面 URL |
| `GITHUB_APP_CALLBACK_URL` | `http://127.0.0.1:8000/api/auth/github/callback` | GitHub App Callback URL，必须与 GitHub 配置完全一致 |
| `PR_COPILOT_FRONTEND_URL` | `http://127.0.0.1:5173/` | 授权完成后的前端跳转地址 |
| `PR_COPILOT_COOKIE_SECURE` | `false` | HTTPS 部署时设置为 `true` |
| `PR_COPILOT_LOCAL_REPO_ROOT` | 空字符串 | 本地联调仓库路径 |
| `PR_COPILOT_GITHUB_TOKEN` | 空字符串 | Review Run 访问远程仓库和 GitHub Checks 时优先使用 |
| `GITHUB_TOKEN` | 空字符串 | `PR_COPILOT_GITHUB_TOKEN` 未设置时的回退值 |
| `PR_COPILOT_STORAGE_DIR` | `~/.pr-copilot` | Agent memory 和临时 workspace 根目录 |
| `PR_COPILOT_REPO_TEMP_ROOT` | `${PR_COPILOT_STORAGE_DIR}/repo-workspaces` | 仓库临时 workspace 根目录 |

CORS 当前允许：

```text
http://localhost:3000
http://127.0.0.1:3000
http://localhost:5173
http://127.0.0.1:5173
```

开发环境启动时，后端会读取项目根目录的 `.env.local`。该文件已被 Git 忽略；生产环境应使用部署平台的 Secret Manager 或环境变量注入。

## 15. 已知限制

| 限制 | 前端影响 | 建议 |
| --- | --- | --- |
| Context 和 Run 使用内存存储 | 后端重启后旧 ID 失效 | 收到 `404` 后引导用户重新分析 |
| GitHub 登录会话使用内存存储 | 后端重启后用户需要重新登录 | 生产环境接入 Redis 或数据库 |
| GitHub App 未安装到目标私有仓库 | 登录后仍无法读取该仓库 | 引导用户调整 GitHub App 安装范围 |
| Swagger 无法完整表达动态 WebSocket payload | OpenAPI 中不会出现全部事件变体 | 以本文档 WebSocket 契约为准 |

## 16. Curl 示例

```bash
# 创建 Context
curl -X POST http://127.0.0.1:8000/api/pr/context \
  -H "Content-Type: application/json" \
  -d '{"pr_url":"https://github.com/org/repo/pull/123"}'

# 获取 Evidence
curl -X POST http://127.0.0.1:8000/api/review/evidence \
  -H "Content-Type: application/json" \
  -d '{"context_id":"ctx_123456789abc"}'

# 创建 AI Review Run
curl -X POST http://127.0.0.1:8000/api/review/runs \
  -H "Content-Type: application/json" \
  -d '{"context_id":"ctx_123456789abc"}'

# 查询 Run
curl http://127.0.0.1:8000/api/review/runs/run_123456789abc

# 取消 Run
curl -X POST http://127.0.0.1:8000/api/review/runs/run_123456789abc/cancel
```
