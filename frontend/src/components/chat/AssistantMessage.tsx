import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import type { Citation } from '../../api/chat.ts'
import { citationElementId, isUncited, needsSettings, type ChatErrorInfo } from '../../lib/chat.ts'
import AnswerMarkdown from './AnswerMarkdown.tsx'
import CitationCard from './CitationCard.tsx'

const HIGHLIGHT_MS = 2000

export const DISCLAIMER = 'Thông tin chỉ để tham khảo, không thay thế tư vấn pháp lý.'

const NOTICE_TONES = {
  error: {
    box: 'border-red-200 bg-red-50 text-red-700',
    button: 'bg-red-700 hover:bg-red-600',
  },
  warning: {
    box: 'border-amber-200 bg-amber-50 text-amber-900',
    button: 'bg-amber-700 hover:bg-amber-600',
  },
}

/** Error box; key / model errors get a button to the Settings page. */
export function ChatErrorNotice({
  error,
  tone = 'error',
}: {
  error: ChatErrorInfo
  tone?: keyof typeof NOTICE_TONES
}) {
  const colors = NOTICE_TONES[tone]
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      className={`flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border px-3 py-2 text-sm ${colors.box}`}
    >
      <span className="min-w-0 flex-1">{error.message}</span>
      {needsSettings(error.code) && (
        <Link
          to="/settings"
          className={`shrink-0 rounded-md px-3 py-1 text-xs font-medium text-white ${colors.button}`}
        >
          Đi tới Cài đặt
        </Link>
      )}
    </div>
  )
}

interface Props {
  messageId: string | null
  text: string
  /** `null` while the answer is still streaming. */
  citations: Citation[] | null
  /** Waiting for the first token. */
  pending?: boolean
  streaming?: boolean
  cancelled?: boolean
  error?: ChatErrorInfo | null
  modelLabel?: string | null
}

export default function AssistantMessage({
  messageId,
  text,
  citations,
  pending = false,
  streaming = false,
  cancelled = false,
  error = null,
  modelLabel = null,
}: Props) {
  const [highlighted, setHighlighted] = useState<number | null>(null)

  useEffect(() => {
    if (highlighted === null) return
    const timer = setTimeout(() => setHighlighted(null), HIGHLIGHT_MS)
    return () => clearTimeout(timer)
  }, [highlighted])

  const valid = useMemo(
    () => (citations === null ? null : new Set(citations.map((c) => c.n))),
    [citations],
  )

  const onCite = useCallback(
    (n: number) => {
      if (!messageId) return
      document
        .getElementById(citationElementId(messageId, n))
        ?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
      setHighlighted(n)
    },
    [messageId],
  )

  const finished = !pending && !streaming
  return (
    <div className="min-w-0 max-w-full space-y-3">
      {pending ? (
        <p className="flex items-center gap-1 py-1 text-sm text-slate-500" aria-live="polite">
          <span className="sr-only">Đang tìm nguồn và soạn câu trả lời…</span>
          <span aria-hidden className="size-1.5 animate-bounce rounded-full bg-slate-400" />
          <span aria-hidden className="size-1.5 animate-bounce rounded-full bg-slate-400 [animation-delay:150ms]" />
          <span aria-hidden className="size-1.5 animate-bounce rounded-full bg-slate-400 [animation-delay:300ms]" />
        </p>
      ) : (
        text && <AnswerMarkdown text={text} valid={valid} onCite={onCite} />
      )}
      {streaming && !pending && (
        <span aria-hidden className="inline-block h-4 w-1.5 animate-pulse bg-slate-400 align-middle" />
      )}

      {cancelled && <p className="text-xs italic text-slate-500">Đã dừng.</p>}
      {error && <ChatErrorNotice error={error} />}

      {citations && citations.length > 0 && messageId && (
        <section aria-label="Nguồn">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            {isUncited(citations) ? 'Nguồn tham khảo (mô hình không trích dẫn cụ thể)' : 'Nguồn'}
          </h3>
          <ol className="space-y-2">
            {citations.map((c) => (
              <CitationCard
                key={c.n}
                messageId={messageId}
                citation={c}
                highlighted={highlighted === c.n}
              />
            ))}
          </ol>
        </section>
      )}

      {finished && (
        <p className="text-xs text-slate-400">
          {DISCLAIMER}
          {modelLabel && <span className="ml-1">· {modelLabel}</span>}
        </p>
      )}
    </div>
  )
}
