import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { ApiError } from '../api/client.ts'
import { QUESTION_MAX_CHARS, type Citation, type Message } from '../api/chat.ts'
import AssistantMessage, { ChatErrorNotice } from '../components/chat/AssistantMessage.tsx'
import ConversationSidebar from '../components/chat/ConversationSidebar.tsx'
import { primaryButton, Spinner } from '../components/ui.tsx'
import { useCreateConversation, useDeleteConversation, useMessages } from '../hooks/useChat.ts'
import { useChatStream } from '../hooks/useChatStream.ts'
import { useModels, useSettings } from '../hooks/useSettings.ts'
import {
  chatErrorText,
  effectiveModelId,
  isSendKey,
  isStreamActive,
  showStreamBubble,
  type ChatErrorInfo,
} from '../lib/chat.ts'
import { errorMessage } from '../lib/errors.ts'
import { selectableModels } from '../lib/settings.ts'

// Distance from the bottom (px) under which new tokens keep the view scrolled to the end.
const STICK_THRESHOLD_PX = 80

// Stable empty list: a new `[]` per render would defeat the answer's markdown memo.
const EMPTY_CITATIONS: Citation[] = []

const NO_KEY_NOTICE: ChatErrorInfo = {
  code: 'no_api_key',
  message: 'Bạn cần thêm API key trong Cài đặt để bắt đầu hỏi đáp',
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <p className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-sm bg-slate-800 px-4 py-2 text-sm text-white">
        {text}
      </p>
    </div>
  )
}

function storedError(message: Message): ChatErrorInfo | null {
  if (!message.error || message.error === 'cancelled') return null
  return { code: message.error, message: chatErrorText(message.error) }
}

