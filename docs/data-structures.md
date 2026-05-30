# PR Copilot 数据结构文档

## Pipeline 总览

```
GitHub PR URL
     │
     ▼
┌─────────────────────────────────────────────────────────┐
│  Stage 1: Ingest (POST /api/pr/context)                 │
│  fetcher → edge_handler → classifier → scorer →         │
│  context_manager                                        │
│  输出: PRContext                                         │
└─────────────────────┬───────────────────────────────────┘
                      │ context_id
                      ▼
┌─────────────────────────────────────────────────────────┐
│  Stage 2: Review Pipeline (POST /api/review/*)          │
│                                                         │
│  ┌──────────┐  ┌──────────────┐  ┌──────────┐          │
│  │  Intake   │  │ File Priority│  │ Evidence │          │
│  └────┬─────┘  └──────┬───────┘  └────┬─────┘          │
│       │               │               │                │
│       ▼               ▼               ▼                │
│  intake_summary  file_priority   evidence_response      │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│  Stage 3: Context Task Planner                          │
│  (POST /api/review/context-tasks)                       │
│                                                         │
│  PRContext + Evidence → Planner → TaskPlan              │
│  输出: ContextTask[], TaskRoute[], AgentDefinition[]    │
└─────────────────────────────────────────────────────────┘
```

---

## Stage 1: Ingest — 数据采集与上下文构建

### 1.1 GitHub API 原始数据

#### PRMetadata — PR 元信息

从 GitHub PR API 直接获取的原始数据。

| 字段 | 类型 | 含义 |
|------|------|------|
| `title` | `str` | PR 标题 |
| `body` | `str` | PR 描述正文 |
| `author` | `str` | PR 作者用户名 |
| `url` | `str` | PR 的 GitHub URL |
| `state` | `str` | PR 状态: `open`, `closed` |
| `merged` | `bool` | 是否已合并 |
| `base_branch` | `str` | 目标分支 (如 `main`) |
| `head_branch` | `str` | 源分支 (如 `feature/xxx`) |
| `created_at` | `str` | 创建时间 (ISO 8601) |
| `updated_at` | `str` | 最后更新时间 |
| `additions` | `int` | 总新增行数 |
| `deletions` | `int` | 总删除行数 |
| `changed_files` | `int` | 变更文件总数 |
| `labels` | `list[str]` | PR 标签列表 |
| `assignees` | `list[str]` | 指派人列表 |
| `requested_reviewers` | `list[str]` | 请求的审查者列表 |

#### CommitInfo — 单个提交信息

| 字段 | 类型 | 含义 |
|------|------|------|
| `sha` | `str` | 提交的 SHA 哈希 |
| `message` | `str` | 提交消息 |
| `author` | `str` | 提交作者 |
| `date` | `str` | 提交日期 |

#### CommitsData — 提交集合

| 字段 | 类型 | 含义 |
|------|------|------|
| `head_sha` | `str` | HEAD 提交的 SHA |
| `commits` | `list[CommitInfo]` | 所有提交列表 |

#### ChangedFile — GitHub API 返回的变更文件

| 字段 | 类型 | 含义 |
|------|------|------|
| `filename` | `str` | 文件路径 |
| `previous_filename` | `str \| None` | 重命名前的路径 (仅 `renamed` 状态) |
| `status` | `str` | 文件状态: `added`, `removed`, `modified`, `renamed` |
| `additions` | `int` | 该文件新增行数 |
| `deletions` | `int` | 该文件删除行数 |
| `changes` | `int` | 该文件总变更行数 |
| `blob_url` | `str` | GitHub blob 链接 |
| `raw_url` | `str` | 原始文件链接 |
| `contents_url` | `str` | GitHub Contents API 链接 |
| `patch` | `str \| None` | diff patch 文本 (大文件可能为 None) |

---

### 1.2 Diff 解析

#### HunkLine — diff 中的单行

