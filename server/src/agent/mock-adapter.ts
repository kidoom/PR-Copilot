import type { ContentBlock, LLMAdapter, LLMChatOptions, LLMMessage, LLMResponse, LLMStreamOptions, StreamEvent } from '@open-multi-agent/core'

function textFromMessages(messages: LLMMessage[]): string {
  return messages.map((message) => {
    if (!Array.isArray(message.content)) return ''
    return message.content.map((block) => block.type === 'text' ? (block as any).text ?? '' : '').join('\n')
  }).join('\n')
}

function response(content: string, model: string): LLMResponse {
  return {
    id: `mock-${Date.now()}`,
    content: [{ type: 'text', text: content } as ContentBlock],
    model,
    stop_reason: 'stop',
    usage: {
      input_tokens: 1,
      output_tokens: Math.max(1, Math.ceil(content.length / 4)),
    },
  }
}

function mockOutput(messages: LLMMessage[]): string {
  const text = textFromMessages(messages)

  if (/Decompose the following goal/i.test(text)) {
    return '```json\n[{"title":"smoke-review","description":"Run a deterministic smoke review for the supplied PR context.","assignee":"code-quality-reviewer","dependsOn":[]}]\n```'
  }

  if (/Synthesize|final answer|all task outputs/i.test(text)) {
    return '```json\n[{"file":"src/example.ts","line":1,"severity":"info","category":"code-quality","title":"Mock smoke finding","description":"Deterministic mock finding used to verify the review pipeline and WebSocket terminal payload.","evidence":[{"file":"src/example.ts","line":1,"snippet":"mock evidence","tool":"mock"}],"suggestion":"Replace mock mode with a real LLM for production reviews."}]\n```'
  }

  return 'Mock agent completed the delegated review task with deterministic evidence.'
}

export function createMockAdapter(): LLMAdapter {
  return {
    name: 'mock',
    async chat(messages: LLMMessage[], options: LLMChatOptions): Promise<LLMResponse> {
      return response(mockOutput(messages), options.model)
    },
    async *stream(messages: LLMMessage[], options: LLMStreamOptions): AsyncIterable<StreamEvent> {
      const output = mockOutput(messages)
      yield { type: 'text', data: output }
      yield { type: 'done', data: response(output, options.model) }
    },
  }
}
