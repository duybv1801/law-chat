import type { IngestStatus, Status } from '../api/documents.ts'
import { INGEST_LABELS, isIngestActive, STATUS_BADGE_CLASS } from '../lib/documents.ts'
import { Spinner } from './ui.tsx'

const badgeBase =
  'inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset'

/**
 * Legal status (tình trạng hiệu lực) badge. `label` is the `status_label` returned by the API
 * (DB metadata, CLAUDE.md rule 2); `status` only picks the colour.
 */
export function StatusBadge({ status, label }: { status: Status; label: string }) {
  return <span className={`${badgeBase} ${STATUS_BADGE_CLASS[status]}`}>{label}</span>
}

const INGEST_CLASS: Record<IngestStatus, string> = {
  pending: 'bg-slate-50 text-slate-600 ring-slate-400/30',
  processing: 'bg-sky-50 text-sky-700 ring-sky-600/20',
  done: 'bg-white text-slate-600 ring-slate-300',
  failed: 'bg-red-50 text-red-700 ring-red-600/20',
}

/** Ingest status of a document (trạng thái xử lý). */
export function IngestBadge({ status, title }: { status: IngestStatus; title?: string }) {
  return (
    <span className={`${badgeBase} ${INGEST_CLASS[status]}`} title={title}>
      {isIngestActive(status) && <Spinner dark />}
      {INGEST_LABELS[status]}
    </span>
  )
}
