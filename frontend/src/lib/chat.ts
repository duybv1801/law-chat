/** Pure helpers for the chat tab: stream state, `[n]` markers, citations, errors. */
import { ApiError } from '../api/client.ts'
import type { Citation, Conversation, Message } from '../api/chat.ts'
import type { ModelInfo } from '../api/settings.ts'
import { errorMessage } from './errors.ts'
import type { SseEvent } from './sse.ts'

// --- Errors ------------------------------------------------------------------------------

/**
 * Text for error codes stored on assistant messages (`Message.error`) or sent without a usable
 * message. Must match backend `USER_MESSAGES` (app/llm/errors.py) and `CHAT_ERROR_MESSAGES`
 * (app/rag/service.py), checked by backend/tests/test_chat.py. `no_api_key` is generic here
 * (the backend fills in the provider name).
 */
export const CHAT_ERROR_TEXT: Record<string, string> = {
  invalid_key: 'API key không hợp lệ hoặc đã bị thu hồi. Vào Cài đặt để cập nhật.',
  access_denied: 'API key hợp lệ nhưng không có quyền dùng model này (hoặc khu vực bị chặn). Kiểm tra quyền truy cập trong tài khoản nhà cung cấp.',
  quota_exceeded: 'Tài khoản nhà cung cấp đã hết hạn mức hoặc hết số dư. Nạp thêm tiền hoặc kiểm tra gói dịch vụ rồi thử lại.',
  rate_limited: 'Nhà cung cấp đang giới hạn tần suất. Vui lòng thử lại sau ít phút.',
  context_too_long: 'Nội dung quá dài so với giới hạn của model. Hãy rút gọn câu hỏi.',
  request_rejected: 'Nhà cung cấp từ chối yêu cầu (model không tồn tại, tham số hoặc nội dung không được chấp nhận). Thử model khác hoặc báo quản trị viên.',
  provider_error: 'Nhà cung cấp mô hình đang gặp lỗi. Vui lòng thử lại sau.',
  not_found: 'Không tìm thấy cuộc hội thoại.',
  no_model: 'Bạn chưa chọn model. Vào Cài đặt để chọn model mặc định.',
  unknown_model: 'Model không tồn tại hoặc đã bị tắt. Chọn model khác.',
  no_api_key: 'Bạn chưa nhập API key cho nhà cung cấp của model này. Vào Cài đặt để thêm key.',
  key_unreadable: 'Không đọc được API key đã lưu. Vào Cài đặt để nhập lại key.',
  retrieval_failed: 'Không truy vấn được cơ sở dữ liệu văn bản. Vui lòng thử lại sau.',
  save_failed: 'Không lưu được câu trả lời. Vui lòng thử lại sau.',
  stream_interrupted: 'Mất kết nối khi đang nhận câu trả lời. Vui lòng thử lại.',
  cancelled: 'Đã dừng.',
}

/** Errors fixed in the Settings page: shown with a "Đi tới Cài đặt" button. */
export const SETTINGS_ERROR_CODES = ['invalid_key', 'no_api_key', 'key_unreadable', 'no_model']

export function needsSettings(code: string | null | undefined): boolean {
  return code != null && SETTINGS_ERROR_CODES.includes(code)
}

export function chatErrorText(code: string): string {
  return CHAT_ERROR_TEXT[code] ?? CHAT_ERROR_TEXT.provider_error
}

export interface ChatErrorInfo {
  code: string
  message: string
}

// --- Stream state --------------------------------------------------------------------------

export type StreamStatus = 'connecting' | 'streaming' | 'done' | 'error' | 'cancelled'

export interface StreamState {
  conversationId: string
  question: string
  /** Assistant message id from the `start` event; `null` until the backend started answering. */
  messageId: string | null
  modelId: string | null
  text: string
  /** `null` until the `citations` event. */
  citations: Citation[] | null
  refused: boolean
  status: StreamStatus
  error: ChatErrorInfo | null
}

export function initialStreamState(conversationId: string, question: string): StreamState {
  return {
    conversationId,
    question,
    messageId: null,
    modelId: null,
    text: '',
    citations: null,
    refused: false,
    status: 'connecting',
    error: null,
  }
}

