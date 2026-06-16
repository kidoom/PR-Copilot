/**
 * WebSocket event types — matches the existing Python backend RunEvent format.
 */

/** Supported event types (mirrors Python backend). */
export type RunEventType =
  | 'run.started'
  | 'message.delta'
  | 'tool.call'
  | 'tool.result'
  | 'subagent.started'
  | 'subagent.completed'
  | 'run.completed'
  | 'run.failed'
  | 'run.cancelled'

/** A single WebSocket event sent to the frontend. */
export interface RunEvent {
  event_id: string
  run_id: string
  type: RunEventType
  sequence: number
  created_at: string
  payload: Record<string, unknown>
}
