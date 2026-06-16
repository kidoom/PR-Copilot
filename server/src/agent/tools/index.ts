/**
 * Repo context tools — barrel export.
 *
 * Each tool is a factory function that returns a ToolDefinition.
 * They close over the PR context, repo root, and budget state
 * provided at creation time.
 */

export { createReadFilePatchTool } from './read-file-patch.js'
export { createSearchDiffTool } from './search-diff.js'
export { createSearchRepoTool } from './search-repo.js'
export { createReadRepoFileTool } from './read-repo-file.js'
export { createSearchTestsForTool } from './search-tests-for.js'
export { createReadRepoManifestTool } from './read-repo-manifest.js'
export { createVerifyRepoContextTool } from './verify-repo-context.js'
export { createReadCheckSummaryTool } from './read-check-summary.js'
export { createTodoWriteTool, type TodoItem } from './todo-write.js'

// Budget and security utilities
export { type ToolBudget, type ToolUsage, checkFileBudget, checkSearchBudget, checkTokenBudget, consumeFileRead, consumeSearch, consumeTokens, createBudget, createUsage } from './budget.js'
export { estimateTokens, isSensitiveFile, isIgnoredDirectory, resolveSafePath } from './security.js'

import type { ToolDefinition } from '@open-multi-agent/core'
import type { PRContext } from '../../types/pr.js'
import type { CheckSummary } from '../../github/checks.js'
import type { ToolBudget, ToolUsage } from './budget.js'
import type { RepoToolScope } from './scope.js'
import { createReadFilePatchTool } from './read-file-patch.js'
import { createSearchDiffTool } from './search-diff.js'
import { createSearchRepoTool } from './search-repo.js'
import { createReadRepoFileTool } from './read-repo-file.js'
import { createSearchTestsForTool } from './search-tests-for.js'
import { createReadRepoManifestTool } from './read-repo-manifest.js'
import { createVerifyRepoContextTool } from './verify-repo-context.js'
import { createReadCheckSummaryTool } from './read-check-summary.js'
import { createTodoWriteTool } from './todo-write.js'

export interface RepoToolContext {
  prContext: PRContext | null
  repoRoot: string
  checkSummary: CheckSummary | null
  budget: ToolBudget | null
  usage: ToolUsage | null
  expectedSha?: string
  expectedBranch?: string
  scope?: RepoToolScope | null
}

/**
 * Create all repo context tools bound to the given context.
 * Returns them as an array ready to pass to OMA agent/team registration.
 */
export function createAllRepoTools(ctx: RepoToolContext): ToolDefinition[] {
  return [
    createReadFilePatchTool(ctx.prContext, ctx.scope),
    createSearchDiffTool(ctx.prContext, ctx.scope),
    createSearchRepoTool(ctx.repoRoot, ctx.budget, ctx.usage, ctx.scope),
    createReadRepoFileTool(ctx.repoRoot, ctx.budget, ctx.usage, ctx.scope),
    createSearchTestsForTool(ctx.repoRoot, ctx.scope),
    createReadRepoManifestTool(ctx.repoRoot),
    createVerifyRepoContextTool(ctx.repoRoot, ctx.expectedSha, ctx.expectedBranch),
    createReadCheckSummaryTool(ctx.checkSummary),
    createTodoWriteTool(),
  ]
}
