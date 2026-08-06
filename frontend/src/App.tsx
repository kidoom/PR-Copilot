import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { ReactNode } from "react"
import {
  AlertTriangle,
  ArrowLeft,
  BarChart3,
  Binary,
  BookOpen,
  Bot,
  CheckCircle2,
  Code,
  ExternalLink,
  FileCode,
  Files,
  FolderOpen,
  GitBranch,
  GitPullRequest,
  History,
  LayoutDashboard,
  Minus,
  Plus,
  Search,
  Settings,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  TestTube,
  X,
} from "lucide-react"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Spinner } from "@/components/ui/spinner"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  analyzePr,
  getFilePatch,
  getIntakeSummary,
  getPrContext,
  getReviewRun,
  listAllSessions,
} from "@/api"
import type { PrSessionSummary } from "@/api"
import { ReviewPanel } from "@/components/ReviewPanel"
import type {
  FileEntry,
  FilePatchResponse,
  FinalReviewResult,
  IntakeSummary,
  PrContextResponse,
} from "@/types"

type View = "home" | "pr"

function getStatusBadgeVariant(status: string) {
  if (status === "added") return "default" as const
  if (status === "removed") return "destructive" as const
  return "secondary" as const
}

function getFileTypeIcon(file: FileEntry) {
  if (file.is_test) return <TestTube className="size-3.5" />
  if (file.is_docs) return <BookOpen className="size-3.5" />
  if (file.is_config) return <Settings className="size-3.5" />
  if (file.is_binary) return <Binary className="size-3.5" />
  if (file.is_source) return <Code className="size-3.5" />
  return <FileCode className="size-3.5" />
}

function getRiskHintLabel(hint: string) {
  const labels: Record<string, string> = {
    auth_path: "认证",
    payment_path: "支付",
    db_path: "数据库",
    config_path: "配置",
    no_test_pair: "缺少测试",
    high_risk_path: "高风险",
  }
  return labels[hint] ?? hint.replaceAll("_", " ")
}

function getErrorGuidance(error: string): string {
  const lower = error.toLowerCase()
  if (lower.includes("404") || lower.includes("not found")) {
    return "请确认 PR 链接正确，且仓库对当前凭据可见。"
  }
  if (lower.includes("401") || lower.includes("403") || lower.includes("unauthorized")) {
    return "该仓库可能需要 GitHub 凭据，请确认 GITHUB_TOKEN 或 GitHub App 配置可用。"
  }
  if (lower.includes("rate")) {
    return "GitHub API 频率受限，请稍后重试或配置更高额度的凭据。"
  }
  if (lower.includes("network") || lower.includes("fetch")) {
    return "无法连接后端服务，请确认 server 已启动并且代理配置正确。"
  }
  return "请检查 PR 链接、后端日志和凭据配置。"
}

function formatNumber(value: number) {
  return value.toLocaleString("zh-CN")
}

