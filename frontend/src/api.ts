import type {
  PrContextResponse,
  IntakeSummary,
  FilePatchResponse,
  ReviewRunEvent,
  ReviewRunStatusResponse,
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

function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : []
}

function normalizePrContextResponse(value: unknown): PrContextResponse {
  const root = asRecord(value)
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

  return normalizePrContextResponse(await res.json())
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
  const res = await fetch(
    apiUrl(`/api/pr/context/${contextId}/files/${encodedFilename}/patch`),
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
  return res.json()
}

export async function getReviewRun(
  runId: string,
): Promise<ReviewRunStatusResponse> {
  const res = await fetch(apiUrl(`/api/review/runs/${encodeURIComponent(runId)}`))
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: "Unknown error" }))
    throw new Error(body.detail || `Request failed: ${res.status}`)
  }
  return res.json()
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
  return res.json()
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
  const wsBase = API_BASE
    ? new URL(API_BASE, window.location.origin)
    : new URL(window.location.origin)
  wsBase.protocol = wsBase.protocol === "https:" ? "wss:" : "ws:"
  wsBase.pathname = `/ws/review-runs/${encodeURIComponent(runId)}`
  wsBase.search = ""
  wsBase.hash = ""
  const ws = new WebSocket(wsBase)
  let receivedTerminalEvent = false
  let disposed = false

  ws.addEventListener("open", () => {
    onOpen?.()
  })

  ws.addEventListener("message", (msg) => {
    try {
      const data = JSON.parse(msg.data) as ReviewRunEvent
      onEvent(data)
      if (TERMINAL_EVENTS.has(data.type)) {
        receivedTerminalEvent = true
        ws.close()
      }
    } catch {
      // ignore malformed messages
    }
  })

  ws.addEventListener("error", () => {
    if (!disposed) {
      onError?.("Live progress connection failed. The review may still be running on the backend.")
    }
  })

  ws.addEventListener("close", () => {
    if (!receivedTerminalEvent && !disposed) {
      onError?.("Live progress connection closed before the review finished.")
    }
  })

  return () => {
    disposed = true
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
      ws.close()
    }
  }
}
