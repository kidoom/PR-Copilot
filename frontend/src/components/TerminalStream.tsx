import { useEffect, useRef, useState, useMemo } from "react"
import {
  Play,
  Wrench,
  CheckCircle,
  XCircle,
  Bot,
  AlertTriangle,
  Loader2,
  ChevronDown,
  ChevronRight,
  Search,
  FileText,
  Zap,
  Brain,
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

// ─── Constants ───────────────────────────────────────────────────────

const AGENT_LABELS: Record<string, string> = {
  coordinator: "协调器",
  "security-reviewer": "安全审查",
  "test-context-analyzer": "测试分析",
  "config-reviewer": "配置审查",
  "code-quality-reviewer": "代码质量",
}

const TOOL_LABELS: Record<string, string> = {
  read_file_patch: "读取差异",
  search_diff: "搜索差异",
  search_repo: "搜索仓库",
  read_repo_file: "读取文件",
  search_tests_for: "搜索测试",
  read_repo_manifest: "读取清单",
  verify_repo_context: "验证上下文",
  read_check_summary: "读取检查",
  todo_write: "更新任务",
  task_dispatch: "分派任务",
  task_complete: "任务完成",
  task_error: "任务错误",
}

type Phase = "planning" | "executing" | "synthesizing" | "done"

const PHASES: { key: Phase; label: string; icon: typeof Brain }[] = [
  { key: "planning", label: "规划中", icon: Brain },
  { key: "executing", label: "执行中", icon: Zap },
  { key: "synthesizing", label: "综合中", icon: FileText },
  { key: "done", label: "完成", icon: CheckCircle },
]

// ─── Helpers ─────────────────────────────────────────────────────────

function agentLabel(type: string): string {
  return AGENT_LABELS[type] || type || "agent"
}

function toolLabel(name: string): string {
  return TOOL_LABELS[name] || name
}

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return m > 0 ? `${m}分${s.toString().padStart(2, "0")}秒` : `${s}秒`
}

function formatSummary(value: unknown, max = 120): string {
  if (value == null || value === "") return ""
  const text = typeof value === "string" ? value : JSON.stringify(value)
  return text.length > max ? `${text.slice(0, max)}...` : text
}

function toolIcon(name: string) {
  if (name.includes("search") || name.includes("Search")) return Search
  if (name.includes("read") || name.includes("Read")) return FileText
  return Wrench
}

// ─── Phase detection ─────────────────────────────────────────────────

function detectPhase(events: ReviewRunEvent[]): Phase {
  let hasAgentStarted = false
  let isTerminal = false
  const started = new Set<string>()
  const completed = new Set<string>()

  for (const e of events) {
    const p = e.payload
    if (e.type === "subagent.started" && p.agent_type !== "coordinator") {
      hasAgentStarted = true
      started.add(p.agent_type as string)
    }
    if (e.type === "subagent.completed" && p.agent_type !== "coordinator") {
      completed.add(p.agent_type as string)
    }
    if (e.type === "run.completed" || e.type === "run.failed" || e.type === "run.cancelled") {
      isTerminal = true
    }
  }

  if (isTerminal) return "done"
  if (!hasAgentStarted) return "planning"
  if (started.size > 0 && started.size === completed.size) return "synthesizing"
  return "executing"
}

// ─── Agent state tracking ────────────────────────────────────────────

interface AgentState {
  type: string
  status: "running" | "completed"
  action: string
  toolCount: number
}

function buildAgentStates(events: ReviewRunEvent[]): AgentState[] {
  const map = new Map<string, AgentState>()

  for (const e of events) {
    const p = e.payload
    const agentType = p.agent_type as string

    if (e.type === "subagent.started" && agentType !== "coordinator") {
      map.set(agentType, {
        type: agentType,
        status: "running",
        action: "正在启动...",
        toolCount: 0,
      })
    }

    if (e.type === "subagent.completed" && agentType !== "coordinator") {
      const agent = map.get(agentType)
      if (agent) {
        agent.status = "completed"
        agent.action = "已完成"
      }
    }

    if (e.type === "tool.call") {
      const tool = p as unknown as ToolEventPayload
      const agent = tool.agent_type
      if (agent && agent !== "coordinator") {
        const state = map.get(agent)
        if (state && state.status === "running") {
          state.toolCount++
          let desc = toolLabel(tool.tool_name)
          const input = tool.input_summary as Record<string, unknown> | undefined
          if (input?.path) desc += ` ${input.path}`
          else if (input?.filename) desc += ` ${input.filename}`
          else if (input?.query) desc += ` "${input.query}"`
          state.action = desc
        }
      }
    }
  }

  return [...map.values()]
}

// ─── Phase Stepper ───────────────────────────────────────────────────

