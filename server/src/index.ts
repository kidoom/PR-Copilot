import express from 'express'
import cors from 'cors'
import { createServer } from 'node:http'
import { WebSocketServer } from 'ws'
import { loadConfig } from './config.js'
import { createOctokit } from './github/client.js'
import { healthRouter } from './routes/health.js'
import { createPrRouter } from './routes/pr.js'
import { createReviewRouter } from './routes/review.js'
import { setupReviewRunWebSockets } from './ws/events.js'
import { JsonSessionStore } from './store/session.js'

const config = loadConfig()
const app = express()
const server = createServer(app)

// Middleware
app.use(cors({ origin: true, credentials: true }))
app.use(express.json({ limit: '10mb' }))

// GitHub client
const octokit = createOctokit(config.github)
const store = new JsonSessionStore(config.storageDir)

// Routes
app.use(healthRouter)
app.use(createPrRouter(octokit, store))
app.use(createReviewRouter(config, process.cwd(), store))

// Error handling middleware
app.use((err: Error, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
  console.error('[Error]', err.message)
  res.status(500).json({ error: err.message })
})

// WebSocket server
const wss = new WebSocketServer({ server })
setupReviewRunWebSockets(wss, config.storageDir)

// Start server
server.listen(config.port, () => {
  console.log(`[PR-Copilot] Server running on port ${config.port}`)
  console.log(`[PR-Copilot] LLM model: ${config.llm.model}`)
})

export { app, server, wss }
