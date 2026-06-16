/**
 * Auto-compact — LLM-based context summarization.
 *
 * Serializes messages into a plain-text conversation transcript,
 * sends it to the LLM with a domain-specific system prompt,
 * and returns a summary + recent messages.
 */

import type { LLMAdapter, LLMMessage, ContentBlock, ToolUseBlock, ToolResultBlock } from '@open-multi-agent/core'
import type { CompressionConfig } from './config.js'
import type { CompactProfile } from './compact-prompts.js'
import { getCompactProfilePrompt, compactUserPrompt } from './compact-prompts.js'
import { estimateTokens } from './estimation.js'
import { selectRecentMessages } from './recent.js'

/**
 * Serialize messages to plain text for compact summarization.
 */
function serializeMessagesForCompact(messages: LLMMessage[]): string {
  const parts: string[] = []

  for (const msg of messages) {
    const role = msg.role.toUpperCase()

    if (Array.isArray(msg.content)) {
      const blockTexts: string[] = []
      for (const block of msg.content) {
        if (block.type === 'tool_use') {
          const tb = block as ToolUseBlock
          const inputStr = JSON.stringify(tb.input, null, 0).slice(0, 500)
          blockTexts.push(`[Tool Call: ${tb.name}] input=${inputStr}`)
        } else if (block.type === 'tool_result') {
          const rb = block as ToolResultBlock
          const resultText = rb.content.length > 500 ? rb.content.slice(0, 500) : rb.content
          if (rb.is_error) {
            blockTexts.push(`[Tool Error] ${resultText}`)
          } else {
            blockTexts.push(`[Tool Result] ${resultText}`)
          }
        } else if (block.type === 'text') {
          blockTexts.push((block as any).text ?? '')
        }
      }
      parts.push(`[${role}] ${blockTexts.join(' ')}`)
    } else {
      parts.push(`[${role}] ${String(msg.content)}`)
    }
  }

  return parts.join('\n\n')
}

/**
 * Detect provider-neutral context-length errors.
 */
function isContextLengthError(error: unknown): boolean {
  if (!(error instanceof Error)) return false
  const msg = error.message.toLowerCase()
  const type = error.constructor.name.toLowerCase()

  const patterns = [
    'context length', 'context window', 'maximum context',
    'token limit', 'too many tokens', 'maximum tokens',
    'context_length_exceeded', 'contextlength', 'contextwindow',
    'maxtoken', 'tokenlimit',
  ]

  return patterns.some(p => msg.includes(p) || type.includes(p))
}

export interface CompactResult {
  summary: string
  recentMessages: LLMMessage[]
}

/**
 * Execute context compaction via LLM summarization.
 *
 * Returns null if compaction fails.
 */
export async function executeCompact(
  adapter: LLMAdapter,
  model: string,
  messages: LLMMessage[],
  profile: CompactProfile,
  config: CompressionConfig,
): Promise<CompactResult | null> {
  if (!messages.length) return null

  const systemPrompt = getCompactProfilePrompt(profile)
  let workMessages = [...messages]
  let userPrompt = compactUserPrompt(workMessages.length, profile)

  // Retry loop for context-length errors
  for (let attempt = 0; attempt <= config.compactMaxRetries; attempt++) {
    const serialized = serializeMessagesForCompact(workMessages)

    const compactMessages: LLMMessage[] = [
      { role: 'user', content: [{ type: 'text' as const, text: `${systemPrompt}\n\n---\n\n${userPrompt}\n\n---\n\n${serialized}` }] },
    ]

    try {
      const response = await adapter.chat(compactMessages, {
        model,
        maxTokens: config.compactMaxSummaryTokens,
        temperature: 0.3,
      })

      // Extract text from response
      const summaryText = extractResponseText(response)
      if (!summaryText) return null

      const recentMessages = selectRecentMessages(messages, config.compactRecentMessages)
      return { summary: summaryText, recentMessages }
    } catch (error) {
      if (isContextLengthError(error) && attempt < config.compactMaxRetries) {
        // Halve messages and retry
        const half = Math.floor(workMessages.length / 2)
        workMessages = workMessages.slice(half)
        userPrompt = compactUserPrompt(workMessages.length, profile)
        continue
      }
      return null
    }
  }

  return null
}

/**
 * Extract text content from an LLM response.
 */
function extractResponseText(response: { content?: string | ContentBlock[] }): string {
  if (typeof response.content === 'string') return response.content
  if (Array.isArray(response.content)) {
    const textBlocks = response.content.filter(b => b.type === 'text')
    return textBlocks.map(b => (b as any).text ?? '').join('\n')
  }
  return ''
}
