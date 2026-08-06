import { Router } from 'express'
import { randomUUID } from 'node:crypto'
import type { Response } from 'express'
import type { ReviewRun, ReviewFinding } from '../types/review.js'
import type { RunEvent } from '../types/events.js'
import type { PRContext } from '../types/pr.js'
import type { ServerConfig } from '../config.js'
import type { SessionStore } from '../store/session.js'
import { runReview } from '../agent/run.js'

// Shared state used by SSE subscriptions and agent orchestration.
export const reviewState = {
  runs: new Map<string, ReviewRun>(),
  eventQueues: new Map<string, RunEvent[]>(),
  abortControllers: new Map<string, AbortController>(),
  subscribers: new Map<string, Set<{ send: (data: string) => void }>>(),
  store: null as null | SessionStore,
  eventStore: null as null | {
    append: (event: RunEvent) => void
    readAfter: (runId: string, sequence: number) => RunEvent[]
  },

  pushEvent(event: RunEvent): void {
    const queue = this.eventQueues.get(event.run_id)
    if (queue) queue.push(event)
    this.store?.appendEvent(event)
    this.eventStore?.append(event)
    const subscribers = this.subscribers.get(event.run_id)
    if (subscribers) {
      const payload = JSON.stringify(event)
      for (const subscriber of subscribers) {
        subscriber.send(payload)
      }
    }
  },

  subscribe(runId: string, subscriber: { send: (data: string) => void }): void {
    const subscribers = this.subscribers.get(runId) ?? new Set()
    subscribers.add(subscriber)
    this.subscribers.set(runId, subscribers)
  },

  unsubscribe(runId: string, subscriber: { send: (data: string) => void }): void {
    const subscribers = this.subscribers.get(runId)
    if (!subscribers) return
    subscribers.delete(subscriber)
    if (subscribers.size === 0) this.subscribers.delete(runId)
  },

  replayEvents(runId: string, afterSequence: number): RunEvent[] {
    const inMemory = this.eventQueues.get(runId) ?? []
    const persisted = [
      ...(this.store?.readEventsAfter(runId, afterSequence) ?? []),
      ...(this.eventStore?.readAfter(runId, afterSequence) ?? []),
    ]
    const byEventId = new Map<string, RunEvent>()
    for (const event of [...persisted, ...inMemory]) {
      if (event.sequence > afterSequence) byEventId.set(event.event_id, event)
    }
    return [...byEventId.values()].sort((a, b) => a.sequence - b.sequence)
  },

  nextSequence(runId: string): number {
    const events = this.replayEvents(runId, -1)
    return events.length === 0 ? 0 : Math.max(...events.map((event) => event.sequence)) + 1
  },

  completeRun(runId: string, findings: ReviewFinding[], tasks?: unknown[]): void {
    const run = this.runs.get(runId)
    if (run) {
      run.status = 'completed'
      run.findings = findings
      run.tasks = tasks
      run.completed_at = new Date().toISOString()
      this.store?.saveReviewRun(run)
    }
  },

  failRun(runId: string, error: string): void {
    const run = this.runs.get(runId)
    if (run) {
      run.status = 'failed'
      run.error = error
      run.completed_at = new Date().toISOString()
      this.store?.saveReviewRun(run)
    }
  },
}

function writeSseEvent(res: Response, event: RunEvent): void {
  res.write(`id: ${event.sequence}\n`)
  res.write(`data: ${JSON.stringify(event).replace(/\n/g, '\\n')}\n\n`)
}

