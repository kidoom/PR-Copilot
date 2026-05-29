import { useState } from "react"
import {
  GitPullRequest,
  Search,
  Shield,
  FileCode,
  AlertTriangle,
  CheckCircle,
  ExternalLink,
  GitBranch,
  Plus,
  Minus,
  Files,
  ShieldCheck,
  ShieldAlert,
  Info,
  BookOpen,
  TestTube,
  Settings,
  Code,
  Binary,
} from "lucide-react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Spinner } from "@/components/ui/spinner"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { analyzePr } from "@/api"
import type { PrContextResponse, FileEntry } from "@/types"

function getStatusBadgeVariant(status: string) {
  if (status === "added") return "default" as const
  if (status === "removed") return "destructive" as const
  return "secondary" as const
}

function getFileTypeIcon(file: FileEntry) {
  if (file.is_test) return <TestTube className="h-3.5 w-3.5" />
  if (file.is_docs) return <BookOpen className="h-3.5 w-3.5" />
  if (file.is_config) return <Settings className="h-3.5 w-3.5" />
  if (file.is_binary) return <Binary className="h-3.5 w-3.5" />
  if (file.is_source) return <Code className="h-3.5 w-3.5" />
  return <FileCode className="h-3.5 w-3.5" />
}

function getErrorGuidance(error: string): string {
  const lower = error.toLowerCase()
  if (lower.includes("404") || lower.includes("not found"))
    return "Check that the PR URL is correct and the repository exists on GitHub."
  if (lower.includes("401") || lower.includes("403") || lower.includes("unauthorized"))
    return "The repository may be private. Try providing a GitHub token with repo access."
  if (lower.includes("rate"))
    return "GitHub API rate limit exceeded. Wait a moment or provide a GitHub token."
  if (lower.includes("network") || lower.includes("fetch"))
    return "Could not reach the backend. Make sure the server is running on port 8000."
  return "Check the PR URL and ensure the repository is accessible."
}

