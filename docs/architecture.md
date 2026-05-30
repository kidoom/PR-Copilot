# PR Copilot 系统设计架构文档

## 1. 项目概述

PR Copilot 是一个 PR 代码审查辅助工具，通过分析 GitHub PR 的元数据、变更文件和 diff 内容，自动生成风险评估、优先级排序和审查建议，帮助开发者高效完成代码审查。

本项目的黑客松目标不是做一个简单的 PR diff 展示器，而是构建一个 **AI 辅助 PR Review 助手**。系统需要支持用户指定 GitHub PR，自动获取代码变更，并结合上下文进行智能分析，最终辅助开发者完成：

- PR 变更总结
- 风险代码识别
- Review 建议生成
- 重点文件导航
- 可解释的证据链展示

当前实现已经完成 PRContext 获取层和部分输入视图层。后续 AI Review 层必须建立在结构化上下文和证据约束之上，不能直接把完整 diff 丢给模型自由生成建议。

---

## 1.1 架构目标重新校准

面向题目要求，项目主线应拆成四层：

```
GitHub PR
  │
  ▼
1. PRContext 获取层
  - 获取 PR metadata / commits / changed files / patch hunks
  - 识别语言、文件类型、高风险路径、patch 边界情况
  - 生成 priority_score_hint 和基础 derived signals
  │
  ▼
2. 输入视图优化层
  - Intake Summary
  - File Priority View
  - Patch Index View
  - Patch Chunk View
  - 未来加入 RepoContext View / Checks View / Comments View
  │
  ▼
3. 证据与风险识别层
  - Rule Analyzer
  - Evidence Store
  - 风险热点和严重程度初判
  - rules-only fallback
  │
  ▼
4. AI Review Agent 层
  - 基于上下文视图和 evidence 生成 PR 总结
  - 生成 Review 建议
  - 提出需要作者确认的问题
  - 模型不可用时回退到 rules-only 报告
```

其中前两层解决“看得清”和“吃得下”，第三层解决“有证据”，第四层才解决“AI 辅助表达和推理”。

---

## 1.2 上下文理解策略

只看 PR diff 的 AI Review 是片面的。它只能知道“这次改了什么”，但很难知道：

- 被改函数在哪里被调用
- 是否破坏接口契约
- 仓库里是否已有更合适的工具函数
- 是否已有相关测试
- 配置变更是否影响部署
- 变更是否符合项目惯例

因此，本项目采用分层上下文策略：

### 当前 MVP 上下文

- `PRContext`：PR metadata、commits、changed files、patch hunks、文件分类、优先级提示。
- `Intake Summary`：PR 大小、改动类型、语言分布、文件类型分布、显著信号。
- `File Priority View`：must_review / should_review / skim 文件分组。
- `Patch Chunk View`：按需读取局部 patch，避免上下文爆炸。

### 下一阶段上下文：RepoContext Lite

在 AI Review Agent 前引入轻量仓库上下文：

- README / 项目说明
- 依赖文件：`package.json`、`requirements.txt`、`pyproject.toml`、`go.mod`、`pom.xml`
- CODEOWNERS
- changed file 的完整 raw content
- changed file 同目录下的相关文件
- changed file 同目录下的候选测试文件

它用于回答：

- 这个项目大概是什么技术栈？
- 被改文件周围有哪些相关文件？
- 有没有明显的测试文件或约定？
- 哪些文件可能是后续 Agent 需要按需读取的上下文？

### 未来上下文：SymbolContext / SemanticContext

成熟 AI Review 工具通常会引入 Symbol Indexing、AST 和 Embeddings。本项目未来也应扩展：

- Symbol Indexing：提取函数、类、导出符号、API route、配置 key。
- 引用搜索：查找 changed symbols 在仓库中的调用方和测试引用。
- AST / tree-sitter：理解语言结构，降低纯文本匹配误报。
- Embedding 检索：按语义找到相关实现、测试和历史模式。
- CI / Checks：结合自动化测试、lint、build、安全扫描结果。
- Review Comments：结合已有人工讨论和作者解释，避免重复建议。

### Agent 使用原则

Agent 默认不接收完整 PR diff，也不接收完整仓库代码。

