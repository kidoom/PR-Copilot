/**
 * search_diff tool — searches within PR diff patches.
 */

import { z } from 'zod'
import { defineTool, type ToolDefinition, type ToolResult } from '@open-multi-agent/core'
import type { PRContext } from '../../types/pr.js'
import type { RepoToolScope } from './scope.js'
import { filterFilesByScope } from './scope.js'

export function createSearchDiffTool(prContext: PRContext | null, scope?: RepoToolScope | null): ToolDefinition {
  return defineTool({
    name: 'search_diff',
    description: 'Search within PR diff patches. Returns matching lines with file, hunk index, and line number.',
    inputSchema: z.object({
      query: z.string().describe('Search query (case-insensitive substring match)'),
      limit: z.number().int().optional().describe('Max results to return (default 20)'),
    }),
    maxOutputChars: 40_000,
    execute: async (input, _ctx): Promise<ToolResult> => {
      const query = input.query.toLowerCase()
      const limit = input.limit ?? 20
      const matches: Array<{ file: string; hunk_index: number; line_number?: number; line_type: string; snippet: string }> = []
      const skipped: string[] = []

      if (!prContext) {
        return { data: JSON.stringify({ matches, total: 0, skipped_files: skipped, truncated: false, error: 'PR context not available' }) }
      }

      for (const f of filterFilesByScope(prContext.files, scope)) {
        if (!f.patch_available) {
          skipped.push(f.filename)
          continue
        }
        for (let hIdx = 0; hIdx < f.hunks.length; hIdx++) {
          const h = f.hunks[hIdx]
          for (const line of h.lines) {
            if (line.content.toLowerCase().includes(query)) {
              const lineNumber = line.type === 'removed' ? line.old_line : line.new_line
              matches.push({
                file: f.filename,
                hunk_index: hIdx,
                line_number: lineNumber,
                line_type: line.type,
                snippet: line.content.trim().slice(0, 200),
              })
              if (matches.length >= limit) {
                return { data: JSON.stringify({ matches, total: matches.length, skipped_files: skipped, truncated: true }) }
              }
            }
          }
        }
      }

      return { data: JSON.stringify({ matches, total: matches.length, skipped_files: skipped, truncated: false }) }
    },
  })
}
