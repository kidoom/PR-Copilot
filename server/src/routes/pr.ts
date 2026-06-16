import { Router } from 'express'
import type { Octokit } from '@octokit/rest'
import { fetchPRContext, parsePrUrl } from '../github/pr.js'
import type { PRContext } from '../types/pr.js'
import type { SessionStore } from '../store/session.js'
import { classifyFiles } from '../review/intake.js'

function languageFor(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase()
  const map: Record<string, string> = {
    ts: 'TypeScript',
    tsx: 'TypeScript',
    js: 'JavaScript',
    jsx: 'JavaScript',
    py: 'Python',
    md: 'Markdown',
    json: 'JSON',
    yml: 'YAML',
    yaml: 'YAML',
  }
  return ext ? map[ext] ?? ext.toUpperCase() : 'Unknown'
}

function normalizePatchLineType(type: string): 'added' | 'removed' | 'context' {
  if (type === 'added' || type === 'removed') return type
  return 'context'
}

function contextOr404(contexts: Map<string, PRContext>, store: SessionStore, contextId: string): PRContext | null {
  const context = contexts.get(contextId) ?? store.getPRContext(contextId)
  if (context) contexts.set(context.context_id, context)
  return context
}

export function createPrRouter(octokit: Octokit, store: SessionStore): Router {
  const router = Router()

  const contexts = new Map<string, PRContext>()

  router.post('/api/pr/context', async (req, res) => {
    try {
      const { pr_url, owner, repo, pull_number } = req.body

      let prOwner = owner
      let prRepo = repo
      let prNumber = pull_number

      if (pr_url) {
        const parsed = parsePrUrl(pr_url)
        prOwner = parsed.owner
        prRepo = parsed.repo
        prNumber = parsed.pull_number
      }

      if (!prOwner || !prRepo || !prNumber) {
        res.status(400).json({ error: 'Missing pr_url or owner/repo/pull_number' })
        return
      }

      const context = await fetchPRContext(octokit, prOwner, prRepo, prNumber)
      contexts.set(context.context_id, context)
      store.savePRContext(context)

      res.json(context)
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      res.status(500).json({ error: message })
    }
  })

  router.get('/api/pr/context/:id', (req, res) => {
    const context = contextOr404(contexts, store, req.params.id)
    if (!context) {
      res.status(404).json({ error: 'PR context not found' })
      return
    }
    res.json(context)
  })

  router.get('/api/pr/context/:id/files/:filename/patch', (req, res) => {
    const context = contextOr404(contexts, store, req.params.id)
    if (!context) {
      res.status(404).json({ error: 'PR context not found' })
      return
    }

    const filename = decodeURIComponent(req.params.filename)
    const file = context.files.find((item) => item.filename === filename)
    if (!file) {
      res.status(404).json({ error: 'File not found in PR' })
      return
    }

    res.json({
      context_id: context.context_id,
      filename: file.filename,
      patch_available: file.patch_available,
      is_binary: !file.patch_available,
      parse_error: null,
      truncated: false,
      hunks: file.hunks.map((hunk) => ({
        header: hunk.header,
        old_start: 0,
        old_lines: 0,
        new_start: 0,
        new_lines: 0,
        lines: hunk.lines.map((line) => ({
          type: normalizePatchLineType(line.type),
          content: line.content,
          old_line: line.old_line ?? null,
          new_line: line.new_line ?? null,
        })),
      })),
    })
  })

  router.post('/api/review/intake', (req, res) => {
    const contextId = req.body?.context_id
    const context = typeof contextId === 'string' ? contextOr404(contexts, store, contextId) : null
    if (!context) {
      res.status(404).json({ error: 'PR context not found' })
      return
    }

    const classifications = classifyFiles(context.files.map((file) => file.filename))
    const categories = new Map(classifications.map((item) => [item.filename, item.category]))
    const distribution = classifications.reduce<Record<string, number>>((acc, item) => {
      acc[item.category] = (acc[item.category] ?? 0) + 1
      return acc
    }, {})
    const languageDistribution = context.files.reduce<Record<string, number>>((acc, file) => {
      const language = languageFor(file.filename)
      acc[language] = (acc[language] ?? 0) + 1
      return acc
    }, {})
    const totalChanges = context.files.reduce((sum, file) => sum + file.additions + file.deletions, 0)
    const topDirectories = [...context.files.reduce<Map<string, number>>((acc, file) => {
      const directory = file.filename.includes('/') ? file.filename.split('/')[0] : '.'
      acc.set(directory, (acc.get(directory) ?? 0) + 1)
      return acc
    }, new Map())].map(([directory, file_count]) => ({ directory, file_count }))

    res.json({
      context_id: context.context_id,
      size: totalChanges < 100 ? 'small' : totalChanges < 500 ? 'medium' : 'large',
      change_type: distribution.source ? 'source' : distribution.test ? 'test' : distribution.config ? 'config' : distribution.doc ? 'docs' : 'mixed',
      docs_only: classifications.length > 0 && classifications.every((item) => item.category === 'doc'),
      source_without_tests: classifications.some((item) => item.category === 'source') && !classifications.some((item) => item.category === 'test'),
      has_high_risk_paths: context.files.some((file) => /auth|security|permission|token|secret/i.test(file.filename)),
      language_distribution: languageDistribution,
      file_type_distribution: distribution,
      top_directories: topDirectories,
      notable_signals: [],
    })
  })

  router.get('/api/pr/context/:id/runs', (req, res) => {
    const runs = store.listReviewRuns()
      .filter((run) => run.context_id === req.params.id)
      .sort((a, b) => b.created_at.localeCompare(a.created_at))
      .map((run) => ({
        run_id: run.run_id,
        lifecycle: run.status,
        created_at: run.created_at,
        completed_at: run.completed_at,
        finding_count: run.findings?.length ?? 0,
        status: run.status,
        summary: run.error,
      }))
    res.json({ runs })
  })

  router.get('/api/pr/sessions', (_req, res) => {
    const runs = store.listReviewRuns()
    const sessions = store.listPRContexts().map((context) => {
      const contextRuns = runs
        .filter((run) => run.context_id === context.context_id)
        .sort((a, b) => b.created_at.localeCompare(a.created_at))
      const latest = contextRuns[0]
      return {
        pr_session_id: context.context_id,
        owner: context.owner,
        repo: context.repo,
        pull_number: context.pull_number,
        updated_at: latest?.created_at ?? context.created_at,
        run_count: contextRuns.length,
        latest_run_id: latest?.run_id,
        latest_lifecycle: latest?.status,
        latest_completed_at: latest?.completed_at,
        latest_finding_count: latest?.findings?.length ?? 0,
        latest_status: latest?.status,
        latest_summary: latest?.error,
      }
    })
    res.json({ sessions })
  })

  return router
}