Agent 默认输入：

- Intake Summary
- File Priority View
- Patch Index View
- Evidence Store
- RepoContext Lite 摘要

Agent 按需工具：

- read_file_patch
- search_diff
- read_related_file
- search_symbol_references
- read_check_summary
- read_review_comments_summary

Agent 路由与分发：

- Planner 生成的是可路由的 `ContextTask`，不是普通 todo，也不直接写死具体 Agent 类名。
- 每个任务包含 `task_type` / `route_key`、来源 evidence、目标文件/符号、查询词、预算、期望输出和 fallback 策略。
- `Task Registry` 负责把 `task_type` 映射到执行配置，例如 agent_type、allowed_tools、output_schema、max_steps。
- `Agent Registry` 负责注册 SubAgent 定义，例如 system prompt、默认步数、工具 allowlist / denylist。
- `Dispatcher` 只做通用分发：根据 task_type 解析 route，再调用统一 `task_tool` 派发给对应 SubAgent，避免在编排层堆积 `if route_to == ...`。
- SubAgent 默认禁止再次调用 `task_tool`，避免递归分发。
- 当前采用树形智能体结构：Main Loop fork 多个 SubAgent，SubAgent 之间不直接通信。
- Planner 生成的每个 ContextTask 必须自包含，兄弟任务之间不能存在信息依赖；如果存在依赖，必须由 Main Loop 分阶段规划和派发。
- Main Loop 是唯一汇总点，负责注入公共上下文、收集结果、去重合并，并决定是否进入第二轮任务规划。
- 执行模型采用主循环复用：Main Loop 通过统一 `task_tool` fork 多个 SubAgent，所有 SubAgent 复用同一个 runner，通过不同 agent_type、subsystem prompt、工具 allowlist、预算和输出 schema 区分职责。
- 七类 read-only 上下文任务可先实现为七套 agent prompt，而不是七套执行器：`test-context-agent`、`reference-context-agent`、`security-context-agent`、`config-context-agent`、`data-context-agent`、`runtime-context-agent`、`patch-deep-dive-agent`。

测试任务分层：

- 第一层读取 CI / Checks 状态和 workflow 配置，属于只读上下文任务，初期可归入 `config_context`，未来可拆成 `ci_context`。
- 第二层检查测试上下文，归入 `test_context`，负责查相关测试文件、测试覆盖线索和 source-without-tests 证据。
- 第三层运行本地测试或 CI 命令不属于默认 SubAgent 任务，未来应作为需要用户确认的受控执行工具，由 Main Loop 决策触发。

输出约束：

- 有证据的地方给明确建议。
- 缺少仓库上下文的地方用“建议确认 / 需要作者说明”表达。
- 不生成没有 evidence 或上下文支撑的强断言。
- 不把 `priority_score_hint` 当作最终风险分数。

---

