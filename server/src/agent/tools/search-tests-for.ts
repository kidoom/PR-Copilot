/**
 * search_tests_for tool — finds test files related to a source file.
 * Searches by naming conventions and content patterns.
 */

import { z } from 'zod'
import { defineTool, type ToolDefinition, type ToolResult } from '@open-multi-agent/core'
import * as fs from 'node:fs'
import * as path from 'node:path'
import { isIgnoredDirectory } from './security.js'
import type { RepoToolScope } from './scope.js'
import { isPathAllowedByScope } from './scope.js'

const TEST_PATTERNS = [
  '.test.', '.spec.', '_test.', '_spec.', '.e2e.', '.integration.',
]
const TEST_DIRS = ['test', 'tests', '__tests__', 'spec', 'specs', '__spec__']
const MAX_TEST_RESULTS = 25

export function createSearchTestsForTool(repoRoot: string, scope?: RepoToolScope | null): ToolDefinition {
  return defineTool({
    name: 'search_tests_for',
    description: 'Find test files related to a source file by naming conventions and content patterns.',
    inputSchema: z.object({
      filename: z.string().describe('Source file path relative to repo root'),
    }),
    maxOutputChars: 20_000,
    execute: async (input, _ctx): Promise<ToolResult> => {
      const { filename } = input
      if (!isPathAllowedByScope(filename, scope)) {
        return { data: JSON.stringify({ error: 'Source file is outside this reviewer scope', source: filename, status: 'forbidden' }), isError: true }
      }

      const root = path.resolve(repoRoot)
      const baseName = path.basename(filename, path.extname(filename))
      const ext = path.extname(filename)
      const relatedTests: Array<{ file: string; match_type: string }> = []

      // Strategy 1: naming convention — same dir or test dirs
      const walk = (dir: string, relDir: string): void => {
        let entries: fs.Dirent[]
        try {
          entries = fs.readdirSync(dir, { withFileTypes: true })
        } catch {
          return
        }

        for (const entry of entries) {
          const fullPath = path.join(dir, entry.name)
          const relPath = path.join(relDir, entry.name)

          if (entry.isDirectory()) {
            if (isIgnoredDirectory(entry.name)) continue
            walk(fullPath, relPath)
          } else if (entry.isFile()) {
            if (relatedTests.length >= MAX_TEST_RESULTS) return
            const entryBase = path.basename(entry.name, path.extname(entry.name))
            const entryExt = path.extname(entry.name)

            // Check if this is a test file for our source
            const isTestPattern = TEST_PATTERNS.some(p => entry.name.includes(p))
            const isInTestDir = TEST_DIRS.some(d => relPath.includes(`${d}${path.sep}`) || relPath.startsWith(`${d}${path.sep}`))

            if (isTestPattern || isInTestDir) {
              // Check if the base name matches
              if (entryBase === baseName || entryBase === `${baseName}.test` || entryBase === `${baseName}.spec`) {
                relatedTests.push({ file: relPath, match_type: 'naming_convention' })
              } else if (entryBase.includes(baseName)) {
                relatedTests.push({ file: relPath, match_type: 'partial_name' })
              }
            }
          }
        }
      }

      walk(root, '')

      // Strategy 2: grep for imports/references
      const importMatches: string[] = []
      const searchImport = (dir: string, relDir: string): void => {
        let entries: fs.Dirent[]
        try {
          entries = fs.readdirSync(dir, { withFileTypes: true })
        } catch {
          return
        }

        for (const entry of entries) {
          const fullPath = path.join(dir, entry.name)
          const relPath = path.join(relDir, entry.name)

          if (entry.isDirectory()) {
            if (isIgnoredDirectory(entry.name)) continue
            searchImport(fullPath, relPath)
          } else if (entry.isFile()) {
            if (importMatches.length >= MAX_TEST_RESULTS) return
            const isTest = TEST_PATTERNS.some(p => entry.name.includes(p))
            if (!isTest) continue

            try {
              const content = fs.readFileSync(fullPath, 'utf-8')
              // Check for import/require of the source file
              if (content.includes(baseName) && !importMatches.includes(relPath)) {
                importMatches.push(relPath)
              }
            } catch {
              // skip
            }
          }
        }
      }

      searchImport(root, '')

      // Merge results
      for (const test of relatedTests) {
        if (importMatches.length >= MAX_TEST_RESULTS) break
        if (!importMatches.includes(test.file)) {
          importMatches.push(test.file)
        }
      }

      return {
        data: JSON.stringify({
          source: filename,
          tests: importMatches.map(f => {
            const existing = relatedTests.find(t => t.file === f)
            return { file: f, match_type: existing?.match_type ?? 'import_reference' }
          }),
          total: importMatches.length,
          truncated: importMatches.length >= MAX_TEST_RESULTS,
        }),
      }
    },
  })
}
