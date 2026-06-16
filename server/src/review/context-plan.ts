/**
 * Context task planning — generates agent tasks from evidence signals and file classifications.
 */

import type { PRFile } from '../types/pr.js'
import type { EvidenceSignal, ContextTask, ContextTaskType, FileClassification } from '../types/static-review.js'

/**
 * Group evidence signals by task type.
 */
function groupEvidenceByType(signals: EvidenceSignal[]): Map<ContextTaskType, EvidenceSignal[]> {
  const groups = new Map<ContextTaskType, EvidenceSignal[]>()

  for (const signal of signals) {
    let taskType: ContextTaskType

    switch (signal.type) {
      case 'hardcoded_secret':
      case 'sql_injection':
      case 'eval_exec':
        taskType = 'security-review'
        break
      case 'unsafe_cast':
      case 'missing_error_handling':
      case 'missing_await':
        taskType = 'code-quality'
        break
      case 'console_log':
      case 'todo_fixme':
      case 'deprecated_api':
      case 'hardcoded_url':
        taskType = 'code-quality'
        break
      default:
        taskType = 'code-quality'
    }

    const existing = groups.get(taskType) ?? []
    existing.push(signal)
    groups.set(taskType, existing)
  }

  return groups
}

/**
 * Check if test files exist for the changed source files.
 */
function findMissingTestCoverage(
  classifications: FileClassification[],
  files: PRFile[],
): ContextTask | null {
  const sourceFiles = classifications
    .filter((c) => c.category === 'source')
    .map((c) => c.filename)

  const testFiles = new Set(
    classifications
      .filter((c) => c.category === 'test')
      .map((c) => c.filename),
  )

  // Simple heuristic: check if any source file has a corresponding test
  const untested: string[] = []
  for (const src of sourceFiles) {
    const base = src.split('/').pop()?.replace(/\.[^.]+$/, '') ?? ''
    const hasTest = [...testFiles].some((t) =>
      t.toLowerCase().includes(base.toLowerCase()) ||
      t.toLowerCase().includes(`test_${base.toLowerCase()}`) ||
      t.toLowerCase().includes(`${base.toLowerCase()}_test`),
    )
    if (!hasTest) untested.push(src)
  }

  if (untested.length === 0) return null

  return {
    type: 'test-coverage',
    targetFiles: untested,
    evidence: [],
    description: `Source files without corresponding test coverage: ${untested.join(', ')}`,
  }
}

/**
 * Generate context tasks for AI agents from static review results.
 */
export function generateContextTasks(
  files: PRFile[],
  classifications: FileClassification[],
  signals: EvidenceSignal[],
): ContextTask[] {
  const tasks: ContextTask[] = []

  // Group evidence signals by task type
  const grouped = groupEvidenceByType(signals)

  for (const [type, evidence] of grouped) {
    const targetFiles = [...new Set(evidence.map((e) => e.file))]
    tasks.push({
      type,
      targetFiles,
      evidence,
      description: `${type} review for: ${targetFiles.join(', ')}`,
    })
  }

  // Check for missing test coverage
  const testTask = findMissingTestCoverage(classifications, files)
  if (testTask) {
    tasks.push(testTask)
  }

  // If no specific tasks, create a general code-quality review
  if (tasks.length === 0) {
    tasks.push({
      type: 'code-quality',
      targetFiles: files.map((f) => f.filename),
      evidence: [],
      description: 'General code quality review for all changed files',
    })
  }

  return tasks
}
