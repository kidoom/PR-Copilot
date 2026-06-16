/**
 * Intake analysis — classify PR files into categories.
 */

import type { FileClassification, FileCategory } from '../types/static-review.js'

const EXTENSION_MAP: Record<string, FileCategory> = {
  // Source
  '.ts': 'source', '.tsx': 'source', '.js': 'source', '.jsx': 'source',
  '.py': 'source', '.go': 'source', '.rs': 'source', '.java': 'source',
  '.rb': 'source', '.php': 'source', '.cpp': 'source', '.c': 'source',
  '.h': 'source', '.cs': 'source', '.swift': 'source', '.kt': 'source',
  // Test
  '.test.ts': 'test', '.test.tsx': 'test', '.test.js': 'test',
  '.spec.ts': 'test', '.spec.tsx': 'test', '.spec.js': 'test',
  '.test.py': 'test', '_test.go': 'test',
  // Config
  '.json': 'config', '.yaml': 'config', '.yml': 'config',
  '.toml': 'config', '.ini': 'config', '.cfg': 'config',
  // Doc
  '.md': 'doc', '.rst': 'doc', '.txt': 'doc',
  // Build
  '.dockerfile': 'build', '.dockerignore': 'build',
  // CI
  '.xml': 'other',
}

const PATH_PATTERNS: Array<{ pattern: RegExp; category: FileCategory }> = [
  { pattern: /(?:^|\/)tests?\//i, category: 'test' },
  { pattern: /(?:^|\/)__tests__\//i, category: 'test' },
  { pattern: /(?:^|\/)spec\//i, category: 'test' },
  { pattern: /(?:^|\/)\.github\/workflows\//i, category: 'ci' },
  { pattern: /(?:^|\/)\.github\/actions\//i, category: 'ci' },
  { pattern: /(?:^|\/)ci\//i, category: 'ci' },
  { pattern: /(?:^|\/)\.gitlab-ci/i, category: 'ci' },
  { pattern: /(?:^|\/)node_modules\//i, category: 'other' },
  { pattern: /(?:^|\/)dist\//i, category: 'other' },
  { pattern: /(?:^|\/)build\//i, category: 'build' },
  { pattern: /(?:^|\/)docs?\//i, category: 'doc' },
  { pattern: /Dockerfile/i, category: 'build' },
  { pattern: /docker-compose/i, category: 'build' },
  { pattern: /Makefile/i, category: 'build' },
]

const CONFIG_FILENAMES = new Set([
  'package.json', 'tsconfig.json', 'pyproject.toml', 'setup.py', 'setup.cfg',
  'Cargo.toml', 'go.mod', 'Gemfile', 'pom.xml', 'build.gradle',
  '.eslintrc', '.prettierrc', 'jest.config', 'vitest.config',
  'webpack.config', 'vite.config', 'rollup.config',
  '.env.example', '.gitignore', '.dockerignore',
])

/**
 * Classify a single file into a category.
 */
export function classifyFile(filename: string): FileCategory {
  // Check path patterns first (more specific)
  for (const { pattern, category } of PATH_PATTERNS) {
    if (pattern.test(filename)) return category
  }

  // Check config filenames
  const basename = filename.split('/').pop() ?? filename
  if (CONFIG_FILENAMES.has(basename)) return 'config'

  // Check extension
  for (const [ext, category] of Object.entries(EXTENSION_MAP)) {
    if (filename.endsWith(ext)) return category
  }

  return 'other'
}

/**
 * Classify all files in a PR.
 */
export function classifyFiles(filenames: string[]): FileClassification[] {
  return filenames.map((filename) => ({
    filename,
    category: classifyFile(filename),
    priority: 0, // filled by priority scoring
  }))
}
