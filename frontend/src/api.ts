import type { PrContextResponse, IntakeSummary, FilePatchResponse } from "./types"

type UnknownRecord = Record<string, unknown>

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

export async function analyzePr(
  prUrl: string,
  githubToken?: string,
): Promise<PrContextResponse> {
  const res = await fetch("/api/pr/context", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      pr_url: prUrl,
      github_token: githubToken || undefined,
    }),
  })

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: "Unknown error" }))
    throw new Error(body.detail || `Request failed: ${res.status}`)
  }

  return normalizePrContextResponse(await res.json())
}

export async function getIntakeSummary(contextId: string): Promise<IntakeSummary> {
  const res = await fetch("/api/review/intake", {
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
    `/api/pr/context/${contextId}/files/${encodedFilename}/patch`,
  )

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: "Unknown error" }))
    throw new Error(body.detail || `Request failed: ${res.status}`)
  }

  return res.json()
}
