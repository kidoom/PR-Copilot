import { useState, useEffect, useRef, useCallback } from "react"
import { X, Play, Loader2, AlertTriangle, CheckCircle2, AlertCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { TerminalStream } from "./TerminalStream"
import { FindingCard, SeveritySummary } from "./FindingCard"
import {
  createReviewRun,
  cancelReviewRun,
  subscribeToReviewRun,
} from "@/api"
import type {
  ReviewRunEvent,
  FinalReviewResult,
  NormalizedFinding,
} from "@/types"

type PanelPhase = "idle" | "running" | "completed" | "failed" | "cancelled"

interface ReviewPanelProps {
  contextId: string
  onClose: () => void
  onFileClick?: (file: string) => void
}

export function ReviewPanel({ contextId, onClose, onFileClick }: ReviewPanelProps) {
  const [phase, setPhase] = useState<PanelPhase>("idle")
  const [runId, setRunId] = useState<string | null>(null)
  const [events, setEvents] = useState<ReviewRunEvent[]>([])
  const [finalResult, setFinalResult] = useState<FinalReviewResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [subagentTotal, setSubagentTotal] = useState(0)
  const [subagentCompleted, setSubagentCompleted] = useState(0)
  const [tokenUsage, setTokenUsage] = useState({ input: 0, output: 0 })
  const [streamConnected, setStreamConnected] = useState(false)
  const [streamError, setStreamError] = useState<string | null>(null)

  const cleanupRef = useRef<(() => void) | null>(null)
  const seenIdsRef = useRef(new Set<string>())

  const cleanup = useCallback(() => {
    cleanupRef.current?.()
    cleanupRef.current = null
  }, [])

  useEffect(() => {
    return cleanup
  }, [cleanup])

  const handleEvent = useCallback((event: ReviewRunEvent) => {
    if (seenIdsRef.current.has(event.event_id)) return
    seenIdsRef.current.add(event.event_id)

    setEvents((prev) => [...prev, event])

    const p = event.payload as Record<string, unknown>

    if (event.type === "subagent.started") {
      setSubagentTotal((n) => n + 1)
    }
    if (event.type === "subagent.completed") {
      setSubagentCompleted((n) => n + 1)
    }
    if (event.type === "tool.result") {
      const usage = p.token_usage as
        | { input_tokens?: number; output_tokens?: number }
        | undefined
      if (usage) {
        setTokenUsage((prev) => ({
          input: prev.input + (usage.input_tokens || 0),
          output: prev.output + (usage.output_tokens || 0),
        }))
      }
    }
    if (event.type === "run.completed") {
      setPhase("completed")
      if (p.findings || p.summary) {
        setFinalResult(p as unknown as FinalReviewResult)
      }
    }
    if (event.type === "run.failed") {
      setPhase("failed")
      setError((p.error as string) || "未知错误")
    }
    if (event.type === "run.cancelled") {
      setPhase("cancelled")
    }
  }, [])

  const startReview = useCallback(async () => {
    setPhase("running")
    setError(null)
    setEvents([])
    setFinalResult(null)
    setSubagentTotal(0)
    setSubagentCompleted(0)
    setTokenUsage({ input: 0, output: 0 })
    setStreamConnected(false)
    setStreamError(null)
    seenIdsRef.current.clear()

    try {
      const { run_id } = await createReviewRun(contextId)
      setRunId(run_id)
      cleanupRef.current = subscribeToReviewRun(
        run_id,
        handleEvent,
        () => {
          setStreamConnected(true)
          setStreamError(null)
        },
        (message) => {
          setStreamConnected(false)
          setStreamError(message)
        },
      )
    } catch (e) {
      setPhase("failed")
      setError(e instanceof Error ? e.message : "启动审查失败")
    }
  }, [contextId, handleEvent])

  const handleCancel = useCallback(async () => {
    if (!runId) return
    try {
      await cancelReviewRun(runId)
    } catch {
      // ignore cancel errors
    }
  }, [runId])

  const handleRerun = useCallback(() => {
    cleanup()
    setRunId(null)
    setEvents([])
    setFinalResult(null)
    setPhase("idle")
    startReview()
  }, [cleanup, startReview])

  const isRunning = phase === "running"
  const isTerminal = phase === "completed" || phase === "failed" || phase === "cancelled"
  const findings: NormalizedFinding[] = finalResult?.findings || []
  const validTasks = finalResult?.task_summaries.filter(
    (task) => task.parse_status === "valid",
  ).length || 0
  const totalTasks = finalResult?.task_summaries.length || 0

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden border-l bg-card">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <h3 className="text-sm font-semibold">AI 审查</h3>
        <div className="flex items-center gap-2">
          {isRunning && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleCancel}
              className="h-7 text-xs"
            >
              取消
            </Button>
          )}
          {isTerminal && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleRerun}
              className="h-7 text-xs"
            >
              重新运行
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon"
            onClick={() => {
              cleanup()
              onClose()
            }}
            className="h-7 w-7"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {phase === "idle" && (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
          <div className="rounded-full bg-muted p-3">
            <Play className="h-6 w-6 text-muted-foreground" />
          </div>
          <p className="text-sm text-muted-foreground">
            启动 AI 审查，使用专业 Agent 分析此 PR。
          </p>
          <Button onClick={startReview} size="sm">
            启动 AI 审查
          </Button>
        </div>
      )}

      {phase !== "idle" && (
        <div className="flex min-h-0 flex-1 flex-col">
          {phase !== "completed" && (
            <div className="min-h-0 flex-1">
              <TerminalStream
                events={events}
                subagentTotal={subagentTotal}
                subagentCompleted={subagentCompleted}
                tokenUsage={tokenUsage}
              />
            </div>
          )}

          {isRunning && (
            <div className="flex items-center gap-2 border-t px-3 py-2 text-xs text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              <span>
                {streamConnected
                  ? "正在使用专业 Agent 分析 PR..."
                  : "正在连接实时进度..."}
              </span>
            </div>
          )}

          {isRunning && streamError && (
            <div className="flex items-start gap-2 border-t border-yellow-200 bg-yellow-50 px-3 py-2 text-xs text-yellow-700 dark:border-yellow-900/50 dark:bg-yellow-950/30 dark:text-yellow-300">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{streamError}</span>
            </div>
          )}

          {phase === "failed" && error && (
            <div className="flex items-start gap-2 border-t border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-300">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {phase === "cancelled" && (
            <div className="flex items-center gap-2 border-t border-yellow-200 bg-yellow-50 px-3 py-2 text-xs text-yellow-700 dark:border-yellow-900/50 dark:bg-yellow-950/30 dark:text-yellow-300">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              <span>审查已取消</span>
            </div>
          )}

          {phase === "completed" && finalResult && (
            <div className="scrollbar-hidden min-h-0 flex-1 overflow-y-auto overscroll-contain">
              <div className="border-b px-3 py-3">
                <div className="flex items-start gap-2">
                  {finalResult.status === "partial" ? (
                    <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-yellow-600" />
                  ) : (
                    <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-green-600" />
                  )}
                  <div className="min-w-0">
                    <p className={`text-xs font-semibold ${finalResult.status === "partial" ? "text-yellow-700 dark:text-yellow-400" : "text-green-700 dark:text-green-400"}`}>
                      审查{finalResult.status === "partial" ? "部分完成" : "已完成"}
                    </p>
                    <p className="mt-1 break-words text-xs text-muted-foreground">
                      {finalResult.summary}
                    </p>
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                  <span>{findings.length} 个发现</span>
                  <span aria-hidden="true">|</span>
                  <span>{validTasks}/{totalTasks} 个任务已验证</span>
                  {finalResult.stopped_by_max_steps && (
                    <>
                      <span aria-hidden="true">|</span>
                      <span>已达步数上限</span>
                    </>
                  )}
                  {finalResult.coverage_counts?.baseline_reviewed != null && (
                    <>
                      <span aria-hidden="true">|</span>
                      <span>{finalResult.coverage_counts.baseline_reviewed} 个文件已审查</span>
                    </>
                  )}
                  {finalResult.coverage_counts?.uncovered_high_priority != null && finalResult.coverage_counts.uncovered_high_priority > 0 && (
                    <>
                      <span aria-hidden="true">|</span>
                      <span className="text-yellow-600 dark:text-yellow-400">
                        {finalResult.coverage_counts.uncovered_high_priority} 个未覆盖
                      </span>
                    </>
                  )}
                  {finalResult.run_usage && (
                    <>
                      <span aria-hidden="true">|</span>
                      <span>{finalResult.run_usage.total_model_calls} 次模型调用</span>
                      <span aria-hidden="true">|</span>
                      <span>{Math.round((finalResult.run_usage.total_input_tokens + finalResult.run_usage.total_output_tokens) / 1000)}k tokens</span>
                    </>
                  )}
                </div>
              </div>

              {/* Coverage summary */}
              {finalResult.coverage_counts && finalResult.coverage_counts.baseline_reviewed != null && (
                <div className="border-b px-3 py-2">
                  <div className="flex flex-wrap gap-1.5">
                    {finalResult.coverage_counts.baseline_reviewed > 0 && (
                      <span className="inline-flex items-center rounded-full bg-green-100 px-2 py-0.5 text-[10px] font-medium text-green-700 dark:bg-green-900/30 dark:text-green-400">
                        {finalResult.coverage_counts.baseline_reviewed} 已审查
                      </span>
                    )}
                    {finalResult.coverage_counts.baseline_partial != null && finalResult.coverage_counts.baseline_partial > 0 && (
                      <span className="inline-flex items-center rounded-full bg-yellow-100 px-2 py-0.5 text-[10px] font-medium text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400">
                        {finalResult.coverage_counts.baseline_partial} 部分
                      </span>
                    )}
                    {finalResult.coverage_counts.baseline_omitted != null && finalResult.coverage_counts.baseline_omitted > 0 && (
                      <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-medium text-gray-600 dark:bg-gray-800 dark:text-gray-400">
                        {finalResult.coverage_counts.baseline_omitted} 已跳过
                      </span>
                    )}
                    {finalResult.coverage_counts.baseline_failed != null && finalResult.coverage_counts.baseline_failed > 0 && (
                      <span className="inline-flex items-center rounded-full bg-red-100 px-2 py-0.5 text-[10px] font-medium text-red-700 dark:bg-red-900/30 dark:text-red-400">
                        {finalResult.coverage_counts.baseline_failed} 失败
                      </span>
                    )}
                  </div>
                </div>
              )}

              {/* Uncovered high-priority files */}
              {finalResult.uncovered_high_priority_paths && finalResult.uncovered_high_priority_paths.length > 0 && (
                <details className="border-b px-3 py-2">
                  <summary className="cursor-pointer text-[11px] font-semibold text-yellow-700 dark:text-yellow-400">
                    未覆盖的高优先级文件 ({finalResult.uncovered_high_priority_paths.length})
                  </summary>
                  <ul className="mt-1.5 space-y-0.5 pl-4 text-[11px] text-muted-foreground">
                    {finalResult.uncovered_high_priority_paths.map((file, index) => (
                      <li key={index} className="list-disc break-words">{file}</li>
                    ))}
                  </ul>
                </details>
              )}

              {/* Run usage details */}
              {finalResult.run_usage && (
                <details className="border-b px-3 py-2">
                  <summary className="cursor-pointer text-[11px] font-semibold">
                    资源用量
                  </summary>
                  <div className="mt-1.5 space-y-1 text-[11px] text-muted-foreground">
                    <div className="flex justify-between">
                      <span>模型调用</span>
                      <span>{finalResult.run_usage.total_model_calls}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>输入 tokens</span>
                      <span>{finalResult.run_usage.total_input_tokens.toLocaleString()}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>输出 tokens</span>
                      <span>{finalResult.run_usage.total_output_tokens.toLocaleString()}</span>
                    </div>
                    {finalResult.run_usage.total_observation_tokens > 0 && (
                      <div className="flex justify-between">
                        <span>观察 tokens</span>
                        <span>{finalResult.run_usage.total_observation_tokens.toLocaleString()}</span>
                      </div>
                    )}
                    {finalResult.run_usage.total_elapsed_ms > 0 && (
                      <div className="flex justify-between">
                        <span>耗时</span>
                        <span>{(finalResult.run_usage.total_elapsed_ms / 1000).toFixed(1)}s</span>
                      </div>
                    )}
                    {finalResult.run_usage.total_retries > 0 && (
                      <div className="flex justify-between">
                        <span>重试次数</span>
                        <span>{finalResult.run_usage.total_retries}</span>
                      </div>
                    )}
                    {finalResult.run_usage.total_fallbacks > 0 && (
                      <div className="flex justify-between">
                        <span>降级次数</span>
                        <span>{finalResult.run_usage.total_fallbacks}</span>
                      </div>
                    )}
                  </div>
                </details>
              )}

              {findings.length > 0 ? (
                <div className="px-3 py-3">
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <h4 className="text-xs font-semibold">
                      发现 ({findings.length})
                    </h4>
                    <SeveritySummary findings={findings} />
                  </div>
                  <div className="space-y-2">
                    {findings.map((finding, index) => (
                      <FindingCard
                        key={finding.fingerprint || index}
                        finding={finding}
                        onFileClick={onFileClick}
                      />
                    ))}
                  </div>
                </div>
              ) : (
                <div className="px-3 py-4 text-xs text-muted-foreground">
                  未发现有证据支持的问题。
                </div>
              )}

              {finalResult.uncertainties.length > 0 && (
                <details className="border-t px-3 py-3">
                  <summary className="cursor-pointer text-xs font-semibold">
                    不确定项 ({finalResult.uncertainties.length})
                  </summary>
                  <ul className="mt-2 space-y-1 pl-4 text-xs text-muted-foreground">
                    {finalResult.uncertainties.map((item, index) => (
                      <li key={index} className="list-disc break-words">
                        {item}
                      </li>
                    ))}
                  </ul>
                </details>
              )}

              {finalResult.notes.length > 0 && (
                <details className="border-t px-3 py-3">
                  <summary className="cursor-pointer text-xs font-semibold">
                    备注 ({finalResult.notes.length})
                  </summary>
                  <ul className="mt-2 space-y-1 pl-4 text-xs text-muted-foreground">
                    {finalResult.notes.map((item, index) => (
                      <li key={index} className="list-disc break-words">
                        {item}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}

          {phase === "completed" && !finalResult && (
            <div className="flex flex-1 items-center justify-center px-3 py-4 text-xs text-muted-foreground">
              审查已完成，但未产生结构化结果。
            </div>
          )}
        </div>
      )}
    </div>
  )
}
