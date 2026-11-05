import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { postMessage } from '../api/chat.ts'
import {
  applyEvent,
  failed,
  initialStreamState,
  interrupted,
  isStreamActive,
  needsSettings,
  type StreamState,
} from '../lib/chat.ts'
import { SseParser } from '../lib/sse.ts'
import { CONVERSATIONS_KEY, messagesKey } from './useChat.ts'
import { SETTINGS_QUERY_KEY } from './useSettings.ts'

// The backend saves a cancelled answer after the client disconnects: refetch again a bit later.
const CANCEL_REFETCH_DELAY_MS = 1500

function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError'
}

/**
 * One answer stream at a time (docs/07-frontend.md `useChatStream`). `send` POSTs the question,
 * reads the SSE body and updates `{text, citations, status, error}`; `stop` aborts it. When the
 * stream ends, the conversation's messages and the conversation list are refetched. The stream is
 * aborted when the component unmounts (the backend keeps the partial answer as `cancelled`).
 */
export function useChatStream() {
  const queryClient = useQueryClient()
  const [state, setState] = useState<StreamState | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => () => abortRef.current?.abort(), [])

  const send = useCallback(
    async (conversationId: string, question: string, modelId: string | null) => {
      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller

      let current = initialStreamState(conversationId, question)
      const update = (next: StreamState) => {
        current = next
        // A newer `send` replaced this stream: its late events must not overwrite the new state.
        if (abortRef.current === controller || abortRef.current === null) setState(next)
      }
      update(current)

      // The conversation list (title, order) changes when the question is saved, refetched at
      // `start`; at the end only the messages need reloading.
      const refresh = () => {
        void queryClient.invalidateQueries({ queryKey: messagesKey(conversationId) })
      }

      try {
        const res = await postMessage(conversationId, question, modelId, controller.signal)
        if (!res.body) throw new Error('empty body')
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        const parser = new SseParser()
        for (;;) {
          const { done, value } = await reader.read()
          const chunk = done ? decoder.decode() : decoder.decode(value, { stream: true })
          // One state update per network chunk, not per token: each update re-renders the
          // whole answer's markdown.
          let next = current
          let started = false
          for (const event of [...parser.push(chunk), ...(done ? parser.finish() : [])]) {
            next = applyEvent(next, event)
            if (event.event === 'start') started = true
          }
          if (next !== current) update(next)
          // The question is saved and the title set: show it in the sidebar right away.
          if (started) void queryClient.invalidateQueries({ queryKey: CONVERSATIONS_KEY })
          if (done) break
        }
        if (isStreamActive(current)) update(interrupted(current))
      } catch (err) {
        if (isAbort(err)) {
          update({ ...current, status: 'cancelled' })
          setTimeout(refresh, CANCEL_REFETCH_DELAY_MS)
        } else if (isStreamActive(current)) {
          // A read error after `done` / `error` keeps the finished state.
          update(current.messageId ? interrupted(current) : failed(current, err))
        }
      } finally {
        if (abortRef.current === controller) abortRef.current = null
        refresh()
        // The key may have been deleted or replaced elsewhere: show the current Settings state.
        if (needsSettings(current.error?.code)) {
          void queryClient.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY })
        }
      }
      return current
    },
    [queryClient],
  )

  const stop = useCallback(() => abortRef.current?.abort(), [])
  const reset = useCallback(() => setState(null), [])

  return { state, send, stop, reset }
}
