/**
 * search_repo tool — searches repository content by keyword.
 * Uses Node.js fs for local repo access with budget and safety checks.
 */

import { z } from 'zod'
import { defineTool, type ToolDefinition, type ToolResult } from '@open-multi-agent/core'
import * as fs from 'node:fs'
import * as path from 'node:path'
import { isIgnoredDirectory, isSensitiveFile, resolveSafePath } from './security.js'
import { checkSearchBudget, consumeSearch, type ToolBudget, type ToolUsage } from './budget.js'
import type { RepoToolScope } from './scope.js'
import { isPathAllowedByScope } from './scope.js'

const MAX_SEARCH_RESULTS = 50

export function createSearchRepoTool(
  repoRoot: string,
  budget: ToolBudget | null,
  usage: ToolUsage | null,
  scope?: RepoToolScope | null,
): ToolDefinition {
  return defineTool({
    name: 'search_repo',
    description: 'Search repository content by keyword. Walks the repo tree and matches lines.',
    inputSchema: z.object({
      query: z.string().describe('Search query (case-insensitive substring match)'),
      path_scope: z.string().optional().describe('Limit search to this directory path'),
      limit: z.number().int().optional().describe('Max results to return (default 20, max 50)'),
    }),
    maxOutputChars: 40_000,
    execute: async (input, _ctx): Promise<ToolResult> => {
      // Budget check
      if (budget && usage && !checkSearchBudget(usage, budget)) {
        return {
          data: JSON.stringify({ error: 'Search budget exhausted', status: 'budget_exhausted', max_searches: budget.maxSearches }),
          isError: true,
        }
      }

      const query = input.query.toLowerCase()
      const limit = Math.min(input.limit ?? 20, MAX_SEARCH_RESULTS)
      const matches: Array<{ file: string; line: number; snippet: string }> = []
      const root = path.resolve(repoRoot)
      const allowedFiles = scope?.allowedFiles ?? []
      const searchPathScopes = scope?.searchPathScopes ?? []

      // Resolve scope
      let resolvedScope: string | null = null
      if (input.path_scope) {
        if (!isPathAllowedByScope(input.path_scope, scope)) {
          return { data: JSON.stringify({ error: 'path_scope is outside this reviewer scope', path_scope: input.path_scope, status: 'forbidden' }), isError: true }
        }
        resolvedScope = resolveSafePath(repoRoot, input.path_scope)
        if (!resolvedScope) {
          return { data: JSON.stringify({ error: 'Invalid or unsafe path_scope', path_scope: input.path_scope }), isError: true }
        }
      }

      const searchFile = (fullPath: string, relPath: string): void => {
        if (matches.length >= limit) return
        if (isSensitiveFile(relPath)) return
        try {
          const content = fs.readFileSync(fullPath, 'utf-8')
          const lines = content.split('\n')
          for (let i = 0; i < lines.length; i++) {
            if (lines[i].toLowerCase().includes(query)) {
              matches.push({ file: relPath, line: i + 1, snippet: lines[i].trim().slice(0, 200) })
              if (matches.length >= limit) return
            }
          }
        } catch {
          // Skip binary or unreadable files
        }
      }

      if (!resolvedScope && allowedFiles.length > 0) {
        for (const allowedFile of allowedFiles) {
          if (matches.length >= limit) break
          const safePath = resolveSafePath(repoRoot, allowedFile)
          if (!safePath) continue
          if (!fs.existsSync(safePath) || !fs.statSync(safePath).isFile()) continue
          searchFile(safePath, allowedFile)
        }

        if (budget && usage) {
          consumeSearch(usage)
        }

        return { data: JSON.stringify({ matches, total: matches.length, truncated: matches.length >= limit, scoped: true }) }
      }

      // Walk the directory tree
      const walk = (dir: string): void => {
        if (matches.length >= limit) return

        let entries: fs.Dirent[]
        try {
          entries = fs.readdirSync(dir, { withFileTypes: true })
        } catch {
          return
        }

        for (const entry of entries) {
          if (matches.length >= limit) return

          const fullPath = path.join(dir, entry.name)
          const relPath = path.relative(root, fullPath)

          if (entry.isDirectory()) {
            if (isIgnoredDirectory(entry.name)) continue
            // Scope check: directory must be within scope
            if (resolvedScope) {
              const resolved = path.resolve(fullPath)
              if (!resolved.startsWith(resolvedScope) && !resolvedScope.startsWith(resolved)) continue
            }
            walk(fullPath)
          } else if (entry.isFile()) {
            if (isIgnoredDirectory(relPath)) continue
            if (isSensitiveFile(relPath)) continue
            if (searchPathScopes.length > 0 && !isPathAllowedByScope(relPath, { searchPathScopes })) continue

            // Scope check
            if (resolvedScope) {
              const resolved = path.resolve(fullPath)
              if (!resolved.startsWith(resolvedScope)) continue
            }

            searchFile(fullPath, relPath)
          }
        }
      }

      walk(root)

      // Consume budget
      if (budget && usage) {
        consumeSearch(usage)
      }

      return { data: JSON.stringify({ matches, total: matches.length, truncated: matches.length >= limit }) }
    },
  })
}