function PhaseStepper({ phase, elapsed }: { phase: Phase; elapsed: number }) {
  const currentIdx = PHASES.findIndex((p) => p.key === phase)

  return (
    <div className="border-b bg-gradient-to-r from-slate-950 via-slate-900 to-slate-950 px-4 py-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1">
          {PHASES.map((p, idx) => {
            const Icon = p.icon
            const isActive = idx === currentIdx
            const isDone = idx < currentIdx
            return (
              <div key={p.key} className="flex items-center">
                {idx > 0 && (
                  <div className={`mx-1 h-px w-6 ${isDone ? "bg-emerald-500" : "bg-slate-700"}`} />
                )}
                <div
                  className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${
                    isActive
                      ? "bg-sky-500/20 text-sky-300 ring-1 ring-sky-500/40"
                      : isDone
                        ? "text-emerald-400"
                        : "text-slate-500"
                  }`}
                >
                  {isActive ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : isDone ? (
                    <CheckCircle className="h-3 w-3" />
                  ) : (
                    <Icon className="h-3 w-3" />
                  )}
                  <span>{p.label}</span>
                </div>
              </div>
            )
          })}
        </div>
        <span className="font-mono text-[11px] text-slate-400">{formatElapsed(elapsed)}</span>
      </div>
    </div>
  )
}

// ─── Live Activity Banner ────────────────────────────────────────────

function LiveActivity({
  phase,
  agents,
  latestEvent,
}: {
  phase: Phase
  agents: AgentState[]
  latestEvent?: ReviewRunEvent
}) {
  const running = agents.find((a) => a.status === "running")
  const isTerminal = phase === "done"

  let icon = <Loader2 className="h-4 w-4 animate-spin text-sky-400" />
  let label = "审查正在运行"
  let detail = ""

  if (phase === "planning") {
    icon = <Brain className="h-4 w-4 text-purple-400" />
    label = "正在规划审查任务"
    detail = "协调器正在分析 PR 并分解审查任务..."
  } else if (phase === "executing") {
    icon = <Zap className="h-4 w-4 text-amber-400" />
    label = running ? `${agentLabel(running.type)} 正在工作` : "正在执行审查任务"
    detail = running?.action || "多个智能体并行分析中..."
  } else if (phase === "synthesizing") {
    icon = <FileText className="h-4 w-4 text-emerald-400" />
    label = "正在综合审查结果"
    detail = "协调器正在汇总所有发现并生成最终报告..."
  } else if (phase === "done") {
    if (latestEvent?.type === "run.failed") {
      icon = <XCircle className="h-4 w-4 text-red-400" />
      label = "审查失败"
      detail = String(latestEvent.payload.error ?? "")
    } else if (latestEvent?.type === "run.cancelled") {
      icon = <AlertTriangle className="h-4 w-4 text-yellow-400" />
      label = "审查已取消"
    } else {
      icon = <CheckCircle className="h-4 w-4 text-emerald-400" />
      label = "审查完成"
    }
  }

  return (
    <div className="border-b bg-slate-950/95 px-4 py-3">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 shrink-0">{icon}</div>
        <div className="min-w-0 flex-1">
          <span className="text-sm font-semibold text-slate-100">{label}</span>
          {detail && <div className="mt-1 text-xs text-slate-400">{detail}</div>}
        </div>
      </div>
      {!isTerminal && (
        <div className="mt-3 flex h-1 gap-1 overflow-hidden rounded-full bg-slate-800">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="h-full flex-1 animate-pulse rounded-full bg-sky-500/70"
              style={{ animationDelay: `${i * 180}ms` }}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Agent Card ──────────────────────────────────────────────────────

function AgentCard({ agent }: { agent: AgentState }) {
  const isRunning = agent.status === "running"
  return (
    <div
      className={`flex items-center gap-3 rounded-lg border px-3 py-2 ${
        isRunning ? "border-sky-500/30 bg-sky-500/5" : "border-emerald-500/20 bg-emerald-500/5"
      }`}
    >
      {isRunning ? (
        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-sky-400" />
      ) : (
        <CheckCircle className="h-4 w-4 shrink-0 text-emerald-400" />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className={`text-xs font-semibold ${isRunning ? "text-sky-300" : "text-emerald-300"}`}>
            {agentLabel(agent.type)}
          </span>
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
              isRunning ? "bg-sky-500/20 text-sky-400" : "bg-emerald-500/20 text-emerald-400"
            }`}
          >
            {isRunning ? "执行中" : "已完成"}
          </span>
          {agent.toolCount > 0 && (
            <span className="text-[10px] text-slate-500">{agent.toolCount} 次工具调用</span>
          )}
        </div>
        {isRunning && agent.action && (
          <div className="mt-0.5 truncate font-mono text-[11px] text-slate-400">{agent.action}</div>
        )}
      </div>
    </div>
  )
}

// ─── Collapsible Event Log ───────────────────────────────────────────

function EventLog({ events }: { events: ReviewRunEvent[] }) {
  const [expanded, setExpanded] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (expanded && ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [expanded, events.length])

  return (
    <div className="border-t">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-2 text-xs text-slate-400 hover:bg-slate-800/50 transition-colors"
      >
        {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <span>详细日志 ({events.length} 条事件)</span>
      </button>
      {expanded && (
        <div ref={ref} className="scrollbar-hidden max-h-64 overflow-y-auto border-t px-3 py-2 font-mono text-[11px]">
          {events.map((e) => (
            <EventLine key={e.event_id} event={e} />
          ))}
        </div>
      )}
    </div>
  )
}

function EventLine({ event }: { event: ReviewRunEvent }) {
  const t = event.type
  const p = event.payload

  if (t === "run.started") {
    return (
      <div className="flex items-center gap-2 py-0.5 text-green-400">
        <Play className="h-3 w-3 shrink-0" />
        <span>审查已开始</span>
      </div>
    )
  }

  if (t === "tool.call") {
    const tool = p as unknown as ToolEventPayload
    const Icon = toolIcon(tool.tool_name)
    return (
      <div className="flex items-center gap-2 py-0.5 text-blue-400">
        <Icon className="h-3 w-3 shrink-0" />
        <span>
          <span className="text-blue-300/60">[{agentLabel(tool.agent_type)}]</span> {toolLabel(tool.tool_name)}
          {tool.input_summary != null && (
            <span className="ml-1 text-slate-500">{formatSummary(tool.input_summary)}</span>
          )}
        </span>
      </div>
    )
  }

  if (t === "tool.result") {
    const tool = p as unknown as ToolEventPayload
    return (
      <div className={`flex items-center gap-2 py-0.5 ${tool.is_error ? "text-red-400" : "text-slate-500"}`}>
        {tool.is_error ? <XCircle className="h-3 w-3 shrink-0" /> : <CheckCircle className="h-3 w-3 shrink-0" />}
        <span>{toolLabel(tool.tool_name)}{tool.is_error && " (错误)"}</span>
      </div>
    )
  }

  if (t === "subagent.started") {
    const sub = p as unknown as SubagentEventPayload
    return (
      <div className="flex items-center gap-2 py-0.5 text-yellow-400">
        <Bot className="h-3 w-3 shrink-0" />
        <span>[{agentLabel(sub.agent_type)}] 已启动</span>
      </div>
    )
  }

  if (t === "subagent.completed") {
    const sub = p as unknown as SubagentEventPayload
    return (
      <div className="flex items-center gap-2 py-0.5 text-emerald-400">
        <Bot className="h-3 w-3 shrink-0" />
        <span>[{agentLabel(sub.agent_type)}] 已完成</span>
      </div>
    )
  }

  if (t === "message.delta") {
    const delta = p as unknown as MessageDeltaPayload
    if (!delta.text?.trim()) return null
    return (
      <div className="py-0.5 pl-5 text-slate-500">
        <span className="whitespace-pre-wrap">{delta.text.slice(0, 200)}</span>
      </div>
    )
  }

  return null
}

// ─── Progress Bar ────────────────────────────────────────────────────

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
    <div className="flex items-center gap-3 border-t bg-slate-950/50 px-4 py-2 text-xs text-slate-400">
      <span>智能体进度</span>
      <span className="font-mono text-sky-400">{completed}/{total}</span>
      <div className="h-2 w-32 overflow-hidden rounded-full bg-slate-800">
        <div
          className="h-full rounded-full bg-gradient-to-r from-sky-500 to-emerald-500 transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      {(tokens.input > 0 || tokens.output > 0) && (
        <span className="ml-auto font-mono text-[10px] text-slate-500">
          {tokens.input.toLocaleString()} in / {tokens.output.toLocaleString()} out
        </span>
      )}
    </div>
  )
}

