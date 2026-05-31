import { useEffect, useRef, useState } from "react"
import {
  Play,
  Wrench,
  CheckCircle,
  XCircle,
  Bot,
  AlertTriangle,
  Loader2,
} from "lucide-react"
import type {
  ReviewRunEvent,
  ToolEventPayload,
  SubagentEventPayload,
  MessageDeltaPayload,
} from "@/types"

interface TerminalStreamProps {
  events: ReviewRunEvent[]
  subagentTotal: number
  subagentCompleted: number
  tokenUsage: { input: number; output: number }
}

function EventLine({ event }: { event: ReviewRunEvent }) {
  const t = event.type
  const p = event.payload

  if (t === "run.started") {
    return (
      <div className="flex items-center gap-2 py-1 text-green-400">
        <Play className="h-3.5 w-3.5 shrink-0" />
        <span className="font-mono text-xs">Review started</span>
      </div>
    )
  }

  if (t === "tool.call") {
    const tool = p as unknown as ToolEventPayload
    return (
      <div className="flex items-center gap-2 py-1 text-blue-400">
        <Wrench className="h-3.5 w-3.5 shrink-0" />
        <span className="font-mono text-xs">
          {tool.tool_name}
          {tool.agent_kind === "subagent" && tool.agent_type && (
            <span className="ml-1 text-blue-300/60">
              [{tool.agent_type}]
            </span>
          )}
        </span>
      </div>
    )
  }

  if (t === "tool.result") {
    const tool = p as unknown as ToolEventPayload
    return (
      <div className="flex items-center gap-2 py-1 text-slate-400">
        {tool.is_error ? (
          <XCircle className="h-3.5 w-3.5 shrink-0 text-red-400" />
        ) : (
          <CheckCircle className="h-3.5 w-3.5 shrink-0" />
        )}
        <span className="font-mono text-xs">
          {tool.tool_name}
          {tool.is_error && " (error)"}
        </span>
      </div>
    )
  }

  if (t === "subagent.started") {
    const sub = p as unknown as SubagentEventPayload
    return (
      <div className="flex items-center gap-2 py-1 text-yellow-400">
        <Bot className="h-3.5 w-3.5 shrink-0" />
        <span className="font-mono text-xs">
          [{sub.agent_type}] started
          {sub.task_type && (
            <span className="ml-1 text-yellow-300/60">
              ({sub.task_type})
            </span>
          )}
        </span>
      </div>
    )
  }

  if (t === "subagent.completed") {
    const sub = p as unknown as SubagentEventPayload
    const status = sub.status || "unknown"
    const color =
      status === "valid"
        ? "text-green-400"
        : status === "error"
          ? "text-red-400"
          : "text-yellow-400"
    return (
      <div className={`flex items-center gap-2 py-1 ${color}`}>
        <Bot className="h-3.5 w-3.5 shrink-0" />
        <span className="font-mono text-xs">
          [{sub.agent_type}] {status}
          {sub.stopped_by_max_steps && " (max steps)"}
        </span>
      </div>
    )
  }

  if (t === "message.delta") {
    const delta = p as unknown as MessageDeltaPayload
    return (
      <div className="py-0.5 pl-5">
        <span className="font-mono text-xs text-slate-200 whitespace-pre-wrap">
          {delta.text}
        </span>
      </div>
    )
  }

  if (t === "run.completed") {
    return (
      <div className="flex items-center gap-2 py-1 text-green-400">
        <CheckCircle className="h-3.5 w-3.5 shrink-0" />
        <span className="font-mono text-xs font-medium">Review completed</span>
      </div>
    )
  }

  if (t === "run.failed") {
    const error = (p as Record<string, unknown>).error as string | undefined
    return (
      <div className="flex items-center gap-2 py-1 text-red-400">
        <XCircle className="h-3.5 w-3.5 shrink-0" />
        <span className="font-mono text-xs">
          Review failed{error ? `: ${error}` : ""}
        </span>
      </div>
    )
  }

  if (t === "run.cancelled") {
    return (
      <div className="flex items-center gap-2 py-1 text-yellow-400">
        <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
        <span className="font-mono text-xs">Review cancelled</span>
      </div>
    )
  }

  return null
}

function ProgressBar({
  completed,
  total,
  tokens,
}: {
  completed: number
  total: number
  tokens: { input: number; output: number }
}) {
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0
  return (
    <div className="flex items-center gap-3 border-t px-3 py-2 text-xs text-muted-foreground">
      <div className="flex items-center gap-2">
        <Loader2 className="h-3 w-3 animate-spin" />
        <span>
          Tasks: {completed}/{total}
        </span>
      </div>
      <div className="h-3 w-24 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-blue-500 transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="ml-auto font-mono text-[10px]">
        {tokens.input.toLocaleString()} in / {tokens.output.toLocaleString()} out
      </span>
    </div>
  )
}

export function TerminalStream({
  events,
  subagentTotal,
  subagentCompleted,
  tokenUsage,
}: TerminalStreamProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [renderTick, setRenderTick] = useState(0)
  const pendingRef = useRef(false)

  useEffect(() => {
    if (pendingRef.current) return
    pendingRef.current = true
    requestAnimationFrame(() => {
      pendingRef.current = false
      setRenderTick((n) => n + 1)
    })
  }, [events.length])

  useEffect(() => {
    const el = containerRef.current
    if (el) {
      el.scrollTop = el.scrollHeight
    }
  }, [renderTick])

  return (
    <div className="flex flex-col h-full">
      <div
        ref={containerRef}
        className="scrollbar-hidden flex-1 overflow-y-auto overscroll-contain px-3 py-2 font-mono text-xs"
      >
        {events.length === 0 && (
          <div className="py-8 text-center text-muted-foreground">
            Waiting for events...
          </div>
        )}
        {events.map((event) => (
          <EventLine key={event.event_id} event={event} />
        ))}
      </div>
      {subagentTotal > 0 && (
        <ProgressBar
          completed={subagentCompleted}
          total={subagentTotal}
          tokens={tokenUsage}
        />
      )}
    </div>
  )
}
