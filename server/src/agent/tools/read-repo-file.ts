/**
 * read_repo_file tool — reads a file from the repository with line limits.
 */

import { z } from 'zod'
import { defineTool, type ToolDefinition, type ToolResult } from '@open-multi-agent/core'
import * as fs from 'node:fs'
import * as path from 'node:path'
import { isSensitiveFile, MAX_LINES, resolveSafePath } from './security.js'
import { checkFileBudget, checkTokenBudget, consumeFileRead, consumeTokens, type ToolBudget, type ToolUsage } from './budget.js'
import { estimateTokens } from './security.js'
import type { RepoToolScope } from './scope.js'
import { isPathAllowedByScope } from './scope.js'

export function createReadRepoFileTool(
  repoRoot: string,
  budget: ToolBudget | null,
  usage: ToolUsage | null,
  scope?: RepoToolScope | null,
): ToolDefinition {
  return defineTool({
    name: 'read_repo_file',
    description: 'Read a file from the repository. Returns content with line numbers.',
    inputSchema: z.object({
      path: z.string().describe('File path relative to the repo root'),
      start_line: z.number().int().optional().describe('Start line (1-based, inclusive)'),
      max_lines: z.number().int().positive().max(MAX_LINES).optional().describe('Maximum lines to return (capped at 50)'),
      end_line: z.number().int().optional().describe('End line (1-based, inclusive; legacy alias)'),
    }),
    maxOutputChars: 60_000,
    execute: async (input, _ctx): Promise<ToolResult> => {
      // Budget checks
      if (budget && usage) {
        if (!checkFileBudget(usage, budget)) {
          return {
            data: JSON.stringify({ error: 'File read budget exhausted', status: 'budget_exhausted', max_files: budget.maxFiles }),
            isError: true,
          }
        }
      }

      if (!isPathAllowedByScope(input.path, scope)) {
        return { data: JSON.stringify({ error: 'File is outside this reviewer scope', path: input.path, status: 'forbidden' }), isError: true }
      }

      // Path safety
      const safePath = resolveSafePath(repoRoot, input.path)
      if (!safePath) {
        return { data: JSON.stringify({ error: 'Path traversal rejected', path: input.path }), isError: true }
      }

      // Sensitive file check
      if (isSensitiveFile(input.path)) {
        return { data: JSON.stringify({ error: 'Sensitive file blocked', path: input.path }), isError: true }
      }

      // Read file
      let content: string
      try {
        content = fs.readFileSync(safePath, 'utf-8')
      } catch (err: any) {
        if (err.code === 'ENOENT') {
          return { data: JSON.stringify({ error: 'File not found', path: input.path }), isError: true }
        }
        return { data: JSON.stringify({ error: `Failed to read file: ${err.message}`, path: input.path }), isError: true }
      }

      // Token budget check
      if (budget && usage) {
        const tokens = estimateTokens(content)
        if (!checkTokenBudget(usage, budget, tokens)) {
          return {
            data: JSON.stringify({
              error: 'Token budget exceeded',
              status: 'budget_exhausted',
              path: input.path,
              estimated_tokens: tokens,
              remaining_tokens: Math.max(0, budget.maxTokens - usage.approximateTokens),
            }),
            isError: true,
          }
        }
      }

      // Apply line range
      const lines = content.split('\n')
      const startLine = Math.max(1, input.start_line ?? 1)
      const requestedMax = Math.min(input.max_lines ?? MAX_LINES, MAX_LINES)
      const endLine = Math.min(lines.length, input.end_line ?? (startLine + requestedMax - 1))
      const selectedLines = lines.slice(startLine - 1, endLine)

      // Consume budget
      if (budget && usage) {
        consumeFileRead(usage)
        consumeTokens(usage, estimateTokens(selectedLines.join('\n')))
      }

      const numbered = selectedLines.map((line, i) => `${startLine + i}: ${line}`).join('\n')
      return {
        data: JSON.stringify({
          path: input.path,
          total_lines: lines.length,
          start_line: startLine,
          end_line: endLine,
          truncated: endLine < lines.length,
          content: numbered,
        }),
      }
    },
  })
}
