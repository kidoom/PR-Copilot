/**
 * GitHub API client — wraps Octokit with App auth or token auth.
 */

import { Octokit } from '@octokit/rest'
import { createAppAuth } from '@octokit/auth-app'
import type { ServerConfig } from '../config.js'

export function createOctokit(config: ServerConfig['github']): Octokit {
  // Prefer GitHub App auth if configured
  if (config.appId && config.privateKey) {
    return new Octokit({
      authStrategy: createAppAuth,
      auth: {
        appId: config.appId,
        privateKey: config.privateKey,
        ...(config.clientId ? { clientId: config.clientId } : {}),
        ...(config.clientSecret ? { clientSecret: config.clientSecret } : {}),
      },
    })
  }

  // Fall back to personal token
  if (config.token) {
    return new Octokit({ auth: config.token })
  }

  throw new Error('No GitHub authentication configured. Set GITHUB_TOKEN or GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY')
}
