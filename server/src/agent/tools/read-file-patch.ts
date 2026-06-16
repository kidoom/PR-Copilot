/**
 * read_file_patch tool — reads diff/hunk patch for a file in the current PR.
 */

import { z } from 'zod'
import { defineTool, type ToolDefinition, type ToolResult } from '@open-multi-agent/core'
import type { PRContext } from '../../types/pr.js'
import type { RepoToolScope } from './scope.js'
import { isPathAllowedByScope } from './scope.js'

export function createReadFilePatchTool(prContext: PRContext | null, scope?: RepoToolScope | null): ToolDefinition {
  return defineTool({
    name: 'read_file_patch',
    description: 'Read diff/hunk patch for a file in the current PR. Returns the hunks with line-level detail.',
    inputSchema: z.object({
      filename: z.string().describe('The file path relative to the repo root'),
    }),
    maxOutputChars: 80_000,
    execute: async (input, _ctx): Promise<ToolResult> => {
      const { filename } = input

      if (!isPathAllowedByScope(filename, scope)) {
        return { data: JSON.stringify({ error: 'File is outside this reviewer scope', file: filename, status: 'forbidden' }), isError: true }
      }

      if (!prContext) {
        return { data: JSON.stringify({ error: 'PR context not available', file: filename, status: 'unavailable' }), isError: true }
      }

      const file = prContext.files.find(f => f.filename === filename)
      if (!file) {
        return { data: JSON.stringify({ error: 'File not found in PR', file: filename, status: 'not_found' }), isError: true }
      }
      if (!file.patch_available) {
        return { data: JSON.stringify({ error: 'Patch not available', file: filename, status: 'unavailable' }), isError: true }
      }

      const hunks = file.hunks.map(h => ({
        header: h.header,
        lines: h.lines.map(l => ({ content: l.content, type: l.type })),
      }))

      return { data: JSON.stringify({ file: filename, hunks, status: 'ok' }) }
    },
  })
}
