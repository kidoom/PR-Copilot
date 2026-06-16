/**
 * Evidence rules engine — extracts signals from PR diffs by pattern matching.
 */

import type { PRFile, HunkLine } from '../types/pr.js'
import type { EvidenceSignal } from '../types/static-review.js'

interface Rule {
  id: string
  description: string
  severity: EvidenceSignal['severity']
  pattern: RegExp
  matchOn: 'added' | 'any' // only match added lines, or any line
}

const RULES: Rule[] = [
  // Hardcoded secrets
  {
    id: 'hardcoded_secret',
    description: 'Possible hardcoded secret or password',
    severity: 'high',
    pattern: /(?:password|secret|api_key|apikey|token|credential)\s*[:=]\s*["'][^"']{8,}["']/i,
    matchOn: 'added',
  },
  // SQL injection
  {
    id: 'sql_injection',
    description: 'Possible SQL injection via string concatenation',
    severity: 'high',
    pattern: /(?:query|execute|raw)\s*\(\s*[`"'].*\$\{|f["'].*SELECT.*\{|\.format\s*\(\s*["'].*SELECT/i,
    matchOn: 'added',
  },
  // eval / exec
  {
    id: 'eval_exec',
    description: 'Use of eval() or exec() — potential code injection',
    severity: 'medium',
    pattern: /\b(?:eval|exec)\s*\(/i,
    matchOn: 'added',
  },
  // Missing error handling (async without try-catch)
  {
    id: 'missing_error_handling',
    description: 'Async call without visible error handling',
    severity: 'low',
    pattern: /^\s*await\s+.*\(.*\)\s*;?\s*$/,
    matchOn: 'added',
  },
  // Console.log in production
  {
    id: 'console_log',
    description: 'Console.log statement left in code',
    severity: 'info',
    pattern: /console\.(log|debug|info|warn|error)\s*\(/i,
    matchOn: 'added',
  },
  // TODO/FIXME/HACK
  {
    id: 'todo_fixme',
    description: 'TODO/FIXME/HACK comment',
    severity: 'info',
    pattern: /\/\/\s*(?:TODO|FIXME|HACK|XXX|TEMP)\b/i,
    matchOn: 'added',
  },
  // Unsafe type assertion
  {
    id: 'unsafe_cast',
    description: 'Unsafe type assertion (as any, as unknown)',
    severity: 'medium',
    pattern: /\bas\s+(?:any|unknown)\b/,
    matchOn: 'added',
  },
  // Deprecated API usage
  {
    id: 'deprecated_api',
    description: 'Possible use of deprecated API',
    severity: 'low',
    pattern: /@deprecated|\.deprecated\b/i,
    matchOn: 'any',
  },
  // Hardcoded IP/URL
  {
    id: 'hardcoded_url',
    description: 'Hardcoded URL or IP address',
    severity: 'low',
    pattern: /https?:\/\/(?:localhost|127\.0\.0\.1|0\.0\.0\.0|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+)/,
    matchOn: 'added',
  },
  // Missing await
  {
    id: 'missing_await',
    description: 'Possible missing await on async function',
    severity: 'medium',
    pattern: /(?:^|\s)(?:return\s+)?(?!await\s)\w+\.\w+(?:Async|async)\s*\(/m,
    matchOn: 'added',
  },
]

/**
 * Run evidence rules against a single file's diff.
 */
export function runEvidenceRules(file: PRFile): EvidenceSignal[] {
  const signals: EvidenceSignal[] = []

  for (const hunk of file.hunks) {
    let lineOffset = 0
    for (const line of hunk.lines) {
      if (line.type === 'added' || line.type === 'removed') {
        lineOffset++
      }

      for (const rule of RULES) {
        if (rule.matchOn === 'added' && line.type !== 'added') continue

        if (rule.pattern.test(line.content)) {
          signals.push({
            type: rule.id,
            file: file.filename,
            line: lineOffset,
            severity: rule.severity,
            snippet: line.content.trim().slice(0, 200),
            description: rule.description,
          })
        }
      }
    }
  }

  // Deduplicate by file + type + line
  const seen = new Set<string>()
  return signals.filter((s) => {
    const key = `${s.file}:${s.type}:${s.line}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

/**
 * Run evidence rules against all files in a PR.
 */
export function runAllEvidenceRules(files: PRFile[]): EvidenceSignal[] {
  return files.flatMap(runEvidenceRules)
}
