import type {
  PrContextResponse,
  IntakeSummary,
  FilePatchResponse,
  ReviewRunEvent,
  ReviewRunStatusResponse,
  FinalReviewResult,
  TaskSummary,
} from "./types"

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "")

function apiUrl(path: string): string {
  return `${API_BASE}${path}`
}

type UnknownRecord = Record<string, unknown>

export class ApiError extends Error {
  status: number
  code: string
  guidance: string

  constructor(message: string, status: number, code = "", guidance = "") {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.code = code
    this.guidance = guidance
  }
}

function asRecord(value: unknown): UnknownRecord {
  return value && typeof value === "object" ? (value as UnknownRecord) : {}
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback
}

function asOptionalNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined
}

function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : []
}

function categoryFor(filename: string): string {
  if (/(^|\/)(tests?|__tests__|spec)\//i.test(filename) || /\.(test|spec)\./i.test(filename)) return "test"
  if (/(^|\/)\.github\/workflows\//i.test(filename)) return "ci"
  if (/(^|\/)docs?\//i.test(filename) || /\.(md|rst|txt)$/i.test(filename)) return "doc"
  if (/(^|\/)(package\.json|tsconfig\.json|vite\.config|webpack\.config)/i.test(filename)) return "config"
  return "source"
}

function languageFor(filename: string): string {
  const ext = filename.split(".").pop()?.toLowerCase()
  const map: Record<string, string> = {
    ts: "TypeScript",
    tsx: "TypeScript",
    js: "JavaScript",
    jsx: "JavaScript",
    py: "Python",
    md: "Markdown",
    json: "JSON",
    yml: "YAML",
    yaml: "YAML",
  }
  return ext ? map[ext] || ext.toUpperCase() : "Unknown"
}

function normalizePrContextResponse(value: unknown): PrContextResponse {
  const root = asRecord(value)
  if (!root.pr) {
    const files = Array.isArray(root.files) ? root.files : []
    const normalizedFiles = files.map((item) => {
      const file = asRecord(item)
      const filename = asString(file.filename, "unknown file")
      const category = categoryFor(filename)
      const riskHints = /auth|security|permission|token|secret/i.test(filename)
        ? ["high_risk_path"]
        : []

      return {
        filename,
        status: asString(file.status, "modified"),
        additions: asNumber(file.additions),
        deletions: asNumber(file.deletions),
        language: languageFor(filename),
        language_family: languageFor(filename).toLowerCase(),
        is_test: category === "test",
        is_docs: category === "doc",
        is_config: category === "config" || category === "ci",
        is_source: category === "source",
        is_binary: asBoolean(file.is_binary, file.patch_available === false),
        is_high_risk_path: riskHints.length > 0,
        risk_hints: riskHints,
        priority_score_hint: riskHints.length > 0 ? 80 : category === "source" ? 60 : 20,
      }
    })

    return {
      context_id: asString(root.context_id),
      pr: {
        title: asString(root.title, "Untitled pull request"),
        author: asString(root.author, "Unknown author"),
        url: `https://github.com/${asString(root.owner)}/${asString(root.repo)}/pull/${asNumber(root.pull_number)}`,
        base_branch: asString(root.base_branch, "base"),
        head_branch: asString(root.head_branch, "head"),
        additions: normalizedFiles.reduce((sum, file) => sum + file.additions, 0),
        deletions: normalizedFiles.reduce((sum, file) => sum + file.deletions, 0),
        changed_files: normalizedFiles.length,
        head_sha: asString(root.head_sha),
      },
      files: normalizedFiles,
      derived: {
        docs_only: normalizedFiles.length > 0 && normalizedFiles.every((file) => file.is_docs),
        has_source_without_tests: normalizedFiles.some((file) => file.is_source) && !normalizedFiles.some((file) => file.is_test),
        high_risk_files: normalizedFiles.filter((file) => file.is_high_risk_path).map((file) => file.filename),
      },
    }
  }

  const pr = asRecord(root.pr)
  const derived = asRecord(root.derived)
  const files = Array.isArray(root.files) ? root.files : []

  return {
    context_id: asString(root.context_id),
    pr: {
      title: asString(pr.title, "Untitled pull request"),
      author: asString(pr.author, "Unknown author"),
      url: asString(pr.url),
      base_branch: asString(pr.base_branch, "base"),
      head_branch: asString(pr.head_branch, "head"),
      additions: asNumber(pr.additions),
      deletions: asNumber(pr.deletions),
      changed_files: asNumber(pr.changed_files, files.length),
      head_sha: asString(pr.head_sha),
    },
    files: files.map((item) => {
      const file = asRecord(item)

      return {
        filename: asString(file.filename, "unknown file"),
        status: asString(file.status, "modified"),
        additions: asNumber(file.additions),
        deletions: asNumber(file.deletions),
        language: asString(file.language, "Unknown"),
        language_family: asString(file.language_family, "unknown"),
        is_test: asBoolean(file.is_test),
        is_docs: asBoolean(file.is_docs),
        is_config: asBoolean(file.is_config),
        is_source: asBoolean(file.is_source),
        is_binary: asBoolean(file.is_binary),
        is_high_risk_path: asBoolean(file.is_high_risk_path),
        risk_hints: asStringArray(file.risk_hints),
        priority_score_hint: asNumber(file.priority_score_hint),
      }
    }),
    derived: {
      docs_only: asBoolean(derived.docs_only),
      has_source_without_tests: asBoolean(derived.has_source_without_tests),
      high_risk_files: asStringArray(derived.high_risk_files),
    },
  }
}

async function throwApiError(res: Response, fallback: string): Promise<never> {
  const body = asRecord(await res.json().catch(() => ({})))
  const detail = body.detail
  if (typeof detail === "string") {
    throw new ApiError(detail, res.status)
  }
  const structured = asRecord(detail)
  throw new ApiError(
    asString(structured.message, fallback),
    res.status,
    asString(structured.code),
    asString(structured.guidance),
  )
}

export async function analyzePr(prUrl: string): Promise<PrContextResponse> {
  const res = await fetch(apiUrl("/api/pr/context"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pr_url: prUrl }),
  })

  if (!res.ok) {
    await throwApiError(res, `Request failed: ${res.status}`)
  }

  const raw = await res.json()
  const normalized = normalizePrContextResponse(raw)
  return normalized
}

export async function getPrContext(contextId: string): Promise<PrContextResponse> {
  const res = await fetch(apiUrl(`/api/pr/context/${encodeURIComponent(contextId)}`))

  if (!res.ok) {
    await throwApiError(res, `Request failed: ${res.status}`)
  }

  const raw = await res.json()
  return normalizePrContextResponse(raw)
}

function normalizeFinding(value: unknown, index: number) {
  const finding = asRecord(value)
  const title = asString(finding.title, "Review finding")
  const description = asString(finding.description)
  const file = asString(finding.file)
  const line = typeof finding.line === "number" ? finding.line : undefined
  const evidence = Array.isArray(finding.evidence) ? finding.evidence.map((item) => {
    const ref = asRecord(item)
    return {
      file: asString(ref.file, file),
      line: typeof ref.line === "number" ? ref.line : undefined,
      snippet: asString(ref.snippet),
      source: asString(ref.tool, asString(ref.source, "agent")),
    }
  }) : []

  return {
    claim: title || description,
    description: title && description && title !== description ? description : undefined,
    confidence: 0.8,
    severity: asString(finding.severity, "info") as "informational" | "info" | "low" | "medium" | "high" | "critical",
    evidence,
    fingerprint: `${file}:${line ?? 0}:${title}:${index}`,
    suggestion: asString(finding.suggestion),
  }
}

function normalizeTaskSummary(value: unknown, index: number): TaskSummary {
  const task = asRecord(value)
  const rawStatus = asString(task.execution_status, asString(task.status))
  const success = task.success
  const executionStatus =
    rawStatus ||
    (success === true ? "completed" : success === false ? "failed" : "completed")

  return {
    task_id: asString(task.task_id, asString(task.id, asString(task.task, `task_${index + 1}`))),
    task_type: asString(task.task_type, asString(task.type, "review")),
    agent_type: asString(task.agent_type, asString(task.agent, asString(task.agent_name, "agent"))),
    child_session_id: asString(task.child_session_id, asString(task.session_id)),
    execution_status: executionStatus,
    parse_status: asString(
      task.parse_status,
      executionStatus === "failed" || executionStatus === "error" ? "invalid" : "valid",
    ),
    validation_errors: Array.isArray(task.validation_errors)
      ? task.validation_errors.filter((item): item is string => typeof item === "string")
      : [],
    model_id: asString(task.model_id) || undefined,
    model_calls: asOptionalNumber(task.model_calls),
    input_tokens: asOptionalNumber(task.input_tokens),
    output_tokens: asOptionalNumber(task.output_tokens),
    observation_tokens: asOptionalNumber(task.observation_tokens),
    elapsed_ms: asOptionalNumber(task.elapsed_ms),
    retries: asOptionalNumber(task.retries),
    fallback_used: typeof task.fallback_used === "boolean" ? task.fallback_used : undefined,
    failure_reason: asString(task.failure_reason) || undefined,
  }
}

function normalizeFinalResultPayload(payload: unknown): FinalReviewResult {
  const root = asRecord(payload)
  const findings = Array.isArray(root.findings) ? root.findings.map(normalizeFinding) : []
  const taskSummaries = Array.isArray(root.task_summaries)
    ? root.task_summaries.map(normalizeTaskSummary)
    : Array.isArray(root.tasks)
      ? root.tasks.map(normalizeTaskSummary)
      : []
  const tokenUsage = asRecord(root.token_usage)

  return {
    status: asString(root.status, "completed"),
    summary: asString(root.summary, findings.length ? `${findings.length} findings` : "No findings"),
    findings,
    uncertainties: asStringArray(root.uncertainties),
    notes: asStringArray(root.notes),
    task_summaries: taskSummaries,
    raw_output: JSON.stringify(root),
    steps: asNumber(root.steps, taskSummaries.length),
    stopped_by_max_steps: asBoolean(root.stopped_by_max_steps),
    token_usage: {
      input_tokens: asNumber(tokenUsage.input_tokens),
      output_tokens: asNumber(tokenUsage.output_tokens),
    },
    coverage: root.coverage as FinalReviewResult["coverage"],
    run_usage: root.run_usage as FinalReviewResult["run_usage"],
    uncovered_high_priority_paths: asStringArray(root.uncovered_high_priority_paths),
    coverage_counts: root.coverage_counts as FinalReviewResult["coverage_counts"],
  }
}

export async function getIntakeSummary(contextId: string): Promise<IntakeSummary> {
  const res = await fetch(apiUrl("/api/review/intake"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ context_id: contextId }),
  })

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: "Unknown error" }))
    throw new Error(body.detail || `Request failed: ${res.status}`)
  }

  return res.json()
}

