# 开发问题记录

## 2026-05-29 Evidence Layer 审查

### P2: 敏感值检测和脱敏范围偏窄

- 位置：`backend/domain/review/evidence.py`
- 相关代码：`SENSITIVE_PATTERNS`、`_SECRET_REDACT_PATTERN`、非 secret 规则的 `excerpt`
- 问题：当前 secret 检测主要依赖变量名或 key 名包含 `secret/token/password/api_key/private_key`。如果新增行里直接出现 GitHub token 形态、Bearer token、Authorization header，或者同一行同时命中 `dangerous_exec` / `sql_injection`，非 secret evidence 的 `excerpt` 可能仍返回原始敏感值。
- 影响：违反 Evidence 响应“不暴露 token”的安全目标，也会让后续 Agent 输入携带不必要的敏感内容。
- 建议：增加值形态检测与全局 excerpt 脱敏函数，例如识别 `ghp_`、`github_pat_`、`Bearer ...`、常见云厂商 key 形态；所有规则生成 excerpt 前统一调用脱敏函数，而不是只在 `sensitive_field` 中脱敏。

### P3: 高风险路径 Evidence 的 category 过于单一

- 位置：`backend/domain/review/evidence.py`
- 相关代码：`_file_level_evidence`
- 问题：所有 `high_risk_path` evidence 都被标记为 `category="security"`，但风险 hint 可能来自 `config_path`、`db_path`、`payment_path`、`auth_path` 等不同来源。
- 影响：UI 统计和后续 ContextTaskPlanner 可能被误导，例如配置目录变更被归入 security，而不是 config。
- 建议：根据 `risk_hints` 映射 category，例如 `config_path -> config`，`db_path -> maintainability`，`auth_path/payment_path -> security`；无法判断时再使用 security 或 maintainability。

## 2026-05-29 Context Task Planner 审查

### P2: 部分 Planner 契约测试是条件断言，可能静默通过

- 位置：`backend/tests/test_context_task_planner.py`
- 相关代码：`test_source_binds_evidence_ids`、`test_source_binds_rule_ids`、`test_source_binds_signals`、`test_source_binds_file_facts`
- 问题：这些测试先筛选目标任务，然后使用 `if sec_tasks:` / `if data_tasks:` / `if ref_tasks:` 再断言字段绑定。若 planner 回归导致对应任务完全没有生成，测试仍会通过。
- 影响：无法真正保护“Evidence / rule / signal / file facts 必须绑定到任务源信息”这个核心契约，后续 subagent 可能拿到缺少证据来源的任务。
- 建议：先显式断言对应任务存在，例如 `assert sec_tasks`，再检查 `source` 字段内容；同时补充具体输入到期望任务类型的映射测试。

### P3: Config 任务统一使用 `inspect_ci_status`，语义过宽

- 位置：`backend/domain/review/context_task_planner.py`
- 相关代码：`_generate_config_context_tasks`
- 问题：普通配置文件、`config_path` 风险文件、依赖文件都被创建为 `intent="inspect_ci_status"`，其中普通配置文件还固定追加 `"check CI status and workflow impact"` 查询。
- 影响：后续 `config-context-agent` 可能把依赖或配置影响误当成 CI 状态问题，导致检索任务偏题，Review 建议也更容易泛化。
- 建议：拆分 intent，例如 CI/workflow 文件使用 `inspect_ci_status`，依赖文件使用 `inspect_dependency_impact`，普通配置文件使用 `inspect_config_usage` 或 `inspect_config_impact`。

### P3: `patch_deep_dive` 容易被 `reference_context` 覆盖而不生成

- 位置：`backend/domain/review/context_task_planner.py`
- 相关代码：`build_context_task_plan`、`_generate_patch_deep_dive_tasks`
- 问题：planner 先为所有 source 文件生成 `reference_context`，随后将所有已生成任务的文件都加入 `covered_files`，再生成 `patch_deep_dive`。因此高优先级 source 文件通常已经被 `reference_context` 覆盖，`patch_deep_dive` 很难触发。
- 影响：七类任务中用于复杂 patch 局部深挖的能力可能长期空转，大 PR 中高复杂度文件缺少专门的 patch 级检查。
- 建议：只把安全、数据、运行时、测试等更具体任务视为覆盖；或者允许高复杂度文件同时生成 `reference_context` 和 `patch_deep_dive`，用 per-type cap 控制数量。

## 2026-05-29 RepoContext Lite Tools 审查

### P1: `verify_repo_context` 必须 fail closed，不能只依赖模型输入