| 字段 | 类型 | 含义 |
|------|------|------|
| `type` | `str` | 行类型: `"context"` (未变), `"added"` (新增), `"removed"` (删除) |
| `content` | `str` | 行内容文本 |
| `old_line` | `int \| None` | 原文件行号 (added 行为 None) |
| `new_line` | `int \| None` | 新文件行号 (removed 行为 None) |

#### Hunk — diff 中的一个代码块

| 字段 | 类型 | 含义 |
|------|------|------|
| `header` | `str` | hunk 头 (如 `@@ -10,5 +10,8 @@`) |
| `old_start` | `int` | 原文件起始行号 |
| `old_lines` | `int` | 原文件覆盖行数 |
| `new_start` | `int` | 新文件起始行号 |
| `new_lines` | `int` | 新文件覆盖行数 |
| `lines` | `list[HunkLine]` | 该 hunk 包含的所有行 |

---

### 1.3 边缘处理

#### ProcessedFile — 边缘处理后的文件

在 ChangedFile 基础上增加了二进制检测、patch 可用性判断和 hunk 解析结果。

| 字段 | 类型 | 含义 |
|------|------|------|
| `filename` | `str` | 文件路径 |
| `previous_filename` | `str \| None` | 重命名前的路径 |
| `status` | `str` | 文件状态 |
| `additions` | `int` | 新增行数 |
| `deletions` | `int` | 删除行数 |
| `changes` | `int` | 总变更行数 |
| `blob_url` | `str` | GitHub blob 链接 |
| `raw_url` | `str` | 原始文件链接 |
| `contents_url` | `str` | Contents API 链接 |
| `is_binary` | `bool` | 是否为二进制文件 |
| `patch_available` | `bool` | patch 数据是否可用 |
| `large_patch` | `bool` | patch 是否超过 50KB 阈值 |
| `parse_error` | `str \| None` | patch 解析错误信息 (成功为 None) |
| `hunks` | `list[Hunk]` | 解析后的 hunk 列表 |

---

### 1.4 分类与评分

#### Classification — 文件分类结果

由 `classify_file()` 生成，不直接存储在最终结构中，而是合并进 FileEntry。

| 字段 | 类型 | 含义 |
|------|------|------|
| `language` | `str` | 编程语言 (如 `python`, `javascript`) |
| `language_family` | `str` | 语言族 (如 `python`, `javascript`, `markup`) |
| `rule_profile` | `str` | 规则配置: `source`, `test`, `docs`, `config` |
| `is_test` | `bool` | 是否为测试文件 |
| `is_docs` | `bool` | 是否为文档文件 |
| `is_config` | `bool` | 是否为配置文件 |
| `is_source` | `bool` | 是否为源代码文件 |
| `is_generated` | `bool` | 是否为自动生成文件 |
| `is_high_risk_path` | `bool` | 是否在高风险路径下 |
| `risk_hints` | `list[str]` | 风险提示列表 (见下表) |

**risk_hints 可选值:**

| 值 | 含义 |
|------|------|
| `auth_path` | 文件路径包含 auth/login/oauth 等关键词 |
| `payment_path` | 文件路径包含 payment/billing/stripe 等关键词 |
| `config_path` | 文件路径包含 config/settings/env 等关键词 |
| `db_path` | 文件路径包含 db/database/migration/schema 等关键词 |
| `no_test_pair` | 源文件没有对应的测试文件 |

---

### 1.5 最终上下文

#### FileEntry — 单个文件的完整信息

Pipeline 第一阶段的最终输出，合并了 ChangedFile + ProcessedFile + Classification + 评分。

