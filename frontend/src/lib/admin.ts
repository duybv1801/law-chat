/** Pure helpers for the Admin page. */
import type { CrawlRun, JobType, RunStatus } from '../api/admin.ts'
import { ApiError } from '../api/client.ts'

// --- Labels ------------------------------------------------------------------------------

export const JOB_LABELS: Record<JobType, string> = {
  backfill: 'Backfill',
  daily_new: 'Văn bản mới hằng ngày',
  status_refresh: 'Cập nhật tình trạng hiệu lực',
}

export const JOB_DESCRIPTIONS: Record<JobType, string> = {
  backfill: 'Tải toàn bộ văn bản từ vbpl.vn (tiếp tục từ lần chạy trước).',
  daily_new: 'Tìm văn bản mới ban hành trong vài ngày gần đây.',
  status_refresh: 'Kiểm tra lại tình trạng hiệu lực của các văn bản đã có.',
}

export const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  running: 'Đang chạy',
  success: 'Thành công',
  partial: 'Một phần',
  failed: 'Lỗi',
}

export const RUN_STATUS_BADGE_CLASS: Record<RunStatus, string> = {
  running: 'bg-sky-50 text-sky-700 ring-sky-600/20',
  success: 'bg-green-100 text-green-800 ring-green-600/20',
  partial: 'bg-yellow-100 text-yellow-800 ring-yellow-600/20',
  failed: 'bg-red-100 text-red-800 ring-red-600/20',
}

export const SOURCE_LABELS: Record<string, string> = {
  vbpl: 'vbpl.vn',
  user_upload: 'Người dùng tải lên',
}

// Must match the fields of backend/app/crawler/jobs.py `CrawlStats`, in the same order
// (checked by backend/tests/test_admin.py).
export const CRAWL_STAT_LABELS: Record<string, string> = {
  fetched: 'Đã tải',
  created: 'Mới',
  updated: 'Cập nhật',
  unchanged: 'Không đổi',
  status_changed: 'Đổi hiệu lực',
  failed: 'Lỗi',
  skipped_no_content: 'Không có toàn văn',
  not_found: 'Không tìm thấy',
  filtered: 'Bị lọc',
}

export interface StatEntry {
  key: string
  label: string
  value: number
}

/**
 * Run counters worth showing: the non-zero ones, known counters first (in `CrawlStats`
 * order), then any unknown key under its raw name.
 */
export function statEntries(stats: Record<string, unknown>): StatEntry[] {
  const known = Object.keys(CRAWL_STAT_LABELS)
  const keys = [...known, ...Object.keys(stats).filter((k) => !known.includes(k)).sort()]
  return keys.flatMap((key) => {
    const value = stats[key]
    if (typeof value !== 'number' || value === 0) return []
    return [{ key, label: CRAWL_STAT_LABELS[key] ?? key, value }]
  })
}

// --- Time --------------------------------------------------------------------------------

/** Run duration: "45 giây", "3 phút 12 giây", "2 giờ 5 phút". Running → until `nowMs`. */
export function formatDuration(
  startedAt: string,
  finishedAt: string | null,
  nowMs: number = Date.now(),
): string | null {
  const start = Date.parse(startedAt)
  const end = finishedAt ? Date.parse(finishedAt) : nowMs
  if (Number.isNaN(start) || Number.isNaN(end)) return null
  const total = Math.max(0, Math.round((end - start) / 1000))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  if (hours > 0) return minutes > 0 ? `${hours} giờ ${minutes} phút` : `${hours} giờ`
  if (minutes > 0) return seconds > 0 ? `${minutes} phút ${seconds} giây` : `${minutes} phút`
  return `${seconds} giây`
}

// --- Run details -------------------------------------------------------------------------

export interface RunFailure {
  source_id: string
  error: string
}

/** Documents that failed in a run (`params.failures`, capped by the backend). */
export function runFailures(params: Record<string, unknown>): RunFailure[] {
  const failures = params.failures
  if (!Array.isArray(failures)) return []
  return failures.flatMap((f: unknown) => {
    if (typeof f !== 'object' || f === null) return []
    const { source_id, error } = f as Record<string, unknown>
    return [{ source_id: String(source_id ?? ''), error: String(error ?? '') }]
  })
}

/** Run options / checkpoint (everything in `params` except `failures`), or `null` if empty. */
export function runOptions(params: Record<string, unknown>): Record<string, unknown> | null {
  const rest = Object.fromEntries(Object.entries(params).filter(([k]) => k !== 'failures'))
  return Object.keys(rest).length > 0 ? rest : null
}

/** Whether a run row has something to show when expanded. */
export function hasRunDetails(run: Pick<CrawlRun, 'error' | 'params'>): boolean {
  return Boolean(run.error) || runFailures(run.params).length > 0 || runOptions(run.params) !== null
}

// --- Manual run --------------------------------------------------------------------------

export const LIMIT_ERROR = 'Số văn bản phải là số nguyên dương.'

/** Backfill `limit` input: empty → no limit; otherwise a positive integer. */
export function parseLimit(input: string): { limit?: number; error?: string } {
  const text = input.trim()
  if (text === '') return {}
  if (!/^\d+$/.test(text)) return { error: LIMIT_ERROR }
  const limit = Number(text)
  if (!Number.isSafeInteger(limit) || limit < 1) return { error: LIMIT_ERROR }
  return { limit }
}

/**
 * A `job_running` (409) error from "Chạy ngay" is stale once the job's latest run finished
 * after the click: the job can be started again, so the message is hidden.
 */
export function isStaleConflict(
  error: unknown,
  lastRun: Pick<CrawlRun, 'finished_at'> | null | undefined,
  submittedAt: number,
): boolean {
  if (!(error instanceof ApiError) || error.code !== 'job_running') return false
  const finished = lastRun?.finished_at ? Date.parse(lastRun.finished_at) : NaN
  return finished > submittedAt
}

// --- Polling -----------------------------------------------------------------------------

export const ADMIN_POLL_INTERVAL_MS = 5000

/**
 * `refetchInterval` value: poll while a run is in progress, or while a run just started from
 * this page has not shown up yet (its `crawl_runs` row appears once the job takes its lock).
 */
export function adminPollInterval(
  runs: (Pick<CrawlRun, 'status'> | null)[] | undefined,
  awaitingStart: boolean,
): number | false {
  if (awaitingStart) return ADMIN_POLL_INTERVAL_MS
  return runs?.some((r) => r?.status === 'running') ? ADMIN_POLL_INTERVAL_MS : false
}

/** Whether the run `runId` is one of the latest runs (`/admin/stats` `last_runs`). */
export function isRunListed(
  lastRuns: Record<string, Pick<CrawlRun, 'id'> | null> | undefined,
  runId: string,
): boolean {
  return lastRuns !== undefined && Object.values(lastRuns).some((r) => r?.id === runId)
}

/** How long to keep polling for a just-started run before giving up (e.g. it was skipped). */
export const AWAIT_START_TIMEOUT_MS = 30_000
