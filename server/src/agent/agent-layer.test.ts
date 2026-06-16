import { describe, expect, it } from 'vitest'
import type { TeamRunResult } from '@open-multi-agent/core'
import { createReviewAgentConfigs, REVIEW_AGENT_NAMES } from './agents.js'
import { buildReviewGoalPrompt, PR_REVIEW_COORDINATOR_PROMPT } from './prompts.js'
import { buildStaticReviewContext, extractFindings, summarizePRContext } from './run.js'
import { buildReviewPackage } from '../review/package.js'
import type { PRContext } from '../types/pr.js'

const fakeAdapter = {
  chat: async () => ({ content: 'ok' }),
  stream: async function* () {},
}

const prContext: PRContext = {
  context_id: 'owner/repo/pull/1',
  owner: 'owner',
  repo: 'repo',
  pull_number: 1,
  title: 'Improve auth',
  description: 'Adds login middleware',
  author: 'alice',
  base_branch: 'main',
  head_branch: 'feature/auth',
  head_sha: 'abc123',
  created_at: '2026-06-16T00:00:00Z',
  commits: [],
  files: [{
    filename: 'src/auth.ts',
    status: 'modified',
    additions: 2,
    deletions: 0,
    patch_available: true,
    hunks: [{
      header: '@@ -1 +1 @@',
      lines: [
        { type: 'added', content: 'const password = "super-secret-value"' },
        { type: 'added', content: 'await saveUser(input)' },
      ],
    }],
  }],
}

describe('PR review agent layer', () => {
  it('defines specialized PR review agents with repo tools and compression', () => {
    const staticReview = buildStaticReviewContext(prContext)
    const reviewPackage = buildReviewPackage({
      prContext,
      classifications: staticReview.classifications,
      evidence: staticReview.evidence,
      contextTasks: staticReview.contextTasks,
    })
    const agents = createReviewAgentConfigs({
      model: 'test-model',
      apiKey: 'test-key',
      baseURL: 'https://example.test/v1',
      adapter: fakeAdapter as any,
      customTools: () => [],
      agentScopes: reviewPackage.agentScopes,
    })

    expect(agents.map((agent) => agent.name)).toEqual([...REVIEW_AGENT_NAMES])
    expect(agents.find((agent) => agent.name === 'security-reviewer')?.systemPrompt).toContain('security-focused')
    expect(agents.every((agent) => agent.tools?.includes('read_repo_file'))).toBe(true)
    expect(agents.every((agent) => agent.contextStrategy?.type === 'custom')).toBe(true)
  })

  it('builds coordinator prompt and review goal with static review context', () => {
    const staticReview = buildStaticReviewContext(prContext)
    const reviewPackage = buildReviewPackage({
      prContext,
      classifications: staticReview.classifications,
      evidence: staticReview.evidence,
      contextTasks: staticReview.contextTasks,
    })
    const prompt = buildReviewGoalPrompt({
      goal: 'Review this PR',
      prSummary: summarizePRContext(prContext),
      reviewPackage,
    })

    expect(PR_REVIEW_COORDINATOR_PROMPT).toContain('dependency-aware DAG')
    expect(PR_REVIEW_COORDINATOR_PROMPT).toContain('JSON array of findings')
    expect(prompt).toContain('owner/repo#1')
    expect(prompt).toContain('Token-aware review package')
    expect(prompt).toContain('hardcoded_secret')
    expect(prompt).toContain('agentScopes')
    expect(staticReview.contextTasks.map((task) => task.type)).toContain('security-review')
  })

  it('extracts structured findings from coordinator synthesis JSON', () => {
    const result = {
      success: true,
      agentResults: new Map([[
        'coordinator',
        {
          success: true,
          output: '```json\n[{"file":"src/auth.ts","line":1,"severity":"high","category":"security","title":"Hardcoded password","description":"A password is committed in source.","evidence":[{"file":"src/auth.ts","line":1,"snippet":"password"}],"suggestion":"Load it from a secret manager."}]\n```',
          messages: [],
          tokenUsage: { input: 0, output: 0, total: 0 },
          toolCalls: [],
        },
      ]]),
      totalTokenUsage: { input: 0, output: 0, total: 0 },
    } as unknown as TeamRunResult

    expect(extractFindings(result)).toEqual([{
      file: 'src/auth.ts',
      line: 1,
      severity: 'high',
      category: 'security',
      title: 'Hardcoded password',
      description: 'A password is committed in source.',
      evidence: [{ file: 'src/auth.ts', line: 1, snippet: 'password', tool: undefined }],
      suggestion: 'Load it from a secret manager.',
    }])
  })

  it('skips coordinator task-plan JSON and extracts later findings JSON', () => {
    const result = {
      success: true,
      agentResults: new Map([[
        'coordinator',
        {
          success: true,
          output: '```json\n[{"title":"review","description":"task","assignee":"code-quality-reviewer","dependsOn":[]}]\n```\n\n```json\n[{"file":"src/example.ts","line":1,"severity":"info","category":"code-quality","title":"Mock finding","description":"Pipeline finding.","evidence":[{"file":"src/example.ts"}]}]\n```',
          messages: [],
          tokenUsage: { input: 0, output: 0 },
          toolCalls: [],
        },
      ]]),
      totalTokenUsage: { input: 0, output: 0 },
    } as unknown as TeamRunResult

    expect(extractFindings(result)).toHaveLength(1)
  })
})
