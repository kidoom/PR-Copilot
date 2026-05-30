# 智能体策略小结

## 当前阶段下一步

我们已经完成了 PR 数据处理的前半段：

```text
PRContext
  -> Intake Summary
  -> File Priority View
```

接下来不应该马上让 Agent 生成 Review，也不应该直接把整个仓库交给模型。下一步应先做 **Evidence Layer**，也就是把“可能值得 Review 的点”结构化出来。

推荐下一阶段顺序：

```text
File Priority View
  -> Evidence Schema
  -> Rule Analyzer v1
  -> Evidence Store
  -> Context Task Planner
  -> Repo Context Lite / Repo Search Tools
  -> AI Review Agent
```

## 为什么下一步是 Evidence

File Priority View 只解决了“先看哪些文件”，但还没有回答：

- 具体哪一行可能有问题？
- 为什么它可能有问题？
- 这个问题属于安全、可靠性、测试、配置，还是可维护性？
- 这个判断来自规则、路径、patch，还是未来的仓库上下文？
- 置信度是多少？
- 能不能被后续 Agent 引用？

这些都应该由 Evidence 层承接。

Evidence 不是最终 Review 结论，而是给规则分析器、Repo 检索任务和 Agent 使用的中间证据。

## Evidence 建议结构

```json
{
  "id": "ev_001",
  "source": "rule",
  "rule_id": "python-bare-except",
  "file": "backend/app/service.py",
  "line": 42,
  "hunk_index": 0,
  "severity": "warning",
  "category": "reliability",
  "message": "新增代码中出现裸 except，可能吞掉真实异常",
  "excerpt": "except:",
  "confidence": 0.85,
  "tags": ["python", "exception", "added_line"]
}
```

第一版 Evidence 可以只来自规则扫描，不接模型：

- secret / token / password 等敏感字段
- 裸 `except`
- `eval` / `exec` / shell 命令执行
- SQL 字符串拼接
- 高风险路径变更
- source changed but tests missing
- 配置、迁移、权限、支付等目录变更

## 智能体总体策略

我们最终采用的是 **Evidence-driven Multi-Agent Review**。

核心思想：

```text
结构化 PR 数据
  -> 生成 Evidence
  -> 根据 Evidence 和文件优先级规划检索任务
  -> 多个 SubAgent 并行检索仓库上下文
  -> SubAgent 返回证据包
  -> 主 Agent 汇总、去重、判断、生成 Review
```

主 Agent 不直接读取整个仓库，也不一次性吃完整 diff。它默认只拿结构化输入：

- Intake Summary
- File Priority View
- Patch Index
- Evidence Store
- Repo Context 摘要
- SubAgent 返回的 evidence package

## SubAgent 的职责

SubAgent 不是自由聊天式 Review。它们是带任务的局部取证器。

示例任务：

- 检查 auth 改动是否影响权限调用链
- 检查 db/migration 改动是否有 schema 兼容风险
- 检查 API 改动是否有调用方同步修改
- 检查 source 变更是否有对应测试
- 检查规则 Evidence 是否能被仓库上下文证实或排除

SubAgent 返回的不是单纯总结，而是 evidence package：

```json
{
  "task_id": "auth-impact-001",
  "status": "found_context",
  "findings": [
    {
      "claim": "修改的权限函数被用户删除接口调用，可能影响删除用户权限",
      "evidence": [
        {
          "file": "backend/auth/permissions.py",
          "line": 42,
          "snippet": "def require_admin(...)"
        },
        {
          "file": "backend/users/routes.py",
          "line": 88,
          "snippet": "@require_admin"
        }
      ],
      "confidence": 0.82,
      "risk": "可能改变删除用户接口的权限行为"
    }
  ],
  "uncertainties": [
    "没有找到对应测试文件"
  ]
}
```

## Task Tool 路由方案

后续 Planner 不能只生成普通 todo，也不应该让 Orchestrator 用大量 `if task.route_to == ...` 来分发任务。更合适的方案是参考 `kgent` 的工具注册和 subagent 注册范式，把 Planner 输出设计成可路由任务，再由统一 Task Tool / Dispatcher 进行分发。

核心结构：

```text
Context Task Planner
  -> 生成结构化 ContextTask
  -> Task Registry 根据 task_type / route_key 找到路由定义
  -> Agent Registry 解析 agent_type 和工具权限
  -> Dispatcher 调用统一 task_tool
  -> SubAgent 执行并返回 evidence package
```

执行模型采用主循环复用：

```text
Main Loop / Orchestrator
  -> 调用统一 TaskTool
  -> TaskTool 根据 agent_type 启动 SubAgent
  -> 所有 SubAgent 复用同一个 SubAgent runner
  -> 不同 agent_type 通过不同 subsystem prompt 区分职责
  -> Main Loop 收集结果并综合
```

也就是说，系统不为七大任务类型分别实现七套 runner。七类任务底层都是同一个 runner，只是 route、agent_type、system prompt、工具 allowlist、预算和输出 schema 不同。

