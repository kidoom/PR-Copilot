/**
 * Compression mechanism — barrel export and ContextStrategy assembly.
 *
 * Combines micro-compact, repair, auto-compact, and recent selection
 * into an OMA ContextStrategy of type 'custom'.
 */

import type { LLMAdapter, LLMMessage, ContextStrategy } from '@open-multi-agent/core'
import type { CompressionConfig } from './config.js'
import type { CompactProfile } from './compact-prompts.js'
import { autoCompactThreshold, createConfig } from './config.js'
import { estimateMessagesTokens } from './estimation.js'
import { microCompactMessages } from './micro-compact.js'
import { repairToolMessagePairs } from './repair.js'
import { executeCompact } from './compact.js'
import { selectRecentMessages } from './recent.js'

// Re-export all public API
export { createConfig, disabledConfig, autoCompactThreshold, type CompressionConfig } from './config.js'
export { estimateTokens, estimateMessageTokens, estimateMessagesTokens } from './estimation.js'
export { microCompactMessages } from './micro-compact.js'
export { repairToolMessagePairs } from './repair.js'
export { selectRecentMessages } from './recent.js'
export { executeCompact, type CompactResult } from './compact.js'
export { getCompactProfilePrompt, selectCompactProfile, type CompactProfile } from './compact-prompts.js'

/**
 * Build an OMA ContextStrategy of type 'custom' that applies
 * PR-Copilot's four-layer compression:
 *
 * 1. Repair — fix orphan tool_use/tool_result pairs
 * 2. Micro-compact — replace old large tool results with placeholders
 * 3. Auto-compact — LLM summarization when over threshold
 * 4. Recent selection — preserve recent messages after summary
 *
 * @param adapter  LLM adapter for auto-compact summarization calls.
 * @param model    Model name for summarization.
 * @param profile  Compact profile (main_agent or subagent).
 * @param config   Compression configuration.
 */
export function buildCompressionStrategy(
  adapter: LLMAdapter,
  model: string,
  profile: CompactProfile,
  config: CompressionConfig = createConfig(),
): ContextStrategy {
  return {
    type: 'custom',
    compress: async (messages: LLMMessage[], estimatedTokens: number): Promise<LLMMessage[]> => {
      if (!config.enabled) return messages

      // Step 1: Repair orphan pairs
      let result = repairToolMessagePairs(messages)

      // Step 2: Micro-compact (if enabled and tokens are non-trivial)
      if (config.microCompactEnabled && estimatedTokens > 10_000) {
        result = microCompactMessages(
          result,
          config.microCompactRecentResults,
          config.microCompactMinChars,
        )
      }

      // Step 3: Auto-compact (if enabled and over threshold)
      const threshold = autoCompactThreshold(config)
      const currentTokens = estimateMessagesTokens(result)

      if (config.autoCompactEnabled && currentTokens > threshold) {
        const compactResult = await executeCompact(adapter, model, result, profile, config)
        if (compactResult) {
          // Build new message list: summary boundary + recent messages
          result = [
            {
              role: 'user' as const,
              content: [{ type: 'text' as const, text: `[Context Summary]\n${compactResult.summary}` }],
            },
            ...compactResult.recentMessages,
          ]
        }
      }

      return result
    },
  }
}
