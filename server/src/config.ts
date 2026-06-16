import * as path from 'node:path'
import dotenv from 'dotenv'

dotenv.config({ path: path.resolve(process.cwd(), '..', '.env.local'), override: false })
dotenv.config({ path: path.resolve(process.cwd(), '.env.local'), override: false })
dotenv.config({ override: false })

export interface ServerConfig {
  port: number
  github: {
    appId?: string
    privateKey?: string
    clientId?: string
    clientSecret?: string
    token?: string
  }
  llm: {
    apiKey: string
    baseURL: string
    model: string
  }
  storageDir: string
  review: {
    maxConcurrency: number
  }
}

function requireEnv(key: string): string {
  const value = process.env[key]
  if (!value) {
    throw new Error(`Missing required environment variable: ${key}`)
  }
  return value
}

export function loadConfig(): ServerConfig {
  return {
    port: parseInt(process.env.PORT ?? '8000', 10),
    github: {
      appId: process.env.GITHUB_APP_ID,
      privateKey: process.env.GITHUB_APP_PRIVATE_KEY,
      clientId: process.env.GITHUB_APP_CLIENT_ID,
      clientSecret: process.env.GITHUB_APP_CLIENT_SECRET,
      token: process.env.GITHUB_TOKEN,
    },
    llm: {
      apiKey: requireEnv('OPENAI_API_KEY'),
      baseURL: process.env.OPENAI_BASE_URL ?? 'https://api.openai.com/v1',
      model: process.env.OPENAI_MODEL ?? 'gpt-4o',
    },
    storageDir: process.env.PR_COPILOT_STORAGE_DIR ?? '~/.pr-copilot',
    review: {
      maxConcurrency: Math.max(1, parseInt(process.env.PR_COPILOT_REVIEW_MAX_CONCURRENCY ?? '1', 10)),
    },
  }
}
