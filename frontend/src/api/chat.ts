/** Chat endpoints (docs/06-api.md, section Chat). */
import { api, apiFetch } from './client.ts'
import type { Status } from './documents.ts'

// Must match backend/app/chat/schemas.py (checked by backend/tests/test_chat.py).
export const QUESTION_MAX_CHARS = 4000
export const TITLE_MAX_CHARS = 200
export const PAGE_SIZE = 20

export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export interface ConversationList {
  items: Conversation[]
  total: number
}

/** Built by the backend from DB metadata only (CLAUDE.md rule 2). */
export interface Citation {
  n: number
  document_id: string
  title: string
  so_hieu: string | null
  doc_type: string | null
  dieu: string | null
  dieu_title: string | null
  issued_date: string | null
  effective_date: string | null
  status: Status
  status_label: string
  source: string
  source_url: string | null
  excerpt: string
  /** The model cited nothing: these are the top sources ("Nguồn tham khảo"). */
  uncited: boolean
}

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  provider: string | null
  model_id: string | null
  citations: Citation[]
  refused: boolean
  /** LLM error code, `retrieval_failed` or `cancelled` (assistant messages only). */
  error: string | null
  created_at: string
}

export function createConversation(): Promise<Conversation> {
  return api<Conversation>('/chat/conversations', { method: 'POST' })
}

export function listConversations(page: number, signal?: AbortSignal): Promise<ConversationList> {
  return api<ConversationList>(`/chat/conversations?page=${page}`, { signal })
}

export function listMessages(conversationId: string, signal?: AbortSignal): Promise<Message[]> {
  return api<Message[]>(`/chat/conversations/${conversationId}/messages`, { signal })
}

export function renameConversation(conversationId: string, title: string): Promise<Conversation> {
  return api<Conversation>(`/chat/conversations/${conversationId}`, {
    method: 'PATCH',
    body: { title },
  })
}

export function deleteConversation(conversationId: string): Promise<void> {
  return api<void>(`/chat/conversations/${conversationId}`, { method: 'DELETE' })
}

/**
 * Start the SSE answer stream. Resolves with the raw response once headers arrive; errors before
 * the stream (404, 422, ...) are thrown as `ApiError`, an abort as `DOMException('AbortError')`.
 */
export function postMessage(
  conversationId: string,
  content: string,
  modelId: string | null,
  signal: AbortSignal,
): Promise<Response> {
  return apiFetch(`/chat/conversations/${conversationId}/messages`, {
    method: 'POST',
    body: JSON.stringify({ content, model_id: modelId }),
    headers: { Accept: 'text/event-stream' },
    signal,
  })
}
