import * as path from 'node:path'
import type { PRContext, PRFile, HunkLine } from '../types/pr.js'
import type { ContextTask, EvidenceSignal, FileClassification, FileCategory } from '../types/static-review.js'

export type ReviewAgentName =
  | 'security-reviewer'
  | 'test-context-analyzer'
  | 'config-reviewer'
  | 'code-quality-reviewer'

export interface ReviewPackageFile {
  filename: string
  status: PRFile['status']
  category: FileCategory
  priority: number
  additions: number
  deletions: number
  patch_available: boolean
  compact_patch: Array<{
    header: string
    lines: Array<{
      type: HunkLine['type']
      old_line?: number
      new_line?: number
      content: string
    }>
    omitted_lines: number
  }>
  omitted_hunks: number
}

export interface ReviewAgentScope {
  reviewer: ReviewAgentName
  focus: string
  allowedFiles: string[]
  searchPathScopes: string[]
  evidence: EvidenceSignal[]
  contextTasks: ContextTask[]
  omittedFiles: string[]
  maxToolFiles: number
  maxToolSearches: number
  maxToolTokens: number
}

export interface ReviewPackage {
  metadata: {
    context_id: string
    repository: string
    pull_number: number
    title: string
    head_sha: string
    branches: string
    file_count: number
  }
  files: ReviewPackageFile[]
  omittedFiles: string[]
  evidence: EvidenceSignal[]
  contextTasks: ContextTask[]
  agentScopes: Record<ReviewAgentName, ReviewAgentScope>
}

export interface BuildReviewPackageInput {
  prContext: PRContext
  classifications: FileClassification[]
  evidence: EvidenceSignal[]
  contextTasks: ContextTask[]
}

const MAX_PACKAGE_FILES = 40
const MAX_AGENT_FILES = 18
const MAX_HUNKS_PER_FILE = 4
const MAX_CHANGED_LINES_PER_HUNK = 24
const MAX_LINE_CHARS = 220

const SECURITY_PATH = /auth|login|session|token|jwt|oauth|password|secret|credential|permission|role|access|acl|rbac|middleware|guard|policy|crypto|encrypt|decrypt|webhook/i
const CONFIG_PATH = /(^|\/)(package(-lock)?\.json|pnpm-lock\.yaml|yarn\.lock|tsconfig|vite\.config|webpack\.config|rollup\.config|jest\.config|vitest\.config|eslint|prettier|dockerfile|docker-compose|compose\.ya?ml|\.github\/workflows|\.env\.example|config|settings)/i

function byPriority(classifications: FileClassification[]): FileClassification[] {
  return [...classifications].sort((a, b) => b.priority - a.priority || a.filename.localeCompare(b.filename))
}

function unique(values: Iterable<string>): string[] {
  return [...new Set([...values].filter(Boolean))].sort()
}

function fileByName(files: PRFile[]): Map<string, PRFile> {
  return new Map(files.map((file) => [file.filename, file]))
}

function compactFile(file: PRFile, classification: FileClassification): ReviewPackageFile {
  const compact_patch = file.hunks.slice(0, MAX_HUNKS_PER_FILE).map((hunk) => {
    const changed = hunk.lines
      .filter((line) => line.type === 'added' || line.type === 'removed')
      .slice(0, MAX_CHANGED_LINES_PER_HUNK)
      .map((line) => ({
        type: line.type,
        old_line: line.old_line,
        new_line: line.new_line,
        content: line.content.slice(0, MAX_LINE_CHARS),
      }))

    const changedCount = hunk.lines.filter((line) => line.type === 'added' || line.type === 'removed').length
    return {
      header: hunk.header,
      lines: changed,
      omitted_lines: Math.max(0, changedCount - changed.length),
    }
  })

  return {
    filename: file.filename,
    status: file.status,
    category: classification.category,
    priority: classification.priority,
    additions: file.additions,
    deletions: file.deletions,
    patch_available: file.patch_available,
    compact_patch,
    omitted_hunks: Math.max(0, file.hunks.length - compact_patch.length),
  }
}

function directoryOf(filename: string): string {
  const normalized = filename.replace(/\\/g, '/')
  const dir = path.posix.dirname(normalized)
  return dir === '.' ? '' : dir
}

function searchScopesFor(files: string[], extras: string[] = []): string[] {
  return unique([
    ...files.map(directoryOf).filter(Boolean),
    ...extras,
  ]).slice(0, 12)
}

function selectFiles(
  classifications: FileClassification[],
  evidence: EvidenceSignal[],
  predicate: (classification: FileClassification) => boolean,
  evidencePredicate: (signal: EvidenceSignal) => boolean = () => false,
): string[] {
  const evidenceFiles = evidence.filter(evidencePredicate).map((signal) => signal.file)
  const selected = [
    ...evidenceFiles,
    ...byPriority(classifications).filter(predicate).map((classification) => classification.filename),
  ]
  return unique(selected).slice(0, MAX_AGENT_FILES)
}

function scopeEvidence(evidence: EvidenceSignal[], files: string[]): EvidenceSignal[] {
  const allowed = new Set(files)
  return evidence.filter((signal) => allowed.has(signal.file)).slice(0, 25)
}

function scopeTasks(tasks: ContextTask[], files: string[], typeHints: ContextTask['type'][]): ContextTask[] {
  const allowed = new Set(files)
  return tasks
    .filter((task) => typeHints.includes(task.type) || task.targetFiles.some((file) => allowed.has(file)))
    .map((task) => ({
      ...task,
      targetFiles: task.targetFiles.filter((file) => allowed.has(file)),
      evidence: task.evidence.filter((signal) => allowed.has(signal.file)).slice(0, 15),
    }))
    .filter((task) => task.targetFiles.length > 0 || task.evidence.length > 0)
    .slice(0, 10)
}

