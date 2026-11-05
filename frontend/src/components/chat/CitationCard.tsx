import { useState } from 'react'
import { Link } from 'react-router'
import type { Citation } from '../../api/chat.ts'
import { citationElementId, citationHeading } from '../../lib/chat.ts'
import { formatDate } from '../../lib/documents.ts'
import { StatusBadge } from '../StatusBadge.tsx'

interface Props {
  messageId: string
  citation: Citation
  highlighted: boolean
}

/** One source card. Every field comes from the backend's DB metadata, never from the answer. */
export default function CitationCard({ messageId, citation: c, highlighted }: Props) {
  const [open, setOpen] = useState(false)
  const isUpload = c.source === 'user_upload'
  return (
    <li
      id={citationElementId(messageId, c.n)}
      className={`scroll-mt-4 rounded-md border bg-white p-3 text-sm transition-colors duration-500 ${
        highlighted ? 'border-sky-400 bg-sky-50 ring-2 ring-sky-200' : 'border-slate-200'
      }`}
    >
      <p className="font-medium text-slate-800">
        <span className="mr-1 text-sky-700">[{c.n}]</span>
        {citationHeading(c)}
      </p>
      <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-600">
        <span>Ban hành: {formatDate(c.issued_date) ?? 'không rõ'}</span>
        <span aria-hidden>·</span>
        <span>Hiệu lực: {formatDate(c.effective_date) ?? 'không rõ'}</span>
        <span aria-hidden>·</span>
        <StatusBadge status={c.status} label={c.status_label} />
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        {c.source_url ? (
          <a
            href={c.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-sky-700 hover:underline"
          >
            Xem văn bản gốc ↗
          </a>
        ) : (
          isUpload && (
            <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-800 ring-1 ring-amber-600/20 ring-inset">
              Tài liệu bạn tải lên — chưa xác minh
            </span>
          )
        )}
        {isUpload && (
          <Link
            to={`/documents?doc=${c.document_id}`}
            className="text-slate-600 hover:text-slate-800 hover:underline"
          >
            Mở trong tab Văn bản
          </Link>
        )}
        {c.excerpt && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="text-slate-600 hover:text-slate-800 hover:underline"
          >
            {open ? 'Ẩn trích đoạn ▴' : 'Xem trích đoạn ▾'}
          </button>
        )}
      </div>
      {open && (
        <p className="mt-2 whitespace-pre-wrap border-l-2 border-slate-200 pl-3 text-xs text-slate-600">
          {c.excerpt}
        </p>
      )}
    </li>
  )
}