| 字段 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| `filename` | `str` | — | 文件路径 |
| `previous_filename` | `str \| None` | — | 重命名前的路径 |
| `status` | `str` | — | 文件状态: `added`, `removed`, `modified`, `renamed` |
| `additions` | `int` | — | 新增行数 |
| `deletions` | `int` | — | 删除行数 |
| `changes` | `int` | — | 总变更行数 |
| `language` | `str` | — | 编程语言 |
| `language_family` | `str` | — | 语言族 |
| `rule_profile` | `str` | — | 规则配置 |
| `is_test` | `bool` | — | 是否测试文件 |
| `is_docs` | `bool` | — | 是否文档文件 |
| `is_config` | `bool` | — | 是否配置文件 |
| `is_source` | `bool` | — | 是否源代码文件 |
| `is_generated` | `bool` | — | 是否自动生成 |
| `is_binary` | `bool` | — | 是否二进制 |
| `patch_available` | `bool` | — | patch 是否可用 |
| `large_patch` | `bool` | — | patch 是否过大 |
| `parse_error` | `str \| None` | — | 解析错误 |
| `is_high_risk_path` | `bool` | — | 是否高风险路径 |
| `risk_hints` | `list[str]` | `[]` | 风险提示列表 |
| `priority_score_hint` | `int` | `0` | 优先级评分 (0-100) |
| `hunk_count` | `int` | `0` | hunk 数量 |
| `added_line_count` | `int` | `0` | 新增行数 (解析后) |
| `removed_line_count` | `int` | `0` | 删除行数 (解析后) |
| `keywords` | `list[str]` | `[]` | 从新增行提取的关键词 |
| `hunks` | `list[Hunk]` | `[]` | 解析后的 hunk 列表 |
| `blob_url` | `str` | `""` | GitHub blob 链接 |
| `raw_url` | `str` | `""` | 原始文件链接 |
| `contents_url` | `str` | `""` | Contents API 链接 |

**priority_score_hint 评分公式:**
```
score = path_risk × 0.45 + change_size × 0.30 + file_status × 0.15 + lang_risk × 0.10
```
结果为 0-100 的整数。

#### DerivedSignals — PR 级别的派生信号

从所有 FileEntry 聚合计算得出。

| 字段 | 类型 | 含义 |
|------|------|------|
| `total_hunks` | `int` | 所有文件的 hunk 总数 |
| `source_files_changed` | `int` | 源代码文件数量 |
| `test_files_changed` | `int` | 测试文件数量 |
| `docs_only` | `bool` | 是否仅修改了文档 |
| `has_source_without_tests` | `bool` | 是否存在源文件变更但无对应测试变更 |
| `high_risk_files` | `list[str]` | 高风险文件路径列表 |

#### PRContext — 完整的 PR 上下文

Pipeline 第一阶段的顶层输出，存储在内存中，通过 `context_id` 引用。

| 字段 | 类型 | 含义 |
|------|------|------|
| `context_id` | `str` | 唯一标识，格式 `ctx_{uuid_hex[:12]}` |
| `source` | `str` | 数据来源: `"github"` |
| `fetched_at` | `str` | 获取时间 (ISO 8601) |
| `cache_key` | `str` | 缓存键: `{owner}/{repo}/{pull_number}/{head_sha}` |
| `owner` | `str` | 仓库所有者 |
| `repo` | `str` | 仓库名称 |
| `pull_number` | `int` | PR 编号 |
| `pr` | `PRMetadata` | PR 元信息 |
| `commits` | `CommitsData` | 提交数据 |
| `files` | `list[FileEntry]` | 所有变更文件 |
| `derived` | `DerivedSignals \| None` | 派生信号 |

---

## Stage 2: Review Pipeline — 分析与评估

通过 `context_id` 引用已存储的 PRContext，生成三个分析视图。

### 2.1 Intake Summary — PR 概览

`POST /api/review/intake` 的响应结构 (纯 dict，无 dataclass)。

