import type { AgentConfig, LLMAdapter, ToolDefinition } from '@open-multi-agent/core'
import type { ReviewAgentScope } from '../review/package.js'
import { buildCompressionStrategy, createConfig } from './compression/index.js'

export const REVIEW_AGENT_NAMES = [
  'security-reviewer',
  'test-context-analyzer',
  'config-reviewer',
  'code-quality-reviewer',
] as const

export type ReviewAgentName = typeof REVIEW_AGENT_NAMES[number]

const AGENT_PROMPTS: Record<ReviewAgentName, string> = {
  'security-reviewer': `You are a security-focused PR reviewer. Look for auth, injection, secrets, permission, data exposure, and trust-boundary issues. Make claims only when supported by file, line, diff, or repository evidence.`,
  'test-context-analyzer': `You are a test-context reviewer. Evaluate whether changed behavior has adequate tests, meaningful assertions, regression coverage, and whether existing tests need updates.`,
  'config-reviewer': `You are a configuration reviewer. Check environment variables, dependencies, CI, build, deployment, and operational risk introduced by this PR.`,
  'code-quality-reviewer': `You are a code-quality reviewer. Focus on correctness, maintainability, error handling, API behavior, complexity, and consistency with local patterns.`,
}

const DEFAULT_TOOLS = [
  'read_file_patch',
  'search_diff',
  'search_repo',
  'read_repo_file',
  'search_tests_for',
  'read_repo_manifest',
  'verify_repo_context',
  'read_check_summary',
  'todo_write',
] as const

export interface ReviewAgentConfigOptions {
  model: string
  apiKey: string
  baseURL: string
  adapter: LLMAdapter
  customTools: ToolDefinition[] | ((name: ReviewAgentName) => ToolDefinition[])
  agentScopes?: Partial<Record<ReviewAgentName, ReviewAgentScope>>
}

export function createReviewAgentConfigs(options: ReviewAgentConfigOptions): AgentConfig[] {
  return REVIEW_AGENT_NAMES.map((name) => {
    const contextWindowTokens = name === 'security-reviewer' ? 256_000 : 128_000
    return {
      name,
      model: options.model,
      provider: 'openai',
      adapter: options.adapter,
      apiKey: options.apiKey,
      baseURL: options.baseURL,
      systemPrompt: [
        AGENT_PROMPTS[name],
        options.agentScopes?.[name]
          ? `You are operating with a scoped PR review package. Focus: ${options.agentScopes[name]?.focus}
Only request files and searches inside your assigned scope. If evidence is outside scope, report the missing context rather than broadening the review.`
          : undefined,
      ].filter(Boolean).join('\n\n'),
      customTools: typeof options.customTools === 'function' ? options.customTools(name) : options.customTools,
      tools: DEFAULT_TOOLS,
      contextStrategy: buildCompressionStrategy(
        options.adapter,
        options.model,
        'subagent',
        createConfig({ contextWindowTokens }),
      ),
      temperature: 0,
    }
  })
}
