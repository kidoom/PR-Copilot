# PR Copilot

AI 驱动的 Pull Request 代码审查助手。

PR Copilot 接收一个 GitHub PR 链接，自动获取 PR 元数据和代码变更，构建分析上下文（文件分类、优先级评分、Hunk 解析），通过 AI Agent 流水线进行多维度代码审查，并将审查结果实时推送到 Web 前端。

## 功能特性

- 🔍 **智能代码分析** — 自动解析 PR diff，识别关键变更文件并进行优先级排序
- 🤖 **AI Agent 流水线** — 多个专业子 Agent 并行审查（安全、测试、配置等维度）
- 📡 **实时流式推送** — 通过 WebSocket 实时展示 Agent 思考过程和工具调用
- 📊 **结构化审查报告** — 生成包含证据引用的结构化 Finding，支持置信度评估
- 🔐 **GitHub App 集成** — 支持 GitHub App OAuth 认证，安全访问仓库

## 技术栈

| 层级 | 技术 |
|------|------|
| **后端** | Python 3.10+ / FastAPI / httpx |
| **前端** | React 19 / TypeScript / Vite / Tailwind CSS |
| **通信** | REST API + WebSocket |
| **AI** | OpenAI 兼容 API（支持自定义端点） |

## 项目结构

```
PR-Copilot/
├── backend/                    # FastAPI 后端应用
│   ├── api/routes/             # HTTP 和 WebSocket 路由处理器
│   │   ├── pr_context.py       # PR 上下文 CRUD
│   │   ├── review.py           # 静态审查流水线
│   │   ├── review_runs.py      # 异步 AI 审查任务管理
│   │   └── review_ws.py        # WebSocket 事件流
│   ├── domain/                 # 业务逻辑层
│   │   ├── github/             # GitHub API 客户端与认证
│   │   └── review/             # PR 上下文构建、文件分析、证据规则
│   ├── agent/                  # AI Agent 运行时
│   │   ├── runtime/            # Agent 主循环、结果组装、上下文压缩
│   │   ├── model/              # LLM 客户端封装
│   │   ├── tools/              # 仓库上下文工具（文件读取、搜索）
│   │   └── subagents.py        # 专业子 Agent 编排
│   ├── storage/                # 会话持久化与存储
│   ├── main.py                 # FastAPI 应用入口
│   └── deps.py                 # 依赖注入与初始化
├── frontend/                   # React 前端应用
│   └── src/
│       ├── api.ts              # API 客户端
│       ├── types.ts            # TypeScript 类型定义
│       └── components/
│           ├── ReviewPanel.tsx  # 主审查工作台
│           ├── FindingCard.tsx  # 审查发现卡片
│           └── TerminalStream.tsx # 实时 Agent 输出流
├── docs/                       # 项目文档
├── deploy/                     # 部署配置
└── package.json                # 根项目脚本（同时启动前后端）
```

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+
- OpenAI 兼容 API 密钥

### 1. 克隆项目

```bash
git clone https://github.com/your-org/pr-copilot.git
cd pr-copilot
```

### 2. 配置环境变量

创建 `.env.local` 文件：

```env
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o

# 可选：GitHub App 配置（用于仓库访问和 Checks）
GITHUB_APP_CLIENT_ID=your-client-id
GITHUB_APP_CLIENT_SECRET=your-client-secret
PR_COPILOT_GITHUB_TOKEN=your-github-token

# 可选：存储目录（默认 ~/.pr-copilot）
PR_COPILOT_STORAGE_DIR=./data
```

### 3. 安装依赖

```bash
# 后端依赖
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt

# 前端依赖
cd frontend && npm install && cd ..
```

### 4. 启动开发服务器

```bash
# 同时启动后端 + 前端
npm run dev

# 或分别启动
npm run dev:backend  # 后端 localhost:8001
npm run dev:frontend # 前端 localhost:5173
```

访问 `http://localhost:5173` 即可使用。

## API 使用示例

### 创建 PR 上下文

```bash
curl -X POST http://localhost:8001/api/pr/context \
  -H "Content-Type: application/json" \
  -d '{"pr_url": "https://github.com/OWNER/REPO/pull/NUMBER"}'
```

### 启动 AI 审查

```bash
curl -X POST http://localhost:8001/api/review/runs \
  -H "Content-Type: application/json" \
  -d '{"context_id": "CONTEXT_ID"}'
```

### WebSocket 连接

```javascript
const ws = new WebSocket('ws://localhost:8001/ws/review-runs/RUN_ID');
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  // data.type: 'message_delta' | 'tool_call' | 'finding' | 'terminal'
};
```

更多 API 细节请参考 [API.md](API.md)。

## 工作流程

```
用户输入 PR URL
       ↓
┌─────────────────────────────────┐
│   1. 获取 PR 元数据与文件列表    │
│   2. 构建分析上下文              │
│      - 文件分类                  │
│      - 优先级评分                │
│      - Hunk 解析                │
└─────────────────────────────────┘
       ↓
┌─────────────────────────────────┐
│   3. 静态分析（并行）            │
│      - Intake Analysis          │
│      - Evidence Rules           │
│      - Context Task Planning    │
└─────────────────────────────────┘
       ↓
┌─────────────────────────────────┐
│   4. AI Agent 流水线             │
│      - 安全审查子 Agent          │
│      - 测试上下文子 Agent        │
│      - 配置审查子 Agent          │
│      - ...更多专业 Agent         │
└─────────────────────────────────┘
       ↓
┌─────────────────────────────────┐
│   5. 结果组装与验证              │
│      - 证据关联                  │
│      - 置信度评估                │
│      - 结构化输出                │
└─────────────────────────────────┘
       ↓
   实时推送到前端
```

## 测试

```bash
# 运行所有测试
python -m pytest backend/tests/ -v

# 运行特定测试文件
python -m pytest backend/tests/test_api/routes/test_review.py -v

# 运行前端 lint
cd frontend && npm run lint
```

## 环境变量参考

| 变量 | 必填 | 说明 |
|------|------|------|
| `OPENAI_API_KEY` | ✅ | LLM API 密钥 |
| `OPENAI_BASE_URL` | ❌ | OpenAI 兼容 API 端点（默认: `https://api.openai.com/v1`） |
| `OPENAI_MODEL` | ❌ | 模型名称（默认: `gpt-4o`） |
| `GITHUB_APP_CLIENT_ID` | ❌ | GitHub App Client ID |
| `GITHUB_APP_CLIENT_SECRET` | ❌ | GitHub App Client Secret |
| `PR_COPILOT_GITHUB_TOKEN` | ❌ | 服务端 GitHub Token |
| `PR_COPILOT_STORAGE_DIR` | ❌ | Agent 内存和临时工作区根目录（默认: `~/.pr-copilot`） |
| `PR_COPILOT_CORS_ORIGINS` | ❌ | 额外的 CORS 允许源（逗号分隔） |

## 开发指南

- **main 分支**：禁止直接推送，所有变更通过 PR 提交团队审查
- **Liziark 分支**：允许直接推送，新功能先在此开发，再 PR 到 main
- PR 描述需包含：变更内容、实现功能、修复问题、测试覆盖、验证方式

## 许可证

[Apache License 2.0](LICENSE)