## 2. 整体架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Frontend (React)                           │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  ┌───────────────────┐  │
│  │ PR Input │  │Dashboard │  │  Intake   │  │   Diff Sidebar    │  │
│  │  (URL)   │→ │ Overview │  │ Summary   │  │   (Patch View)    │  │
│  └──────────┘  └──────────┘  └───────────┘  └───────────────────┘  │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ HTTP (REST)
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         Backend (FastAPI)                            │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    API Routes Layer                          │   │
│  │  POST /api/pr/context          → 创建 PR 上下文             │   │
│  │  GET  /api/pr/context/{id}/patch-index → 补丁索引           │   │
│  │  GET  /api/pr/context/{id}/files/{name}/patch → 文件补丁    │   │
│  │  POST /api/review/intake       → Intake 摘要                │   │
│  └───────────────────────────┬─────────────────────────────────┘   │
│                              │                                      │
│  ┌───────────────────────────▼─────────────────────────────────┐   │
│  │                   PR Context Module                          │   │
│  │  ┌──────────┐ ┌───────────┐ ┌──────────┐ ┌──────────────┐  │   │
│  │  │ Fetcher  │ │  Parser   │ │Classifier│ │   Scorer     │  │   │
│  │  │(GitHub)  │ │ (Hunks)   │ │(Lang/Risk)│ │ (Priority)   │  │   │
│  │  └──────────┘ └───────────┘ └──────────┘ └──────────────┘  │   │
│  │  ┌──────────────────────────────────────────────────────┐   │   │
│  │  │              Context Manager (Orchestrator)           │   │   │
│  │  └──────────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                 Review Pipeline Module                       │   │
│  │  ┌────────────┐ ┌──────────────┐ ┌────────────────────┐ │   │
│  │  │  Intake    │ │File Priority │ │ Patch Input Views  │ │   │
│  │  │ Summary    │ │    View      │ │ (Index/Chunk)      │ │   │
│  │  └────────────┘ └──────────────┘ └────────────────────┘ │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │          Future Context / Evidence / Agent Modules           │   │
│  │  ┌────────────┐ ┌──────────────┐ ┌────────────────────┐    │   │
│  │  │RepoContext │ │ Evidence     │ │ AI Review Agent    │    │   │
│  │  │    Lite    │ │ Store/Rules  │ │ (Model Adapter)    │    │   │
│  │  └────────────┘ └──────────────┘ └────────────────────┘    │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                  GitHub Client Module                        │   │
│  │  ┌──────────────┐  ┌──────────────┐                         │   │
│  │  │ GitHubClient │  │  URL Parser  │                         │   │
│  │  │  (httpx)     │  │              │                         │   │
│  │  └──────────────┘  └──────────────┘                         │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
                    ┌──────────────────────┐
                    │    GitHub API        │
                    │  (REST v2022-11-28)  │
                    └──────────────────────┘
```

---

## 3. 模块职责划分

### 3.1 `backend/domain/github/` — GitHub 数据获取层

| 文件 | 职责 |
|------|------|
| `client.py` | 封装 GitHub REST API，支持分页、认证、错误处理（401/403/404/限流） |
| `url_parser.py` | 解析 PR URL，提取 owner/repo/pull_number |

### 3.2 `backend/domain/pr_context/` — PR 上下文构建层

| 文件 | 职责 |
|------|------|
| `fetcher.py` | 定义原始数据模型（PRMetadata, ChangedFile, CommitInfo），归一化 API 响应 |
| `hunk_parser.py` | 解析 unified diff 补丁为结构化 Hunk/HunkLine 对象 |
| `edge_handler.py` | 处理边缘情况：二进制文件、大补丁（>50KB）、解析错误隔离 |
| `classifier.py` | 文件分类：语言识别（30+扩展名）、类型判定、高风险路径检测 |
| `scorer.py` | 优先级评分算法（0-100分） |
| `context_manager.py` | 核心编排器：组装完整上下文，内存存储，提供三种视图函数 |

### 3.3 `backend/domain/review/` — 审查流水线层

| 文件 | 职责 |
|------|------|
| `intake.py` | PR 摘要分析：大小分类、变更类型、分布统计、显著信号 |
| `file_priority.py` | 文件审查优先级视图：must_review / should_review / skim |
| `evidence.py` | 证据分析：规则命中、证据去重、严重程度和置信度 |
| `context_task_planner.py` | ContextTask 规划：任务类型、路由、Agent 定义 |

### 3.4 `backend/api/routes/` — API 路由层

| 文件 | 职责 |
|------|------|
| `pr_context.py` | PR 上下文 API 路由：创建上下文、补丁索引、文件补丁 |
| `review.py` | 审查流水线 API 路由：Intake、文件优先级、证据、任务规划 |

### 3.5 `backend/agent/` — Agent 运行时层

| 包 | 职责 |
|------|------|
| `model/` | LLM 客户端抽象：ModelClient、ModelConfig、OpenAI 客户端、消息类型 |
| `runtime/` | Agent 循环与编排：run_loop、SubAgent、TaskTool、执行追踪 |
| `tools/` | Tool 协议、ToolRegistry、Agent Tool 定义 |
| `tools/repo_context/` | RepoContext Lite 工具：模型、策略、业务服务、Tool 包装器 |

### 3.5 `frontend/` — 前端展示层

| 文件 | 职责 |
|------|------|
| `App.tsx` | 主应用组件，包含输入、Dashboard、Diff 侧边栏 |
| `api.ts` | API 客户端，含数据归一化处理 |
| `types.ts` | TypeScript 类型定义 |
| `components/ui/` | shadcn/ui 组件库 |

---

## 4. 核心数据结构

### 4.1 数据模型关系图

```
PRContext (顶层上下文)
├── context_id: str
├── source: str
├── fetched_at: str
├── cache_key: str
├── owner / repo / pull_number
│
├── pr: PRMetadata
│   ├── title, body, author, url, state, merged
│   ├── base_branch, head_branch
│   ├── created_at, updated_at
│   ├── additions, deletions, changed_files
│   └── labels[], assignees[], requested_reviewers[]
│
├── commits: CommitsData
│   ├── head_sha: str
│   └── commits: CommitInfo[]
│       ├── sha, message, author, date
│
├── files: FileEntry[]
│   ├── 文件标识: filename, previous_filename, status
│   ├── 变更统计: additions, deletions, changes
│   ├── 语言分类: language, language_family, rule_profile
│   ├── 类型标记: is_test, is_docs, is_config, is_source, is_generated
│   ├── 补丁元数据: is_binary, patch_available, large_patch, parse_error
│   ├── 风险评估: is_high_risk_path, risk_hints[], priority_score_hint
│   ├── 统计数据: hunk_count, added_line_count, removed_line_count, keywords[]
│   └── 实际数据: hunks: Hunk[]
│       ├── header, old_start, old_lines, new_start, new_lines
│       └── lines: HunkLine[]
│           ├── type: "context" | "added" | "removed"
│           ├── content, old_line, new_line
│
└── derived: DerivedSignals
    ├── total_hunks: int
    ├── source_files_changed: int
    ├── test_files_changed: int
    ├── docs_only: bool
    ├── has_source_without_tests: bool
    └── high_risk_files: str[]
