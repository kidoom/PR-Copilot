import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import type { PRContext } from '../types/pr.js'
import type { ReviewRun } from '../types/review.js'
import type { RunEvent } from '../types/events.js'

function expandHome(dir: string): string {
  if (dir === '~') return os.homedir()
  if (dir.startsWith('~/') || dir.startsWith('~\\')) return path.join(os.homedir(), dir.slice(2))
  return dir
}

function encodeId(id: string): string {
  return encodeURIComponent(id)
}

function readJson<T>(file: string): T | null {
  if (!fs.existsSync(file)) return null
  return JSON.parse(fs.readFileSync(file, 'utf-8')) as T
}

function writeJson(file: string, value: unknown): void {
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`, 'utf-8')
}

export interface SessionStore {
  savePRContext(context: PRContext): void
  getPRContext(contextId: string): PRContext | null
  listPRContexts(): PRContext[]
  saveReviewRun(run: ReviewRun): void
  getReviewRun(runId: string): ReviewRun | null
  listReviewRuns(): ReviewRun[]
  appendEvent(event: RunEvent): void
  readEventsAfter(runId: string, sequence: number): RunEvent[]
}

export class JsonSessionStore implements SessionStore {
  private readonly root: string

  constructor(storageDir: string) {
    this.root = expandHome(storageDir)
    fs.mkdirSync(this.root, { recursive: true })
  }

  savePRContext(context: PRContext): void {
    writeJson(path.join(this.root, 'contexts', `${encodeId(context.context_id)}.json`), context)
  }

  getPRContext(contextId: string): PRContext | null {
    return readJson<PRContext>(path.join(this.root, 'contexts', `${encodeId(contextId)}.json`))
  }

  listPRContexts(): PRContext[] {
    const dir = path.join(this.root, 'contexts')
    if (!fs.existsSync(dir)) return []
    return fs.readdirSync(dir)
      .filter((file) => file.endsWith('.json'))
      .map((file) => readJson<PRContext>(path.join(dir, file)))
      .filter((context): context is PRContext => context !== null)
  }

  saveReviewRun(run: ReviewRun): void {
    writeJson(path.join(this.root, 'runs', `${encodeId(run.run_id)}.json`), run)
  }

  getReviewRun(runId: string): ReviewRun | null {
    return readJson<ReviewRun>(path.join(this.root, 'runs', `${encodeId(runId)}.json`))
  }

  listReviewRuns(): ReviewRun[] {
    const dir = path.join(this.root, 'runs')
    if (!fs.existsSync(dir)) return []
    return fs.readdirSync(dir)
      .filter((file) => file.endsWith('.json'))
      .map((file) => readJson<ReviewRun>(path.join(dir, file)))
      .filter((run): run is ReviewRun => run !== null)
  }

  appendEvent(event: RunEvent): void {
    const dir = path.join(this.root, 'events')
    fs.mkdirSync(dir, { recursive: true })
    fs.appendFileSync(path.join(dir, `${encodeId(event.run_id)}.jsonl`), `${JSON.stringify(event)}\n`, 'utf-8')
  }

  readEventsAfter(runId: string, sequence: number): RunEvent[] {
    const file = path.join(this.root, 'events', `${encodeId(runId)}.jsonl`)
    if (!fs.existsSync(file)) return []
    return fs.readFileSync(file, 'utf-8')
      .split('\n')
      .filter(Boolean)
      .map((line) => JSON.parse(line) as RunEvent)
      .filter((event) => event.sequence > sequence)
  }
}