export function createReviewRouter(config: ServerConfig, repoRoot: string, store: SessionStore): Router {
  const router = Router()
  reviewState.store = store

  router.post('/api/review/runs', async (req, res) => {
    try {
      const { context_id, pr_context, goal, planOnly } = req.body as {
        context_id?: string
        pr_context?: PRContext
        goal?: string
        planOnly?: boolean
      }

      if (!context_id) {
        res.status(400).json({ detail: 'Missing context_id' })
        return
      }
      const prContext = pr_context ?? store.getPRContext(context_id)
      if (!prContext) {
        res.status(404).json({ detail: 'PR context not found' })
        return
      }
      if (pr_context) store.savePRContext(pr_context)

      const runId = randomUUID()
      const run: ReviewRun = {
        run_id: runId,
        context_id,
        status: 'queued',
        created_at: new Date().toISOString(),
      }

      reviewState.runs.set(runId, run)
      store.saveReviewRun(run)
      reviewState.eventQueues.set(runId, [])
      reviewState.abortControllers.set(runId, new AbortController())

      const controller = reviewState.abortControllers.get(runId)
      queueMicrotask(() => {
        const current = reviewState.runs.get(runId)
        if (!current || current.status === 'cancelled') return
        current.status = 'running'
        store.saveReviewRun(current)

        void runReview({
          runId,
          contextId: context_id,
          prContext,
          goal,
          planOnly,
          config,
          repoRoot,
          abortSignal: controller?.signal,
          onEvent: (event) => {
            if (reviewState.runs.get(runId)?.status === 'cancelled' && event.type !== 'run.cancelled') return
            reviewState.pushEvent(event)
          },
        })
          .then((result) => {
            if (reviewState.runs.get(runId)?.status === 'cancelled') return
            reviewState.completeRun(runId, result.findings, [...result.tasks])
          })
          .catch((error) => {
            if (reviewState.runs.get(runId)?.status === 'cancelled') return
            reviewState.failRun(runId, error instanceof Error ? error.message : String(error))
          })
      })

      res.json({ run_id: runId })
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      res.status(500).json({ detail: message })
    }
  })

  router.get('/api/review/runs/:id', (req, res) => {
    const run = reviewState.runs.get(req.params.id) ?? store.getReviewRun(req.params.id)
    if (!run) {
      res.status(404).json({ detail: 'Run not found' })
      return
    }
    reviewState.runs.set(run.run_id, run)
    res.json(run)
  })

  router.get('/api/review/runs/:id/events', (req, res) => {
    const runId = req.params.id
    const requestedAfter = Number.parseInt(String(req.query.after_sequence ?? '-1'), 10)
    const lastEventId = Number.parseInt(String(req.headers['last-event-id'] ?? ''), 10)
    const afterSequence = Number.isFinite(lastEventId)
      ? lastEventId
      : Number.isFinite(requestedAfter) ? requestedAfter : -1

    res.setHeader('Content-Type', 'text/event-stream; charset=utf-8')
    res.setHeader('Cache-Control', 'no-cache, no-transform')
    res.setHeader('Connection', 'keep-alive')
    res.setHeader('X-Accel-Buffering', 'no')
    res.flushHeaders?.()

    for (const event of reviewState.replayEvents(runId, afterSequence)) {
      writeSseEvent(res, event)
    }

    const subscriber = {
      send: (data: string) => {
        const event = JSON.parse(data) as RunEvent
        writeSseEvent(res, event)
      },
    }

    reviewState.subscribe(runId, subscriber)
    const heartbeat = setInterval(() => res.write(': keep-alive\n\n'), 15_000)
    req.on('close', () => {
      clearInterval(heartbeat)
      reviewState.unsubscribe(runId, subscriber)
      res.end()
    })
  })

  router.post('/api/review/runs/:id/cancel', (req, res) => {
    const run = reviewState.runs.get(req.params.id)
    if (!run) {
      res.status(404).json({ detail: 'Run not found' })
      return
    }

    const controller = reviewState.abortControllers.get(req.params.id)
    controller?.abort()
    run.status = 'cancelled'
    run.completed_at = new Date().toISOString()
    store.saveReviewRun(run)
    reviewState.pushEvent({
      event_id: randomUUID().slice(0, 16),
      run_id: run.run_id,
      type: 'run.cancelled',
      sequence: reviewState.nextSequence(run.run_id),
      created_at: new Date().toISOString(),
      payload: { status: 'cancelled' },
    })
    res.json({ run_id: run.run_id, status: 'cancelled' })
  })

  return router
}
