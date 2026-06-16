import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import type { IncomingMessage } from 'node:http'
import type { WebSocket, WebSocketServer } from 'ws'
import type { RunEvent } from '../types/events.js'
import { reviewState } from '../routes/review.js'
import { JsonSessionStore } from '../store/session.js'

function expandHome(dir: string): string {
  if (dir === '~') return os.homedir()
  if (dir.startsWith('~/') || dir.startsWith('~\\')) return path.join(os.homedir(), dir.slice(2))
  return dir
}

export function createJsonlEventStore(storageDir: string) {
  const eventDir = path.join(expandHome(storageDir), 'events')
  fs.mkdirSync(eventDir, { recursive: true })

  function fileFor(runId: string): string {
    return path.join(eventDir, `${encodeURIComponent(runId)}.jsonl`)
  }

  return {
    append(event: RunEvent): void {
      fs.appendFileSync(fileFor(event.run_id), `${JSON.stringify(event)}\n`, 'utf-8')
    },
    readAfter(runId: string, sequence: number): RunEvent[] {
      const file = fileFor(runId)
      if (!fs.existsSync(file)) return []
      return fs.readFileSync(file, 'utf-8')
        .split('\n')
        .filter(Boolean)
        .map((line) => JSON.parse(line) as RunEvent)
        .filter((event) => event.sequence > sequence)
    },
  }
}

function parseReviewRunSocketUrl(req: IncomingMessage): { runId: string; afterSequence: number } | null {
  const url = new URL(req.url ?? '', 'http://localhost')
  const match = url.pathname.match(/^\/ws\/review-runs\/([^/]+)$/)
  if (!match) return null
  const afterSequence = Number.parseInt(url.searchParams.get('after_sequence') ?? '-1', 10)
  return {
    runId: decodeURIComponent(match[1]),
    afterSequence: Number.isFinite(afterSequence) ? afterSequence : -1,
  }
}

export function setupReviewRunWebSockets(wss: WebSocketServer, storageDir: string): void {
  reviewState.store ??= new JsonSessionStore(storageDir)

  wss.on('connection', (ws: WebSocket, req: IncomingMessage) => {
    const parsed = parseReviewRunSocketUrl(req)
    if (!parsed) {
      ws.close(1008, 'Unsupported WebSocket path')
      return
    }

    const subscriber = {
      send: (data: string) => {
        if (ws.readyState === ws.OPEN) ws.send(data)
      },
    }

    for (const event of reviewState.replayEvents(parsed.runId, parsed.afterSequence)) {
      subscriber.send(JSON.stringify(event))
    }

    reviewState.subscribe(parsed.runId, subscriber)
    ws.on('close', () => reviewState.unsubscribe(parsed.runId, subscriber))
    ws.on('error', () => reviewState.unsubscribe(parsed.runId, subscriber))
  })
}
