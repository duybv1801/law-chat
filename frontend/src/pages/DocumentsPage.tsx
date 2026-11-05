import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router'
import { DOC_TYPES, STATUS_LABELS, STATUSES, type DocumentSummary, type Status } from '../api/documents.ts'
import DocumentDrawer from '../components/documents/DocumentDrawer.tsx'
import UploadPanel from '../components/documents/UploadPanel.tsx'
import { IngestBadge, StatusBadge } from '../components/StatusBadge.tsx'
import { ErrorText, inputClass, secondaryButton, Spinner } from '../components/ui.tsx'
import { useDocumentList } from '../hooks/useDocuments.ts'
import { errorMessage } from '../lib/errors.ts'
import {
  formatDate,
  parseListParams,
  SCOPE_LABELS,
  SCOPES,
  totalPages,
  withListParams,
} from '../lib/documents.ts'

const SEARCH_DEBOUNCE_MS = 300

function SearchInput({ value, onChange }: { value: string; onChange: (q: string) => void }) {
  const [text, setText] = useState(value)
  const lastSent = useRef(value)

  // URL changed from outside (back button, reset): show it, unless it is our own debounced value
  // (the URL holds it trimmed, so keep a trailing space the user is still typing).
  useEffect(() => {
    if (value !== lastSent.current.trim()) {
      lastSent.current = value
      setText(value)
    }
  }, [value])

  useEffect(() => {
    if (text === lastSent.current) return
    const timer = setTimeout(() => {
      lastSent.current = text
      onChange(text)
    }, SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [text, onChange])

  return (
    <input
      type="search"
      value={text}
      onChange={(e) => setText(e.target.value)}
      placeholder="Tìm theo tên hoặc số hiệu…"
      aria-label="Tìm kiếm văn bản"
      maxLength={200}
      className={inputClass}
    />
  )
}

function DocumentRow({ doc, onOpen }: { doc: DocumentSummary; onOpen: () => void }) {
  const cell = 'px-3 py-2.5 align-top'
  return (
    <tr onClick={onOpen} className="cursor-pointer hover:bg-slate-50">
      <td className={`${cell} whitespace-nowrap font-mono text-xs text-slate-700`}>
        {doc.so_hieu ?? '—'}
      </td>
      <td className={`${cell} min-w-[16rem]`}>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            onOpen()
          }}
          className="text-left text-sm font-medium text-slate-800 hover:underline"
        >
          {doc.title}
        </button>
        {doc.source === 'user_upload' && (
          <span className="ml-2 whitespace-nowrap rounded bg-amber-50 px-1.5 py-0.5 text-xs text-amber-700">
            Tải lên
          </span>
        )}
      </td>
      <td className={`${cell} whitespace-nowrap text-sm text-slate-600`}>{doc.doc_type ?? '—'}</td>
      <td className={`${cell} whitespace-nowrap text-sm text-slate-600`}>
        {formatDate(doc.issued_date) ?? '—'}
      </td>
      <td className={`${cell} whitespace-nowrap text-sm text-slate-600`}>
        {formatDate(doc.effective_date) ?? '—'}
      </td>
      <td className={cell}>
        <StatusBadge status={doc.status} label={doc.status_label} />
      </td>
      <td className={cell}>
        <IngestBadge status={doc.ingest_status} />
      </td>
    </tr>
  )
}

