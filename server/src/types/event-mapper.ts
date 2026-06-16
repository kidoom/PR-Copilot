/**
 * Maps OMA OrchestratorEvent to PR-Copilot RunEvent format.
 */

import { randomUUID } from 'node:crypto'
import type { OrchestratorEvent, StreamEvent } from '@open-multi-agent/core'
import type { RunEvent, RunEventType } from './events.js'

let sequenceCounter = 0

function nextSequence(): number {
  return sequenceCounter++
}

function makeEvent(runId: string, type: RunEventType, payload: Record<string, unknown>): RunEvent {
  return {
    event_id: randomUUID().slice(0, 16),
    run_id: runId,
    type,
    sequence: nextSequence(),
    created_at: new Date().toISOString(),
    payload,
  }
}

/**
 * Map an OMA OrchestratorEvent to a PR-Copilot RunEvent.
 */
export function mapOrchestratorEvent(runId: string, event: OrchestratorEvent): RunEvent | null {
  switch (event.type) {
    case 'agent_start':
      return makeEvent(runId, 'subagent.started', {
        agent: event.agent,
        task: event.task,
        data: event.data,
      })
    case 'agent_complete':
      return makeEvent(runId, 'subagent.completed', {
        agent: event.agent,
        task: event.task,
        data: event.data,
      })
    case 'task_start':
      return makeEvent(runId, 'tool.call', {
        name: 'task_dispatch',
        task: event.task,
        agent: event.agent,
        input: event.data,
      })
    case 'task_complete':
      return makeEvent(runId, 'tool.result', {
        name: 'task_complete',
        task: event.task,
        agent: event.agent,
        output: event.data,
      })
    case 'error':
      return makeEvent(runId, 'tool.result', {
        name: 'task_error',
        task: event.task,
        agent: event.agent,
        output: event.data,
        is_error: true,
      })
    default:
      return null
  }
}

/**
 * Map an OMA StreamEvent (from onAgentStream) to a PR-Copilot RunEvent.
 */
export function mapStreamEvent(runId: string, agentName: string, event: StreamEvent): RunEvent | null {
  switch (event.type) {
    case 'text':
      return makeEvent(runId, 'message.delta', {
        agent: agentName,
        delta: event.data,
      })
    case 'tool_use':
      return makeEvent(runId, 'tool.call', {
        agent: agentName,
        input: event.data,
      })
    case 'tool_result':
      return makeEvent(runId, 'tool.result', {
        agent: agentName,
        output: event.data,
      })
    default:
      return null
  }
}

/** Reset sequence counter (for testing). */
export function resetSequence(): void {
  sequenceCounter = 0
}
