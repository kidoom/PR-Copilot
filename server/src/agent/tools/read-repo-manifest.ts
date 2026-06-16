/**
 * read_repo_manifest tool — reads the repo manifest (package.json, pyproject.toml, etc.)
 * and returns project metadata (name, version, dependencies, scripts).
 */

import { z } from 'zod'
import { defineTool, type ToolDefinition, type ToolResult } from '@open-multi-agent/core'
import * as fs from 'node:fs'
import * as path from 'node:path'

const MANIFEST_FILES = [
  'package.json',
  'pyproject.toml',
  'setup.py',
  'setup.cfg',
  'Cargo.toml',
  'go.mod',
  'pom.xml',
  'build.gradle',
  'Gemfile',
  'requirements.txt',
  'Pipfile',
]

export function createReadRepoManifestTool(repoRoot: string): ToolDefinition {
  return defineTool({
    name: 'read_repo_manifest',
    description: 'Read the repo manifest file (package.json, pyproject.toml, etc.) and return project metadata.',
    inputSchema: z.object({
      manifest: z.string().optional().describe('Specific manifest file to read (default: auto-detect)'),
    }),
    maxOutputChars: 30_000,
    execute: async (input, _ctx): Promise<ToolResult> => {
      const root = path.resolve(repoRoot)

      // Find manifest file
      let manifestPath: string
      if (input.manifest) {
        manifestPath = path.join(root, input.manifest)
      } else {
        // Auto-detect: try common manifests in order
        const found = MANIFEST_FILES.find(f => fs.existsSync(path.join(root, f)))
        if (!found) {
          return { data: JSON.stringify({ error: 'No manifest file found in repo root' }), isError: true }
        }
        manifestPath = path.join(root, found)
      }

      // Read and parse
      let content: string
      try {
        content = fs.readFileSync(manifestPath, 'utf-8')
      } catch (err: any) {
        return { data: JSON.stringify({ error: `Failed to read manifest: ${err.message}` }), isError: true }
      }

      const fileName = path.basename(manifestPath)

      // Parse based on file type
      if (fileName === 'package.json') {
        try {
          const pkg = JSON.parse(content)
          return {
            data: JSON.stringify({
              manifest: fileName,
              name: pkg.name,
              version: pkg.version,
              description: pkg.description,
              scripts: pkg.scripts,
              dependencies: pkg.dependencies,
              devDependencies: pkg.devDependencies,
              peerDependencies: pkg.peerDependencies,
            }),
          }
        } catch {
          return { data: JSON.stringify({ manifest: fileName, raw: content.slice(0, 10_000) }) }
        }
      }

      // For non-JSON manifests, return raw content (truncated)
      return { data: JSON.stringify({ manifest: fileName, raw: content.slice(0, 10_000) }) }
    },
  })
}
