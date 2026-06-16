/**
 * Micro-compact — replace old large tool results with placeholders.
 *
 * Only targets compactable tools (not evidence-critical), keeps recent
 * results unchanged, never mutates the original message array.
 */

import type { LLMMessage, ToolUseBlock, ToolResultBlock, ContentBlock } from '@open-multi-agent/core'
// ContentBlock is used in the type assertion below

/** Tools whose results can be compacted. */
const COMPACTABLE_TOOLS = new Set([
  'read_file_patch',
  'search_repo',
  'search_tests_for',
  'read_repo_manifest',
  'read_check_summary',
  'search_diff',
])

/** Tools that are critical evidence sources — never compact. */
const EVIDENCE_CRITICAL_TOOLS = new Set(['read_repo_file'])

/** Tools that should never be compacted. */
const EXCLUDED_TOOLS = new Set(['finish_context_package', 'todo_write'])

/**
 * Deep clone messages for safe mutation.
 */
function cloneMessages(messages: LLMMessage[]): LLMMessage[] {
  // Structured clone handles the readonly arrays
  return messages.map(msg => ({
    ...msg,
    content: Array.isArray(msg.content)
      ? msg.content.map(block => ({ ...block }))
      : msg.content,
  }))
}

interface ToolInfo {
  name: string
  input: Record<string, unknown>
}

function buildPlaceholder(toolName: string, originalChars: number, toolInput?: Record<string, unknown>): string {
  const parts = [`[Compacted: ${toolName}]`, `Original size: ${originalChars.toLocaleString()} chars`]

  if (toolInput) {
    if (typeof toolInput.path === 'string') parts.push(`File: ${toolInput.path}`)
    if (typeof toolInput.query === 'string') parts.push(`Query: ${toolInput.query}`)
    if (typeof toolInput.filename === 'string') parts.push(`File: ${toolInput.filename}`)
  }

  return parts.join(' | ')
}

/**
 * Create a compacted copy of messages for the model request.
 *
 * Replaces only old, large, successful, compactable tool results with
 * placeholders.  Never mutates the original messages.
 *
 * @param messages   Original messages.
 * @param recentCount  Number of recent tool results to keep unchanged.
 * @param minChars     Minimum char count to consider for compaction.
 */
export function microCompactMessages(
  messages: LLMMessage[],
  recentCount = 3,
  minChars = 1000,
): LLMMessage[] {
  if (!messages.length) return []

  const compacted = cloneMessages(messages)

  // Collect all tool_result positions: [msgIdx, blockIdx]
  const toolResultPositions: Array<[number, number]> = []
  for (let i = 0; i < compacted.length; i++) {
    const msg = compacted[i]
    if (!Array.isArray(msg.content)) continue
    for (let j = 0; j < msg.content.length; j++) {
      if (msg.content[j].type === 'tool_result') {
        toolResultPositions.push([i, j])
      }
    }
  }

  // Keep last N tool results unchanged
  const recentResultIds = new Set<string>()
  if (recentCount > 0) {
    const recentPositions = toolResultPositions.slice(-recentCount)
    for (const [msgIdx, blockIdx] of recentPositions) {
      const block = compacted[msgIdx].content[blockIdx] as ToolResultBlock
      recentResultIds.add(block.tool_use_id)
    }
  }

  // Build map: tool_use_id -> { name, input }
  const toolInfo = new Map<string, ToolInfo>()
  for (const msg of compacted) {
    if (!Array.isArray(msg.content)) continue
    for (const block of msg.content) {
      if (block.type === 'tool_use') {
        const tb = block as ToolUseBlock
        toolInfo.set(tb.id, { name: tb.name, input: tb.input })
      }
    }
  }

  // Compact eligible tool results
  for (const [msgIdx, blockIdx] of toolResultPositions) {
    const msg = compacted[msgIdx]
    if (!Array.isArray(msg.content)) continue

    const block = msg.content[blockIdx] as ToolResultBlock

    // Skip recent results
    if (recentResultIds.has(block.tool_use_id)) continue

    // Get tool info
    const info = toolInfo.get(block.tool_use_id) ?? { name: 'unknown', input: {} }

    // Check compactability
    if (EXCLUDED_TOOLS.has(info.name)) continue
    if (EVIDENCE_CRITICAL_TOOLS.has(info.name)) continue
    if (!COMPACTABLE_TOOLS.has(info.name)) continue
    if (block.is_error) continue
    if (block.content.length < minChars) continue

    // Replace with placeholder — create new message since content is readonly
    const placeholder = buildPlaceholder(info.name, block.content.length, info.input)
    const newContent = msg.content.map((b, idx) =>
      idx === blockIdx
        ? { type: 'tool_result' as const, tool_use_id: block.tool_use_id, content: placeholder }
        : b,
    ) as unknown as ContentBlock[]
    compacted[msgIdx] = { ...msg, content: newContent }
  }

  return compacted
}
