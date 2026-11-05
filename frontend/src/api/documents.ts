/** Documents endpoints (docs/06-api.md, section Documents). */
import { api } from './client.ts'

// Must match backend/app/ingest/schemas.py `Status` / `STATUS_LABELS`
// (checked by backend/tests/test_documents.py).
export const STATUSES = [
  'con_hieu_luc',
  'het_hieu_luc_mot_phan',
  'het_hieu_luc_toan_bo',
  'chua_co_hieu_luc',
  'khong_xac_dinh',
] as const
export type Status = (typeof STATUSES)[number]

export const STATUS_LABELS: Record<Status, string> = {
  con_hieu_luc: 'Còn hiệu lực',
  het_hieu_luc_mot_phan: 'Hết hiệu lực một phần',
  het_hieu_luc_toan_bo: 'Hết hiệu lực toàn bộ',
  chua_co_hieu_luc: 'Chưa có hiệu lực',
  khong_xac_dinh: 'Không xác định',
}

// Must match backend/app/documents/schemas.py and service.py (checked by backend tests).
export const TITLE_MAX = 1000
export const SHORT_FIELD_MAX = 200
export const PAGE_SIZE_MAX = 50
export const MAX_UPLOAD_BYTES = 20971520
export const UPLOAD_EXTENSIONS = ['.docx', '.pdf', '.txt'] as const

// Document types offered in the filter and suggested in the upload form (free text is allowed).
// Same list as backend/app/ingest/parsers/detect.py `DOC_TYPES` plus "Sắc lệnh" (vbpl.vn).
export const DOC_TYPES = [
  'Hiến pháp',
  'Bộ luật',
  'Luật',
  'Pháp lệnh',
  'Lệnh',
  'Sắc lệnh',
  'Nghị quyết',
  'Nghị quyết liên tịch',
  'Nghị định',
  'Thông tư',
  'Thông tư liên tịch',
  'Quyết định',
  'Chỉ thị',
  'Văn bản hợp nhất',
  'Công văn',
  'Thông báo',
] as const

export type Scope = 'all' | 'mine' | 'vbpl'
export type Source = 'vbpl' | 'user_upload'
export type IngestStatus = 'pending' | 'processing' | 'done' | 'failed'

export interface DocumentSummary {
  id: string
  source: Source
  so_hieu: string | null
  title: string
  doc_type: string | null
  issued_date: string | null
  effective_date: string | null
  status: Status
  status_label: string
  ingest_status: IngestStatus
  source_url: string | null
  created_at: string
}

export interface DocumentList {
  items: DocumentSummary[]
  total: number
}

export interface DieuInfo {
  dieu: string
  dieu_title: string | null
}

export interface DocumentDetail extends DocumentSummary {
  issuing_body: string | null
  expiry_date: string | null
  status_note: string | null
  replaced_by: string[]
  amended_by: string[]
  is_consolidated: boolean
  file_url: string | null
  ingest_error: string | null
  updated_at: string
  chunk_count: number
  dieu_list: DieuInfo[]
  can_delete: boolean
}

/** Metadata guessed from the file; only pre-fills the upload form. */
export interface DetectedMetadata {
  so_hieu?: string | null
  doc_type?: string | null
  issued_date?: string | null
  /** `fallback` = guessed from any date in the text: ask the user to double-check. */
  issued_date_source?: 'passed' | 'place_date' | 'fallback' | null
  title?: string | null
}

export interface ParseWarning {
  code: string
  [key: string]: unknown
}

export interface UploadPreview {
  detected: DetectedMetadata
  warnings: ParseWarning[]
}

export interface UploadAccepted {
  id: string
  ingest_status: IngestStatus
}

export interface ListParams {
  scope: Scope
  q: string
  doc_type: string
  status: Status | ''
  page: number
  page_size: number
}

/** Query string for `GET /documents`; empty filters are left out. */
export function listQueryString(params: ListParams): string {
  const qs = new URLSearchParams()
  qs.set('scope', params.scope)
  const q = params.q.trim()
  if (q) qs.set('q', q)
  if (params.doc_type) qs.set('doc_type', params.doc_type)
  if (params.status) qs.set('status', params.status)
  qs.set('page', String(params.page))
  qs.set('page_size', String(params.page_size))
  return qs.toString()
}

export function listDocuments(params: ListParams, signal?: AbortSignal): Promise<DocumentList> {
  return api<DocumentList>(`/documents?${listQueryString(params)}`, { signal })
}

export function getDocument(id: string, signal?: AbortSignal): Promise<DocumentDetail> {
  return api<DocumentDetail>(`/documents/${encodeURIComponent(id)}`, { signal })
}

export function previewUpload(file: File, signal?: AbortSignal): Promise<UploadPreview> {
  const body = new FormData()
  body.append('file', file)
  return api<UploadPreview>('/documents/upload/preview', { method: 'POST', body, signal })
}

export function uploadDocument(body: FormData): Promise<UploadAccepted> {
  return api<UploadAccepted>('/documents/upload', { method: 'POST', body })
}

export function deleteDocument(id: string): Promise<void> {
  return api<void>(`/documents/${encodeURIComponent(id)}`, { method: 'DELETE' })
}