这样新增一种任务或一种 SubAgent 时，只需要注册新的 route 或 agent definition，不需要修改编排器的主逻辑。

### Planner 输出不是类名，而是路由键

Planner 不应该直接写死 `TestContextAgent` 这样的执行类名。它应该输出稳定的业务路由字段：

```json
{
  "task_id": "ctx_task_001",
  "task_type": "test_context",
  "route_key": "test_context",
  "source": {
    "evidence_ids": ["ev_source_without_tests"],
    "signals": ["source_without_tests"]
  },
  "target": {
    "files": ["backend/payment/service.py"],
    "symbols": ["PaymentService"],
    "directories": ["backend/payment"]
  },
  "tools_required": ["search_repo", "read_repo_file"],
  "queries": ["PaymentService", "test_payment", "payment service"],
  "expected_output": "related_tests_evidence_package",
  "priority": "high",
  "budget": {
    "max_searches": 3,
    "max_files": 5,
    "max_tokens": 2000
  },
  "fallback": "mark_inconclusive_if_no_related_tests_found"
}
```

其中 `task_type` / `route_key` 是路由入口，真正交给哪个 Agent、允许哪些工具、最大执行步数、输出格式，由注册表决定。

### Task Registry

Task Registry 负责把业务任务类型映射到执行配置：

```python
TaskRoute(
    task_type="test_context",
    agent_type="test-context-agent",
    allowed_tools=["search_repo", "read_repo_file", "read_file_patch"],
    output_schema="related_tests_evidence_package",
    max_steps=5,
)
```

第一版建议注册这些任务类型：

- `test_context`：查相关测试、测试缺口、候选测试文件。
- `symbol_reference`：查被改函数、类、API、配置 key 的引用。
- `security_context`：查 secret、权限、认证、危险执行、SQL 风险上下文。
- `config_context`：查配置项、环境变量、CI/CD、部署影响。
- `database_context`：查 migration、schema、model、SQL、数据兼容影响。
- `patch_deep_dive`：对高优先级文件或复杂 hunk 做局部深挖。

### Agent Registry

Agent Registry 负责注册不同 SubAgent 的系统提示词、默认步数和工具权限。参考 `kgent` 的思路，每个 Agent definition 至少包含：

- `name`
- `description`
- `system_prompt`
- `default_max_steps`
- `allowed_tools`
- `disallowed_tools`

比如：

```text
test-context-agent
  allowed_tools: search_repo, read_repo_file, read_file_patch

security-context-agent
  allowed_tools: search_repo, read_repo_file, read_file_patch

symbol-reference-agent
  allowed_tools: search_repo, read_repo_file
```

SubAgent 默认不允许再调用 `task_tool`，避免递归派发和失控。

第一版可以只实现一个通用 SubAgent runtime，然后注册七套 subsystem prompt：

- `test-context-agent`
- `reference-context-agent`
- `security-context-agent`
- `config-context-agent`
- `data-context-agent`
- `runtime-context-agent`
- `patch-deep-dive-agent`

这些 Agent 都是只读取证员，不直接修改代码、不写评论、不触发外部执行。它们的区别主要来自：

- system prompt
- task_type / intent
- allowed_tools
- max_steps / budget
- expected_output

这个设计和 `kgent` 的复用思路一致：主循环只知道统一 `task_tool`，`task_tool` 根据 `agent_type` fork 子智能体，子智能体使用同一个 runner 但带不同 prompt 和工具权限。

### Dispatcher

Dispatcher 只做通用分发，不写业务 `if/else`：

```python
route = task_registry.resolve(task.task_type)
agent = agent_registry.resolve(route.agent_type)
result = task_tool.call(task=task, route=route, agent=agent)
```

后续如果要新增 `performance_context`，只需要新增 route 和 agent definition：

```text
performance_context -> performance-context-agent
```

主编排器不需要改。

### 为什么这样设计

- 避免硬编码分发逻辑。
- Planner 输出稳定，执行策略可替换。
- 每类 SubAgent 的工具权限可控。
- 方便并行执行 read-only review task。
- 适合黑客松展示“可扩展的智能体路由编排”，而不是一个大模型直接看 diff。
- 后续可以从单进程函数平滑演进到真正多 SubAgent 并行执行。

### 树形智能体约束

当前智能体结构采用树形执行模型：

```text
Main Loop
  -> fork SubAgent A
  -> fork SubAgent B
  -> fork SubAgent C
  -> collect results
  -> Main Agent synthesize
```

这意味着 Planner 设计任务时必须遵守一个硬原则：**兄弟任务之间不能存在耦合信息依赖**。

不允许出现这种任务关系：

```text
Task B 需要等 Task A 找到的符号再继续
Task C 需要读取 Task B 的中间结论
SubAgent A 和 SubAgent B 需要互相通信才能完成任务
```

如果任务之间有依赖，应该由 Main Loop 拆成两个阶段：

```text
阶段 1：并行执行互不依赖的上下文发现任务
阶段 2：Main Loop 汇总阶段 1 结果后，再规划下一批任务
```

因此每个 ContextTask 必须是自包含的：

