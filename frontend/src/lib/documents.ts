/** Pure helpers for the Documents tab (and status badges in chat citations, T23). */
import {
  MAX_UPLOAD_BYTES,
  SHORT_FIELD_MAX,
  STATUSES,
  TITLE_MAX,
  UPLOAD_EXTENSIONS,
  type DetectedMetadata,
  type DieuInfo,
  type DocumentSummary,
  type IngestStatus,
  type ListParams,
  type ParseWarning,
  type Scope,
  type Status,
} from '../api/documents.ts'

// --- Display -----------------------------------------------------------------------------

/** `YYYY-MM-DD` (or an ISO datetime) → `dd/mm/yyyy`; missing / unparsable → `null`. */
export function formatDate(value: string | null | undefined): string | null {
  if (!value) return null
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value)
  if (!match) return null
  return `${match[3]}/${match[2]}/${match[1]}`
}

/** Badge colours (docs/07-frontend.md): green / yellow / red / blue / grey. */
export const STATUS_BADGE_CLASS: Record<Status, string> = {
  con_hieu_luc: 'bg-green-100 text-green-800 ring-green-600/20',
  het_hieu_luc_mot_phan: 'bg-yellow-100 text-yellow-800 ring-yellow-600/20',
  het_hieu_luc_toan_bo: 'bg-red-100 text-red-800 ring-red-600/20',
  chua_co_hieu_luc: 'bg-blue-100 text-blue-800 ring-blue-600/20',
  khong_xac_dinh: 'bg-slate-100 text-slate-700 ring-slate-500/20',
}

export const INGEST_LABELS: Record<IngestStatus, string> = {
  pending: 'Đang chờ',
  processing: 'Đang xử lý',
  done: 'Hoàn tất',
  failed: 'Lỗi',
}

export const SCOPE_LABELS: Record<Scope, string> = {
  all: 'Tất cả',
  mine: 'Của tôi',
  vbpl: 'Chính thống (vbpl)',
}

/** "Điều 5. Tiêu đề" (title omitted when unknown). */
export function formatDieu({ dieu, dieu_title }: DieuInfo): string {
  return dieu_title ? `${dieu}. ${dieu_title}` : dieu
}

// --- Polling -----------------------------------------------------------------------------

export const POLL_INTERVAL_MS = 3000

export function isIngestActive(status: IngestStatus): boolean {
  return status === 'pending' || status === 'processing'
}

/** `refetchInterval` value: poll while any listed document is still being processed. */
export function pollInterval(items: Pick<DocumentSummary, 'ingest_status'>[] | undefined): number | false {
  return items?.some((d) => isIngestActive(d.ingest_status)) ? POLL_INTERVAL_MS : false
}

// --- List filters <-> URL ----------------------------------------------------------------

export const PAGE_SIZE = 20
export const SCOPES: readonly Scope[] = ['all', 'mine', 'vbpl']

/** Read the list filters from the page URL, ignoring invalid values. */
export function parseListParams(search: URLSearchParams): ListParams {
  const scope = search.get('scope') as Scope | null
  const status = search.get('status') as Status | null
  const docType = search.get('doc_type') ?? ''
  const page = Number(search.get('page'))
  return {
    scope: scope && SCOPES.includes(scope) ? scope : 'all',
    q: (search.get('q') ?? '').trim().slice(0, SHORT_FIELD_MAX),
    doc_type: docType.length <= SHORT_FIELD_MAX ? docType : '',
    status: status && STATUSES.includes(status) ? status : '',
    page: Number.isInteger(page) && page >= 1 ? page : 1,
    page_size: PAGE_SIZE,
  }
}

/**
 * New URL search params after changing some filters. A filter change that actually changes a
 * value (other than the page) goes back to page 1. `q` is trimmed. Defaults are left out of
 * the URL; unrelated params (`doc`) are kept.
 */
export function withListParams(
  search: URLSearchParams,
  changes: Partial<Omit<ListParams, 'page_size'>>,
): URLSearchParams {
  const current = parseListParams(search)
  const next = { ...current, ...changes }
  next.q = next.q.trim().slice(0, SHORT_FIELD_MAX)
  const filterChanged = (['scope', 'q', 'doc_type', 'status'] as const).some(
    (key) => next[key] !== current[key],
  )
  if (filterChanged && !('page' in changes)) next.page = 1
  const result = new URLSearchParams(search)
  const set = (key: string, value: string, isDefault: boolean) => {
    if (isDefault) result.delete(key)
    else result.set(key, value)
  }
  set('scope', next.scope, next.scope === 'all')
  set('q', next.q, next.q === '')
  set('doc_type', next.doc_type, next.doc_type === '')
  set('status', next.status, next.status === '')
  set('page', String(next.page), next.page === 1)
  return result
}

export function totalPages(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil(total / pageSize))
}

// --- Upload ------------------------------------------------------------------------------

export function fileExtension(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot > 0 ? name.slice(dot).toLowerCase() : ''
}

/** Client-side check before sending (the backend checks again, including magic bytes). */
export function uploadFileError(file: { name: string; size: number }): string | null {
  if (!(UPLOAD_EXTENSIONS as readonly string[]).includes(fileExtension(file.name))) {
    return 'Chỉ hỗ trợ file PDF, DOCX hoặc TXT.'
  }
  if (file.size === 0) return 'File rỗng.'
  if (file.size > MAX_UPLOAD_BYTES) return 'File vượt quá giới hạn 20 MB.'
  return null
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1).replace('.', ',')} KB`
  return `${(bytes / 1024 / 1024).toFixed(1).replace('.', ',')} MB`
}

export interface UploadForm {
  title: string
  so_hieu: string
  doc_type: string
  issued_date: string
  effective_date: string
  status: Status
}

/** Form pre-filled from the preview. Without a detected title, the file name is used. */
export function formFromPreview(detected: DetectedMetadata, fileName: string): UploadForm {
  const stem = fileName.slice(0, fileName.length - fileExtension(fileName).length).trim()
  const isDate = (v: string | null | undefined) => (v && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : '')
  return {
    title: (detected.title?.trim() || stem).slice(0, TITLE_MAX),
    so_hieu: (detected.so_hieu ?? '').slice(0, SHORT_FIELD_MAX),
    doc_type: (detected.doc_type ?? '').slice(0, SHORT_FIELD_MAX),
    issued_date: isDate(detected.issued_date),
    effective_date: '',
    status: 'khong_xac_dinh',
  }
}

/** Multipart body of `POST /documents/upload`; blank optional fields are left out. */
export function uploadFormData(file: File, form: UploadForm): FormData {
  const body = new FormData()
  body.append('file', file)
  body.append('title', form.title.trim())
  for (const key of ['so_hieu', 'doc_type', 'issued_date', 'effective_date'] as const) {
    const value = form[key].trim()
    if (value) body.append(key, value)
  }
  body.append('status', form.status)
  return body
}

/** Vietnamese text for parser warnings returned by the preview. Unknown codes are skipped. */
export function warningMessages(warnings: ParseWarning[]): string[] {
  const messages: string[] = []
  for (const w of warnings) {
    if (w.code === 'partially_scanned') {
      const pages = Array.isArray(w.scanned_pages) ? w.scanned_pages.join(', ') : ''
      messages.push(
        pages
          ? `Trang ${pages} là ảnh scan, nội dung các trang này sẽ không được đọc.`
          : 'Một số trang là ảnh scan, nội dung các trang này sẽ không được đọc.',
      )
    }
  }
  return messages
}