```

### 4.2 Intake 摘要结构

```
IntakeSummary
├── context_id: str
├── size: "small" | "medium" | "large"
├── change_type: "docs" | "test" | "source" | "config" | "mixed"
├── docs_only: bool
├── source_without_tests: bool
├── has_high_risk_paths: bool
├── language_distribution: { [lang]: count }
├── file_type_distribution: { [type]: count }
├── top_directories: { directory, file_count }[]
└── notable_signals: str[]
```

---

## 5. 数据处理流水线

```
GitHub API
    │
    ▼
┌─────────────────┐
│ 1. 获取原始数据  │  get_pr() / get_commits() / get_files()
└────────┬────────┘
         ▼
┌─────────────────┐
│ 2. 归一化模型    │  fetch_pr_metadata() / fetch_changed_files()
└────────┬────────┘
         ▼
┌─────────────────┐
│ 3. 边缘处理      │  process_file()
│  - 二进制检测    │    → is_binary, large_patch, parse_error
│  - 大补丁检测    │    → hunks: Hunk[]
│  - 补丁解析      │
└────────┬────────┘
         ▼
┌─────────────────┐
│ 4. 文件分类      │  classify_file()
│  - 语言识别      │    → language, language_family
│  - 类型判定      │    → is_test, is_docs, is_config, is_source
│  - 风险路径检测  │    → is_high_risk_path, risk_hints[]
└────────┬────────┘
         ▼
┌─────────────────┐
│ 5. 优先级评分    │  compute_priority_score()
│  - 路径风险 45%  │    → priority_score_hint (0-100)
│  - 变更大小 30%  │
│  - 文件状态 15%  │
│  - 语言风险 10%  │
└────────┬────────┘
         ▼
┌─────────────────┐
│ 6. 关键词提取    │  _extract_keywords()
│  - 安全关键词    │    → keywords[]
│  - 风险模式      │
└────────┬────────┘
         ▼
┌─────────────────┐
│ 7. 组装上下文    │  build_pr_context()
│  - FileEntry[]   │    → PRContext
│  - DerivedSignals│
└────────┬────────┘
         ▼
┌─────────────────┐
│ 8. Intake 分析   │  build_intake_summary()
│  - 大小分类      │    → IntakeSummary
│  - 变更类型      │
│  - 分布统计      │
│  - 显著信号      │
└─────────────────┘
```

---

## 5.1 Planner → TaskTool → Subagent 执行流程

```text
Planner (context_task_planner.py)
  │
  │ build_context_task_plan(ctx, evidence)
  ▼
