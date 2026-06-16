/**
 * todo_write tool — allows the agent to write structured todo items
 * for tracking review progress. Stored in shared memory.
 */

import { z } from 'zod'
import { defineTool, type ToolDefinition, type ToolResult } from '@open-multi-agent/core'

export interface TodoItem {
  id: string
  content: string
  status: 'pending' | 'in_progress' | 'done'
  priority: 'high' | 'medium' | 'low'
}

export function createTodoWriteTool(): ToolDefinition {
  return defineTool({
    name: 'todo_write',
    description: 'Write a structured todo item for tracking review progress. Use to plan and track analysis steps.',
    inputSchema: z.object({
      id: z.string().describe('Unique identifier for the todo item'),
      content: z.string().describe('Description of the todo item'),
      status: z.enum(['pending', 'in_progress', 'done']).describe('Current status'),
      priority: z.enum(['high', 'medium', 'low']).optional().describe('Priority level (default: medium)'),
    }),
    execute: async (input, _ctx): Promise<ToolResult> => {
      const item: TodoItem = {
        id: input.id,
        content: input.content,
        status: input.status,
        priority: input.priority ?? 'medium',
      }

      // The todo is returned as data — the orchestrator/agent runtime
      // can collect these from tool results if needed.
      return {
        data: JSON.stringify({
          status: 'ok',
          todo: item,
          message: `Todo "${item.id}" updated to ${item.status}`,
        }),
      }
    },
  })
}
