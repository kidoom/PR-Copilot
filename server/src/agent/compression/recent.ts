/**
 * Recent message selection — preserve the last N messages with tool-pair repair.
 */

import type { LLMMessage } from '@open-multi-agent/core'
import { repairToolMessagePairs } from './repair.js'

/**
 * Select recent messages with tool-pair-safe repair.
 *
 * Takes the last N messages and repairs any broken tool pairs.
 */
export function selectRecentMessages(messages: LLMMessage[], count: number): LLMMessage[] {
  if (!messages.length || count <= 0) return []
  const recent = messages.slice(-count)
  return repairToolMessagePairs(recent)
}