export async function getFilePatch(
  contextId: string,
  filename: string,
): Promise<FilePatchResponse> {
  const encodedFilename = encodeURIComponent(filename)
  const encodedContextId = encodeURIComponent(contextId)
  const res = await fetch(
    apiUrl(`/api/pr/context/${encodedContextId}/files/${encodedFilename}/patch`),
  )

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: "Unknown error" }))
    throw new Error(body.detail || `Request failed: ${res.status}`)
  }

  return res.json()
}

export async function createReviewRun(
  contextId: string,
  localRepoRoot?: string,
): Promise<{ run_id: string; status: string }> {
  const res = await fetch(apiUrl("/api/review/runs"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      context_id: contextId,
      local_repo_root: localRepoRoot || undefined,
    }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: "Unknown error" }))
    throw new Error(body.detail || `Request failed: ${res.status}`)
  }
  const body = await res.json()
  return { run_id: asString(body.run_id), status: asString(body.status, "queued") }
}

export async function getReviewRun(
  runId: string,
): Promise<ReviewRunStatusResponse> {
  const res = await fetch(apiUrl(`/api/review/runs/${encodeURIComponent(runId)}`))
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: "Unknown error" }))
    throw new Error(body.detail || `Request failed: ${res.status}`)
  }
  const body = asRecord(await res.json())
  return {
    run_id: asString(body.run_id),
    context_id: asString(body.context_id),
    status: asString(body.status, "queued") as ReviewRunStatusResponse["status"],
    final_result: Array.isArray(body.findings) ? normalizeFinalResultPayload(body) : undefined,
    error_summary: asString(body.error),
  }
}