// ─── Main Component ──────────────────────────────────────────────────

export function TerminalStream({
  events,
  subagentTotal,
  subagentCompleted,
  tokenUsage,
}: TerminalStreamProps) {
  const [elapsed, setElapsed] = useState(0)
  const startedAt = useRef<number | null>(null)

  const phase = useMemo(() => detectPhase(events), [events])
  const agents = useMemo(() => buildAgentStates(events), [events])
  const latest = events[events.length - 1]
  const isDone = phase === "done"

  useEffect(() => {
    if (events.length > 0 && startedAt.current == null) {
      startedAt.current = Date.now()
    }
    if (events.length === 0) {
      startedAt.current = null
      setElapsed(0)
    }
  }, [events.length])

  useEffect(() => {
    if (!startedAt.current || isDone) return
    const timer = setInterval(() => {
      if (startedAt.current) setElapsed(Math.floor((Date.now() - startedAt.current) / 1000))
    }, 1000)
    return () => clearInterval(timer)
  }, [isDone, events.length])

  return (
    <div className="flex flex-col h-full">
      <PhaseStepper phase={phase} elapsed={elapsed} />
      <LiveActivity phase={phase} agents={agents} latestEvent={latest} />

      {agents.length > 0 && (
        <div className="border-b px-4 py-3">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">审查智能体</div>
          <div className="grid gap-2">
            {agents.map((a) => (
              <AgentCard key={a.type} agent={a} />
            ))}
          </div>
        </div>
      )}

      {events.length === 0 && (
        <div className="flex flex-1 items-center justify-center">
          <div className="text-center text-slate-500">
            <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin" />
            <p className="text-xs">正在等待审查事件...</p>
          </div>
        </div>
      )}

      {events.length > 0 && <EventLog events={events} />}

      {subagentTotal > 0 && (
        <ProgressBar completed={subagentCompleted} total={subagentTotal} tokens={tokenUsage} />
      )}
    </div>
  )
}
