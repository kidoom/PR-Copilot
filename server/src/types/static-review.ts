/**
 * Static review pipeline types — evidence signals, context tasks, file classification.
 */

/** File category from intake analysis. */
export type FileCategory = 'source' | 'test' | 'config' | 'doc' | 'build' | 'ci' | 'other'

/** Result of classifying a file. */
export interface FileClassification {
  filename: string
  category: FileCategory
  priority: number // 0-100
}

/** An evidence signal extracted from diff content by rules. */
export interface EvidenceSignal {
  type: string
  file: string
  line?: number
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info'
  snippet?: string
  description: string
}

/** Task type for agent context planning. */
export type ContextTaskType = 'security-review' | 'test-coverage' | 'code-quality' | 'config-review'

/** A context task generated for AI agents. */
export interface ContextTask {
  type: ContextTaskType
  targetFiles: string[]
  evidence: EvidenceSignal[]
  description: string
}
