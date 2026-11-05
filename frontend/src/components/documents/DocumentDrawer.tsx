import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { DocumentDetail } from '../../api/documents.ts'
import { forgetDocument, useDeleteDocument, useDocument } from '../../hooks/useDocuments.ts'
import { formatDate, formatDieu, isIngestActive } from '../../lib/documents.ts'
import { errorMessage } from '../../lib/errors.ts'
import { IngestBadge, StatusBadge } from '../StatusBadge.tsx'
import { dangerButton, ErrorText, secondaryButton, Spinner } from '../ui.tsx'

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[8.5rem_1fr] gap-2 py-1.5 text-sm">
      <dt className="text-slate-500">{label}</dt>
      <dd className="min-w-0 break-words text-slate-800">{children}</dd>
    </div>
  )
}

const dash = <span className="text-slate-400">—</span>

function DeleteButton({ doc, onDeleted }: { doc: DocumentDetail; onDeleted: () => void }) {
  const remove = useDeleteDocument()
  const [confirming, setConfirming] = useState(false)

  // While confirming / deleting, Esc only backs out of the confirmation (never mid-delete) and
  // must not reach the drawer's close handler. Capture phase runs before that (bubble) handler.
  useEffect(() => {
    if (!confirming) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      e.stopPropagation()
      if (!remove.isPending) {
        remove.reset()
        setConfirming(false)
      }
    }
    document.addEventListener('keydown', onKeyDown, true)
    return () => document.removeEventListener('keydown', onKeyDown, true)
  }, [confirming, remove])

  return (
    <div className="border-t border-slate-200 pt-4">
      {confirming ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm text-slate-700">
            Xoá văn bản này? Thao tác không hoàn tác được.
          </span>
          <button
            type="button"
            onClick={() => remove.mutate(doc.id, { onSuccess: onDeleted })}
            disabled={remove.isPending}
            className={dangerButton}
          >
            {remove.isPending ? 'Đang xoá…' : 'Xoá'}
          </button>
          <button
            type="button"
            onClick={() => {
              remove.reset()
              setConfirming(false)
            }}
            disabled={remove.isPending}
            className={secondaryButton}
          >
            Huỷ
          </button>
        </div>
      ) : (
        <button type="button" onClick={() => setConfirming(true)} className={dangerButton}>
          Xoá văn bản
        </button>
      )}
      {remove.isError && <ErrorText>{`Xoá thất bại: ${errorMessage(remove.error)}`}</ErrorText>}
    </div>
  )
}

function DetailBody({ doc, onDeleted }: { doc: DocumentDetail; onDeleted: () => void }) {
  const related = (label: string, values: string[]) =>
    values.length > 0 && <Field label={label}>{values.join(', ')}</Field>

  return (
    <div className="space-y-5">
      <div>
        <h2 id="doc-drawer-title" className="text-lg font-semibold text-slate-800">
          {doc.title}
        </h2>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <StatusBadge status={doc.status} label={doc.status_label} />
          {doc.is_consolidated && (
            <span className="text-xs text-slate-500">Văn bản hợp nhất</span>
          )}
        </div>
        <p className="mt-3 text-sm">
          {doc.source === 'vbpl' ? (
            doc.source_url ? (
              <a
                href={doc.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-sky-700 hover:underline"
              >
                Xem văn bản gốc ↗
              </a>
            ) : (
              <span className="text-slate-500">Nguồn: vbpl.vn</span>
            )
          ) : (
            <span className="text-amber-700">Tài liệu bạn tải lên — chưa xác minh</span>
          )}
        </p>
      </div>

      <dl className="divide-y divide-slate-100">
        <Field label="Số hiệu">{doc.so_hieu ?? dash}</Field>
        <Field label="Loại văn bản">{doc.doc_type ?? dash}</Field>
        <Field label="Cơ quan ban hành">{doc.issuing_body ?? dash}</Field>
        <Field label="Ngày ban hành">{formatDate(doc.issued_date) ?? dash}</Field>
        <Field label="Ngày hiệu lực">{formatDate(doc.effective_date) ?? dash}</Field>
        {doc.expiry_date && (
          <Field label="Ngày hết hiệu lực">{formatDate(doc.expiry_date)}</Field>
        )}
        <Field label="Tình trạng">
          {doc.status_label}
          {doc.status_note && <p className="mt-0.5 text-slate-500">{doc.status_note}</p>}
        </Field>
        {related('Bị thay thế bởi', doc.replaced_by)}
        {related('Được sửa đổi bởi', doc.amended_by)}
        <Field label="Xử lý">
          <span className="flex flex-wrap items-center gap-2">
            <IngestBadge status={doc.ingest_status} />
            {doc.ingest_status === 'done' && (
              <span className="text-slate-500">{doc.chunk_count} đoạn</span>
            )}
          </span>
          {doc.ingest_status === 'failed' && doc.ingest_error && (
            <p className="mt-1 text-red-600">{doc.ingest_error}</p>
          )}
        </Field>
      </dl>

      <div>
        <h3 className="text-sm font-semibold text-slate-800">
          Danh sách Điều {doc.dieu_list.length > 0 && `(${doc.dieu_list.length})`}
        </h3>
        {doc.dieu_list.length > 0 ? (
          <ul className="mt-2 max-h-80 space-y-1 overflow-y-auto rounded-md border border-slate-200 p-3 text-sm text-slate-700">
            {doc.dieu_list.map((d) => (
              <li key={d.dieu}>{formatDieu(d)}</li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-sm text-slate-500">
            {isIngestActive(doc.ingest_status)
              ? 'Sẽ hiện khi xử lý xong.'
              : 'Không tách được Điều nào trong văn bản này.'}
          </p>
        )}
      </div>

      {doc.can_delete && <DeleteButton key={doc.id} doc={doc} onDeleted={onDeleted} />}
    </div>
  )
}

/** Right-side drawer with a document's detail. */
export default function DocumentDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const query = useDocument(id)
  const queryClient = useQueryClient()
  const panelRef = useRef<HTMLDivElement>(null)
  const deleted = useRef(false)

  // Drop the deleted document's cache only after unmount, so it is not refetched (404) here.
  useEffect(
    () => () => {
      if (deleted.current) forgetDocument(queryClient, id)
    },
    [queryClient, id],
  )

  const onDeleted = () => {
    deleted.current = true
    onClose()
  }

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  useEffect(() => {
    panelRef.current?.focus()
  }, [id])

  return (
    <div className="fixed inset-0 z-30">
      <div className="absolute inset-0 bg-slate-900/30" aria-hidden onClick={onClose} />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="doc-drawer-title"
        tabIndex={-1}
        className="absolute inset-y-0 right-0 flex w-full max-w-xl flex-col bg-white shadow-xl focus:outline-none"
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
          <span className="text-sm font-medium text-slate-500">Chi tiết văn bản</span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Đóng"
            className="rounded-md px-2 py-1 text-lg leading-none text-slate-500 hover:bg-slate-100"
          >
            ×
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {query.isPending && (
            <p className="flex items-center gap-2 text-sm text-slate-500">
              <Spinner dark /> Đang tải…
            </p>
          )}
          {query.isError && !query.data && (
            <>
              <h2 id="doc-drawer-title" className="sr-only">
                Chi tiết văn bản
              </h2>
              <ErrorText>{errorMessage(query.error)}</ErrorText>
            </>
          )}
          {query.data && <DetailBody doc={query.data} onDeleted={onDeleted} />}
        </div>
      </div>
    </div>
  )
}
