/**
 * Tool budget system — tracks file reads, searches, and token usage.
 */

export interface ToolBudget {
  maxFiles: number
  maxSearches: number
  maxTokens: number
}

export interface ToolUsage {
  fileReadCount: number
  searchCount: number
  approximateTokens: number
}

const DEFAULT_BUDGET: ToolBudget = {
  maxFiles: 100,
  maxSearches: 50,
  maxTokens: 500_000,
}

export function createUsage(): ToolUsage {
  return { fileReadCount: 0, searchCount: 0, approximateTokens: 0 }
}

export function createBudget(overrides?: Partial<ToolBudget>): ToolBudget {
  return { ...DEFAULT_BUDGET, ...overrides }
}

export function checkFileBudget(usage: ToolUsage, budget: ToolBudget): boolean {
  return usage.fileReadCount < budget.maxFiles
}

export function checkSearchBudget(usage: ToolUsage, budget: ToolBudget): boolean {
  return usage.searchCount < budget.maxSearches
}

export function checkTokenBudget(usage: ToolUsage, budget: ToolBudget, estimated: number): boolean {
  return usage.approximateTokens + estimated <= budget.maxTokens
}

export function consumeFileRead(usage: ToolUsage): void {
  usage.fileReadCount++
}

export function consumeSearch(usage: ToolUsage): void {
  usage.searchCount++
}

export function consumeTokens(usage: ToolUsage, tokens: number): void {
  usage.approximateTokens += tokens
}
