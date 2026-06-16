import { describe, expect, it } from 'vitest'
import type { PRContext, PRFile } from '../types/pr.js'
import { classifyFiles } from './intake.js'
import { scorePriorities } from './priority.js'
import { runAllEvidenceRules } from './evidence.js'
import { generateContextTasks } from './context-plan.js'
import { buildReviewPackage } from './package.js'

function makeFile(filename: string, content: string, additions = 1): PRFile {
  return {
    filename,
    status: 'modified',
    additions,
    deletions: 0,
    patch_available: true,
    hunks: [{
      header: '@@ -1 +1 @@',
      lines: [
        { type: 'added', content, new_line: 1 },
        ...Array.from({ length: 40 }, (_, index) => ({
          type: 'added' as const,
          content: `extra changed line ${index}`,
          new_line: index + 2,
        })),
      ],
    }],
  }
}

describe('review package builder', () => {
  it('builds compact package files and reviewer-specific scopes', () => {
    const files = [
      makeFile('server/src/auth/login.ts', 'const password = "super-secret-value"', 80),
      makeFile('server/src/app.ts', 'await startServer()', 20),
      makeFile('server/src/app.test.ts', 'expect(result).toBeTruthy()', 10),
      makeFile('.github/workflows/ci.yml', 'node-version: 20', 5),
      ...Array.from({ length: 45 }, (_, index) => makeFile(`docs/page-${index}.md`, `doc ${index}`)),
    ]
    const prContext: PRContext = {
      context_id: 'owner/repo/pull/1',
      owner: 'owner',
      repo: 'repo',
      pull_number: 1,
      title: 'Scoped review',
      description: '',
      author: 'alice',
      base_branch: 'main',
      head_branch: 'feature/scoped',
      head_sha: 'abc123',
      created_at: '2026-06-16T00:00:00Z',
      commits: [],
      files,
    }

    const classifications = scorePriorities(
      classifyFiles(files.map((file) => file.filename)),
      new Map(files.map((file) => [file.filename, { additions: file.additions, deletions: file.deletions }])),
    )
    const evidence = runAllEvidenceRules(files)
    const contextTasks = generateContextTasks(files, classifications, evidence)
    const reviewPackage = buildReviewPackage({ prContext, classifications, evidence, contextTasks })

    expect(reviewPackage.files.length).toBeLessThanOrEqual(40)
    expect(reviewPackage.omittedFiles.length).toBeGreaterThan(0)
    expect(reviewPackage.files.find((file) => file.filename === 'server/src/auth/login.ts')?.compact_patch[0]?.lines.length)
      .toBeLessThanOrEqual(24)

    expect(reviewPackage.agentScopes['security-reviewer'].allowedFiles).toContain('server/src/auth/login.ts')
    expect(reviewPackage.agentScopes['config-reviewer'].allowedFiles).toContain('.github/workflows/ci.yml')
    expect(reviewPackage.agentScopes['test-context-analyzer'].allowedFiles).toContain('server/src/app.test.ts')
    expect(reviewPackage.agentScopes['security-reviewer'].maxToolTokens).toBeLessThan(500_000)
  })
})

