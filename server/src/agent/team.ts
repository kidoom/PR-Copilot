import { OpenMultiAgent, createAdapter, type LLMAdapter, type OrchestratorConfig, type Team } from '@open-multi-agent/core'
import type { PRContext } from '../types/pr.js'
import type { CheckSummary } from '../github/checks.js'
import type { ServerConfig } from '../config.js'
import type { ReviewPackage, ReviewAgentName } from '../review/package.js'
import { createAllRepoTools } from './tools/index.js'
import { createBudget, createUsage } from './tools/budget.js'
import { createReviewAgentConfigs } from './agents.js'
import { createMockAdapter } from './mock-adapter.js'

export interface ReviewTeamInput {
  config: ServerConfig
  prContext: PRContext
  reviewPackage: ReviewPackage
  repoRoot: string
  checkSummary?: CheckSummary | null
  orchestratorConfig?: Partial<OrchestratorConfig>
}

export interface ReviewTeamRuntime {
  orchestrator: OpenMultiAgent
  team: Team
  adapter: LLMAdapter
}

export async function createReviewTeam(input: ReviewTeamInput): Promise<ReviewTeamRuntime> {
  const adapter = process.env.PR_COPILOT_MOCK_LLM === 'true'
    ? createMockAdapter()
    : await createAdapter('openai', input.config.llm.apiKey, input.config.llm.baseURL)

  const customTools = (agentName: ReviewAgentName) => {
    const scope = input.reviewPackage.agentScopes[agentName]
    return createAllRepoTools({
      prContext: input.prContext,
      repoRoot: input.repoRoot,
      checkSummary: input.checkSummary ?? null,
      budget: createBudget({
        maxFiles: scope.maxToolFiles,
        maxSearches: scope.maxToolSearches,
        maxTokens: scope.maxToolTokens,
      }),
      usage: createUsage(),
      expectedSha: input.prContext.head_sha,
      expectedBranch: input.prContext.head_branch,
      scope: {
        allowedFiles: scope.allowedFiles,
        searchPathScopes: scope.searchPathScopes,
      },
    })
  }

  const orchestrator = new OpenMultiAgent({
      defaultProvider: 'openai',
      defaultModel: input.config.llm.model,
      defaultApiKey: input.config.llm.apiKey,
      defaultBaseURL: input.config.llm.baseURL,
      ...(process.env.PR_COPILOT_MOCK_LLM === 'true' ? { defaultProvider: undefined, defaultApiKey: undefined, defaultBaseURL: undefined } : {}),
    maxConcurrency: input.config.review.maxConcurrency,
    ...input.orchestratorConfig,
  })

  const team = orchestrator.createTeam('pr-review', {
    name: 'pr-review',
    agents: createReviewAgentConfigs({
      model: input.config.llm.model,
      apiKey: input.config.llm.apiKey,
      baseURL: input.config.llm.baseURL,
      adapter,
      customTools,
      agentScopes: input.reviewPackage.agentScopes,
    }),
    sharedMemory: false,
    maxConcurrency: input.config.review.maxConcurrency,
  })

  return { orchestrator, team, adapter }
}
