import { describe, expect, it } from 'vitest'
import { classifyFile, classifyFiles } from './intake.js'
import { scorePriority } from './priority.js'
import { runEvidenceRules } from './evidence.js'
import { generateContextTasks } from './context-plan.js'
import type { PRFile } from '../types/pr.js'

describe('static review pipeline', () => {
  it('classifies source, test, config, doc, build, and ci files', () => {
    expect(classifyFile('src/auth/login.ts')).toBe('source')
    expect(classifyFile('tests/test_login.py')).toBe('test')
    expect(classifyFile('package.json')).toBe('config')
    expect(classifyFile('docs/usage.md')).toBe('doc')
    expect(classifyFile('Dockerfile')).toBe('build')
    expect(classifyFile('.github/workflows/ci.yml')).toBe('ci')
  })

  it('scores security-sensitive source changes above low-risk docs', () => {
    expect(scorePriority('src/auth/middleware.ts', 'source', 45, 10)).toBeGreaterThan(70)
    expect(scorePriority('README.md', 'doc', 5, 0)).toBeLessThan(30)
  })

  it('extracts evidence signals from added diff lines', () => {
    const file: PRFile = {
      filename: 'src/auth.ts',
      status: 'modified',
      additions: 2,
      deletions: 0,
      patch_available: true,
      hunks: [{
        header: '@@ -1 +1 @@',
        lines: [
          { type: 'added', content: 'const password = "super-secret-value"' },
          { type: 'added', content: 'await saveUser(input)' },
        ],
      }],
    }

    const signals = runEvidenceRules(file)
    expect(signals.map((signal) => signal.type)).toEqual(expect.arrayContaining([
      'hardcoded_secret',
      'missing_error_handling',
    ]))
  })

  it('plans context tasks from evidence and missing tests', () => {
    const files: PRFile[] = [{
      filename: 'src/auth.ts',
      status: 'added',
      additions: 10,
      deletions: 0,
      patch_available: true,
      hunks: [],
    }]
    const classifications = classifyFiles(files.map((file) => file.filename))
    const tasks = generateContextTasks(files, classifications, [{
      type: 'hardcoded_secret',
      file: 'src/auth.ts',
      line: 1,
      severity: 'high',
      snippet: 'const password = "super-secret-value"',
      description: 'Possible hardcoded secret',
    }])

    expect(tasks.map((task) => task.type)).toEqual(expect.arrayContaining([
      'security-review',
      'test-coverage',
    ]))
  })
})
