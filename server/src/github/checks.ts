/**
 * GitHub Checks API — fetch CI/CD check runs for a PR head SHA.
 */

import type { Octokit } from '@octokit/rest'

export interface CheckRun {
  name: string
  status: string
  conclusion: string | null
  output_title: string | null
  output_summary: string | null
  html_url: string | null
}

export interface CheckSummary {
  total_count: number
  completed: number
  failed: number
  success: number
  neutral: number
  skipped: number
  pending: number
  runs: CheckRun[]
}

/**
 * Fetch check runs for a specific commit SHA.
 */
export async function fetchCheckSummary(
  octokit: Octokit,
  owner: string,
  repo: string,
  head_sha: string,
): Promise<CheckSummary> {
  const response = await octokit.checks.listForRef({
    owner,
    repo,
    ref: head_sha,
    per_page: 100,
  })

  const runs: CheckRun[] = response.data.check_runs.map((run) => ({
    name: run.name,
    status: run.status,
    conclusion: run.conclusion,
    output_title: run.output?.title ?? null,
    output_summary: run.output?.summary?.slice(0, 500) ?? null,
    html_url: run.html_url,
  }))

  const completed = runs.filter((r) => r.status === 'completed').length
  const failed = runs.filter((r) => r.conclusion === 'failure').length
  const success = runs.filter((r) => r.conclusion === 'success').length
  const neutral = runs.filter((r) => r.conclusion === 'neutral').length
  const skipped = runs.filter((r) => r.conclusion === 'skipped').length
  const pending = runs.filter((r) => r.status !== 'completed').length

  return {
    total_count: runs.length,
    completed,
    failed,
    success,
    neutral,
    skipped,
    pending,
    runs,
  }
}