export default function DocumentsPage() {
  const [search, setSearch] = useSearchParams()
  const params = parseListParams(search)
  const openId = search.get('doc')
  const list = useDocumentList(params)

  const update = useCallback(
    (changes: Parameters<typeof withListParams>[1]) =>
      setSearch((prev) => withListParams(prev, changes), { replace: true }),
    [setSearch],
  )
  const onSearch = useCallback((q: string) => update({ q }), [update])

  const location = useLocation()
  const navigate = useNavigate()
  // Opening pushes a history entry (Back closes the drawer). Closing pops that entry when this
  // page pushed it, otherwise (a shared ?doc= link) replaces the URL: no duplicate entries.
  const openedHere = (location.state as { drawer?: boolean } | null)?.drawer === true
  const openDoc = (id: string) =>
    setSearch(
      (prev) => {
        const next = new URLSearchParams(prev)
        next.set('doc', id)
        return next
      },
      { state: { drawer: true } },
    )
  const closeDoc = useCallback(() => {
    if (openedHere) {
      void navigate(-1)
      return
    }
    setSearch(
      (prev) => {
        const next = new URLSearchParams(prev)
        next.delete('doc')
        return next
      },
      { replace: true },
    )
  }, [openedHere, navigate, setSearch])

  const total = list.data?.total ?? 0
  const pages = totalPages(total, params.page_size)
  const lastPage = list.data && !list.isPlaceholderData ? pages : null
  // Past the last page (e.g. after a delete or an old link): go to the last page.
  useEffect(() => {
    if (lastPage !== null && params.page > lastPage) update({ page: lastPage })
  }, [lastPage, params.page, update])

  const hasFilters = params.q !== '' || params.doc_type !== '' || params.status !== ''
  const selectClass = `${inputClass} sm:w-auto`

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-4 py-8">
      <h1 className="text-xl font-semibold text-slate-800">Văn bản</h1>

      <UploadPanel onUploaded={openDoc} />

      <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
        <div className="space-y-3 border-b border-slate-200 p-4">
          <div role="tablist" aria-label="Phạm vi" className="flex flex-wrap gap-1">
            {SCOPES.map((scope) => (
              <button
                key={scope}
                type="button"
                role="tab"
                aria-selected={params.scope === scope}
                onClick={() => update({ scope })}
                className={`rounded-md px-3 py-1.5 text-sm font-medium ${
                  params.scope === scope
                    ? 'bg-slate-800 text-white'
                    : 'text-slate-600 hover:bg-slate-100'
                }`}
              >
                {SCOPE_LABELS[scope]}
              </button>
            ))}
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <div className="flex-1">
              <SearchInput value={params.q} onChange={onSearch} />
            </div>
            <select
              aria-label="Loại văn bản"
              value={params.doc_type}
              onChange={(e) => update({ doc_type: e.target.value })}
              className={selectClass}
            >
              <option value="">Mọi loại văn bản</option>
              {/* Keep a type from the URL selectable even if it is not in the list. */}
              {params.doc_type && !(DOC_TYPES as readonly string[]).includes(params.doc_type) && (
                <option value={params.doc_type}>{params.doc_type}</option>
              )}
              {DOC_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <select
              aria-label="Tình trạng hiệu lực"
              value={params.status}
              onChange={(e) => update({ status: e.target.value as Status | '' })}
              className={selectClass}
            >
              <option value="">Mọi tình trạng</option>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {STATUS_LABELS[s]}
                </option>
              ))}
            </select>
          </div>
        </div>

        {list.isPending && (
          <p className="flex items-center gap-2 p-4 text-sm text-slate-500">
            <Spinner dark /> Đang tải danh sách…
          </p>
        )}
        {list.isError && !list.data && (
          <div className="space-y-2 p-4">
            <ErrorText>{errorMessage(list.error)}</ErrorText>
            <button type="button" onClick={() => void list.refetch()} className={secondaryButton}>
              Thử lại
            </button>
          </div>
        )}

        {/* total > 0 with no items: past the last page, the effect above is redirecting. */}
        {list.data && list.data.items.length === 0 && total === 0 && (
          <p className="p-6 text-center text-sm text-slate-500">
            {hasFilters || params.scope !== 'all'
              ? 'Không có văn bản nào phù hợp với bộ lọc.'
              : 'Chưa có văn bản nào.'}
          </p>
        )}

        {list.data && list.data.items.length > 0 && (
          <>
            <div className={`overflow-x-auto ${list.isPlaceholderData ? 'opacity-60' : ''}`}>
              <table className="w-full text-left">
                <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-3 py-2 font-medium">Số hiệu</th>
                    <th className="px-3 py-2 font-medium">Tên</th>
                    <th className="px-3 py-2 font-medium">Loại</th>
                    <th className="px-3 py-2 font-medium">Ban hành</th>
                    <th className="px-3 py-2 font-medium">Hiệu lực</th>
                    <th className="px-3 py-2 font-medium">Tình trạng</th>
                    <th className="px-3 py-2 font-medium">Xử lý</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {list.data.items.map((doc) => (
                    <DocumentRow key={doc.id} doc={doc} onOpen={() => openDoc(doc.id)} />
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 px-4 py-3 text-sm text-slate-600">
              <span>{total.toLocaleString('vi-VN')} văn bản</span>
              {pages > 1 && (
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => update({ page: params.page - 1 })}
                    disabled={params.page <= 1}
                    className={secondaryButton}
                  >
                    ‹ Trước
                  </button>
                  <span>
                    Trang {params.page} / {pages}
                  </span>
                  <button
                    type="button"
                    onClick={() => update({ page: params.page + 1 })}
                    disabled={params.page >= pages}
                    className={secondaryButton}
                  >
                    Sau ›
                  </button>
                </div>
              )}
            </div>
          </>
        )}
      </section>

      {openId && <DocumentDrawer key={openId} id={openId} onClose={closeDoc} />}
    </div>
  )
}
