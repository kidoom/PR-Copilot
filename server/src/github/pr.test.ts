import { describe, expect, it } from 'vitest'
import { fetchPRContext, parsePatch, parsePrUrl } from './pr.js'

describe('github PR helpers', () => {
  it('parses GitHub PR URLs', () => {
    expect(parsePrUrl('https://github.com/open-multi-agent/open-multi-agent/pull/123')).toEqual({
      owner: 'open-multi-agent',
      repo: 'open-multi-agent',
      pull_number: 123,
    })
  })

  it('parses unified diff hunks', () => {
    expect(parsePatch('@@ -1,2 +1,2 @@\n const a = 1\n-old\n+new\n')).toEqual([
      {
        header: '@@ -1,2 +1,2 @@',
        lines: [
          { content: 'const a = 1', type: 'unchanged' },
          { content: 'old', type: 'removed' },
          { content: 'new', type: 'added' },
          { content: '', type: 'unchanged' },
        ],
      },
    ])
  })

  it('fetches PR context from an Octokit-compatible client', async () => {
    const octokit = {
      pulls: {
        get: async () => ({
          data: {
            title: 'Improve auth',
            body: 'Adds checks',
            user: { login: 'alice' },
            base: { ref: 'main' },
            head: { ref: 'feature', sha: 'abc123' },
            created_at: '2026-06-16T00:00:00Z',
          },
        }),
        listFiles: async () => ({
          data: [{
            filename: 'src/auth.ts',
            status: 'modified',
            additions: 1,
            deletions: 1,
            patch: '@@ -1 +1 @@\n-old\n+new',
          }],
        }),
        listCommits: async () => ({
          data: [{
            sha: 'abc123',
            commit: {
              message: 'Improve auth',
              author: { name: 'Alice', date: '2026-06-16T00:00:00Z' },
            },
          }],
        }),
      },
    }

    const context = await fetchPRContext(octokit as any, 'owner', 'repo', 7)
    expect(context.context_id).toBe('owner/repo/pull/7')
    expect(context.files[0]?.hunks[0]?.lines).toContainEqual({ content: 'new', type: 'added' })
    expect(context.commits[0]?.sha).toBe('abc123')
  })
})
