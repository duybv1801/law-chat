import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { TITLE_MAX_CHARS, type Conversation } from '../../api/chat.ts'
import { useConversations, useDeleteConversation, useRenameConversation } from '../../hooks/useChat.ts'
import { flattenConversations } from '../../lib/chat.ts'
import { errorMessage } from '../../lib/errors.ts'
import { secondaryButton, Spinner } from '../ui.tsx'

interface ItemProps {
  conversation: Conversation
  active: boolean
  disabled: boolean
  onNavigate: () => void
  onDeleted: (id: string) => void
}

function ConversationItem({ conversation, active, disabled, onNavigate, onDeleted }: ItemProps) {
  const [mode, setMode] = useState<'view' | 'rename' | 'confirm'>('view')
  const [title, setTitle] = useState(conversation.title)
  const rename = useRenameConversation()
  const remove = useDeleteConversation()

  const onRename = (e: FormEvent) => {
    e.preventDefault()
    const value = title.trim()
    if (!value || value === conversation.title) {
      setMode('view')
      return
    }
    rename.mutate(
      { id: conversation.id, title: value },
      { onSuccess: () => setMode('view') },
    )
  }

  if (mode === 'rename') {
    return (
      <li className="px-2 py-1">
        <form onSubmit={onRename}>
          <input
            autoFocus
            value={title}
            maxLength={TITLE_MAX_CHARS}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                setTitle(conversation.title)
                setMode('view')
              }
            }}
            aria-label="Tên hội thoại"
            className="w-full rounded border border-slate-300 px-2 py-1 text-sm focus:border-slate-500 focus:outline-none"
          />
          <div className="mt-1 flex gap-1">
            <button type="submit" className={`${secondaryButton} !px-2 !py-0.5 text-xs`} disabled={rename.isPending}>
              Lưu
            </button>
            <button
              type="button"
              className={`${secondaryButton} !px-2 !py-0.5 text-xs`}
              onClick={() => {
                setTitle(conversation.title)
                setMode('view')
              }}
            >
              Huỷ
            </button>
          </div>
          {rename.isError && (
            <p role="alert" className="mt-1 text-xs text-red-600">
              {errorMessage(rename.error)}
            </p>
          )}
        </form>
      </li>
    )
  }

  if (mode === 'confirm') {
    return (
      <li className="rounded-md bg-red-50 px-3 py-2 text-sm">
        <p className="text-red-800">Xoá hội thoại này?</p>
        <div className="mt-1 flex gap-1">
          <button
            type="button"
            className="rounded bg-red-700 px-2 py-0.5 text-xs font-medium text-white hover:bg-red-600 disabled:opacity-60"
            disabled={remove.isPending || disabled}
            onClick={() =>
              // mutateAsync: per-call `mutate` callbacks are skipped if this item unmounts first
              // (the refetched list no longer contains it).
              remove.mutateAsync(conversation.id).then(
                () => onDeleted(conversation.id),
                () => {}, // shown through `remove.isError`
              )
            }
          >
            {remove.isPending ? 'Đang xoá…' : 'Xoá'}
          </button>
          <button
            type="button"
            className={`${secondaryButton} !px-2 !py-0.5 text-xs`}
            onClick={() => setMode('view')}
          >
            Huỷ
          </button>
        </div>
        {disabled && <p className="mt-1 text-xs text-red-700">Hãy dừng câu trả lời đang chạy trước.</p>}
        {remove.isError && (
          <p role="alert" className="mt-1 text-xs text-red-600">
            {errorMessage(remove.error)}
          </p>
        )}
      </li>
    )
  }

  return (
    <li
      className={`group flex items-center rounded-md ${
        active ? 'bg-slate-200 text-slate-900' : 'text-slate-700 hover:bg-slate-100'
      }`}
    >
      <Link
        to={`/chat/${conversation.id}`}
        onClick={onNavigate}
        className="min-w-0 flex-1 truncate px-3 py-2 text-sm"
        title={conversation.title}
        aria-current={active ? 'page' : undefined}
      >
        {conversation.title}
      </Link>
      <div className="flex shrink-0 pr-1 opacity-100 sm:opacity-0 sm:group-focus-within:opacity-100 sm:group-hover:opacity-100">
        <button
          type="button"
          onClick={() => {
            setTitle(conversation.title)
            rename.reset()
            setMode('rename')
          }}
          className="rounded px-1.5 py-1 text-xs text-slate-500 hover:bg-white hover:text-slate-800"
          aria-label={`Đổi tên "${conversation.title}"`}
          title="Đổi tên"
        >
          ✎
        </button>
        <button
          type="button"
          onClick={() => {
            remove.reset()
            setMode('confirm')
          }}
          className="rounded px-1.5 py-1 text-xs text-slate-500 hover:bg-white hover:text-red-700"
          aria-label={`Xoá "${conversation.title}"`}
          title="Xoá"
        >
          ✕
        </button>
      </div>
    </li>
  )
}

interface Props {
  activeId: string | null
  /** Id of the conversation whose answer is streaming (it cannot be deleted meanwhile). */
  streamingId: string | null
  onNew: () => void
  onNavigate: () => void
  onDeleted: (id: string) => void
}

export default function ConversationSidebar({ activeId, streamingId, onNew, onNavigate, onDeleted }: Props) {
  const query = useConversations()
  const conversations = flattenConversations(query.data?.pages)

  return (
    <div className="flex h-full flex-col">
      <div className="p-3">
        <button
          type="button"
          onClick={onNew}
          className="w-full rounded-md bg-slate-800 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700"
        >
          + Hội thoại mới
        </button>
      </div>
      <nav aria-label="Hội thoại" className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {query.isPending ? (
          <p className="flex items-center gap-2 px-3 py-2 text-sm text-slate-500">
            <Spinner dark /> Đang tải…
          </p>
        ) : query.isError ? (
          <p role="alert" className="px-3 py-2 text-sm text-red-600">
            {errorMessage(query.error)}
          </p>
        ) : conversations.length === 0 ? (
          <p className="px-3 py-2 text-sm text-slate-500">Chưa có hội thoại nào.</p>
        ) : (
          <ul className="space-y-0.5">
            {conversations.map((c) => (
              <ConversationItem
                key={c.id}
                conversation={c}
                active={c.id === activeId}
                disabled={c.id === streamingId}
                onNavigate={onNavigate}
                onDeleted={onDeleted}
              />
            ))}
          </ul>
        )}
        {query.hasNextPage && (
          <button
            type="button"
            onClick={() => void query.fetchNextPage()}
            disabled={query.isFetchingNextPage}
            className="mt-2 w-full rounded-md px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100 disabled:opacity-60"
          >
            {query.isFetchingNextPage ? 'Đang tải…' : 'Tải thêm'}
          </button>
        )}
      </nav>
    </div>
  )
}