```json
{
  "context_id": "ctx_abc123",
  "size": "small | medium | large",
  "change_type": "docs | test | config | source | mixed",
  "docs_only": false,
  "source_without_tests": true,
  "has_high_risk_paths": false,
  "language_distribution": {
    "python": 5,
    "typescript": 3
  },
  "file_type_distribution": {
    "source": 4,
    "test": 2,
    "config": 1
  },
  "top_directories": [
    {"directory": "backend/domain/review", "file_count": 3},
    {"directory": "frontend/src", "file_count": 2}
  ],
  "notable_signals": ["source_without_tests", "large_pr"]
}
```

**字段说明:**

| 字段 | 类型 | 含义 |
|------|------|------|
| `context_id` | `str` | 关联的 PRContext ID |
| `size` | `str` | PR 大小分类: `small` (≤3文件, ≤100行), `medium` (≤10文件, ≤500行), `large` |
| `change_type` | `str` | 变更类型: `docs`, `test`, `config`, `source`, `mixed` |
| `docs_only` | `bool` | 是否仅文档变更 |
| `source_without_tests` | `bool` | 是否有源文件无对应测试 |
| `has_high_risk_paths` | `bool` | 是否包含高风险路径文件 |
| `language_distribution` | `dict[str,int]` | 各语言文件数量 |
| `file_type_distribution` | `dict[str,int]` | 各类型文件数量 |
| `top_directories` | `list[dict]` | 变更最多的目录 (最多10个) |
| `notable_signals` | `list[str]` | 显著信号列表 |

**notable_signals 可选值:** `docs_only`, `source_without_tests`, `high_risk_paths_changed`, `large_pr`

---

### 2.2 File Priority View — 文件优先级视图

`POST /api/review/file-priority` 的响应结构。

```json
{
  "context_id": "ctx_abc123",
  "groups": {
    "must_review": [ ... ],
    "should_review": [ ... ],
    "skim": [ ... ]
  }
}
```

**分组阈值:**

| 组别 | 分数范围 | 含义 |
|------|----------|------|
| `must_review` | ≥ 70 | 必须审查 |
| `should_review` | 35-69 | 建议审查 |
| `skim` | < 35 | 浏览即可 |

**每个文件条目:**

| 字段 | 类型 | 含义 |
|------|------|------|
| `filename` | `str` | 文件路径 |
| `status` | `str` | 文件状态 |
| `additions` | `int` | 新增行数 |
| `deletions` | `int` | 删除行数 |
| `language` | `str` | 编程语言 |
| `language_family` | `str` | 语言族 |
| `is_test` | `bool` | 是否测试 |
| `is_docs` | `bool` | 是否文档 |
| `is_config` | `bool` | 是否配置 |
| `is_source` | `bool` | 是否源代码 |
| `is_binary` | `bool` | 是否二进制 |
| `is_generated` | `bool` | 是否自动生成 |
| `patch_available` | `bool` | patch 是否可用 |
| `large_patch` | `bool` | patch 是否过大 |
| `hunk_count` | `int` | hunk 数量 |
| `added_line_count` | `int` | 新增行数 |
| `removed_line_count` | `int` | 删除行数 |
| `priority_score_hint` | `int` | 优先级评分 (0-100) |
| `reasons` | `list[str]` | 该文件被标记的原因列表 |

**reasons 可选值:** `auth_path`, `payment_path`, `config_path`, `db_path`, `no_test_pair`, `high_risk_path`, `source_change`, `test_change`, `docs_change`, `config_change`, `generated_file`, `binary_file`, `patch_unavailable`, `large_patch`, `parse_error`, `new_file`, `renamed_file`, `removed_file`, `large_change`

---

### 2.3 Evidence — 静态规则分析

`POST /api/review/evidence` 的响应结构。

#### Severity — 严重性枚举

| 名称 | 值 | 含义 |
|------|-----|------|
| `CRITICAL` | `0` | 严重问题，必须处理 |
| `WARNING` | `1` | 警告，建议处理 |
| `INFO` | `2` | 信息性，供参考 |

#### EvidenceItem — 单条证据

