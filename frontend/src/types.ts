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