export default function ChatPage() {
  const { conversationId = null } = useParams()
  const navigate = useNavigate()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const [pickedModel, setPickedModel] = useState<string | null>(null)
  const [sendError, setSendError] = useState<{ conversationId: string | null; error: ChatErrorInfo } | null>(null)

  const settings = useSettings()
  const models = useModels()
  const messages = useMessages(conversationId)
  const createConversation = useCreateConversation()
  const deleteConversation = useDeleteConversation()
  const stream = useChatStream()

  const selectable = useMemo(
    () => selectableModels(models.data ?? [], settings.data?.api_keys ?? []),
    [models.data, settings.data],
  )
  const modelId = effectiveModelId(pickedModel, settings.data?.default_model_id ?? null, selectable)
  const modelLabels = useMemo(
    () => new Map((models.data ?? []).map((m) => [m.id, m.label])),
    [models.data],
  )
  const noKey = settings.isSuccess && settings.data.api_keys.length === 0

  const streaming = isStreamActive(stream.state)
  const streamHere = stream.state?.conversationId === conversationId ? stream.state : null
  const showBubble = showStreamBubble(stream.state, conversationId, messages.data)
  // The question is saved before `start`: a refetch during the stream already contains it.
  const lastMessage = messages.data?.at(-1)
  const shownMessages =
    showBubble && lastMessage?.role === 'user' && lastMessage.content === stream.state?.question
      ? messages.data?.slice(0, -1)
      : messages.data

  // --- Scrolling -------------------------------------------------------------------------
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(true)
  const onScroll = () => {
    const el = scrollRef.current
    if (el) stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD_PX
  }
  // Declared before the scroll effect: layout effects run in order, so a newly opened
  // conversation (even one already cached) scrolls to its end.
  const currentIdRef = useRef(conversationId)
  useLayoutEffect(() => {
    stickRef.current = true
    currentIdRef.current = conversationId
  }, [conversationId])
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (el && stickRef.current) el.scrollTop = el.scrollHeight
  }, [messages.data, streamHere?.text, streamHere?.status, showBubble, conversationId])

  // --- Input -----------------------------------------------------------------------------
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    textareaRef.current?.focus()
  }, [conversationId])

  const question = draft.trim()
  const tooLong = question.length > QUESTION_MAX_CHARS
  const blocked = noKey || streaming || createConversation.isPending
  const canSend = !blocked && question.length > 0 && !tooLong

  const submit = useCallback(async () => {
    if (!canSend) return
    setSendError(null)
    stickRef.current = true
    let targetId = conversationId
    const created = !targetId
    if (!targetId) {
      try {
        targetId = (await createConversation.mutateAsync()).id
      } catch (err) {
        setSendError({ conversationId, error: { code: 'create_failed', message: errorMessage(err) } })
        return
      }
      navigate(`/chat/${targetId}`)
    }
    setDraft('')
    const final = await stream.send(targetId, question, modelId)
    // The answer never started (error or stop before `start`): give the question back.
    if (!final.messageId && (final.status === 'error' || final.status === 'cancelled')) {
      // An error before `start` saved nothing (the backend rolls back): don't leave the
      // conversation created for this question behind as an empty one.
      const discard = created && final.status === 'error'
      if (discard) {
        void deleteConversation.mutateAsync(targetId).catch(() => {})
        if (currentIdRef.current === targetId) navigate('/chat', { replace: true })
      }
      if (final.error) setSendError({ conversationId: discard ? null : targetId, error: final.error })
      setDraft((current) => current || question)
    }
  }, [canSend, conversationId, createConversation, deleteConversation, navigate, stream, question, modelId])

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    void submit()
  }

  // --- Navigation ------------------------------------------------------------------------
  const onNew = () => {
    setDrawerOpen(false)
    setSendError(null)
    navigate('/chat')
  }
  const onDeleted = (id: string) => {
    if (stream.state?.conversationId === id) stream.reset()
    if (id === conversationId) navigate('/chat', { replace: true })
  }

  const notFound =
    messages.error instanceof ApiError && (messages.error.status === 404 || messages.error.status === 422)
  const banner = sendError && sendError.conversationId === conversationId ? sendError.error : null

  const sidebar = (
    <ConversationSidebar
      activeId={conversationId}
      streamingId={streaming ? (stream.state?.conversationId ?? null) : null}
      onNew={onNew}
      onNavigate={() => setDrawerOpen(false)}
      onDeleted={onDeleted}
    />
  )

  return (
    <div className="flex h-[calc(100dvh-3.5rem-1px)]">
      {/* Sidebar: fixed column on desktop, drawer on mobile. Mounted once, so rename / delete
          state is not split between two copies. */}
      {drawerOpen && (
        <div className="fixed inset-0 z-30 bg-slate-900/40 md:hidden" onClick={() => setDrawerOpen(false)} />
      )}
      <aside
        {...(drawerOpen ? { role: 'dialog', 'aria-modal': true, 'aria-label': 'Hội thoại' } : {})}
        className={
          drawerOpen
            ? 'fixed inset-y-0 left-0 z-40 w-72 max-w-[85%] bg-white shadow-xl md:static md:z-auto md:w-64 md:max-w-none md:shrink-0 md:border-r md:border-slate-200 md:shadow-none'
            : 'hidden w-64 shrink-0 border-r border-slate-200 bg-white md:block'
        }
      >
        {sidebar}
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-slate-200 bg-white px-3 py-2 md:hidden">
          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            className="rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-700"
          >
            ☰ Hội thoại
          </button>
        </div>

        <div ref={scrollRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
            {conversationId && messages.isPending && (
              <p className="flex items-center gap-2 text-sm text-slate-500">
                <Spinner dark /> Đang tải hội thoại…
              </p>
            )}
            {notFound ? (
              <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-sm text-slate-600">
                <p>Không tìm thấy cuộc hội thoại.</p>
                <Link to="/chat" className="mt-2 inline-block font-medium text-sky-700 hover:underline">
                  Bắt đầu hội thoại mới
                </Link>
              </div>
            ) : (
              messages.isError && <ChatErrorNotice error={{ code: 'load_failed', message: errorMessage(messages.error) }} />
            )}

            {!conversationId && !showBubble && (
              <div className="py-10 text-center">
                <h1 className="text-lg font-semibold text-slate-800">Hỏi đáp pháp luật</h1>
                <p className="mx-auto mt-2 max-w-md text-sm text-slate-600">
                  Câu trả lời chỉ dựa trên văn bản trong hệ thống, kèm nguồn trích dẫn, ngày ban hành, ngày hiệu
                  lực và tình trạng hiệu lực.
                </p>
              </div>
            )}

            {shownMessages?.map((m) =>
              m.role === 'user' ? (
                <UserBubble key={m.id} text={m.content} />
              ) : m.role === 'assistant' ? (
                <AssistantMessage
                  key={m.id}
                  messageId={m.id}
                  text={m.content}
                  citations={m.citations}
                  cancelled={m.error === 'cancelled'}
                  error={storedError(m)}
                  modelLabel={m.model_id ? (modelLabels.get(m.model_id) ?? m.model_id) : null}
                />
              ) : null,
            )}

            {showBubble && stream.state && (
              <>
                <UserBubble text={stream.state.question} />
                <AssistantMessage
                  messageId={stream.state.messageId}
                  text={stream.state.text}
                  citations={stream.state.citations ?? (streaming ? null : EMPTY_CITATIONS)}
                  pending={stream.state.status === 'connecting' || (streaming && !stream.state.text)}
                  streaming={streaming}
                  cancelled={stream.state.status === 'cancelled'}
                  error={stream.state.error}
                  modelLabel={
                    stream.state.modelId ? (modelLabels.get(stream.state.modelId) ?? stream.state.modelId) : null
                  }
                />
              </>
            )}
          </div>
        </div>

        <div className="border-t border-slate-200 bg-white">
          <form onSubmit={onSubmit} className="mx-auto max-w-3xl space-y-2 px-4 py-3">
            {noKey && <ChatErrorNotice tone="warning" error={NO_KEY_NOTICE} />}
            {banner && <ChatErrorNotice error={banner} />}

            {selectable.length > 0 && (
              <div className="flex items-center gap-2">
                <label htmlFor="chat-model" className="text-xs text-slate-500">
                  Model
                </label>
                <select
                  id="chat-model"
                  value={modelId ?? ''}
                  onChange={(e) => setPickedModel(e.target.value)}
                  disabled={streaming}
                  className="min-w-0 max-w-full rounded-md border border-slate-300 bg-white px-2 py-1 text-xs text-slate-700 focus:border-slate-500 focus:outline-none"
                >
                  {selectable.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}
                    </option>
                  ))}
                </select>
              </div>
            )}

            <div className="flex items-end gap-2">
              <textarea
                ref={textareaRef}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (isSendKey({ key: e.key, shiftKey: e.shiftKey, isComposing: e.nativeEvent.isComposing })) {
                    e.preventDefault()
                    void submit()
                  }
                }}
                rows={1}
                disabled={noKey}
                placeholder={noKey ? 'Thêm API key trong Cài đặt để hỏi đáp' : 'Nhập câu hỏi về pháp luật…'}
                aria-label="Câu hỏi"
                className="max-h-48 min-h-10 flex-1 resize-none rounded-md border border-slate-300 bg-white px-3 py-2 text-sm [field-sizing:content] focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-200 disabled:bg-slate-50"
              />
              {streaming ? (
                <button
                  type="button"
                  onClick={stream.stop}
                  className="inline-flex h-10 shrink-0 items-center gap-2 rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-700 hover:bg-slate-50"
                >
                  <span aria-hidden className="size-3 rounded-sm bg-slate-700" />
                  Dừng
                </button>
              ) : (
                <button type="submit" disabled={!canSend} className={`${primaryButton} h-10`}>
                  {createConversation.isPending && <Spinner />}
                  Gửi
                </button>
              )}
            </div>
            <p className="flex justify-between gap-2 text-xs text-slate-400">
              <span>Enter để gửi, Shift+Enter để xuống dòng.</span>
              {tooLong ? (
                <span className="text-red-600">
                  {question.length}/{QUESTION_MAX_CHARS} ký tự
                </span>
              ) : (
                question.length > QUESTION_MAX_CHARS * 0.9 && (
                  <span>
                    {question.length}/{QUESTION_MAX_CHARS}
                  </span>
                )
              )}
            </p>
          </form>
        </div>
      </section>
    </div>
  )
}
