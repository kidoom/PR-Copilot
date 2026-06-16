import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { JsonSessionStore } from './session.js'
import type { PRContext } from '../types/pr.js'
import type { ReviewRun } from '../types/review.js'
import type { RunEvent } from '../types/events.js'

const tempDirs: string[] = []

function makeStore(): JsonSessionStore {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pr-copilot-store-'))
  tempDirs.push(dir)
  return new JsonSessionStore(dir)
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

describe('JsonSessionStore', () => {
  it('persists PR contexts, review runs, and event logs', () => {
    const store = makeStore()
    const context: PRContext = {
      context_id: 'owner/repo/pull/9',
      owner: 'owner',
      repo: 'repo',
      pull_number: 9,
      title: 'Test PR',
      description: '',
      author: 'alice',
      base_branch: 'main',
      head_branch: 'feature',
      head_sha: 'abc123',
      files: [],
      commits: [],
      created_at: '2026-06-16T00:00:00Z',
    }
    const run: ReviewRun = {
      run_id: 'run-9',
      context_id: context.context_id,
      status: 'completed',
      findings: [],
      created_at: '2026-06-16T00:00:00Z',
      completed_at: '2026-06-16T00:01:00Z',
    }
    const event: RunEvent = {
      event_id: 'event-1',
      run_id: run.run_id,
      type: 'run.completed',
      sequence: 1,
      created_at: '2026-06-16T00:01:00Z',
      payload: { findings: [] },
    }

    store.savePRContext(context)
    store.saveReviewRun(run)
    store.appendEvent({ ...event, sequence: 0 })
    store.appendEvent(event)

    expect(store.getPRContext(context.context_id)?.title).toBe('Test PR')
    expect(store.getReviewRun(run.run_id)?.status).toBe('completed')
    expect(store.readEventsAfter(run.run_id, 0)).toEqual([event])
  })
})
