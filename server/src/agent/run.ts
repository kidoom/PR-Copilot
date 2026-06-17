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

/**
 * Fallback parser for when the LLM returns findings as markdown prose
 * instead of a JSON array. Looks for structured patterns like:
 *   **File:** path/to/file.ts
 *   **Severity:** high
 *   **Title:** Some issue
 *   **Description:** ...
 */
function parseFindingsFromMarkdown(text: string): ReviewFinding[] {
  const findings: ReviewFinding[] = []
  // Split by markdown heading or horizontal rule boundaries
  const blocks = text.split(/(?=###?\s)|(?:\n---+\n)/)

  for (const block of blocks) {
    const fileMatch = block.match(/\*\*File:?\*\*\s*`?([^\s`*][^\n`]*?)`?\s*$/im)
    const titleMatch = block.match(/\*\*Title:?\*\*\s*`?(.+?)`?\s*$/im)
      ?? block.match(/\*\*Finding:?\*\*\s*`?(.+?)`?\s*$/im)
      ?? block.match(/\*\*Issue:?\*\*\s*`?(.+?)`?\s*$/im)
    const descMatch = block.match(/\*\*Description:?\*\*\s*`?(.+?)`?\s*$/im)
      ?? block.match(/\*\*Details:?\*\*\s*`?(.+?)`?\s*$/im)
    const sevMatch = block.match(/\*\*Severity:?\*\*\s*`?(\w+)`?\s*$/im)
    const catMatch = block.match(/\*\*Category:?\*\*\s*`?([\w-]+)`?\s*$/im)
    const lineMatch = block.match(/\*\*Line:?\*\*\s*`?(\d+)`?\s*$/im)
    const suggMatch = block.match(/\*\*(?:Suggestion|Recommendation|Fix):?\*\*\s*`?(.+?)`?\s*$/im)

    if (fileMatch && (titleMatch || descMatch)) {
      const finding = coerceFinding({
        file: fileMatch[1].trim(),
        line: lineMatch ? Number.parseInt(lineMatch[1], 10) : undefined,
        severity: sevMatch ? sevMatch[1].trim() : 'info',
        category: catMatch ? catMatch[1].trim() : 'code-quality',
        title: titleMatch ? titleMatch[1].trim() : descMatch![1].trim().slice(0, 80),
        description: descMatch ? descMatch[1].trim() : titleMatch![1].trim(),
        evidence: [],
        suggestion: suggMatch ? suggMatch[1].trim() : undefined,
      })
      if (finding) findings.push(finding)
    }
  }

  return findings
}

export function extractFindings(result: TeamRunResult): ReviewFinding[] {
  const coordinatorOutput = result.agentResults.get('coordinator')?.output

  // 1. Try coordinator output first — it should contain the synthesised findings.
  if (coordinatorOutput && typeof coordinatorOutput === 'string' && coordinatorOutput.length > 0) {
    const findings = parseFindingsFromText(coordinatorOutput)
    if (findings.length > 0) return findings
  }

  // 2. Coordinator returned no JSON findings (prose summary or empty []).
  //    Aggregate findings from individual sub-agent outputs.
  const aggregated: ReviewFinding[] = []
  const seen = new Set<string>()

  for (const [, agentResult] of result.agentResults) {
    if (!agentResult.output || typeof agentResult.output !== 'string' || agentResult.output.length === 0) continue
    // Skip the coordinator's own output — already tried above.
    if (agentResult === result.agentResults.get('coordinator')) continue

    const findings = parseFindingsFromText(agentResult.output)
    for (const finding of findings) {
      const key = `${finding.file}:${finding.line ?? 0}:${finding.title}`
      if (!seen.has(key)) {
        seen.add(key)
        aggregated.push(finding)
      }
    }
  }

  if (aggregated.length > 0) return aggregated

  // 3. Last resort: try markdown fallback on all outputs.
  const allOutputs = [
    coordinatorOutput,
    ...[...result.agentResults.values()].map((r) => r.output),
  ].filter((output): output is string => typeof output === 'string' && output.length > 0)

  for (const output of allOutputs) {
    const findings = parseFindingsFromMarkdown(output)
    for (const finding of findings) {
      const key = `${finding.file}:${finding.line ?? 0}:${finding.title}`
      if (!seen.has(key)) {
        seen.add(key)
        aggregated.push(finding)
      }
    }
  }

  return aggregated
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
    if (!input.abortSignal?.aborted) {
      emit(makeRunEvent(input.runId, 'run.failed', sequence++, { error: message }))
    }
    throw error
  } finally {
    await runtime.orchestrator.shutdown()
  }
}