function SidebarNav({
  result,
  sessions,
  view,
  showReviewPanel,
  onHome,
  onToggleReview,
}: {
  result: PrContextResponse | null
  sessions: PrSessionSummary[]
  view: View
  showReviewPanel: boolean
  onHome: () => void
  onToggleReview: () => void
}) {
  const highRiskCount = result?.derived.high_risk_files.length ?? 0
  const navItemClassName = (active: boolean) =>
    `flex items-center gap-2 rounded-lg px-3 py-2 text-left ${
      active
        ? "bg-muted font-medium"
        : "text-muted-foreground hover:bg-muted hover:text-foreground"
    }`

  return (
    <aside className="hidden w-60 shrink-0 border-r bg-card lg:flex lg:flex-col">
      <div className="flex h-14 items-center gap-2 border-b px-4">
        <div className="flex size-8 items-center justify-center rounded-lg border bg-background">
          <GitPullRequest className="size-4" />
        </div>
        <div className="min-w-0">
          <div className="text-sm font-semibold">PR Copilot</div>
          <div className="text-xs text-muted-foreground">AI Review Console</div>
        </div>
      </div>

      <nav className="flex flex-1 flex-col gap-1 px-3 py-4 text-sm">
        <button
          className={navItemClassName(view === "home")}
          onClick={onHome}
        >
          <LayoutDashboard className="size-4" />
          工作台
        </button>
        <button
          className={navItemClassName(view === "pr" && showReviewPanel)}
          onClick={onToggleReview}
        >
          <Bot className="size-4" />
          AI 审查
        </button>
        <button
          className={navItemClassName(false)}
          onClick={onHome}
        >
          <History className="size-4" />
          历史记录
        </button>
      </nav>

      <div className="border-t p-3">
        <div className="rounded-lg border bg-background p-3">
          <div className="text-xs font-medium text-muted-foreground">当前上下文</div>
          <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
            <div>
              <div className="text-lg font-semibold">{result?.pr.changed_files ?? 0}</div>
              <div className="text-muted-foreground">文件</div>
            </div>
            <div>
              <div className="text-lg font-semibold">{highRiskCount}</div>
              <div className="text-muted-foreground">风险</div>
            </div>
          </div>
          <div className="mt-3 text-xs text-muted-foreground">
            已缓存 {sessions.length} 个 PR 会话
          </div>
        </div>
      </div>
    </aside>
  )
}

