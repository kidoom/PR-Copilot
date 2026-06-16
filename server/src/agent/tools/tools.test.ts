import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { createBudget, createUsage } from './budget.js'
import { createReadRepoFileTool } from './read-repo-file.js'
import { estimateTokens, isIgnoredDirectory, isSensitiveFile, resolveSafePath } from './security.js'

const tempDirs: string[] = []

function makeRepo(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pr-copilot-tools-'))
  tempDirs.push(dir)
  return dir
}

async function execute(tool: any, input: Record<string, unknown>) {
  return JSON.parse((await tool.execute(input, {})).data)
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

describe('repo tool safety utilities', () => {
  it('rejects path traversal and detects ignored and sensitive paths', () => {
    const root = makeRepo()
    expect(resolveSafePath(root, 'src/index.ts')).toBe(path.join(root, 'src', 'index.ts'))
    expect(resolveSafePath(root, '../escape.txt')).toBeNull()
    expect(isIgnoredDirectory('src/node_modules/pkg/index.js')).toBe(true)
    expect(isSensitiveFile('config/.env.production')).toBe(true)
    expect(isSensitiveFile('keys/id_rsa')).toBe(true)
  })

  it('estimates CJK text more conservatively than English text of similar length', () => {
    expect(estimateTokens('这是一个测试')).toBeGreaterThan(estimateTokens('this is a test'))
  })
})

describe('read_repo_file tool', () => {
  it('reads bounded line ranges and caps max_lines at 50', async () => {
    const root = makeRepo()
    fs.mkdirSync(path.join(root, 'src'), { recursive: true })
    fs.writeFileSync(path.join(root, 'src', 'main.ts'), Array.from({ length: 80 }, (_, i) => `line ${i + 1}`).join('\n'))

    const tool = createReadRepoFileTool(root, createBudget(), createUsage())
    const result = await execute(tool, { path: 'src/main.ts', start_line: 10, max_lines: 20 })
    expect(result.start_line).toBe(10)
    expect(result.end_line).toBe(29)
    expect(result.content).toContain('10: line 10')
    expect(result.content).not.toContain('30: line 30')
    expect(result.truncated).toBe(true)
  })

  it('blocks traversal, sensitive files, and exhausted file budgets', async () => {
    const root = makeRepo()
    fs.writeFileSync(path.join(root, 'safe.txt'), 'ok')
    fs.writeFileSync(path.join(root, '.env.production'), 'SECRET=value')

    const tool = createReadRepoFileTool(root, createBudget({ maxFiles: 0 }), createUsage())
    await expect(execute(tool, { path: '../outside.txt' })).resolves.toMatchObject({ status: 'budget_exhausted' })

    const unrestricted = createReadRepoFileTool(root, null, null)
    await expect(execute(unrestricted, { path: '../outside.txt' })).resolves.toMatchObject({ error: 'Path traversal rejected' })
    await expect(execute(unrestricted, { path: '.env.production' })).resolves.toMatchObject({ error: 'Sensitive file blocked' })
  })

  it('rejects reads outside a reviewer scope', async () => {
    const root = makeRepo()
    fs.mkdirSync(path.join(root, 'src'), { recursive: true })
    fs.mkdirSync(path.join(root, 'docs'), { recursive: true })
    fs.writeFileSync(path.join(root, 'src', 'main.ts'), 'const ok = true')
    fs.writeFileSync(path.join(root, 'docs', 'guide.md'), 'outside scope')

    const tool = createReadRepoFileTool(root, createBudget(), createUsage(), {
      allowedFiles: ['src/main.ts'],
      searchPathScopes: ['src'],
    })

    await expect(execute(tool, { path: 'src/main.ts' })).resolves.toMatchObject({ path: 'src/main.ts' })
    await expect(execute(tool, { path: 'docs/guide.md' })).resolves.toMatchObject({ status: 'forbidden' })
  })
})