TaskPlan { tasks[], routes[], agents[] }
  │
  │ 传递给 TaskTool.call({ task_plan })
  ▼
TaskTool.run_many()
  │
  ├─ 遍历每个 task
  │   ├─ _resolve_route(task, route_index)
  │   │   → route_key 优先，task_type 次之，conventional fallback 最后
  │   ├─ 确定 effective_agent_type
  │   ├─ _build_dispatch_prompt(task_payload)
  │   └─ self.run(prompt, task, agent_type, max_steps)
  │       │
  │       │ 调用 runner
  │       ▼
  │   build_subagent_runner() 返回的 runner
  │       ├─ AgentRegistry.resolve(agent_type) → AgentDefinition
  │       ├─ child_tool_factory(child_session_id, task)
  │       │   └─ build_context_child_tools()
  │       │       ├─ 创建 RepoContextSession(context_id, task_id, budget)
  │       │       └─ create_context_tools(session, pr_context)
  │       ├─ filter_tools(registry, agent_def)
  │       │   └─ 按 allowed_tools 过滤，移除 task/task_tool/sub_agent
  │       └─ run_subagent(model, agent_def, prompt, max_steps, child_tools)
  │           │
  │           │ SubAgent 独立执行
  │           ▼
  │       SubAgent { output, agent_type, child_session_id, steps }
  │
  ▼
results[] — 每个 task 一个结构化结果
```

### 子 Agent 注册

七个 context subagent 定义在 `backend/agent/subagents.py`：

| Agent Type | 职责 |
|---|---|
| `test-context-agent` | 查找相关测试、测试缺口、覆盖率信号 |
| `reference-context-agent` | 查找引用、调用方、API 使用、符号影响 |
| `security-context-agent` | 检查认证、授权、密钥、注入、输入验证 |
| `config-context-agent` | 检查配置、环境变量、依赖、CI/CD |
| `data-context-agent` | 检查 schema、migration、model、缓存、数据访问 |
| `runtime-context-agent` | 检查异常处理、异步、并发、超时、重试、资源生命周期 |
| `patch-deep-dive-agent` | 深入分析复杂 patch 的边界情况和实现风险 |

### 添加新的 SubAgent 类型

1. 在 `backend/agent/subagents.py` 中添加 prompt 常量（base + focus section）
2. 在 `_PROMPT_MAP`、`_AGENT_ALLOWED_TOOLS`、`_AGENT_DESCRIPTIONS` 中注册
3. 在 `backend/domain/review/context_task_planner.py` 的 `TASK_ROUTES` 和 `AGENT_DEFINITIONS` 中添加对应路由
4. 运行 `test_subagents.py` 验证注册完整性

---

## 5.2 Agent Run 与 WebSocket 流式输出约束

前端真正使用 Agent 时，后端不能用一个长时间阻塞的 HTTP 请求等待整轮 review 完成。Agent 执行应拆成两条通道：

- HTTP API：创建 run、返回 `run_id` 和初始状态；查询 run 状态；取消 run；读取最终结果或历史记录。
- WebSocket：按 `run_id` 订阅实时事件，流式返回 main agent 输出、工具调用、SubAgent 启动/完成、错误和最终完成事件。

推荐交互流程：

```text
Frontend
  │
  │ POST /api/review/runs { context_id, task_plan? }
  ▼
Backend HTTP
  │
  │ 创建 AgentRunSession，返回 { run_id, status: "queued" | "running" }
  ▼
Frontend
  │
  │ WS /ws/review-runs/{run_id}
  ▼
Backend WebSocket
  │
  ├─ run.started
  ├─ message.delta
  ├─ tool.call { name: "task", input_summary }
  ├─ subagent.started { agent_type, task_id }
  ├─ subagent.progress / tool.call / tool.result
  ├─ subagent.completed { agent_type, task_id, status }
  ├─ message.delta
  └─ run.completed / run.failed / run.cancelled