export async function cancelReviewRun(
  runId: string,
): Promise<{ run_id: string; status: string }> {
  const res = await fetch(
    apiUrl(`/api/review/runs/${encodeURIComponent(runId)}/cancel`),
    { method: "POST" },
  )
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: "Unknown error" }))
    throw new Error(body.detail || `Request failed: ${res.status}`)
  }
  const body = await res.json()
  return { run_id: runId, status: asString(asRecord(body).status, "cancelled") }
}

export interface ReviewHistoryRun {
  run_id: string
  lifecycle: string
  created_at: string
  completed_at?: string
  finding_count: number
  summary?: string
  status?: string
}

export async function listReviewRuns(
  contextId: string,
): Promise<{ runs: ReviewHistoryRun[] }> {
  const res = await fetch(
    apiUrl(`/api/pr/context/${encodeURIComponent(contextId)}/runs`),
  )
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: "Unknown error" }))
    throw new Error(body.detail || `Request failed: ${res.status}`)
  }
  return res.json()
}

export interface PrSessionSummary {
  pr_session_id: string
  owner: string
  repo: string
  pull_number: number
  updated_at: string
  run_count: number
  latest_run_id?: string
  latest_lifecycle?: string
  latest_completed_at?: string
  latest_finding_count?: number
  latest_summary?: string
  latest_status?: string
}