| 字段 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| `id` | `str` | — | 证据唯一 ID (SHA256 前16位) |
| `source` | `str` | — | 来源: `"rule_analyzer_v1"` |
| `rule_id` | `str` | — | 规则 ID (见下表) |
| `file` | `str` | — | 关联文件路径 (PR 级证据为空字符串) |
| `severity` | `str` | — | 严重性: `"critical"`, `"warning"`, `"info"` |
| `category` | `str` | — | 分类: `"security"`, `"reliability"`, `"maintainability"`, `"test"`, `"config"` |
| `message` | `str` | — | 人类可读的问题描述 |
| `confidence` | `float` | — | 置信度 (0.0-1.0) |
| `tags` | `list[str]` | `[]` | 标签列表 |
| `line` | `int \| None` | `None` | 问题所在行号 (PR 级证据为 None) |
| `hunk_index` | `int \| None` | `None` | 问题所在 hunk 索引 |
| `excerpt` | `str \| None` | `None` | 问题代码片段 (已脱敏) |

**rule_id 可选值:**

| rule_id | category | severity | 含义 |
|---------|----------|----------|------|
| `sensitive_field` | security | critical | 检测到硬编码密钥或凭证 |
| `bare_except` | reliability | warning | Python 裸 except 子句 |
| `dangerous_exec` | security | warning | 动态代码执行 (eval/exec/os.system/subprocess) |
| `sql_injection` | security | warning | SQL 拼接注入风险 |
| `high_risk_path` | security/reliability/config/maintainability | warning | 高风险路径文件 |
| `patch_unavailable` | maintainability | info | patch 不可用 |
| `large_patch` | maintainability | info | patch 过大 |
| `source_without_tests` | test | warning | 源文件无对应测试 |

**API 响应结构:**

```json
{
  "context_id": "ctx_abc123",
  "items": [ {EvidenceItem}, ... ],
  "summary": {
    "by_severity": {"critical": 1, "warning": 3, "info": 2},
    "by_category": {"security": 3, "reliability": 1, "test": 1, "maintainability": 1}
  }
}
```

---

## Stage 3: Context Task Planner — 任务规划

`POST /api/review/context-tasks` 的响应结构。

### 3.1 任务数据结构

#### TaskSource — 任务来源追溯

记录任务是由哪些证据/信号触发的。

| 字段 | 类型 | 含义 |
|------|------|------|
| `evidence_ids` | `list[str]` | 触发该任务的 EvidenceItem ID 列表 |
| `rule_ids` | `list[str]` | 关联的规则 ID |
| `signals` | `list[str]` | 关联的信号 (如 `source_without_tests`, `db_path`) |
| `file_facts` | `list[str]` | 关联的文件事实 (文件路径) |

#### TaskTarget — 任务执行目标

SubAgent 执行任务时需要检查的文件和搜索目标。

| 字段 | 类型 | 含义 |
|------|------|------|
| `files` | `list[str]` | 目标文件路径列表 |
| `directories` | `list[str]` | 目标目录列表 |
| `symbols` | `list[str]` | 目标符号/类名 (从文件名推导) |
| `keywords` | `list[str]` | 搜索关键词 |

#### TaskBudget — 执行预算

限制 SubAgent 的资源消耗。

| 字段 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| `max_searches` | `int` | 5 | 最大搜索次数 |
| `max_files` | `int` | 10 | 最大读取文件数 |
| `max_tokens` | `int` | 3000 | 最大 token 消耗 |

**各任务类型的默认预算:**

| task_type | max_searches | max_files | max_tokens |
|-----------|-------------|-----------|------------|
| test_context | 5 | 10 | 3000 |
| reference_context | 5 | 10 | 3000 |
| security_context | 4 | 8 | 2500 |
| config_context | 3 | 6 | 2000 |
| data_context | 4 | 8 | 2500 |
| runtime_context | 4 | 8 | 2500 |
| patch_deep_dive | 3 | 5 | 2000 |