export function isStreamActive(state: StreamState | null): boolean {
  return state?.status === 'connecting' || state?.status === 'streaming'
}

function parseData(data: string): Record<string, unknown> | null {
  try {
    const value: unknown = JSON.parse(data)
    return value && typeof value === 'object' ? (value as Record<string, unknown>) : null
  } catch {
    return null
  }
}

/** Apply one SSE event (`start → token* → citations → done`, or `error`) to the stream state. */
export function applyEvent(state: StreamState, { event, data }: SseEvent): StreamState {
  const payload = parseData(data)
  if (!payload) return state
  switch (event) {
    case 'start':
      return {
        ...state,
        status: 'streaming',
        messageId: typeof payload.message_id === 'string' ? payload.message_id : null,
        modelId: typeof payload.model_id === 'string' ? payload.model_id : null,
      }
    case 'token':
      return typeof payload.t === 'string'
        ? { ...state, status: 'streaming', text: state.text + payload.t }
        : state
    case 'citations':
      return {
        ...state,
        citations: Array.isArray(payload.citations) ? (payload.citations as Citation[]) : [],
      }
    case 'done':
      return { ...state, status: 'done', refused: payload.refused === true }
    case 'error': {
      const code = typeof payload.code === 'string' ? payload.code : 'provider_error'
      const message =
        typeof payload.message === 'string' && payload.message
          ? payload.message
          : chatErrorText(code)
      return { ...state, status: 'error', error: { code, message } }
    }
    default:
      return state
  }
}

/** The body ended without `done` / `error`. */
export function interrupted(state: StreamState): StreamState {
  return {
    ...state,
    status: 'error',
    error: { code: 'stream_interrupted', message: chatErrorText('stream_interrupted') },
  }
}

/** The request failed before the stream (404, network, ...). */
export function failed(state: StreamState, err: unknown): StreamState {
  const code = err instanceof ApiError ? err.code : 'network_error'
  return { ...state, status: 'error', error: { code, message: errorMessage(err) } }
}

/**
 * Whether the local (streamed) copy of an answer must still be shown: until the refetched
 * message list contains it. An error before `start` saved nothing and is shown as a banner.
 */
export function showStreamBubble(
  state: StreamState | null,
  conversationId: string | null,
  messages: Message[] | undefined,
): boolean {
  if (!state || state.conversationId !== conversationId) return false
  if (state.status === 'connecting') return true
  if (!state.messageId) return false
  return !messages?.some((m) => m.id === state.messageId)
}

// --- Markers -------------------------------------------------------------------------------

// Same grammar as backend app/rag/citations.py `MARKER`: "[1]", "[1, 2]", "[1;2]", "[1-3]".
const ITEM = String.raw`\d{1,3}(?:\s*[-–—]\s*\d{1,3})?`
const MARKER = new RegExp(String.raw`\[\s*(${ITEM}(?:\s*[,;]\s*${ITEM})*)\s*\]`, 'g')
const RANGE = /^(\d+)\s*[-–—]\s*(\d+)$/
const MAX_RANGE = 20

export const CITE_HREF_PREFIX = '#cite-'

function markerNumbers(inner: string): number[] {
  const result: number[] = []
  for (const raw of inner.split(/[,;]/)) {
    const item = raw.trim()
    const range = RANGE.exec(item)
    if (!range) {
      result.push(Number(item))
      continue
    }
    const start = Number(range[1])
    const end = Number(range[2])
    if (start <= end && end <= start + MAX_RANGE) {
      for (let n = start; n <= end; n++) result.push(n)
    } else {
      result.push(start, end)
    }
  }
  return [...new Set(result)]
}

/**
 * Split text at `[n]` markers: strings stay text, numbers become cite chips.
 * `valid` is the set of cited source numbers; `null` (still streaming) accepts every number.
 * A marker whose numbers are all unknown is left as plain text.
 */
