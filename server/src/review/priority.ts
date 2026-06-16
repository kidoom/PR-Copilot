/**
 * File priority scoring — assigns priority 0-100 based on category, change size, and risk patterns.
 */

import type { FileClassification, FileCategory } from '../types/static-review.js'

/** Base priority by file category. */
const CATEGORY_BASE: Record<FileCategory, number> = {
  source: 60,
  test: 30,
  config: 40,
  doc: 10,
  build: 35,
  ci: 45,
  other: 20,
}

/** Risk patterns that boost priority for source files. */
const RISK_PATTERNS: Array<{ pattern: RegExp; boost: number; description: string }> = [
  { pattern: /auth|login|session|token|jwt|oauth/i, boost: 15, description: 'Authentication-related' },
  { pattern: /password|secret|credential|key/i, boost: 15, description: 'Credential-related' },
  { pattern: /permission|role|access|acl|rbac/i, boost: 10, description: 'Authorization-related' },
  { pattern: /inject|sql|query|exec|eval/i, boost: 12, description: 'Injection risk' },
  { pattern: /upload|download|file|stream/i, boost: 8, description: 'File operations' },
  { pattern: /api|route|endpoint|handler|controller/i, boost: 5, description: 'API surface' },
  { pattern: /middleware|interceptor|filter/i, boost: 8, description: 'Middleware' },
  { pattern: /config|env|setting/i, boost: 5, description: 'Configuration' },
]

/**
 * Score a single file's priority.
 */
export function scorePriority(
  filename: string,
  category: FileCategory,
  additions: number,
  deletions: number,
): number {
  let score = CATEGORY_BASE[category]

  // Boost for large changes
  const totalChanges = additions + deletions
  if (totalChanges > 200) score += 15
  else if (totalChanges > 100) score += 10
  else if (totalChanges > 50) score += 5

  // Boost for risk patterns in filename
  for (const { pattern, boost } of RISK_PATTERNS) {
    if (pattern.test(filename)) {
      score += boost
    }
  }

  // New files get a small boost
  if (additions > 0 && deletions === 0) {
    score += 5
  }

  return Math.min(100, Math.max(0, score))
}

/**
 * Score all classifications.
 */
export function scorePriorities(
  classifications: FileClassification[],
  fileStats: Map<string, { additions: number; deletions: number }>,
): FileClassification[] {
  return classifications.map((fc) => {
    const stats = fileStats.get(fc.filename) ?? { additions: 0, deletions: 0 }
    return {
      ...fc,
      priority: scorePriority(fc.filename, fc.category, stats.additions, stats.deletions),
    }
  }).sort((a, b) => b.priority - a.priority)
}
