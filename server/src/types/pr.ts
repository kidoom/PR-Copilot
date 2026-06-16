/**
 * PR Context types — mirrors the Python backend's PR context structure.
 */

/** A single line in a diff hunk. */
export interface HunkLine {
  content: string
  type: 'added' | 'removed' | 'unchanged' | 'header'
  old_line?: number
  new_line?: number
}

/** A parsed diff hunk. */
export interface Hunk {
  header: string
  lines: HunkLine[]
}

/** A file in the PR with its diff information. */
export interface PRFile {
  filename: string
  status: 'added' | 'removed' | 'modified' | 'renamed'
  additions: number
  deletions: number
  patch_available: boolean
  hunks: Hunk[]
  raw_patch?: string
}

/** Commit information. */
export interface CommitInfo {
  sha: string
  message: string
  author: string
  date: string
}

/** Full PR context built from GitHub API. */
export interface PRContext {
  context_id: string
  owner: string
  repo: string
  pull_number: number
  title: string
  description: string
  author: string
  base_branch: string
  head_branch: string
  head_sha: string
  files: PRFile[]
  commits: CommitInfo[]
  created_at: string
}
