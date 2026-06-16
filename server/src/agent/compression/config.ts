/**
 * Compression configuration — thresholds, toggles, and derived properties.
 */

export interface CompressionConfig {
  /** Master toggle for all compression. */
  enabled: boolean
  /** Toggle micro-compact layer. */
  microCompactEnabled: boolean
  /** Toggle auto-compact (LLM summarization) layer. */
  autoCompactEnabled: boolean
  /** Context window size in tokens (default: 128K). */
  contextWindowTokens: number
  /** Buffer reserved for compact summary output. */
  compactMaxSummaryTokens: number
  /** Auto-compact triggers when estimated tokens exceed threshold. */
  autoCompactBufferTokens: number
  /** Number of recent tool results to keep unchanged in micro-compact. */
  microCompactRecentResults: number
  /** Minimum char count for micro-compact to replace a tool result. */
  microCompactMinChars: number
  /** Number of recent messages to preserve after compact summary. */
  compactRecentMessages: number
  /** Maximum retry count for compact summarization. */
  compactMaxRetries: number
}

/**
 * Derived threshold: tokens above this trigger auto-compact.
 */
export function autoCompactThreshold(config: CompressionConfig): number {
  return (
    config.contextWindowTokens -
    config.compactMaxSummaryTokens -
    config.autoCompactBufferTokens
  )
}

export function createConfig(overrides?: Partial<CompressionConfig>): CompressionConfig {
  return {
    enabled: true,
    microCompactEnabled: true,
    autoCompactEnabled: true,
    contextWindowTokens: 128_000,
    compactMaxSummaryTokens: 4000,
    autoCompactBufferTokens: 8000,
    microCompactRecentResults: 10,
    microCompactMinChars: 8000,
    compactRecentMessages: 10,
    compactMaxRetries: 2,
    ...overrides,
  }
}

export function disabledConfig(): CompressionConfig {
  return createConfig({ enabled: false })
}