#### ContextTask — 单个上下文任务

| 字段 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| `task_id` | `str` | — | 任务唯一 ID (SHA256 前12位) |
| `task_type` | `str` | — | 任务类型 (见下表) |
| `intent` | `str` | — | 具体意图 (见下表) |
| `route_key` | `str` | — | 路由键: `route:{task_type}` |
| `source` | `TaskSource` | — | 任务来源追溯 |
| `target` | `TaskTarget` | — | 执行目标 |
| `queries` | `list[str]` | — | SubAgent 需要执行的搜索查询 |
| `priority` | `str` | — | 优先级: `critical`, `high`, `medium`, `low` |
| `budget` | `TaskBudget` | — | 执行预算 |
| `expected_output` | `str` | — | 期望输出描述 |
| `fallback` | `str` | — | 找不到结果时的回退行为 |
| `status` | `str` | `"pending"` | 任务状态 (始终为 `pending`) |

---

### 3.2 七种任务类型

| task_type | intent | 触发条件 | 目标 |
|-----------|--------|----------|------|
| `test_context` | `find_related_tests` | 源文件无对应测试 / `no_test_pair` 信号 | 查找相关测试文件和覆盖缺口 |
| `reference_context` | `find_references` | 任何源代码文件变更 | 查找引用、调用者、API 使用 |
| `security_context` | `verify_security_evidence` | 安全类证据 / `auth_path` / `payment_path` 风险提示 | 安全发现和风险评估 |
| `config_context` | `inspect_config_impact` | 配置文件变更 / `config_path` 风险提示 | 配置影响分析 |
| `config_context` | `inspect_dependency_impact` | 依赖文件变更 (package.json 等) | 依赖变更影响分析 |
| `data_context` | `inspect_data_impact` | `db_path` 风险提示 / 数据库相关文件 / SQL 注入证据 | 数据访问和迁移影响 |
| `runtime_context` | `inspect_runtime_risk` | `bare_except` 证据 / 运行时相关关键词 | 异常处理、并发、超时分析 |
| `patch_deep_dive` | `inspect_patch_complexity` | 高优先级文件 (≥70) 未被其他任务覆盖 | 补丁复杂度和改进建议 |

---

### 3.3 路由与代理元数据

#### TaskRoute — 任务路由配置

描述每种任务类型如何路由到 SubAgent。

| 字段 | 类型 | 含义 |
|------|------|------|
| `task_type` | `str` | 任务类型 |
| `route_key` | `str` | 路由键 |
| `agent_type` | `str` | 代理类型 (如 `test-context-agent`) |
| `allowed_tools` | `list[str]` | 允许的工具列表 |
| `output_schema` | `dict` | 输出 JSON Schema |
| `max_steps` | `int` | 最大执行步数 (默认 5) |

**allowed_tools (所有路由统一):**
- `search_repo` — 搜索仓库
- `read_repo_file` — 读取仓库文件
- `read_file_patch` — 读取文件 patch
- `search_diff` — 搜索 diff
- `read_check_summary` — 读取 CI 检查摘要
- `read_review_comments_summary` — 读取审查评论摘要

#### AgentDefinition — 代理定义

| 字段 | 类型 | 含义 |
|------|------|------|
| `agent_type` | `str` | 代理类型标识 |
| `description` | `str` | 代理功能描述 |
| `allowed_tools` | `list[str]` | 允许的工具 |
| `disallowed_tools` | `list[str]` | 禁止的工具 (含 `task_tool`, `sub_agent`) |

**七种代理类型:**