function EmptyState() {
  return (
    <div className="space-y-8">
      <div className="flex flex-col items-center justify-center py-12 text-center">
        <div className="mb-4 rounded-full bg-muted p-4">
          <GitPullRequest className="h-8 w-8 text-muted-foreground" />
        </div>
        <h2 className="mb-2 text-lg font-semibold">Analyze a Pull Request</h2>
        <p className="mb-6 max-w-md text-sm text-muted-foreground">
          Paste a GitHub PR URL above to get instant risk analysis, changed file
          summary, and review guidance.
        </p>
        <div className="flex items-center gap-2 rounded-lg border bg-muted/50 px-4 py-2 text-xs text-muted-foreground">
          <Info className="h-3.5 w-3.5" />
          <span>Try: https://github.com/owner/repo/pull/123</span>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <Card className="border-dashed">
          <CardHeader className="pb-3">
            <div className="flex items-center gap-2 text-muted-foreground">
              <Shield className="h-4 w-4" />
              <CardTitle className="text-sm font-medium">Risk Summary</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <p className="text-xs text-muted-foreground">
              Identifies high-risk files, security-sensitive changes, and source
              modifications without test coverage.
            </p>
          </CardContent>
        </Card>

        <Card className="border-dashed">
          <CardHeader className="pb-3">
            <div className="flex items-center gap-2 text-muted-foreground">
              <Files className="h-4 w-4" />
              <CardTitle className="text-sm font-medium">Changed Files</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <p className="text-xs text-muted-foreground">
              Lists all modified files with language, change size, status badges,
              and priority scores for focused review.
            </p>
          </CardContent>
        </Card>

        <Card className="border-dashed">
          <CardHeader className="pb-3">
            <div className="flex items-center gap-2 text-muted-foreground">
              <Search className="h-4 w-4" />
              <CardTitle className="text-sm font-medium">Review Guidance</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <p className="text-xs text-muted-foreground">
              Provides actionable recommendations based on change patterns, risk
              signals, and file dependencies.
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function LoadingState() {
  return (
    <Card>
      <CardContent className="flex flex-col items-center justify-center py-14 text-center">
        <div className="mb-4 rounded-full bg-muted p-4">
          <Spinner className="h-8 w-8" />
        </div>
        <h2 className="mb-2 text-lg font-semibold">Analyzing pull request</h2>
        <p className="max-w-md text-sm text-muted-foreground">
          Fetching PR metadata, changed files, and risk signals from the backend.
        </p>
        <div className="mt-6 grid w-full max-w-xl gap-2 text-left text-xs text-muted-foreground sm:grid-cols-3">
          <div className="rounded-lg border bg-muted/30 p-3">Fetching PR</div>
          <div className="rounded-lg border bg-muted/30 p-3">Reading files</div>
          <div className="rounded-lg border bg-muted/30 p-3">Preparing review</div>
        </div>
      </CardContent>
    </Card>
  )
}

function ResultDashboard({ result }: { result: PrContextResponse }) {
  const hasHighRisk = result.derived.high_risk_files.length > 0
  const hasSourceWithoutTests = result.derived.has_source_without_tests
  const isDocsOnly = result.derived.docs_only

  const totalAdditions = result.pr.additions
  const totalDeletions = result.pr.deletions

  const highPriorityFiles = result.files.filter((f) => f.priority_score_hint >= 60)
  const riskFiles = result.files.filter((f) => f.is_high_risk_path)
  const reviewFocus =
    riskFiles[0]?.filename ?? highPriorityFiles[0]?.filename ?? "No urgent file focus"

  return (
    <div className="space-y-6">
      {/* PR Metadata Summary Cards */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center justify-between">
              <div className="text-xs text-muted-foreground">Changes</div>
              <Files className="h-3.5 w-3.5 text-muted-foreground" />
            </div>
            <div className="mt-1 text-2xl font-bold">{result.pr.changed_files}</div>
            <div className="text-xs text-muted-foreground">files modified</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center justify-between">
              <div className="text-xs text-muted-foreground">Additions</div>
              <Plus className="h-3.5 w-3.5 text-green-600" />
            </div>
            <div className="mt-1 text-2xl font-bold text-green-600">
              +{totalAdditions}
            </div>
            <div className="text-xs text-muted-foreground">lines added</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center justify-between">
              <div className="text-xs text-muted-foreground">Deletions</div>
              <Minus className="h-3.5 w-3.5 text-red-600" />
            </div>
            <div className="mt-1 text-2xl font-bold text-red-600">
              -{totalDeletions}
            </div>
            <div className="text-xs text-muted-foreground">lines removed</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center justify-between">
              <div className="text-xs text-muted-foreground">Risk Level</div>
              {hasHighRisk || hasSourceWithoutTests ? (
                <ShieldAlert className="h-3.5 w-3.5 text-destructive" />
              ) : (
                <ShieldCheck className="h-3.5 w-3.5 text-green-600" />
              )}
            </div>
            <div className="mt-1 text-2xl font-bold">
              {hasHighRisk || hasSourceWithoutTests ? "Elevated" : "Low"}
            </div>
            <div className="text-xs text-muted-foreground">
              {hasHighRisk
                ? `${result.derived.high_risk_files.length} high-risk files`
                : "no critical signals"}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* PR Info + Risk Signals Row */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <CardTitle className="truncate text-base">
                  <a
                    href={result.pr.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 hover:underline"
                  >
                    <GitPullRequest className="h-4 w-4 shrink-0" />
                    <span className="truncate">{result.pr.title}</span>
                    <ExternalLink className="h-3 w-3 shrink-0 text-muted-foreground" />
                  </a>
                </CardTitle>
                <CardDescription className="mt-1 flex items-center gap-2">
                  <span>{result.pr.author}</span>
                  <span className="text-muted-foreground/50">|</span>
                  <span className="inline-flex items-center gap-1">
                    <GitBranch className="h-3 w-3" />
                    {result.pr.base_branch}
                    <span className="text-muted-foreground/50">&larr;</span>
                    {result.pr.head_branch}
                  </span>
                </CardDescription>
              </div>
            </div>
          </CardHeader>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center gap-2">
              <Shield className="h-4 w-4" />
              <CardTitle className="text-sm font-medium">Risk Signals</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="space-y-2">
            {isDocsOnly && (
              <div className="flex items-center gap-2">
                <Badge variant="secondary">Docs only</Badge>
                <span className="text-xs text-muted-foreground">
                  Documentation changes only
                </span>
              </div>
            )}
            {hasSourceWithoutTests && (
              <div className="flex items-center gap-2">
                <Badge variant="destructive">No test coverage</Badge>
                <span className="text-xs text-muted-foreground">
                  Source files changed without corresponding tests
                </span>
              </div>
            )}
            {hasHighRisk && (
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <Badge variant="destructive">High-risk paths</Badge>
                </div>
                <div className="flex flex-wrap gap-1">
                  {result.derived.high_risk_files.map((f) => (
                    <Badge key={f} variant="outline" className="font-mono text-xs">
                      {f}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
            {!isDocsOnly && !hasSourceWithoutTests && !hasHighRisk && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <CheckCircle className="h-4 w-4 text-green-600" />
                <span>No critical risk signals detected</span>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Review Recommendations */}
      {(highPriorityFiles.length > 0 || hasSourceWithoutTests || hasHighRisk) && (
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center gap-2">
              <Search className="h-4 w-4" />
              <CardTitle className="text-sm font-medium">Review Recommendations</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2 text-sm">
              {highPriorityFiles.length > 0 && (
                <li className="flex items-start gap-2">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
                  <span>
                    Focus on <strong>{highPriorityFiles.length} high-priority files</strong> first
                    (score &ge; 60):
                    <span className="ml-1 font-mono text-xs text-muted-foreground">
                      {highPriorityFiles.slice(0, 3).map((f) => f.filename).join(", ")}
                      {highPriorityFiles.length > 3 && ` +${highPriorityFiles.length - 3} more`}
                    </span>
                  </span>
                </li>
              )}
              {hasSourceWithoutTests && (
                <li className="flex items-start gap-2">
                  <TestTube className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
                  <span>
                    Consider adding or updating tests for the source files changed in this PR.
                  </span>
                </li>
              )}
              {hasHighRisk && (
                <li className="flex items-start gap-2">
                  <Shield className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                  <span>
                    Review high-risk paths carefully for security, auth, or infrastructure changes.
                  </span>
                </li>
              )}
              {isDocsOnly && (
                <li className="flex items-start gap-2">
                  <BookOpen className="mt-0.5 h-4 w-4 shrink-0 text-blue-500" />
                  <span>
                    Documentation-only change. Lower review urgency unless it affects public API docs.
                  </span>
                </li>
              )}
            </ul>
          </CardContent>
        </Card>
      )}

      {/* Changed Files Table */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <FileCode className="h-4 w-4" />
              <CardTitle className="text-sm font-medium">
                Changed Files ({result.files.length})
              </CardTitle>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>File</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Language</TableHead>
                <TableHead className="text-right">+/-</TableHead>
                <TableHead>Risk</TableHead>
                <TableHead className="text-right">Priority</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {result.files.map((file) => (
                <TableRow
                  key={file.filename}
                  className={
                    file.is_high_risk_path ? "bg-destructive/5 font-medium" : ""
                  }
                >
                  <TableCell className="max-w-[280px] lg:max-w-sm">
                    <div className="flex items-center gap-1.5">
                      {getFileTypeIcon(file)}
                      <span className="truncate font-mono text-xs">
                        {file.filename}
                      </span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <Badge variant={getStatusBadgeVariant(file.status)}>
                      {file.status}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {file.language}
                  </TableCell>
                  <TableCell className="text-right text-xs">
                    <span className="text-green-600">+{file.additions}</span>{" "}
                    <span className="text-red-600">-{file.deletions}</span>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {file.risk_hints.length > 0 ? (
                        file.risk_hints.map((h) => (
                          <Badge
                            key={h}
                            variant="outline"
                            className="text-xs"
                          >
                            {h}
                          </Badge>
                        ))
                      ) : (
                        <span className="text-xs text-muted-foreground">None</span>
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
                      className="text-xs"
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

      {/* AI Review Readiness */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4" />
            <CardTitle className="text-sm font-medium">
              AI Review Readiness
            </CardTitle>
          </div>
          <CardDescription>
            PR Copilot prepared the review inputs below from the current PR.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 md:grid-cols-3">
            <div className="rounded-lg border bg-muted/30 p-3">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Files className="h-3.5 w-3.5" />
                Files scanned
              </div>
              <div className="mt-1 text-xl font-semibold">
                {result.pr.changed_files}
              </div>
              <div className="text-xs text-muted-foreground">
                {totalAdditions + totalDeletions} changed lines
              </div>
            </div>

            <div className="rounded-lg border bg-muted/30 p-3">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Shield className="h-3.5 w-3.5" />
                Risk focus
              </div>
              <div className="mt-1 truncate text-sm font-medium">
                {reviewFocus}
              </div>
              <div className="text-xs text-muted-foreground">
                {riskFiles.length > 0
                  ? `${riskFiles.length} high-risk files`
                  : "No high-risk paths detected"}
              </div>
            </div>

            <div className="rounded-lg border bg-muted/30 p-3">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <TestTube className="h-3.5 w-3.5" />
                Test signal
              </div>
              <div className="mt-1 text-sm font-medium">
                {hasSourceWithoutTests ? "Needs test review" : "No test gap signal"}
              </div>
              <div className="text-xs text-muted-foreground">
                {isDocsOnly ? "Documentation-only change" : "Based on file categories"}
              </div>
            </div>
          </div>

          <div className="mt-4 rounded-lg border bg-background p-3 text-sm">
            <div className="mb-2 font-medium">Suggested reviewer focus</div>
            <ul className="space-y-1 text-muted-foreground">
              {hasHighRisk && <li>Review high-risk paths before merge approval.</li>}
              {hasSourceWithoutTests && <li>Check whether source changes need tests.</li>}
              {highPriorityFiles.length > 0 && (
                <li>Start with files scoring 60 or higher in priority.</li>
              )}
              {!hasHighRisk && !hasSourceWithoutTests && highPriorityFiles.length === 0 && (
                <li>No urgent signals detected. Continue with standard review.</li>
              )}
            </ul>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function App() {
  const [prUrl, setPrUrl] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<PrContextResponse | null>(null)
  const contentState = loading
    ? "loading"
    : result
      ? "result"
      : error
        ? "error"
        : "empty"

  const handleAnalyze = async () => {
    if (!prUrl.trim()) {
      setError("A GitHub PR URL is required to start analysis.")
      return
    }
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const data = await analyzePr(prUrl)
      setResult(data)
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Request failed"
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-background">
      {/* Top Command Bar */}
      <header className="sticky top-0 z-10 border-b bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-4">
          <div className="flex items-center gap-2">
            <GitPullRequest className="h-5 w-5" />
            <span className="text-sm font-semibold">PR Copilot</span>
            <Badge variant="outline" className="hidden text-xs sm:inline-flex">
              v0.1
            </Badge>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant="secondary" className="hidden text-xs sm:inline-flex">
              Backend: localhost:8000
            </Badge>
            <Badge variant="outline" className="hidden text-xs md:inline-flex">
              Model: GPT-4
            </Badge>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-6">
        {/* PR Input Area */}
        <div className="mb-6">
          <div className="flex flex-col gap-2 sm:flex-row">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="https://github.com/owner/repo/pull/123"
                value={prUrl}
                onChange={(e) => {
                  setPrUrl(e.target.value)
                  if (error) setError(null)
                }}
                onKeyDown={(e) => e.key === "Enter" && !loading && handleAnalyze()}
                disabled={loading}
                className="pl-9"
              />
            </div>
            <Button
              onClick={handleAnalyze}
              disabled={loading}
              className="sm:w-auto"
            >
              {loading ? (
                <Spinner className="mr-2 h-4 w-4" />
              ) : (
                <Search className="mr-2 h-4 w-4" />
              )}
              {loading ? "Analyzing..." : "Analyze PR"}
            </Button>
          </div>

          {loading && (
            <div className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
              <Spinner className="h-3.5 w-3.5" />
              <span>Fetching PR context from GitHub...</span>
            </div>
          )}
        </div>

        <section key={contentState}>
          {contentState === "loading" && <LoadingState />}
          {contentState === "error" && error && (
            <Alert variant="destructive">
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription>
                <div className="font-medium">{error}</div>
                <div className="mt-1 text-xs opacity-80">
                  {getErrorGuidance(error)}
                </div>
              </AlertDescription>
            </Alert>
          )}
          {contentState === "empty" && <EmptyState />}
          {contentState === "result" && result && <ResultDashboard result={result} />}
        </section>
      </main>
    </div>
  )
}

export default App