function makeScope(input: {
  reviewer: ReviewAgentName
  focus: string
  files: string[]
  allFiles: string[]
  evidence: EvidenceSignal[]
  contextTasks: ContextTask[]
  taskTypes: ContextTask['type'][]
  searchExtras?: string[]
  maxToolFiles?: number
  maxToolSearches?: number
  maxToolTokens?: number
}): ReviewAgentScope {
  const allowedFiles = unique(input.files)
  const allowed = new Set(allowedFiles)
  return {
    reviewer: input.reviewer,
    focus: input.focus,
    allowedFiles,
    searchPathScopes: searchScopesFor(allowedFiles, input.searchExtras),
    evidence: scopeEvidence(input.evidence, allowedFiles),
    contextTasks: scopeTasks(input.contextTasks, allowedFiles, input.taskTypes),
    omittedFiles: input.allFiles.filter((file) => !allowed.has(file)),
    maxToolFiles: input.maxToolFiles ?? 18,
    maxToolSearches: input.maxToolSearches ?? 12,
    maxToolTokens: input.maxToolTokens ?? 120_000,
  }
}

export function buildReviewPackage(input: BuildReviewPackageInput): ReviewPackage {
  const { prContext, classifications, evidence, contextTasks } = input
  const files = fileByName(prContext.files)
  const ranked = byPriority(classifications)
  const packageFiles = ranked.slice(0, MAX_PACKAGE_FILES)
  const allFilenames = ranked.map((classification) => classification.filename)
  const packageFileSet = new Set(packageFiles.map((classification) => classification.filename))

  const sourceFiles = selectFiles(classifications, evidence, (classification) => classification.category === 'source')
  const testFiles = selectFiles(classifications, evidence, (classification) => classification.category === 'test')
  const configFiles = selectFiles(
    classifications,
    evidence,
    (classification) => ['config', 'build', 'ci'].includes(classification.category) || CONFIG_PATH.test(classification.filename),
  )
  const securityFiles = selectFiles(
    classifications,
    evidence,
    (classification) =>
      classification.category === 'source' && (SECURITY_PATH.test(classification.filename) || classification.priority >= 65),
    (signal) => ['hardcoded_secret', 'sql_injection', 'eval_exec'].includes(signal.type),
  )

  const qualityFiles = selectFiles(
    classifications,
    evidence,
    (classification) => classification.category === 'source' || classification.priority >= 60,
  )

  const agentScopes: Record<ReviewAgentName, ReviewAgentScope> = {
    'security-reviewer': makeScope({
      reviewer: 'security-reviewer',
      focus: 'Authentication, authorization, injection, secrets, data exposure, and trust-boundary risks.',
      files: securityFiles.length > 0 ? securityFiles : sourceFiles.slice(0, 8),
      allFiles: allFilenames,
      evidence,
      contextTasks,
      taskTypes: ['security-review'],
      searchExtras: ['src', 'server', 'app', 'api', 'routes', 'middleware', 'config'],
      maxToolFiles: 14,
      maxToolSearches: 10,
      maxToolTokens: 100_000,
    }),
    'test-context-analyzer': makeScope({
      reviewer: 'test-context-analyzer',
      focus: 'Changed behavior, related tests, missing assertions, regression coverage, and test impact.',
      files: unique([...sourceFiles.slice(0, 12), ...testFiles.slice(0, 8)]),
      allFiles: allFilenames,
      evidence,
      contextTasks,
      taskTypes: ['test-coverage'],
      searchExtras: ['test', 'tests', '__tests__', 'spec', 'specs'],
      maxToolFiles: 22,
      maxToolSearches: 14,
      maxToolTokens: 140_000,
    }),
    'config-reviewer': makeScope({
      reviewer: 'config-reviewer',
      focus: 'Environment variables, dependencies, CI, build, deployment, and operational configuration.',
      files: configFiles,
      allFiles: allFilenames,
      evidence,
      contextTasks,
      taskTypes: ['config-review'],
      searchExtras: ['.github', 'config', 'scripts'],
      maxToolFiles: 12,
      maxToolSearches: 8,
      maxToolTokens: 80_000,
    }),
    'code-quality-reviewer': makeScope({
      reviewer: 'code-quality-reviewer',
      focus: 'Correctness, maintainability, API behavior, error handling, and consistency with local patterns.',
      files: qualityFiles,
      allFiles: allFilenames,
      evidence,
      contextTasks,
      taskTypes: ['code-quality'],
      searchExtras: ['src', 'server', 'frontend', 'lib', 'app'],
      maxToolFiles: 18,
      maxToolSearches: 12,
      maxToolTokens: 120_000,
    }),
  }

  return {
    metadata: {
      context_id: prContext.context_id,
      repository: `${prContext.owner}/${prContext.repo}`,
      pull_number: prContext.pull_number,
      title: prContext.title,
      head_sha: prContext.head_sha,
      branches: `${prContext.base_branch} <- ${prContext.head_branch}`,
      file_count: prContext.files.length,
    },
    files: packageFiles
      .map((classification) => {
        const file = files.get(classification.filename)
        return file ? compactFile(file, classification) : null
      })
      .filter((file): file is ReviewPackageFile => file !== null),
    omittedFiles: allFilenames.filter((filename) => !packageFileSet.has(filename)),
    evidence: evidence.slice(0, 40),
    contextTasks: contextTasks.slice(0, 20),
    agentScopes,
  }
}

