/** Admin endpoints (docs/06-api.md, section Admin). */
import { api } from './client.ts'

// Must match backend/app/db/models.py `CRAWL_JOB_TYPES` / `CRAWL_RUN_STATUSES` and
// backend/app/admin/router.py `PAGE_SIZE_MAX` (checked by backend/tests/test_admin.py).
export const CRAWL_JOB_TYPES = ['backfill', 'daily_new', 'status_refresh'] as const
export type JobType = (typeof CRAWL_JOB_TYPES)[number]

export const CRAWL_RUN_STATUSES = ['running', 'success', 'failed', 'partial'] as const
export type RunStatus = (typeof CRAWL_RUN_STATUSES)[number]

export const PAGE_SIZE_MAX = 100

export interface CrawlRun {
  id: string
  job_type: JobType
  status: RunStatus
  started_at: string
  finished_at: string | null
  updated_at: string
  stats: Record<string, number>
  // Job options, checkpoint and `failures` ({source_id, error}[]).
  params: Record<string, unknown>
  error: string | null
}

export interface CrawlRunList {
  items: CrawlRun[]
  total: number
}

export interface QdrantStats {
  collection: string
  status: string
  points_count: number | null
  indexed_vectors_count: number | null
  segments_count: number | null
}

export interface AdminStats {
  documents: {
    total: number
    by_source: Record<string, number>
    by_status: Record<string, number>
    by_ingest_status: Record<string, number>
  }
  chunks: number
  qdrant: QdrantStats | null
  last_runs: Record<JobType, CrawlRun | null>
}

export interface CrawlRunParams {
  job_type: JobType | null
  page: number
  page_size: number
}

export function getAdminStats(signal?: AbortSignal): Promise<AdminStats> {
  return api<AdminStats>('/admin/stats', { signal })
}

export function listCrawlRuns(params: CrawlRunParams, signal?: AbortSignal): Promise<CrawlRunList> {
  const search = new URLSearchParams({
    page: String(params.page),
    page_size: String(params.page_size),
  })
  if (params.job_type) search.set('job_type', params.job_type)
  return api<CrawlRunList>(`/admin/crawl-runs?${search}`, { signal })
}

/** `202 {crawl_run_id}`; a run of this job already in progress → `ApiError(409, 'job_running')`. */
export function startCrawl(
  jobType: JobType,
  params?: Record<string, unknown>,
): Promise<{ crawl_run_id: string }> {
  return api<{ crawl_run_id: string }>(`/admin/crawl/${jobType}`, {
    method: 'POST',
    body: params ? { params } : {},
  })
}