| agent_type | description |
|------------|-------------|
| `test-context-agent` | 查找相关测试、测试缺口和覆盖信号 |
| `reference-context-agent` | 查找引用、调用者、API 使用和符号影响 |
| `security-context-agent` | 检查认证、授权、密钥、SQL 风险和输入验证 |
| `config-context-agent` | 检查配置、环境变量、依赖文件、CI/检查 |
| `data-context-agent` | 检查数据库、schema、迁移、缓存和数据访问 |
| `runtime-context-agent` | 检查异常处理、异步行为、并发、超时和资源生命周期 |
| `patch-deep-dive-agent` | 深入检查高优先级或复杂补丁 |

---

### 3.4 API 响应结构

```json
{
  "context_id": "ctx_abc123",
  "tasks": [
    {
      "task_id": "task_a1b2c3d4e5f6",
      "task_type": "security_context",
      "intent": "verify_security_evidence",
      "route_key": "route:security_context",
      "source": {
        "evidence_ids": ["ev_abc123"],
        "rule_ids": ["sensitive_field"],
        "signals": ["security"],
        "file_facts": ["src/auth/login.py"]
      },
      "target": {
        "files": ["src/auth/login.py"],
        "directories": [],
        "symbols": ["Login"],
        "keywords": ["login", "auth"]
      },
      "queries": ["inspect security context for src/auth/login.py"],
      "priority": "critical",
      "budget": {
        "max_searches": 4,
        "max_files": 8,
        "max_tokens": 2500
      },
      "expected_output": "Security findings, risk assessment, and related patterns",
      "fallback": "Mark as no-security-context-found",
      "status": "pending"
    }
  ],
  "routes": [ {TaskRoute}, ... ],
  "agents": [ {AgentDefinition}, ... ],
  "summary": {
    "by_type": {
      "test_context": 2,
      "reference_context": 3,
      "security_context": 1,
      "config_context": 0,
      "data_context": 0,
      "runtime_context": 1,
      "patch_deep_dive": 1
    },
    "by_priority": {
      "critical": 1,
      "high": 3,
      "medium": 4
    }
  }
}
```

---

## API 端点汇总

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| POST | `/api/pr/context` | `{pr_url, github_token?}` | Overview View (PRContext 子集) |
| GET | `/api/pr/context/{id}/patch-index` | — | 文件排序列表 |
| GET | `/api/pr/context/{id}/files/{filename}/patch` | `?hunk_index=&max_lines=` | 单文件 patch |
| POST | `/api/review/intake` | `{context_id}` | Intake Summary |
| POST | `/api/review/file-priority` | `{context_id}` | File Priority View |
| POST | `/api/review/evidence` | `{context_id}` | Evidence Response |
| POST | `/api/review/context-tasks` | `{context_id}` | TaskPlan |
| GET | `/api/health` | — | `{status: "ok"}` |

---

## 数据流转关系图

```
                    ┌──────────────┐
                    │  GitHub API  │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │   fetcher    │ PRMetadata, CommitsData, ChangedFile[]
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │ edge_handler │ ProcessedFile[] (加 is_binary, hunks)
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  classifier  │ Classification (加 language, is_test, risk_hints)
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │    scorer    │ priority_score_hint (0-100)
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │   context    │ FileEntry[], DerivedSignals
                    │   _manager   │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │ PRContext     │ ──→ _contexts[context_id] (内存存储)
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
       ┌──────▼─────┐ ┌───▼────┐ ┌────▼─────┐
       │   intake   │ │ file_  │ │ evidence │
       │            │ │priority│ │          │
       └──────┬─────┘ └───┬────┘ └────┬─────┘
              │            │          │
              ▼            ▼          ▼
         summary      groups[]   EvidenceItem[]
                                   │
                                   ▼
                          ┌────────────────┐
                          │ context_task   │
                          │   _planner     │
                          └────────┬───────┘
                                   │
                          ┌────────▼───────┐
                          │   TaskPlan     │
                          │  ├─ tasks[]    │ ContextTask
                          │  ├─ routes[]   │ TaskRoute
                          │  ├─ agents[]   │ AgentDefinition
                          │  └─ summary{}  │
                          └────────────────┘
```