- 位置：`backend/agent/tools/repo_context/service.py`
- 相关代码：`verify_repo_context`、`RepoVerificationState`
- 问题：当前实现只检查 `workspace_root/.git` 是否存在，然后把 tool input 里的 `owner`、`repo`、`head_sha` 写入 verified 状态。`workspace_root`、`owner`、`repo`、`head_sha` 都来自 tool input 时，模型有机会把任意本地 git 目录声明成目标 PR 仓库。
- 影响：验证门控一旦被绕过，`search_repo`、`read_repo_file`、`read_repo_manifest` 会读取错误仓库，严重时会越权读取本机其他仓库内容。
- 建议：可信 owner/repo/head_sha 应来自 `PRContext` 或服务端 session，不应由 subagent 自由提供；remote、HEAD 校验失败或不可确认时返回未验证；`repo_root` 最好由服务端绑定，不暴露为模型可任意切换的参数。
- 复现：传入临时 `.git` 目录和错误的 `owner/repo/head_sha` 仍返回 `{"verified": true}`。

### P1: `search_repo` 必须和 `read_repo_file` 使用同一套敏感文件过滤

- 位置：`backend/agent/tools/repo_context/service.py`
- 相关代码：`search_repo`、`is_sensitive_file`
- 问题：`read_repo_file` 阻止 `.env`、key、credential 等敏感文件还不够，`search_repo` 如果遍历这些文件并返回 snippet，同样会泄露内容。
- 影响：subagent 不需要直接读文件，只要搜索 `TOKEN`、`SECRET` 等关键词就可能拿到敏感值。
- 建议：所有返回内容片段的工具都必须调用 `is_sensitive_file`；搜索结果只返回允许文件；为 `.env`、`id_rsa`、`*.pem` 等场景补回归测试。
- 复现：临时仓库中 `.env` 包含 `SECRET_TOKEN=abc123` 时，`search_repo("SECRET_TOKEN")` 会返回该行 snippet。

### P1: diff 工具必须使用真实 `HunkLine` 字段

- 位置：`backend/agent/tools/repo_context/service.py`、`backend/domain/pr_context/hunk_parser.py`
- 相关代码：`read_file_patch`、`search_diff`、`HunkLine`
- 问题：`HunkLine` 的字段是 `type`、`content`、`old_line`、`new_line`。工具实现和测试必须覆盖真实 `parse_patch` 输出，避免误用 `line_type`、`line_number` 这类不存在字段。
- 影响：所有依赖 PR diff 的 subagent 会在第一步读 patch 或搜 diff 时崩溃，导致 RepoContext Lite 无法进入后续上下文检索。
- 建议：`read_file_patch` 输出 `type/old_line/new_line`；`search_diff` 对 added 行优先返回 `new_line`，removed 行返回 `old_line`，并保留 line type。
- 复现：使用真实 `parse_patch` 输出调用 `read_file_patch` 会报 `HunkLine` 没有 `line_type`；调用 `search_diff` 会报没有 `line_number`。

### P2: token budget 必须是硬限制，而不是事后统计

- 位置：`backend/agent/tools/repo_context/service.py`
- 相关代码：`read_repo_file`、`check_budget_tokens`、`consume_token_budget`
- 问题：读取文件片段前必须先估算 token 并检查剩余额度；超过预算时应拒绝或缩小输出，而不是先返回内容再累加 usage。
- 影响：context subagent 可以通过多次读取不断超过 `max_tokens`，导致主循环上下文膨胀，也削弱 planner 分配预算的意义。
- 建议：`read_repo_file` 在返回内容前调用 `check_budget_tokens`；必要时按剩余额度截断行数；为超预算读取补测试。

### P2: `path_scope` 必须做路径归一化和包含关系校验

- 位置：`backend/agent/tools/repo_context/service.py`
- 相关代码：`search_repo`、`resolve_safe_path`
- 问题：不能用简单字符串 `startswith` 判断 scope。`src2` 会误匹配 `src`，指定 scope 时也不应继续搜索根目录无关文件。
- 影响：subagent 以为只搜某个目录，实际可能扩大搜索范围，增加噪音和误读上下文。
- 建议：`path_scope` 也走 `resolve_safe_path`，用 `Path.relative_to` 判断目录包含关系；非法 scope 直接返回错误或空结果。

### P2: 当前单测需要覆盖真实 PRContext 路径和安全回归

- 位置：`backend/tests/test_agent_runtime/test_repo_context.py`
- 相关代码：`test_read_file_patch`、`test_search_diff`、`test_search_repo_sensitive_file_blocked`
- 问题：仅测试 policy helper 和轻量 happy path 不够，必须用真实 `parse_patch` 生成的 `HunkLine`、临时 git repo、敏感文件、token 预算耗尽等场景覆盖工具行为。
- 影响：测试全绿也可能漏掉 diff 崩溃、敏感信息泄露、预算绕过这类核心问题。
- 建议：新增回归测试：真实 patch 可读可搜；`.env` 不会被 `search_repo` 返回；错误 owner/repo/head_sha 验证失败；token 超预算返回错误；`path_scope=src` 不搜索 `src2`。
