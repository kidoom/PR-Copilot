export interface PrOverview {
  title: string
  author: string
  url: string
  base_branch: string
  head_branch: string
  additions: number
  deletions: number
  changed_files: number
  head_sha: string
}

export interface FileEntry {
  filename: string
  status: string
  additions: number
  deletions: number
  language: string
  language_family: string
  is_test: boolean
  is_docs: boolean
  is_config: boolean
  is_source: boolean
  is_binary: boolean
  is_high_risk_path: boolean
  risk_hints: string[]
  priority_score_hint: number
}

export interface DerivedSignals {
  docs_only: boolean
  has_source_without_tests: boolean
  high_risk_files: string[]
}

export interface PrContextResponse {
  context_id: string
  pr: PrOverview
  files: FileEntry[]
  derived: DerivedSignals
}

export interface TopDirectory {
  directory: string
  file_count: number
}

export interface PatchLine {
  type: "added" | "removed" | "context"
  content: string
  old_line: number | null
  new_line: number | null
}

export interface PatchHunk {
  header: string
  old_start: number
  old_lines: number
  new_start: number
  new_lines: number
  lines: PatchLine[]
}

export interface FilePatchResponse {
  context_id: string
  filename: string
  patch_available: boolean
  is_binary: boolean
  parse_error: string | null
  truncated: boolean
  hunks: PatchHunk[]
}

export interface IntakeSummary {
  context_id: string
  size: "small" | "medium" | "large"
  change_type: "docs" | "test" | "source" | "config" | "mixed"
  docs_only: boolean
  source_without_tests: boolean
  has_high_risk_paths: boolean
  language_distribution: Record<string, number>
  file_type_distribution: Record<string, number>
  top_directories: TopDirectory[]
  notable_signals: string[]
}

export type ReviewRunStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "cancelled"
  | "completed"
  | "failed"

export type ReviewRunEventType =
  | "run.started"
  | "message.delta"
  | "tool.call"
  | "tool.result"
  | "subagent.started"
  | "subagent.completed"
  | "run.completed"
  | "run.failed"
  | "run.cancelled"

export interface ReviewRunEvent {
  event_id: string
  run_id: string
  type: ReviewRunEventType
  sequence: number
  created_at: string
  payload: Record<string, unknown>
}

export interface EvidenceRef {
  file: string
  line?: number
  snippet?: string
  source?: string
}

export interface NormalizedFinding {
  claim: string
  confidence: number
  severity: "informational" | "info" | "low" | "medium" | "high" | "critical"
  evidence: EvidenceRef[]
  fingerprint: string
}

export interface TaskSummary {
  task_id: string
  task_type: string
  agent_type: string
  child_session_id: string
  execution_status: string
  parse_status: string
  validation_errors: string[]
}

export interface FinalReviewResult {
  status: string
  summary: string
  findings: NormalizedFinding[]
  uncertainties: string[]
  notes: string[]
  task_summaries: TaskSummary[]
  raw_output: string
  steps: number
  stopped_by_max_steps: boolean
  token_usage: { input_tokens: number; output_tokens: number }
}

export interface ReviewRunStatusResponse {
  run_id: string
  context_id: string
  status: ReviewRunStatus
  final_result?: FinalReviewResult
  error_summary?: string
}

export interface ToolEventPayload {
  agent_kind: string
  agent_type: string
  task_id: string
  child_session_id: string
  tool_name: string
  tool_use_id: string
  input_summary?: unknown
  output_summary?: unknown
  is_error?: boolean
}

export interface SubagentEventPayload {
  task_id: string
  task_type: string
  agent_type: string
  child_session_id: string
  status?: string
  stopped_by_max_steps?: boolean
  validation_errors?: string[]
  error?: string
}

export interface MessageDeltaPayload {
  text: string
  agent_type: string
}
