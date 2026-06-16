/**
 * Repair tool-use/tool-result pairs — fix orphan blocks.
 *
 * Every tool_result must have a matching tool_use, and vice versa.
 * Orphan results are removed; orphan uses are removed.
 */

import type { LLMMessage, ToolUseBlock, ToolResultBlock, ContentBlock } from '@open-multi-agent/core'

/**
 * Repair tool-use and tool-result pairs in a message list.
 *
 * - Removes orphan tool_result blocks (no matching tool_use)
 * - Removes orphan tool_use blocks (no matching tool_result)
 * - Preserves text content in assistant messages
 */
export function repairToolMessagePairs(messages: LLMMessage[]): LLMMessage[] {
  if (!messages.length) return messages

  // Collect tool_use ids and tool_result refs
  const toolUseIds = new Set<string>()
  const toolResultRefs = new Set<string>()

  for (const msg of messages) {
    if (!Array.isArray(msg.content)) continue
    for (const block of msg.content) {
      if (block.type === 'tool_use') {
        toolUseIds.add((block as ToolUseBlock).id)
      } else if (block.type === 'tool_result') {
        toolResultRefs.add((block as ToolResultBlock).tool_use_id)
      }
    }
  }

  const orphanResults = new Set([...toolResultRefs].filter(id => !toolUseIds.has(id)))
  const orphanUses = new Set([...toolUseIds].filter(id => !toolResultRefs.has(id)))

  // If no orphans, return as-is
  if (orphanResults.size === 0 && orphanUses.size === 0) return messages

  // Repair messages
  const repaired: LLMMessage[] = []

  for (const msg of messages) {
    if (msg.role === 'assistant' && Array.isArray(msg.content)) {
      // Filter out orphan tool_use blocks
      const validBlocks: ContentBlock[] = []
      let hasOrphan = false

      for (const block of msg.content) {
        if (block.type === 'tool_use' && orphanUses.has((block as ToolUseBlock).id)) {
          hasOrphan = true
        } else {
          validBlocks.push(block)
        }
      }

      if (hasOrphan) {
        // Only keep if there's meaningful content left
        if (validBlocks.length > 0) {
          repaired.push({ role: 'assistant', content: validBlocks })
        }
        // Skip entirely empty assistant messages
      } else {
        repaired.push(msg)
      }
    } else if (msg.role === 'user' && Array.isArray(msg.content)) {
      // Filter out orphan tool_result blocks
      const validBlocks = msg.content.filter(
        block => !(block.type === 'tool_result' && orphanResults.has((block as ToolResultBlock).tool_use_id))
      )
      if (validBlocks.length > 0) {
        repaired.push({ role: 'user', content: validBlocks })
      }
    } else {
      repaired.push(msg)
    }
  }

  return repaired
}