```

因此，`TaskPlan -> main agent -> TaskTool -> SubAgent` 的执行入口不应设计成同步 HTTP 大响应。HTTP 层只负责启动和管理 run；Agent 输出、执行追踪和工具观测结果都应通过 WebSocket 事件流返回前端。

后续运行时建议拆分为：

- `backend/api/routes/review_runs.py`：HTTP 创建、查询、取消 review run。
- `backend/api/routes/review_ws.py`：WebSocket 订阅 run 事件。
- `backend/agent/runtime/run_manager.py`：管理 `run_id`、状态、后台任务、取消信号和最终结果。
- `backend/agent/runtime/events.py`：定义统一事件结构，例如 `run.started`、`message.delta`、`tool.call`、`tool.result`、`subagent.started`、`subagent.completed`、`run.completed`。
- `backend/agent/runtime/main_runner.py`：使用全局 `AgentDeps` 构建 model、main messages、main runtime，并启动 `run_loop()`。

边界原则：

- `backend/deps.py` 只预加载静态依赖和构造单次运行态，不直接代表一次用户 run。
- `main_runner` 负责把 planner 输出 append 到 main agent messages，并启动 main loop。
- main agent 仍通过 `TaskTool` 分发任务，不能由 HTTP API 或 run manager 直接绕过 main agent 去调 SubAgent。
- WebSocket 事件必须来自实际执行过程，不能为了前端展示伪造 tool/subagent 事件。

---

## 6. 评分算法设计

### 6.1 优先级评分（0-100）

```
priority_score = path_risk × 0.45
               + change_size × 0.30
               + file_status × 0.15
               + language_risk × 0.10
```

| 维度 | 计算方式 |
|------|---------|
| path_risk | 高风险路径（auth/payment/db/config）→ 高分 |
| change_size | additions + deletions 线性映射，capped at 500 |
| file_status | added > modified > renamed > removed |
| language_risk | backend > frontend > config > docs |

### 6.2 PR 大小分类

| 级别 | 文件数 | 总行数 |
|------|--------|--------|
| small | ≤3 | ≤100 |
| medium | ≤10 | ≤500 |
| large | >10 | >500 |

### 6.3 变更类型分类

| 类型 | 条件 |
|------|------|
| docs | 全部文件为文档 |
| test | 全部文件为测试 |
| config | 全部文件为配置 |
| source | 源码文件数 > max(测试, 配置, 文档) |
| mixed | 其他情况 |

---

## 7. API 接口设计

### 7.1 PR Context API

```
POST /api/pr/context
├── 入参: { pr_url: str, github_token?: str }
├── 处理: 获取 GitHub 数据 → 构建上下文 → 存储内存
└── 返回: Overview View（不含补丁数据）

GET /api/pr/context/{context_id}/patch-index
├── 处理: 从内存获取上下文
└── 返回: 文件列表（按优先级降序）

GET /api/pr/context/{context_id}/files/{filename}/patch
├── 查询参数: hunk_index?, max_lines=500
├── 处理: 获取指定文件的补丁数据
└── 返回: { hunks, truncated, ... }
```

### 7.2 Review Pipeline API

```
POST /api/review/intake
├── 入参: { context_id: str }
├── 处理: 从内存获取上下文 → 分析摘要
└── 返回: IntakeSummary
```

---

## 8. 前端交互流程

```
用户输入 PR URL
        │
        ▼
  [Analyze PR] 按钮
        │
        ▼
  POST /api/pr/context ──────────────────┐
        │                                 │
        ▼                                 │
  显示 Loading 状态                       │
        │                                 │
        ▼                                 │
  接收 PrContextResponse                  │
        │                                 │
        ├──→ POST /api/review/intake      │
        │    接收 IntakeSummary           │
        │                                 │
        ▼                                 ▼
  渲染 ResultDashboard                    │
  ├── PR 元数据卡片                       │
  ├── Intake 摘要面板                     │
  ├── 风险信号面板                        │
  ├── 审查建议面板                        │
  ├── 变更文件表格                        │
  └── AI 审查就绪面板                     │
        │                                 │
        ▼                                 │
  点击文件行                              │
        │                                 │
        ▼                                 │
  GET /api/pr/context/{id}/files/{name}/patch
        │
        ▼
  打开 Diff Sidebar
  ├── 文件信息头部
  ├── Hunk 头部
  └── 行级 Diff（增/删/上下文）
