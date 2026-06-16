/**
 * read_check_summary tool — reads CI/CD check run results for the PR's head commit.
 */

import { z } from 'zod'
import { defineTool, type ToolDefinition, type ToolResult } from '@open-multi-agent/core'
import type { CheckSummary } from '../../github/checks.js'

/**
 * Creates the read_check_summary tool.
 * The checkSummary is fetched once and cached — the tool simply returns it.
 */
export function createReadCheckSummaryTool(checkSummary: CheckSummary | null): ToolDefinition {
  return defineTool({
    name: 'read_check_summary',
    description: 'Read CI/CD check run results for the PR head commit. Returns status of all checks.',
    inputSchema: z.object({}),
    execute: async (_input, _ctx): Promise<ToolResult> => {
      if (!checkSummary) {
        return { data: JSON.stringify({ status: 'unavailable', message: 'No check summary available' }) }
      }

      return {
        data: JSON.stringify({
          status: 'ok',
          total_count: checkSummary.total_count,
          completed: checkSummary.completed,
          failed: checkSummary.failed,
          success: checkSummary.success,
          runs: checkSummary.runs,
        }),
      }
    },
  })
}
