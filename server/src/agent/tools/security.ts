/**
 * Security utilities for repo context tools: path traversal, sensitive files, ignored directories.
 */

import * as path from 'node:path'

export const IGNORED_DIRECTORIES = new Set([
  '.git', 'node_modules', 'dist', 'build', 'coverage', '.venv', 'venv',
  '__pycache__', '.pytest_cache', '.mypy_cache', '.tox', '.eggs',
  '.next', '.nuxt', 'target', 'vendor',
])

export const SENSITIVE_PATTERNS = [
  '.env', '.env.', 'private_key', 'private-key', 'id_rsa', 'id_ed25519',
  '.pem', '.key', 'credentials', 'secret', '.secret',
]

export const MAX_LINES = 50
export const MAX_SEARCH_RESULTS = 50
const CJK_PATTERN = /[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]/g

/**
 * Resolve a path safely, preventing traversal attacks.
 * Returns null if the path escapes the repo root.
 */
export function resolveSafePath(repoRoot: string, requestedPath: string): string | null {
  const root = path.resolve(repoRoot)
  const target = path.resolve(root, requestedPath)
  const relative = path.relative(root, target)
  if (relative.startsWith('..') || path.isAbsolute(relative)) return null
  return target
}

/**
 * Check if a path contains an ignored directory.
 */
export function isIgnoredDirectory(filePath: string): boolean {
  const parts = filePath.split(/[\\/]+/)
  return parts.some((part) => IGNORED_DIRECTORIES.has(part))
}

/**
 * Check if a file matches sensitive patterns.
 */
export function isSensitiveFile(filePath: string): boolean {
  const normalized = filePath.replace(/\\/g, '/').toLowerCase()
  const name = path.posix.basename(normalized)
  return SENSITIVE_PATTERNS.some((pattern) => name.includes(pattern) || normalized.includes(`/${pattern}`))
}

/**
 * Estimate token count (conservative: ~4 chars per token, ~1 per CJK char).
 */
export function estimateTokens(text: string): number {
  if (!text) return 0
  const cjkCount = (text.match(CJK_PATTERN) ?? []).length
  const nonCjkLen = text.length - cjkCount
  const englishEstimate = Math.floor(text.length / 4)
  const cjkEstimate = cjkCount + Math.floor(nonCjkLen / 4)
  return Math.max(1, Math.max(englishEstimate, cjkEstimate))
}
