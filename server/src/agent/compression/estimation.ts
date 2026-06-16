/**
 * Token estimation: conservative estimates with CJK character support.
 */

import type { LLMMessage, ContentBlock, ToolUseBlock, ToolResultBlock } from '@open-multi-agent/core'

/** Conservative chars-per-token for English/code. */
const CHARS_PER_TOKEN = 4

/** CJK characters typically use about 1 token per character. */
const CJK_PATTERN = /[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]/g

/** Overhead per message (role, separators). */
const MESSAGE_OVERHEAD_TOKENS = 4

/** Overhead per tool block. */
const TOOL_BLOCK_OVERHEAD_TOKENS = 8

function countCJK(text: string): number {
  const matches = text.match(CJK_PATTERN)
  return matches ? matches.length : 0
}

export function estimateTokens(text: string): number {
  if (!text) return 0

  const englishEstimate = Math.floor(text.length / CHARS_PER_TOKEN)
  const cjkCount = countCJK(text)
  const nonCjkLen = text.length - cjkCount
  const cjkEstimate = cjkCount + Math.floor(nonCjkLen / CHARS_PER_TOKEN)

  return Math.max(1, Math.max(englishEstimate, cjkEstimate))
}

function estimateBlockTokens(block: ContentBlock): number {
  let total = TOOL_BLOCK_OVERHEAD_TOKENS

  if (block.type === 'tool_use') {
    const tb = block as ToolUseBlock
    total += estimateTokens(tb.name)
    total += estimateTokens(JSON.stringify(tb.input, null, 0))
  } else if (block.type === 'tool_result') {
    const rb = block as ToolResultBlock
    total += estimateTokens(rb.content)
  } else if (block.type === 'text') {
    total += estimateTokens((block as any).text ?? '')
  } else if (block.type === 'reasoning') {
    total += estimateTokens((block as any).thinking ?? '')
  }

  return total
}

export function estimateMessageTokens(message: LLMMessage): number {
  let total = MESSAGE_OVERHEAD_TOKENS

  if (Array.isArray(message.content)) {
    for (const block of message.content) {
      total += estimateBlockTokens(block)
    }
  }

  return total
}

export function estimateMessagesTokens(messages: LLMMessage[]): number {
  let sum = 0
  for (const msg of messages) {
    sum += estimateMessageTokens(msg)
  }
  return sum
}
