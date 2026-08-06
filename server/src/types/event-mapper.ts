/**
 * Maps OMA OrchestratorEvent to PR-Copilot RunEvent format.
 */

import { randomUUID } from 'node:crypto'
import type { OrchestratorEvent, StreamEvent } from '@open-multi-agent/core'
import type { RunEvent, RunEventType } from './events.js'

let sequenceCounter = 0
const toolUseIndex = new Map<string, { name: string; input: unknown }>()

function nextSequence(): number {
  return sequenceCounter++
}

function toolUseKey(runId: string, agentName: string, id: string): string {
  return `${runId}:${agentName}:${id}`
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

function stringifySummary(value: unknown, max = 500): string {
  const text = typeof value === 'string' ? value : JSON.stringify(value)
  return text.length > max ? `${text.slice(0, max)}...` : text
}

function tryParseJsonObject(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>
  if (typeof value !== 'string') return null
  try {
    const parsed = JSON.parse(value)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null
  } catch {
    return null
  }
}

function summarizeToolOutput(data: any, max = 500): string {
  const raw = data?.content ?? data?.data ?? data?.output ?? data
  const parsed = tryParseJsonObject(raw)
  if (parsed) {
    const error = parsed.error
    const status = parsed.status
    const path = parsed.path ?? parsed.file ?? parsed.path_scope
    if (typeof error === 'string') {
      return stringifySummary({
        error,
        ...(typeof status === 'string' ? { status } : {}),
        ...(typeof path === 'string' ? { path } : {}),
      }, max)
    }
  }
  return stringifySummary(raw, max)
}

function toolUsePayload(runId: string, agentName: string, data: any): Record<string, unknown> {
  const id = String(data?.id ?? '')
  const name = String(data?.name ?? 'tool')
  if (id) {
    toolUseIndex.set(toolUseKey(runId, agentName, id), { name, input: data?.input ?? data })
  }

  return {
    agent_kind: agentName === 'coordinator' ? 'coordinator' : 'subagent',
    agent_type: agentName,
    task_id: '',
    child_session_id: '',
    tool_name: name,
    tool_use_id: id,
    input_summary: data?.input ?? data,
  }
}

function toolResultPayload(runId: string, agentName: string, data: any): Record<string, unknown> {
  const id = String(data?.tool_use_id ?? data?.id ?? '')
  const indexed = id ? toolUseIndex.get(toolUseKey(runId, agentName, id)) : undefined
  const toolName = data?.name ?? data?.tool_name ?? indexed?.name ?? 'tool_result'

  return {
    agent_kind: agentName === 'coordinator' ? 'coordinator' : 'subagent',
    agent_type: agentName,
    task_id: '',
    child_session_id: '',
    tool_name: toolName,
    tool_use_id: id,
    input_summary: indexed?.input,
    output_summary: summarizeToolOutput(data),
    is_error: data?.is_error === true || data?.isError === true,
  }
}

/**
 * Map an OMA OrchestratorEvent to a PR-Copilot RunEvent.
 */
export function mapOrchestratorEvent(runId: string, event: OrchestratorEvent): RunEvent | null {
  switch (event.type) {
    case 'agent_start':
      return makeEvent(runId, 'subagent.started', {
        task_id: event.task ?? '',
        task_type: event.task ?? '',
        agent_type: event.agent ?? '',
        child_session_id: '',
        status: 'running',
        data: event.data,
      })
    case 'agent_complete':
      return makeEvent(runId, 'subagent.completed', {
        task_id: event.task ?? '',
        task_type: event.task ?? '',
        agent_type: event.agent ?? '',
        child_session_id: '',
        status: 'completed',
        data: event.data,
      })
    case 'task_start':
      return makeEvent(runId, 'tool.call', {
        agent_kind: 'coordinator',
        agent_type: event.agent ?? 'coordinator',
        task_id: event.task ?? '',
        child_session_id: '',
        tool_name: 'task_dispatch',
        tool_use_id: event.task ?? '',
        input_summary: event.data,
      })
    case 'task_complete':
      return makeEvent(runId, 'tool.result', {
        agent_kind: 'coordinator',
        agent_type: event.agent ?? 'coordinator',
        task_id: event.task ?? '',
        child_session_id: '',
        tool_name: 'task_complete',
        tool_use_id: event.task ?? '',
        output_summary: event.data,
      })
    case 'error':
      return makeEvent(runId, 'tool.result', {
        agent_kind: 'coordinator',
        agent_type: event.agent ?? 'coordinator',
        task_id: event.task ?? '',
        child_session_id: '',
        tool_name: 'task_error',
        tool_use_id: event.task ?? '',
        output_summary: event.data,
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
        agent_type: agentName,
        text: String(event.data ?? ''),
      })
    case 'reasoning':
      return makeEvent(runId, 'message.delta', {
        agent_type: agentName,
        text: String(event.data ?? ''),
      })
    case 'tool_use':
      return makeEvent(runId, 'tool.call', toolUsePayload(runId, agentName, event.data))
    case 'tool_result':
      return makeEvent(runId, 'tool.result', toolResultPayload(runId, agentName, event.data))
    default:
      return null
  }
}

/** Reset sequence counter (for testing). */
export function resetSequence(): void {
  sequenceCounter = 0
  toolUseIndex.clear()
}
