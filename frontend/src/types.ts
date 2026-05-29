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