- 包含完整 `source`，说明来自哪些 evidence / intake signal / file priority signal。
- 包含完整 `target`，说明要查哪些文件、符号、目录。
- 包含完整 `queries` 和 `budget`。
- 包含 `expected_output`，说明返回什么 evidence package。
- 包含 `fallback`，说明查不到时如何标记 inconclusive。

SubAgent 只看自己的任务输入和允许工具，不读取兄弟 SubAgent 的上下文，也不依赖兄弟任务的中间状态。

Main Loop 是唯一的汇总点：

- 负责把公共上下文注入每个任务。
- 负责并行派发任务。
- 负责收集 SubAgent 结果。
- 负责去重、合并、排序、降噪。
- 负责决定是否需要第二轮任务规划。

这个约束可以避免智能体之间形成隐式通信网络，让并行执行更简单，也更适合当前 MVP。

## Repo Context 使用原则

我们不默认本地仓库一定和远程 PR 同步。使用仓库检索前必须先校验：

```text
verify_repo_context
  -> local git remote 是否匹配 PR owner/repo
  -> local repo 是否包含 PR head_sha
  -> 校验通过后才允许 search_repo/read_repo_file
```

如果校验失败，则降级为 diff-grounded review：

- 不做调用链强判断
- 不做“仓库里没有测试”这类绝对结论
- 输出“需要作者确认”或“仓库上下文不可用”

## 工具边界

未来给 Agent 或 SubAgent 的仓库工具必须受控：

- `search_repo(query, path_glob, limit)`
- `read_repo_file(file, start, end)`
- `read_file_patch(file, hunk_index)`
- `search_tests_for(file_or_symbol)`

限制：

- 最大搜索次数
- 最大读取文件数
- 最大返回 token
- 忽略 `.git`、`node_modules`、`dist`、`build`、`coverage`、`.venv` 等目录
- 不默认读取敏感配置文件全文
- 每条结论必须绑定证据来源

## 测试任务分层

真实 Reviewer 通常不会对每个 PR 都本地跑完整测试。更常见的顺序是：

```text
先看 CI/checks 是否通过
再看 PR 是否补了合理测试
高风险或不确定时才本地验证
```

因此测试相关能力应该拆成三层：

### 第一层：读取 CI / Checks 状态

这是只读任务，适合当前 SubAgent 架构。

任务目标：

- 读取 GitHub Checks / Status。
- 判断测试、lint、typecheck、build 是否通过。
- 读取 workflow 配置，理解 CI 覆盖了哪些命令。
- 将失败或缺失状态作为 context evidence 返回。

适合路由到：

```text
task_type: config_context
intent: inspect_ci_status
agent_type: config-context-agent
```

后续如果 CI 能力变复杂，可以单独拆出：

```text
task_type: ci_context
agent_type: ci-context-agent
```

### 第二层：检查测试上下文

这是当前 `test_context` 的核心任务。

任务目标：

- 根据 changed source files 查候选测试文件。
- 根据符号、文件名、目录名查相关测试。
- 判断 PR 是否存在 source changed but tests missing 的证据。
- 返回 found tests / missing test signals / uncertainty。

适合路由到：

```text
task_type: test_context
intent: find_related_tests
agent_type: test-context-agent
```

返回时不能直接说“没有测试”，只能说：

```text
在当前搜索预算和范围内未找到相关测试。
```

### 第三层：运行本地测试

这不属于当前默认 SubAgent 任务。

运行测试不是纯读操作，可能很慢、依赖环境、写缓存、触发外部服务，甚至产生构建产物。因此它应作为未来扩展中的受控执行工具：

```text
run_local_tests
run_lint
run_typecheck
```

这些工具必须：

- 由 Main Loop 决策，不允许普通 SubAgent 自行触发。
- 需要用户确认。
- 有命令 allowlist。
- 有 timeout。
- 有清晰的风险等级。

当前 MVP 只做前两层：

```text
读取 CI/checks 状态
检查测试上下文
```

不让智能体自动跑 CI/CD 或本地测试。

## 主 Agent 输出原则

主 Agent 最终负责生成 Review 报告，但它必须被 Evidence 约束。

输出原则：

- 有 evidence 的问题，可以给明确 Review 建议
- 有仓库上下文支持的问题，可以提高置信度
- 缺少上下文的问题，只能标注为“建议确认”
- 不输出没有证据支撑的强断言
- 不把 `priority_score_hint` 当最终风险分数
- 不把 SubAgent 的观点直接当结论，必须汇总、去重、排序、降噪

## 当前 MVP 取舍

现阶段先不要实现真正复杂的多智能体系统。

更稳的 MVP 路线是：

```text
1. Evidence Schema
2. Rule Analyzer v1
3. Evidence Store
4. Context Task Planner
5. Repo Search Tools
6. 单进程 Orchestrator 模拟多任务
7. 后续再替换为真正 SubAgent 并行执行
```

这样可以先把数据结构和证据链打牢，避免过早进入“Agent 看起来很聪明，但实际不可控”的状态。