export function splitMarkers(text: string, valid: ReadonlySet<number> | null): (string | number)[] {
  const parts: (string | number)[] = []
  let last = 0
  for (const match of text.matchAll(MARKER)) {
    const numbers = markerNumbers(match[1]).filter((n) => n > 0 && (valid === null || valid.has(n)))
    if (numbers.length === 0) continue
    if (match.index > last) parts.push(text.slice(last, match.index))
    parts.push(...numbers)
    last = match.index + match[0].length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}

/** The subset of an mdast node this module reads / writes. */
export interface MdNode {
  type: string
  value?: string
  url?: string
  children?: MdNode[]
}

/**
 * Replace `[n]` markers in mdast text nodes with links to `#cite-n` (rendered as chips).
 * Works on the parsed tree, so code spans, code blocks and links written by the model are
 * left untouched (a regex over the markdown source would rewrite `[1](url)` and `` `a[1]` ``).
 */
export function linkMarkersInTree(node: MdNode, valid: ReadonlySet<number> | null): void {
  if (!node.children || node.type === 'link' || node.type === 'linkReference') return
  const children: MdNode[] = []
  for (const child of node.children) {
    if (child.type !== 'text' || child.value === undefined) {
      linkMarkersInTree(child, valid)
      children.push(child)
      continue
    }
    const parts = splitMarkers(child.value, valid)
    if (parts.length === 1 && typeof parts[0] === 'string') {
      children.push(child)
      continue
    }
    for (const part of parts) {
      children.push(
        typeof part === 'string'
          ? { type: 'text', value: part }
          : { type: 'link', url: `${CITE_HREF_PREFIX}${part}`, children: [{ type: 'text', value: String(part) }] },
      )
    }
  }
  node.children = children
}

/** Remark plugin wrapping `linkMarkersInTree`. */
export function remarkCiteMarkers(valid: ReadonlySet<number> | null) {
  return (tree: MdNode) => linkMarkersInTree(tree, valid)
}

/** `#cite-3` → 3; anything else → `null`. */
export function citeNumber(href: string | undefined): number | null {
  if (!href?.startsWith(CITE_HREF_PREFIX)) return null
  const n = Number(href.slice(CITE_HREF_PREFIX.length))
  return Number.isInteger(n) && n > 0 ? n : null
}

/** DOM id of a source card. */
export function citationElementId(messageId: string, n: number): string {
  return `cite-${messageId}-${n}`
}

// --- Citations -----------------------------------------------------------------------------

/** `Tên văn bản (Số hiệu) — Điều X. Tiêu đề`. */
export function citationHeading(c: Citation): string {
  let heading = c.so_hieu ? `${c.title} (${c.so_hieu})` : c.title
  if (c.dieu) heading += ` — ${c.dieu_title ? `${c.dieu}. ${c.dieu_title}` : c.dieu}`
  return heading
}

/** All citations of the answer are the uncited top sources. */
export function isUncited(citations: Citation[]): boolean {
  return citations.length > 0 && citations.every((c) => c.uncited)
}

// --- Models & conversations ----------------------------------------------------------------

/** The model to send: the user's pick, else the default from Settings, else the first selectable. */
export function effectiveModelId(
  picked: string | null,
  defaultModelId: string | null,
  selectable: ModelInfo[],
): string | null {
  const ids = new Set(selectable.map((m) => m.id))
  if (picked && ids.has(picked)) return picked
  if (defaultModelId && ids.has(defaultModelId)) return defaultModelId
  return selectable[0]?.id ?? null
}

/** Flatten paged conversation lists, dropping duplicates (pages shift when one is updated). */
export function flattenConversations(pages: { items: Conversation[] }[] | undefined): Conversation[] {
  const seen = new Set<string>()
  const result: Conversation[] = []
  for (const page of pages ?? []) {
    for (const c of page.items) {
      if (seen.has(c.id)) continue
      seen.add(c.id)
      result.push(c)
    }
  }
  return result
}

/** Enter sends, Shift+Enter is a newline; never while an IME (e.g. Vietnamese Telex) composes. */
export function isSendKey(e: { key: string; shiftKey: boolean; isComposing: boolean }): boolean {
  return e.key === 'Enter' && !e.shiftKey && !e.isComposing
}
