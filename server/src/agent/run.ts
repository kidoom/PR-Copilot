import { randomUUID } from 'node:crypto'
import type { TeamRunResult, TaskExecutionRecord } from '@open-multi-agent/core'
import type { ServerConfig } from '../config.js'
import type { CheckSummary } from '../github/checks.js'
import type { PRContext } from '../types/pr.js'
import type { ReviewFinding } from '../types/review.js'
import type { RunEvent } from '../types/events.js'
import { classifyFiles } from '../review/intake.js'
import { scorePriorities } from '../review/priority.js'
import { runAllEvidenceRules } from '../review/evidence.js'
import { generateContextTasks } from '../review/context-plan.js'
import { buildReviewPackage } from '../review/package.js'
import { mapOrchestratorEvent, mapStreamEvent } from '../types/event-mapper.js'
import { buildReviewGoalPrompt, PR_REVIEW_COORDINATOR_PROMPT } from './prompts.js'
import { createReviewTeam } from './team.js'

export interface RunReviewInput {
  runId: string
  contextId: string
  prContext: PRContext
  config: ServerConfig
  repoRoot: string
  goal?: string
  planOnly?: boolean
  checkSummary?: CheckSummary | null
  abortSignal?: AbortSignal
  onEvent?: (event: RunEvent) => void
}

export interface RunReviewResult {
  findings: ReviewFinding[]
  tasks: readonly TaskExecutionRecord[]
  raw: TeamRunResult
}

export function makeRunEvent(
  runId: string,
  type: RunEvent['type'],
  sequence: number,
  payload: Record<string, unknown>,
): RunEvent {
  return {
    event_id: randomUUID().slice(0, 16),
    run_id: runId,
    type,
    sequence,
    created_at: new Date().toISOString(),
    payload,
  }
}

export function buildStaticReviewContext(prContext: PRContext) {
  const classifications = scorePriorities(
    classifyFiles(prContext.files.map((file) => file.filename)),
    new Map(prContext.files.map((file) => [file.filename, {
      additions: file.additions,
      deletions: file.deletions,
    }])),
  )
  const evidence = runAllEvidenceRules(prContext.files)
  const contextTasks = generateContextTasks(prContext.files, classifications, evidence)
  return { classifications, evidence, contextTasks }
}

export function summarizePRContext(prContext: PRContext): string {
  const files = prContext.files
    .map((file) => `- ${file.filename} (${file.status}, +${file.additions}/-${file.deletions})`)
    .join('\n')

  return [
    `${prContext.owner}/${prContext.repo}#${prContext.pull_number}: ${prContext.title}`,
    `Author: ${prContext.author}`,
    `Branches: ${prContext.base_branch} <- ${prContext.head_branch}`,
    `Head SHA: ${prContext.head_sha}`,
    `Description: ${prContext.description || '(none)'}`,
    'Changed files:',
    files,
  ].join('\n')
}

function normalizeSeverity(value: unknown): ReviewFinding['severity'] {
  const allowed = new Set(['critical', 'high', 'medium', 'low', 'info'])
  return typeof value === 'string' && allowed.has(value) ? value as ReviewFinding['severity'] : 'info'
}

function normalizeCategory(value: unknown): ReviewFinding['category'] {
  const allowed = new Set(['security', 'test-coverage', 'code-quality', 'config', 'performance', 'documentation'])
  return typeof value === 'string' && allowed.has(value) ? value as ReviewFinding['category'] : 'code-quality'
}

function coerceFinding(value: any): ReviewFinding | null {
  if (!value || typeof value !== 'object' || typeof value.file !== 'string') return null
  if (typeof value.title !== 'string' || typeof value.description !== 'string') return null

  return {
    file: value.file,
    line: typeof value.line === 'number' ? value.line : undefined,
    severity: normalizeSeverity(value.severity),
    category: normalizeCategory(value.category),
    title: value.title,
    description: value.description,
    evidence: Array.isArray(value.evidence)
      ? value.evidence.map((item: any) => ({
        file: typeof item?.file === 'string' ? item.file : value.file,
        line: typeof item?.line === 'number' ? item.line : undefined,
        snippet: typeof item?.snippet === 'string' ? item.snippet : undefined,
        tool: typeof item?.tool === 'string' ? item.tool : undefined,
      }))
      : [],
    suggestion: typeof value.suggestion === 'string' ? value.suggestion : undefined,
  }
}

