/**
 * GitHub PR operations — fetch metadata, diffs, commits.
 */

import type { Octokit } from '@octokit/rest'
import type { PRContext, PRFile, Hunk, HunkLine, CommitInfo } from '../types/pr.js'

/**
 * Parse a unified diff string into Hunk structures.
 */
export function parsePatch(patch: string): Hunk[] {
  if (!patch) return []

  const hunks: Hunk[] = []
  let currentHunk: Hunk | null = null

  for (const line of patch.split('\n')) {
    if (line.startsWith('@@')) {
      if (currentHunk) hunks.push(currentHunk)
      currentHunk = { header: line, lines: [] }
      continue
    }

    if (!currentHunk) continue

    let type: HunkLine['type']
    let content: string

    if (line.startsWith('+')) {
      type = 'added'
      content = line.slice(1)
    } else if (line.startsWith('-')) {
      type = 'removed'
      content = line.slice(1)
    } else if (line.startsWith(' ')) {
      type = 'unchanged'
      content = line.slice(1)
    } else if (line.startsWith('\\')) {
      // "\ No newline at end of file"
      continue
    } else {
      type = 'unchanged'
      content = line
    }

    currentHunk.lines.push({ content, type })
  }

  if (currentHunk) hunks.push(currentHunk)
  return hunks
}

/**
 * Parse a PR URL into owner, repo, and pull number.
 */
export function parsePrUrl(url: string): { owner: string; repo: string; pull_number: number } {
  const match = url.match(/github\.com\/([^/]+)\/([^/]+)\/pull\/(\d+)/)
  if (!match) {
    throw new Error(`Invalid PR URL: ${url}`)
  }
  return { owner: match[1], repo: match[2], pull_number: parseInt(match[3], 10) }
}

/**
 * Fetch full PR context from GitHub API.
 */
export async function fetchPRContext(
  octokit: Octokit,
  owner: string,
  repo: string,
  pull_number: number,
): Promise<PRContext> {
  // Fetch PR metadata and files in parallel
  const [prResponse, filesResponse, commitsResponse] = await Promise.all([
    octokit.pulls.get({ owner, repo, pull_number }),
    octokit.pulls.listFiles({ owner, repo, pull_number, per_page: 100 }),
    octokit.pulls.listCommits({ owner, repo, pull_number, per_page: 100 }),
  ])

  const pr = prResponse.data

  // Build file list with hunks
  const files: PRFile[] = filesResponse.data.map((f) => {
    const hunks = f.patch ? parsePatch(f.patch) : []
    return {
      filename: f.filename,
      status: f.status as PRFile['status'],
      additions: f.additions,
      deletions: f.deletions,
      patch_available: !!f.patch,
      hunks,
      raw_patch: f.patch ?? undefined,
    }
  })

  // Build commit list
  const commits: CommitInfo[] = commitsResponse.data.map((c) => ({
    sha: c.sha,
    message: c.commit.message,
    author: c.commit.author?.name ?? 'unknown',
    date: c.commit.author?.date ?? '',
  }))

  return {
    context_id: `${owner}/${repo}/pull/${pull_number}`,
    owner,
    repo,
    pull_number,
    title: pr.title,
    description: pr.body ?? '',
    author: pr.user?.login ?? 'unknown',
    base_branch: pr.base.ref,
    head_branch: pr.head.ref,
    head_sha: pr.head.sha,
    files,
    commits,
    created_at: pr.created_at,
  }
}