export async function listAllSessions(): Promise<{ sessions: PrSessionSummary[] }> {
  const res = await fetch(apiUrl("/api/pr/sessions"))
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: "Unknown error" }))
    throw new Error(body.detail || `Request failed: ${res.status}`)
  }
  return res.json()
}

const TERMINAL_EVENTS = new Set([
  "run.completed",
  "run.failed",
  "run.cancelled",
])

export function subscribeToReviewRun(
  runId: string,
  onEvent: (event: ReviewRunEvent) => void,
  onOpen?: () => void,
  onError?: (message: string) => void,
): () => void {
  const sseUrl = API_BASE
    ? new URL(API_BASE, window.location.origin)
    : new URL(window.location.origin)
  sseUrl.pathname = `/api/review/runs/${encodeURIComponent(runId)}/events`
  sseUrl.searchParams.set("after_sequence", "-1")
  sseUrl.hash = ""
  const source = new EventSource(sseUrl.toString())
  let disposed = false

  source.addEventListener("open", () => {
    onOpen?.()
  })

  source.addEventListener("message", (msg) => {
    try {
      const data = JSON.parse(msg.data) as ReviewRunEvent
      if (data.type === "run.completed") {
        data.payload = normalizeFinalResultPayload(data.payload) as unknown as Record<string, unknown>
      }
      onEvent(data)
      if (TERMINAL_EVENTS.has(data.type)) {
        source.close()
      }
    } catch {
      // ignore malformed messages
    }
  })

  source.addEventListener("error", () => {
    if (!disposed) {
      onError?.("Live progress connection is reconnecting. The review may still be running on the backend.")
    }
  })

  return () => {
    disposed = true
    source.close()
  }
}