```

---

## 9. 设计决策与权衡

### 9.1 内存存储 vs 持久化

**当前方案**：内存字典 `_contexts: dict[str, PRContext]`
- 优点：简单、零依赖、开发快
- 缺点：重启丢失、单进程限制
- **未来演进**：引入 Redis 或数据库持久化

### 9.2 补丁按需加载

**设计选择**：Overview 不含补丁数据，点击文件时按需获取
- 优点：初始加载快、减少带宽
- 缺点：点击时有额外请求
- **权衡**：补丁数据量大（每个文件可达数十KB），按需加载更合理

### 9.3 分页处理

**GitHub API 分页**：自动遍历所有页面（per_page=100）
- 适用于 commits 和 files 端点
- Link header 解析下一页 URL

### 9.4 错误处理策略

| 错误类型 | 处理方式 |
|---------|---------|
| 401 Unauthorized | 提示 token 无效或过期 |
| 403 Rate Limited | 提示提供 GitHub token |
| 404 Not Found | 提示 PR 不存在或仓库私有 |
| 大补丁（>50KB）| 标记 large_patch，跳过解析 |
| 二进制文件 | 标记 is_binary，跳过解析 |
| 解析错误 | 隔离错误，标记 parse_error |

### 9.5 语言识别策略

基于文件扩展名的映射表（30+扩展名），分为 5 个语言族：
- backend: py, java, go, rs, rb, php, cs, sql
- frontend: js, ts, jsx, tsx, html, css, scss, less
- config: yaml, yml, json, toml, ini, cfg, sh, bash, dockerfile
- docs: md, rst, txt
- unknown: 其他

---

## 10. 高风险路径检测

自动识别以下路径模式：

| 风险类型 | 路径关键词 |
|---------|-----------|
| 认证 | auth, login, session, oauth, jwt, token |
| 支付 | payment, billing, stripe |
| 数据库 | database, db, migration, schema |
| 配置 | config, settings, env, .env |

---

## 11. 安全关键词提取

扫描新增行，检测以下风险模式：

```
token, password, secret, api_key, credential,
auth, login, session,
sql, query, execute, raw_sql,
eval, exec, subprocess, shell,
cors, csrf, xss, injection
```

---

## 12. 技术栈

| 层 | 技术 |
|----|------|
| Frontend | React 18 + TypeScript + Vite + shadcn/ui + Tailwind CSS |
| Backend | Python 3.12 + FastAPI + Pydantic |
| HTTP Client | httpx (async) |
| GitHub API | REST v2022-11-28 |
| 测试 | pytest + pytest-asyncio |

---

## 12.1 黑客松 MVP 交付路线

题目要求最终体现“AI 辅助分析为核心”，因此 MVP 不能停留在 PR 数据获取和文件展示。建议交付路线如下：

### 已完成 / 当前进行中

1. **PRContext 获取**
   - GitHub PR URL 解析
   - PR metadata / commits / changed files 获取
   - patch hunk 解析
   - 文件语言、类型、高风险路径识别
   - priority_score_hint

2. **输入视图优化**
   - Intake Summary
   - File Priority View
   - Patch Index / Patch Chunk

### 下一步建议

3. **RepoContext Lite**
   - 获取 README、依赖文件、CODEOWNERS
   - 获取 changed file raw content
   - 查找同目录相关文件和候选测试文件
   - 为 Agent 提供最小仓库上下文，避免只看 diff 的片面性

4. **Evidence / Rule Analyzer**
   - 基于 added lines 和文件上下文生成 evidence
   - 初版规则：secret/token、裸 except、eval、危险命令、SQL 拼接、source without tests、高风险路径触达
   - 每条 evidence 绑定 file、line/hunk、category、severity、confidence

5. **AI Review Agent**
   - 输入：Intake Summary、File Priority View、Patch Index、RepoContext Lite、Evidence
   - 工具：按需读取 patch、搜索 diff、读取相关文件
   - 输出：PR 变更总结、风险热点、Review 建议、需要作者确认的问题
   - 约束：所有强建议必须引用 evidence 或上下文来源

6. **前端 Review Report**
   - 展示 PR 总结
   - 展示 must_review 文件
   - 展示 evidence-backed suggestions
   - 展示模型不可用时的 rules-only fallback

### 作品说明重点

在答辩或 README 中需要明确：

- 当前上下文获取方式：PRContext + RepoContext Lite。
- 当前模型策略：OpenAI-compatible 模型适配，模型不可用时 rules-only fallback。
- 当前误报控制：规则先产出 evidence，Agent 基于 evidence 生成建议。
- 当前上下文控制：默认不把完整 diff 或完整仓库塞给模型，只按需读取局部 patch 和相关文件。
- 未来扩展方向：Symbol Indexing、AST、Embeddings、CI Checks、Review Comments、GitHub App、企业私有化。

---

## 13. 项目目录结构

```
PR-Copilot/
├── backend/
│   ├── main.py                    # FastAPI 应用入口
│   ├── requirements.txt           # Python 依赖
│   ├── api/                       # API 层：HTTP 路由和请求/响应 schema
│   │   ├── routes/
│   │   │   ├── pr_context.py      # PR 上下文路由
│   │   │   └── review.py          # 审查流水线路由
│   │   └── schemas/               # 请求/响应 schema
│   ├── domain/                    # 领域层：业务逻辑
│   │   ├── github/
│   │   │   ├── client.py          # GitHub API 客户端
│   │   │   └── url_parser.py      # PR URL 解析器
│   │   ├── pr_context/
│   │   │   ├── fetcher.py         # 数据模型 + 归一化
│   │   │   ├── hunk_parser.py     # Diff 补丁解析
│   │   │   ├── edge_handler.py    # 边缘情况处理
│   │   │   ├── classifier.py      # 文件分类器
│   │   │   ├── scorer.py          # 优先级评分
│   │   │   └── context_manager.py # 核心编排器
│   │   └── review/
│   │       ├── intake.py          # Intake 摘要分析
│   │       ├── file_priority.py   # 文件优先级视图
│   │       ├── evidence.py        # 证据分析
│   │       └── context_task_planner.py # 任务规划
│   ├── agent/                     # Agent 层：运行时和工具
│   │   ├── model/
│   │   │   ├── client.py          # ModelClient 基类
│   │   │   ├── config.py          # ModelConfig
│   │   │   ├── messages.py        # 消息类型
│   │   │   └── openai_client.py   # OpenAI 客户端
│   │   ├── runtime/
│   │   │   ├── agent_def.py       # AgentDefinition, AgentRegistry
│   │   │   ├── loop.py            # Agent 主循环
│   │   │   ├── results.py         # AgentResult
│   │   │   ├── sub_agent.py       # SubAgentResult
│   │   │   ├── subagent_runner.py # SubAgent 执行器
│   │   │   └── trace.py           # 执行追踪
│   │   └── tools/
│   │       ├── protocol.py        # Tool ABC
│   │       ├── registry.py        # ToolRegistry
│   │       ├── task.py            # TaskTool
│   │       └── repo_context/      # RepoContext 工具
│   │           ├── models.py      # 数据模型
│   │           ├── policy.py      # 安全策略
│   │           ├── service.py     # 纯业务函数
│   │           └── tool_defs.py   # Tool 类包装器
│   └── tests/
│       └── test_agent_runtime/
├── frontend/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── api.ts
│   │   ├── types.ts
│   │   └── components/ui/
│   ├── package.json
│   └── vite.config.ts
└── docs/
    └── architecture.md
```

### 包所有权说明

| 层 | 包 | 职责 |
|---|---|---|
| API | `backend/api/` | HTTP 路由、请求解析、响应构建。不包含业务逻辑。 |
| Domain | `backend/domain/` | 纯业务逻辑：PRContext、GitHub 集成、审查流水线、证据分析。不依赖 Agent 运行时。 |
| Agent | `backend/agent/` | 模型客户端、Agent 循环、Tool 协议、Agent 工具定义。可调用 Domain 层。 |

依赖方向：`api` → `domain`，`api` → `agent`，`agent` → `domain`。Domain 层不依赖 `agent` 或 `api`。
