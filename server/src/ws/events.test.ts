import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { RunEvent } from '../types/events.js'
import { reviewState } from '../routes/review.js'
import { createJsonlEventStore } from './events.js'

const tempDirs: string[] = []

function makeTempDir(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pr-copilot-ws-'))
  tempDirs.push(dir)
  return dir
}

function event(sequence: number): RunEvent {
  return {
    event_id: `event-${sequence}`,
    run_id: 'run-1',
    type: sequence === 2 ? 'run.completed' : 'message.delta',
    sequence,
    created_at: '2026-06-16T00:00:00Z',
    payload: sequence === 2 ? { findings: [] } : { delta: `part-${sequence}` },
  }
}

beforeEach(() => {
  reviewState.runs.clear()
  reviewState.eventQueues.clear()
  reviewState.abortControllers.clear()
  reviewState.subscribers.clear()
  reviewState.eventStore = null
})

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

describe('review run event streaming state', () => {
  it('broadcasts pushed events to subscribers', () => {
    const sent: string[] = []
    reviewState.eventQueues.set('run-1', [])
    reviewState.subscribe('run-1', { send: (data) => sent.push(data) })

    reviewState.pushEvent(event(0))

    expect(sent).toHaveLength(1)
    expect(JSON.parse(sent[0])).toMatchObject({ run_id: 'run-1', sequence: 0 })
    expect(reviewState.eventQueues.get('run-1')).toHaveLength(1)
  })

  it('persists events and replays only events after the requested sequence', () => {
    const store = createJsonlEventStore(makeTempDir())
    store.append(event(0))
    store.append(event(1))
    store.append(event(2))

    expect(store.readAfter('run-1', 0).map((item) => item.sequence)).toEqual([1, 2])
  })

  it('deduplicates persisted and in-memory replay events', () => {
    const store = createJsonlEventStore(makeTempDir())
    const persisted = event(1)
    store.append(persisted)
    reviewState.eventStore = store
    reviewState.eventQueues.set('run-1', [persisted, event(2)])

    expect(reviewState.replayEvents('run-1', 0).map((item) => item.sequence)).toEqual([1, 2])
  })
})