function EmptyState({
  sessions,
  onSessionClick,
}: {
  sessions: PrSessionSummary[]
  onSessionClick: (session: PrSessionSummary) => void
}) {
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <Card className="min-h-[420px] justify-center">
        <CardContent className="flex flex-col items-center justify-center gap-4 py-14 text-center">
          <div className="flex size-12 items-center justify-center rounded-lg border bg-muted">
            <GitPullRequest className="size-6 text-muted-foreground" />
          </div>
          <div>
            <h2 className="text-lg font-semibold">分析一个 GitHub Pull Request</h2>
            <p className="mt-2 max-w-md text-sm text-muted-foreground">
              粘贴 PR 链接后，PR Copilot 会先提取上下文，再把审查过程流式展示出来。
            </p>
          </div>
          <div className="rounded-lg border bg-muted/40 px-3 py-2 font-mono text-xs text-muted-foreground">
            https://github.com/owner/repo/pull/123
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            <History className="size-4" />
            最近审查
          </CardTitle>
          <CardDescription>点击记录可以快速回到历史 PR。</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {sessions.length === 0 ? (
            <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
              暂无历史记录。
            </div>
          ) : (
            sessions.slice(0, 8).map((session) => (
              <button
                key={session.pr_session_id}
                className="rounded-lg border bg-background px-3 py-2 text-left transition-colors hover:bg-muted"
                onClick={() => onSessionClick(session)}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-medium">
                    {session.owner}/{session.repo}#{session.pull_number}
                  </span>
                  {session.latest_lifecycle && (
                    <Badge variant={session.latest_lifecycle === "failed" ? "destructive" : "secondary"}>
                      {session.latest_lifecycle === "completed"
                        ? "完成"
                        : session.latest_lifecycle === "failed"
                          ? "失败"
                          : session.latest_lifecycle}
                    </Badge>
                  )}
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {new Date(session.updated_at).toLocaleString("zh-CN")} · {session.run_count} 次审查
                </div>
              </button>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function LoadingState() {
  return (
    <Card className="min-h-[420px] justify-center">
      <CardContent className="flex flex-col items-center justify-center gap-4 py-14 text-center">
        <div className="flex size-12 items-center justify-center rounded-lg border bg-muted">
          <Spinner className="size-6" />
        </div>
        <div>
          <h2 className="text-lg font-semibold">正在分析 PR</h2>
          <p className="mt-2 max-w-md text-sm text-muted-foreground">
            正在获取 PR 元数据、变更文件、风险信号和审查输入。
          </p>
        </div>
        <div className="grid w-full max-w-xl gap-2 text-left text-xs text-muted-foreground sm:grid-cols-3">
          <div className="rounded-lg border bg-muted/30 p-3">获取 PR</div>
          <div className="rounded-lg border bg-muted/30 p-3">扫描文件</div>
          <div className="rounded-lg border bg-muted/30 p-3">准备审查</div>
        </div>
      </CardContent>
    </Card>
  )
}

function MetricCard({
  label,
  value,
  caption,
  icon,
}: {
  label: string
  value: string
  caption: string
  icon: ReactNode
}) {
  return (
    <Card size="sm">
      <CardContent>
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-xs text-muted-foreground">{label}</div>
            <div className="mt-1 text-2xl font-semibold tracking-tight">{value}</div>
            <div className="mt-1 text-xs text-muted-foreground">{caption}</div>
          </div>
          <div className="flex size-9 items-center justify-center rounded-lg border bg-muted text-muted-foreground">
            {icon}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function ResultDashboard({
  result,
  intake,
  onFileClick,
  onBack,
}: {
  result: PrContextResponse
  intake: IntakeSummary | null
  onFileClick: (file: FileEntry) => void
  onBack: () => void
}) {
  const hasHighRisk = result.derived.high_risk_files.length > 0
  const hasSourceWithoutTests = result.derived.has_source_without_tests
  const riskFiles = result.files.filter((file) => file.is_high_risk_path)
  const highPriorityFiles = result.files.filter((file) => file.priority_score_hint >= 60)
  const reviewFocus = riskFiles[0]?.filename ?? highPriorityFiles[0]?.filename ?? "暂无高优先级文件"

  return (
    <div className="flex flex-col gap-4">
      <Button
        className="self-start"
        variant="ghost"
        size="sm"
        onClick={onBack}
      >
        <ArrowLeft className="size-4" data-icon="inline-start" />
        返回工作台
      </Button>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="变更文件"
          value={formatNumber(result.pr.changed_files)}
          caption="本次 PR 修改"
          icon={<Files className="size-4" />}
        />
        <MetricCard
          label="新增"
          value={`+${formatNumber(result.pr.additions)}`}
          caption="行添加"
          icon={<Plus className="size-4" />}
        />
        <MetricCard
          label="删除"
          value={`-${formatNumber(result.pr.deletions)}`}
          caption="行删除"
          icon={<Minus className="size-4" />}
        />
        <MetricCard
          label="风险等级"
          value={hasHighRisk || hasSourceWithoutTests ? "偏高" : "低"}
          caption={hasHighRisk ? `${result.derived.high_risk_files.length} 个风险文件` : "无关键风险信号"}
          icon={hasHighRisk || hasSourceWithoutTests ? <ShieldAlert className="size-4" /> : <ShieldCheck className="size-4" />}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="min-w-0">
            <a
              className="inline-flex max-w-full items-center gap-2 hover:underline"
              href={result.pr.url}
              rel="noopener noreferrer"
              target="_blank"
            >
              <GitPullRequest className="size-4 shrink-0" />
              <span className="truncate">{result.pr.title}</span>
              <ExternalLink className="size-3.5 shrink-0 text-muted-foreground" />
            </a>
          </CardTitle>
          <CardDescription className="flex flex-wrap items-center gap-2">
            <span>{result.pr.author}</span>
            <span className="text-muted-foreground/50">·</span>
            <span className="inline-flex items-center gap-1">
              <GitBranch className="size-3.5" />
              {result.pr.base_branch} ← {result.pr.head_branch}
            </span>
            {result.pr.head_sha && (
              <>
                <span className="text-muted-foreground/50">·</span>
                <span className="font-mono">{result.pr.head_sha.slice(0, 8)}</span>
              </>
            )}
          </CardDescription>
        </CardHeader>
      </Card>

      {intake && (
        <div className="grid gap-3 md:grid-cols-3">
          <Card size="sm">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <Sparkles className="size-4" />
                PR 规模
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Badge variant={intake.size === "large" ? "destructive" : "secondary"}>
                {intake.size}
              </Badge>
              <div className="mt-2 text-xs text-muted-foreground">
                {intake.change_type === "mixed" ? "混合变更" : `${intake.change_type} 变更`}
              </div>
            </CardContent>
          </Card>

          <Card size="sm">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <BarChart3 className="size-4" />
                语言分布
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2">
              {Object.entries(intake.language_distribution)
                .sort(([, a], [, b]) => b - a)
                .slice(0, 4)
                .map(([language, count]) => (
                  <div key={language} className="flex items-center justify-between gap-2 text-xs">
                    <span className="truncate font-mono">{language}</span>
                    <Badge variant="secondary">{count}</Badge>
                  </div>
                ))}
            </CardContent>
          </Card>

          <Card size="sm">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <FolderOpen className="size-4" />
                热门目录
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2">
              {intake.top_directories.slice(0, 4).map((directory) => (
                <div key={directory.directory} className="flex items-center justify-between gap-2 text-xs">
                  <span className="truncate font-mono">{directory.directory}</span>
                  <Badge variant="secondary">{directory.file_count}</Badge>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      )}

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm">
              <Shield className="size-4" />
              审查建议
            </CardTitle>
            <CardDescription>根据变更模式和风险信号生成的优先级提示。</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-col gap-2 text-sm">
              {hasHighRisk && (
                <div className="rounded-lg border bg-muted/30 p-3">
                  优先审查高风险路径，尤其是认证、配置、权限和敏感数据相关变更。
                </div>
              )}
              {hasSourceWithoutTests && (
                <div className="rounded-lg border bg-muted/30 p-3">
                  当前存在源代码变更但没有明显测试配套，建议重点检查回归风险。
                </div>
              )}
              {highPriorityFiles.length > 0 && (
                <div className="rounded-lg border bg-muted/30 p-3">
                  优先关注 {highPriorityFiles.slice(0, 3).map((file) => file.filename).join("、")}
                  {highPriorityFiles.length > 3 ? " 等文件。" : "。"}
                </div>
              )}
              {!hasHighRisk && !hasSourceWithoutTests && highPriorityFiles.length === 0 && (
                <div className="rounded-lg border bg-muted/30 p-3">
                  未检测到紧急风险信号，可以按常规代码审查流程推进。
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm">
              <CheckCircle2 className="size-4" />
              AI 审查就绪
            </CardTitle>
            <CardDescription>已准备好交给 OMA 智能体执行。</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 text-sm">
            <div>
              <div className="text-xs text-muted-foreground">审查焦点</div>
              <div className="mt-1 truncate font-mono text-xs">{reviewFocus}</div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-lg border bg-muted/30 p-2">
                <div className="text-lg font-semibold">{riskFiles.length}</div>
                <div className="text-xs text-muted-foreground">风险文件</div>
              </div>
              <div className="rounded-lg border bg-muted/30 p-2">
                <div className="text-lg font-semibold">{highPriorityFiles.length}</div>
                <div className="text-xs text-muted-foreground">高优先级</div>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            <Files className="size-4" />
            变更文件 ({result.files.length})
          </CardTitle>
          <CardDescription>点击文件可打开右侧 diff 查看器。</CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>文件</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>语言</TableHead>
                <TableHead className="text-right">+/-</TableHead>
                <TableHead>风险</TableHead>
                <TableHead className="text-right">优先级</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {result.files.map((file) => (
                <TableRow
                  key={file.filename}
                  className="cursor-pointer"
                  onClick={() => onFileClick(file)}
                >
                  <TableCell className="max-w-[360px]">
                    <div className="flex items-center gap-2">
                      {getFileTypeIcon(file)}
                      <span className="truncate font-mono text-xs">{file.filename}</span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <Badge variant={getStatusBadgeVariant(file.status)}>{file.status}</Badge>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">{file.language}</TableCell>
                  <TableCell className="text-right text-xs">
                    <span>+{file.additions}</span>{" "}
                    <span className="text-muted-foreground">-{file.deletions}</span>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {file.risk_hints.length > 0 ? (
                        file.risk_hints.map((hint) => (
                          <Badge key={hint} variant="outline">
                            {getRiskHintLabel(hint)}
                          </Badge>
                        ))
                      ) : (
                        <Badge variant="outline">无</Badge>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <Badge
                      variant={
                        file.priority_score_hint >= 60
                          ? "destructive"
                          : file.priority_score_hint >= 30
                            ? "secondary"
                            : "outline"
                      }
                    >
                      {file.priority_score_hint}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}

function DiffSidebar({
  file,
  patch,
  loading,
  error,
  onClose,
}: {
  file: FileEntry
  patch: FilePatchResponse | null
  loading: boolean
  error: string | null
  onClose: () => void
}) {
  return (
    <>
      <div className="fixed inset-0 z-40 bg-background/70 backdrop-blur-sm" onClick={onClose} />
      <div className="fixed right-0 top-0 z-50 flex h-full w-full flex-col border-l bg-card shadow-lg md:w-[560px]">
        <div className="flex items-start justify-between gap-3 border-b px-4 py-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              {getFileTypeIcon(file)}
              <span className="truncate font-mono text-sm font-medium">{file.filename}</span>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <Badge variant={getStatusBadgeVariant(file.status)}>{file.status}</Badge>
              <span>{file.language}</span>
              <span>+{file.additions}</span>
              <span>-{file.deletions}</span>
            </div>
          </div>
          <Button variant="ghost" size="icon-sm" onClick={onClose}>
            <X data-icon="inline-start" />
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto">
          {loading && (
            <div className="flex items-center justify-center py-12">
              <Spinner className="size-6" />
            </div>
          )}
          {error && (
            <div className="p-4">
              <Alert variant="destructive">
                <AlertTriangle className="size-4" />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            </div>
          )}
          {patch && (
            <div className="text-xs">
              {!patch.patch_available && (
                <div className="p-4 text-muted-foreground">该文件没有可显示的 patch。</div>
              )}
              {patch.is_binary && (
                <div className="p-4 text-muted-foreground">二进制文件无法展示文本 diff。</div>
              )}
              {patch.parse_error && (
                <div className="p-4 text-destructive">解析错误：{patch.parse_error}</div>
              )}
              {patch.truncated && (
                <div className="border-b bg-muted px-4 py-2 text-muted-foreground">
                  diff 已截断，仅展示前 500 行。
                </div>
              )}
              {patch.hunks.map((hunk, hunkIndex) => (
                <div key={`${hunk.header}-${hunkIndex}`}>
                  <div className="border-b bg-muted/60 px-4 py-1.5 font-mono text-muted-foreground">
                    {hunk.header}
                  </div>
                  {hunk.lines.map((line, lineIndex) => (
                    <div
                      key={`${hunkIndex}-${lineIndex}`}
                      className={`flex font-mono ${
                        line.type === "added"
                          ? "bg-emerald-500/10"
                          : line.type === "removed"
                            ? "bg-destructive/10"
                            : ""
                      }`}
                    >
                      <span className="w-12 shrink-0 border-r px-2 text-right text-muted-foreground">
                        {line.old_line ?? ""}
                      </span>
                      <span className="w-12 shrink-0 border-r px-2 text-right text-muted-foreground">
                        {line.new_line ?? ""}
                      </span>
                      <span className="w-5 shrink-0 text-center">
                        {line.type === "added" ? "+" : line.type === "removed" ? "-" : " "}
                      </span>
                      <span className="min-w-0 flex-1 whitespace-pre px-1">{line.content}</span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  )
}

function App() {
  const [prUrl, setPrUrl] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<PrContextResponse | null>(null)
  const [intake, setIntake] = useState<IntakeSummary | null>(null)
  const [view, setView] = useState<View>("home")
  const [showReviewPanel, setShowReviewPanel] = useState(false)
  const [sessions, setSessions] = useState<PrSessionSummary[]>([])
  const [historicalResult, setHistoricalResult] = useState<FinalReviewResult | null>(null)
  const [selectedFile, setSelectedFile] = useState<FileEntry | null>(null)
  const [patchLoading, setPatchLoading] = useState(false)
  const [patchData, setPatchData] = useState<FilePatchResponse | null>(null)
  const [patchError, setPatchError] = useState<string | null>(null)
  const patchRequestId = useRef(0)

  useEffect(() => {
    listAllSessions()
      .then((data) => setSessions(data.sessions))
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (!selectedFile) return
    document.body.style.overflow = "hidden"
    return () => {
      document.body.style.overflow = ""
    }
  }, [selectedFile])

  const closeDiff = useCallback(() => {
    setSelectedFile(null)
    setPatchData(null)
    setPatchError(null)
  }, [])

  const goHome = useCallback(() => {
    setView("home")
    setResult(null)
    setIntake(null)
    setHistoricalResult(null)
    setShowReviewPanel(false)
    setError(null)
    closeDiff()
  }, [closeDiff])

  const handleSidebarAiReview = useCallback(() => {
    if (view === "pr" && result) {
      setShowReviewPanel((visible) => !visible)
    } else {
      goHome()
    }
  }, [view, result, goHome])

  const handleFileClick = useCallback(
    async (file: FileEntry) => {
      if (!result) return
      setSelectedFile(file)
      setPatchLoading(true)
      setPatchData(null)
      setPatchError(null)
      const requestId = ++patchRequestId.current
      try {
        const data = await getFilePatch(result.context_id, file.filename)
        if (patchRequestId.current === requestId) setPatchData(data)
      } catch (e) {
        if (patchRequestId.current === requestId) {
          setPatchError(e instanceof Error ? e.message : "加载 diff 失败")
        }
      } finally {
        if (patchRequestId.current === requestId) setPatchLoading(false)
      }
    },
    [result],
  )

  const handleSessionClick = useCallback(async (session: PrSessionSummary) => {
    const url = `https://github.com/${session.owner}/${session.repo}/pull/${session.pull_number}`
    setPrUrl(url)
    setLoading(true)
    setView("home")
    setError(null)
    setResult(null)
    setIntake(null)
    setHistoricalResult(null)
    closeDiff()

    try {
      const data = await getPrContext(session.pr_session_id)
      setResult(data)
      setView("pr")

      try {
        const intakeData = await getIntakeSummary(data.context_id)
        setIntake(intakeData)
      } catch {
        // Intake is helpful, not required for restoring a historical PR.
      }

      let restoredResult: FinalReviewResult | null = null
      if (session.latest_run_id && session.latest_lifecycle === "completed") {
        try {
          const status = await getReviewRun(session.latest_run_id)
          if (status.final_result) {
            restoredResult = status.final_result
          }
        } catch {
          // The PR context is still useful even if the latest run cannot be restored.
        }
      }
      setHistoricalResult(restoredResult)
      setShowReviewPanel(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载历史记录失败")
    } finally {
      setLoading(false)
    }
  }, [closeDiff])

  const handleAnalyze = useCallback(async () => {
    if (!prUrl.trim()) {
      setError("请输入 GitHub PR 链接。")
      return
    }
    setLoading(true)
    setView("home")
    setError(null)
    setResult(null)
    setIntake(null)
    setHistoricalResult(null)
    closeDiff()
    try {
      const data = await analyzePr(prUrl)
      setResult(data)
      setView("pr")
      setShowReviewPanel(true)
      try {
        const intakeData = await getIntakeSummary(data.context_id)
        setIntake(intakeData)
      } catch {
        // Intake is helpful, not required for the main workflow.
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "请求失败")
    } finally {
      setLoading(false)
    }
  }, [closeDiff, prUrl])

  const contentState = loading ? "loading" : result ? "result" : error ? "error" : "empty"
  const showConsole = view === "pr" && showReviewPanel && (result || historicalResult)

  const workspaceClassName = useMemo(
    () =>
      showConsole
        ? "grid gap-4 xl:grid-cols-[minmax(0,1fr)_460px]"
        : "grid gap-4",
    [showConsole],
  )

  return (
    <div className="flex min-h-screen bg-muted/30 text-foreground">
      <SidebarNav
        result={result}
        sessions={sessions}
        view={view}
        showReviewPanel={Boolean(showConsole)}
        onHome={goHome}
        onToggleReview={handleSidebarAiReview}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 border-b bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80">
          <div className="flex min-h-14 flex-col gap-3 px-4 py-3 xl:flex-row xl:items-center xl:justify-between">
            <div className="flex min-w-0 items-center gap-3 lg:hidden">
              <div className="flex size-8 items-center justify-center rounded-lg border bg-background">
                <GitPullRequest className="size-4" />
              </div>
              <div>
                <div className="text-sm font-semibold">PR Copilot</div>
                <div className="text-xs text-muted-foreground">AI Review Console</div>
              </div>
            </div>

            <div className="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row">
              <div className="relative min-w-0 flex-1">
                <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  className="pl-9"
                  disabled={loading}
                  onChange={(event) => {
                    setPrUrl(event.target.value)
                    if (error) setError(null)
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !loading) handleAnalyze()
                  }}
                  placeholder="https://github.com/owner/repo/pull/123"
                  value={prUrl}
                />
              </div>
              <Button disabled={loading} onClick={handleAnalyze}>
                {loading ? <Spinner data-icon="inline-start" /> : <Search data-icon="inline-start" />}
                {loading ? "分析中" : "分析 PR"}
              </Button>
              <Button
                disabled={!result && !historicalResult}
                onClick={() => setShowReviewPanel((visible) => !visible)}
                variant={showReviewPanel ? "secondary" : "outline"}
              >
                <Bot data-icon="inline-start" />
                {showReviewPanel ? "隐藏审查" : "AI 审查"}
              </Button>
            </div>
          </div>
        </header>

        <main className="min-w-0 flex-1 p-4">
          <div className={workspaceClassName}>
            <section className="min-w-0">
              {contentState === "loading" && <LoadingState />}
              {contentState === "error" && error && (
                <Alert variant="destructive">
                  <AlertTriangle className="size-4" />
                  <AlertDescription>
                    <div className="font-medium">{error}</div>
                    <div className="mt-1 text-xs opacity-80">{getErrorGuidance(error)}</div>
                  </AlertDescription>
                </Alert>
              )}
              {contentState === "empty" && (
                <EmptyState sessions={sessions} onSessionClick={handleSessionClick} />
              )}
              {contentState === "result" && result && (
                <ResultDashboard
                  result={result}
                  intake={intake}
                  onFileClick={handleFileClick}
                  onBack={goHome}
                />
              )}
            </section>

            {showConsole && (
              <aside className="min-h-[520px] min-w-0 xl:sticky xl:top-[5.25rem] xl:h-[calc(100vh-6.25rem)]">
                <Card className="h-full gap-0 py-0">
                  <CardHeader className="border-b py-3">
                    <CardTitle className="flex items-center gap-2 text-sm">
                      <Bot className="size-4" />
                      AI 审查控制台
                    </CardTitle>
                    <CardDescription>实时查看任务规划、智能体执行和结果汇总。</CardDescription>
                    <CardAction>
                      <Button variant="ghost" size="icon-sm" onClick={() => setShowReviewPanel(false)}>
                        <X data-icon="inline-start" />
                      </Button>
                    </CardAction>
                  </CardHeader>
                  <CardContent className="min-h-0 flex-1 px-0">
                    <ReviewPanel
                      contextId={result?.context_id}
                      initialResult={historicalResult}
                      onClose={() => {
                        setShowReviewPanel(false)
                        setHistoricalResult(null)
                      }}
                      onFileClick={(filename) => {
                        const file = result?.files.find((item) => item.filename === filename)
                        if (file) handleFileClick(file)
                      }}
                    />
                  </CardContent>
                </Card>
              </aside>
            )}
          </div>
        </main>
      </div>

      {selectedFile && (
        <DiffSidebar
          error={patchError}
          file={selectedFile}
          loading={patchLoading}
          onClose={closeDiff}
          patch={patchData}
        />
      )}
    </div>
  )
}

export default App
