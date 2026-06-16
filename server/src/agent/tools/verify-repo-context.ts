/**
 * verify_repo_context tool — verifies the agent has access to the repository
 * and the repo state matches expectations (branch, commit SHA).
 */

import { z } from 'zod'
import { defineTool, type ToolDefinition, type ToolResult } from '@open-multi-agent/core'
import * as fs from 'node:fs'
import * as path from 'node:path'
import { execSync } from 'node:child_process'

export function createVerifyRepoContextTool(
  repoRoot: string,
  expectedSha?: string,
  expectedBranch?: string,
): ToolDefinition {
  return defineTool({
    name: 'verify_repo_context',
    description: 'Verify the agent has access to the repository and the repo state matches expectations.',
    inputSchema: z.object({}),
    execute: async (_input, _ctx): Promise<ToolResult> => {
      const root = path.resolve(repoRoot)

      // Check if directory exists
      if (!fs.existsSync(root)) {
        return { data: JSON.stringify({ status: 'error', error: `Repository root does not exist: ${root}` }), isError: true }
      }

      // Check if it's a git repo
      const gitDir = path.join(root, '.git')
      if (!fs.existsSync(gitDir)) {
        return { data: JSON.stringify({ status: 'error', error: 'Not a git repository' }), isError: true }
      }

      // Get current state
      let currentSha: string | null = null
      let currentBranch: string | null = null
      try {
        currentSha = execSync('git rev-parse HEAD', { cwd: root, encoding: 'utf-8' }).trim()
        currentBranch = execSync('git rev-parse --abbrev-ref HEAD', { cwd: root, encoding: 'utf-8' }).trim()
      } catch {
        // git might not be available
      }

      // Verify expectations
      const issues: string[] = []
      if (expectedSha && currentSha && currentSha !== expectedSha) {
        issues.push(`HEAD is ${currentSha}, expected ${expectedSha}`)
      }
      if (expectedBranch && currentBranch && currentBranch !== expectedBranch) {
        issues.push(`Branch is ${currentBranch}, expected ${expectedBranch}`)
      }

      // Count accessible files
      let fileCount = 0
      const countFiles = (dir: string): void => {
        try {
          const entries = fs.readdirSync(dir, { withFileTypes: true })
          for (const entry of entries) {
            if (entry.name === '.git' || entry.name === 'node_modules') continue
            if (entry.isDirectory()) countFiles(path.join(dir, entry.name))
            else fileCount++
          }
        } catch { /* ignore */ }
      }
      countFiles(root)

      return {
        data: JSON.stringify({
          status: issues.length === 0 ? 'verified' : 'mismatch',
          repo_root: root,
          current_sha: currentSha,
          current_branch: currentBranch,
          expected_sha: expectedSha ?? null,
          expected_branch: expectedBranch ?? null,
          accessible_files: fileCount,
          issues,
        }),
        isError: issues.length > 0,
      }
    },
  })
}
