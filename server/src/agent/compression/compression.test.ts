import { describe, expect, it, vi } from 'vitest'
import type { LLMMessage } from '@open-multi-agent/core'
import { buildCompressionStrategy, createConfig, estimateTokens, executeCompact, microCompactMessages, repairToolMessagePairs, selectRecentMessages } from './index.js'

function toolPair(id: string, name: string, content: string): LLMMessage[] {
  return [
    {
      role: 'assistant',
      content: [{ type: 'tool_use' as const, id, name, input: { query: 'auth middleware' } }],
    },
    {
      role: 'user',
      content: [{ type: 'tool_result' as const, tool_use_id: id, content }],
    },
  ]
}

describe('compression token estimation', () => {
  it('counts CJK characters more conservatively than English-only text', () => {
    expect(estimateTokens('这是一个测试')).toBeGreaterThan(estimateTokens('this is a test'))
  })
})

describe('micro compact', () => {
  it('replaces old large compactable tool results with placeholders', () => {
    const messages = [
      ...toolPair('a', 'search_repo', 'x'.repeat(120)),
      ...toolPair('b', 'search_diff', 'recent result'),
    ]

    const compacted = microCompactMessages(messages, 1, 100)
    const result = (compacted[1]?.content as any[])[0]
    expect(result.content).toContain('[Compacted: search_repo]')
    expect(result.content).toContain('Query: auth middleware')
  })

  it('preserves evidence-critical read_repo_file results', () => {
    const messages = toolPair('a', 'read_repo_file', 'x'.repeat(120))
    const compacted = microCompactMessages(messages, 0, 100)
    const result = (compacted[1]?.content as any[])[0]
    expect(result.content).toBe('x'.repeat(120))
  })
})

describe('tool pair repair and recent selection', () => {
  it('removes orphan tool results and tool uses', () => {
    const repaired = repairToolMessagePairs([
      { role: 'assistant', content: [{ type: 'tool_use' as const, id: 'missing-result', name: 'search_repo', input: {} }] },
      { role: 'user', content: [{ type: 'tool_result' as const, tool_use_id: 'missing-use', content: 'orphan' }] },
      ...toolPair('ok', 'search_repo', 'result'),
    ])

    expect(repaired).toHaveLength(2)
    expect((repaired[0]?.content as any[])[0].id).toBe('ok')
    expect((repaired[1]?.content as any[])[0].tool_use_id).toBe('ok')
  })

  it('preserves the last N messages and repairs broken pairs inside the slice', () => {
    const selected = selectRecentMessages([
      { role: 'user', content: [{ type: 'text' as const, text: 'old' }] },
      ...toolPair('ok', 'search_repo', 'result'),
      { role: 'user', content: [{ type: 'text' as const, text: 'latest' }] },
    ], 3)

    expect(selected).toHaveLength(3)
    expect((selected[2]?.content as any[])[0].text).toBe('latest')
  })
})

describe('auto compact and strategy assembly', () => {
  it('summarizes older messages and returns recent messages', async () => {
    const adapter = { chat: vi.fn(async () => ({ content: 'summary text' })) }
    const result = await executeCompact(adapter as any, 'test-model', [
      { role: 'user', content: [{ type: 'text' as const, text: 'old' }] },
      { role: 'assistant', content: [{ type: 'text' as const, text: 'recent' }] },
    ], 'main_agent', createConfig({ compactRecentMessages: 1 }))

    expect(adapter.chat).toHaveBeenCalled()
    expect(result?.summary).toBe('summary text')
    expect(result?.recentMessages).toHaveLength(1)
  })

  it('builds a custom OMA context strategy combining compression layers', async () => {
    const adapter = { chat: vi.fn(async () => ({ content: 'compacted state' })) }
    const strategy = buildCompressionStrategy(adapter as any, 'test-model', 'subagent', createConfig({
      contextWindowTokens: 100,
      compactMaxSummaryTokens: 20,
      autoCompactBufferTokens: 20,
      compactRecentMessages: 1,
      microCompactMinChars: 10,
    }))

    expect(strategy.type).toBe('custom')
    const messages = toolPair('a', 'search_repo', 'large result '.repeat(30))
    const compacted = await strategy.compress(messages, 10_000)
    expect((compacted[0]?.content as any[])[0].text).toContain('[Context Summary]')
  })
})
