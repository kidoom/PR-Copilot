import { describe, expect, it, beforeEach } from 'vitest'
import type { StreamEvent } from '@open-multi-agent/core'
import { mapStreamEvent, resetSequence } from './event-mapper.js'

describe('event mapper', () => {
  beforeEach(() => {
    resetSequence()
  })

  it('recovers tool name and input for tool_result events', () => {
    const toolUse = mapStreamEvent('run-1', 'config-reviewer', {
      type: 'tool_use',
      data: {
        id: 'toolu_1',
        name: 'read_repo_file',
        input: { path: 'pyproject.toml' },
      },
    } as StreamEvent)

    const result = mapStreamEvent('run-1', 'config-reviewer', {
      type: 'tool_result',
      data: {
        tool_use_id: 'toolu_1',
        content: JSON.stringify({
          error: 'File is outside this reviewer scope',
          path: 'pyproject.toml',
          status: 'forbidden',
        }),
        isError: true,
      },
    } as StreamEvent)

    expect(toolUse?.payload.tool_name).toBe('read_repo_file')
    expect(result?.payload.tool_name).toBe('read_repo_file')
    expect(result?.payload.input_summary).toEqual({ path: 'pyproject.toml' })
    expect(result?.payload.is_error).toBe(true)
    expect(result?.payload.output_summary).toContain('File is outside this reviewer scope')
    expect(result?.payload.output_summary).toContain('pyproject.toml')
  })
})
