/**
 * Review run types — findings, evidence, run lifecycle.
 */

/** Severity level for a finding. */
export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info'

/** Category of a finding. */
export type FindingCategory = 'security' | 'test-coverage' | 'code-quality' | 'config' | 'performance' | 'documentation'

/** A single evidence reference supporting a finding. */
export interface Evidence {
  file: string
  line?: number
  snippet?: string
  tool?: string
}

/** A review finding produced by agent synthesis. */
export interface ReviewFinding {
  file: string
  line?: number
  severity: Severity
  category: FindingCategory
  title: string
  description: string
  evidence: Evidence[]
  suggestion?: string
}

/** Review run status. */
export type RunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'

/** A review run record. */
export interface ReviewRun {
  run_id: string
  context_id: string
  status: RunStatus
  findings?: ReviewFinding[]
  tasks?: unknown[]
  error?: string
  created_at: string
  completed_at?: string
}