function parseFindingsFromText(text: string): ReviewFinding[] {
  const fenced = [...text.matchAll(/```(?:json)?\s*([\s\S]*?)```/gi)].map((match) => match[1])
  const candidates = [...fenced, text.match(/\[[\s\S]*\]/)?.[0]].filter(Boolean) as string[]

  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate)
      const findings = Array.isArray(parsed) ? parsed.map(coerceFinding).filter(Boolean) : []
      if (findings.length > 0) return findings as ReviewFinding[]
    } catch {
      // Try the next candidate.
    }
  }

  return []
}

export function extractFindings(result: TeamRunResult): ReviewFinding[] {
  const coordinatorOutput = result.agentResults.get('coordinator')?.output
  const outputs = [
    coordinatorOutput,
    ...[...result.agentResults.values()].map((agentResult) => agentResult.output),
  ].filter((output): output is string => typeof output === 'string' && output.length > 0)

  for (const output of outputs) {
    const findings = parseFindingsFromText(output)
    if (findings.length > 0) return findings
  }

  return []
}

export async function runReview(input: RunReviewInput): Promise<RunReviewResult> {
  let sequence = 0
  const emit = (event: RunEvent) => input.onEvent?.(event)
  emit(makeRunEvent(input.runId, 'run.started', sequence++, {
    context_id: input.contextId,
    planOnly: input.planOnly ?? false,
  }))

  const staticReview = buildStaticReviewContext(input.prContext)
  const reviewPackage = buildReviewPackage({
    prContext: input.prContext,
    classifications: staticReview.classifications,
    evidence: staticReview.evidence,
    contextTasks: staticReview.contextTasks,
  })
  const goal = buildReviewGoalPrompt({
    goal: input.goal ?? 'Review this PR for security issues, test coverage, config risk, and code quality.',
    prSummary: summarizePRContext(input.prContext),
    reviewPackage,
  })

  const runtime = await createReviewTeam({
    config: input.config,
    prContext: input.prContext,
    reviewPackage,
    repoRoot: input.repoRoot,
    checkSummary: input.checkSummary,
    orchestratorConfig: {
      onProgress: (event) => {
        const mapped = mapOrchestratorEvent(input.runId, event)
        if (mapped) emit({ ...mapped, sequence: sequence++ })
      },
      ...(process.env.PR_COPILOT_MOCK_LLM === 'true'
        ? {}
        : {
          onAgentStream: (agentName, event) => {
            const mapped = mapStreamEvent(input.runId, agentName, event)
            if (mapped) emit({ ...mapped, sequence: sequence++ })
          },
        }),
    },
  })

  try {
    const raw = await runtime.orchestrator.runTeam(runtime.team, goal, {
      coordinator: {
        systemPrompt: PR_REVIEW_COORDINATOR_PROMPT,
        model: input.config.llm.model,
        adapter: runtime.adapter,
      },
      revealCoordinator: true,
      planOnly: input.planOnly,
      abortSignal: input.abortSignal,
    })

    const findings = input.planOnly ? [] : extractFindings(raw)
    emit(makeRunEvent(input.runId, 'run.completed', sequence++, {
      findings,
      tasks: raw.tasks ?? [],
      planOnly: raw.planOnly ?? false,
      success: raw.success,
    }))

    return { findings, tasks: raw.tasks ?? [], raw }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    emit(makeRunEvent(input.runId, 'run.failed', sequence++, { error: message }))
    throw error
  } finally {
    await runtime.orchestrator.shutdown()
  }
}
