import { Fragment, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import {
  CRAWL_JOB_TYPES,
  type AdminStats,
  type CrawlRun,
  type JobType,
  type RunStatus,
} from '../api/admin.ts'
import { STATUS_LABELS, STATUSES } from '../api/documents.ts'
import {
  useAdminStats,
  useCrawlRuns,
  useInvalidateCrawlRuns,
  useStartCrawl,
} from '../hooks/useAdmin.ts'
import {
  ErrorText,
  inputClass,
  primaryButton,
  secondaryButton,
  sectionClass,
  Spinner,
} from '../components/ui.tsx'
import {
  AWAIT_START_TIMEOUT_MS,
  formatDuration,
  hasRunDetails,
  isRunListed,
  isStaleConflict,
  JOB_DESCRIPTIONS,
  JOB_LABELS,
  parseLimit,
  RUN_STATUS_BADGE_CLASS,
  RUN_STATUS_LABELS,
  runFailures,
  runOptions,
  SOURCE_LABELS,
  statEntries,
} from '../lib/admin.ts'
import { INGEST_LABELS, totalPages } from '../lib/documents.ts'
import { errorMessage } from '../lib/errors.ts'
import { formatDateTime } from '../lib/settings.ts'

const RUNS_PAGE_SIZE = 20

const fmt = (n: number | null | undefined) => (n == null ? '—' : n.toLocaleString('vi-VN'))

/** Current time, re-read every second while `active` (durations of running jobs). */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [active])
  return now
}

function RunStatusBadge({ status }: { status: RunStatus }) {
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${RUN_STATUS_BADGE_CLASS[status]}`}
    >
      {status === 'running' && <Spinner dark />}
      {RUN_STATUS_LABELS[status]}
    </span>
  )
}

// --- Stats ---------------------------------------------------------------------------------

function StatCard({ title, value, children }: { title: string; value: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{title}</p>
      <p className="mt-1 text-2xl font-semibold text-slate-800">{value}</p>
      {children && <dl className="mt-2 space-y-0.5 text-sm text-slate-600">{children}</dl>}
    </div>
  )
}

function StatLine({ label, value }: { label: string; value: number | null | undefined }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="truncate">{label}</dt>
      <dd className="shrink-0 tabular-nums">{fmt(value)}</dd>
    </div>
  )
}

function StatsCards({ stats }: { stats: AdminStats }) {
  const { documents, qdrant } = stats
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard title="Văn bản" value={fmt(documents.total)}>
        {Object.entries(documents.by_source).map(([source, n]) => (
          <StatLine key={source} label={SOURCE_LABELS[source] ?? source} value={n} />
        ))}
      </StatCard>
      <StatCard title="Còn hiệu lực" value={fmt(documents.by_status.con_hieu_luc ?? 0)}>
        {STATUSES.filter((s) => documents.by_status[s]).map((s) => (
          <StatLine key={s} label={STATUS_LABELS[s]} value={documents.by_status[s]} />
        ))}
      </StatCard>
      <StatCard title="Đã xử lý xong" value={fmt(documents.by_ingest_status.done ?? 0)}>
        {Object.entries(documents.by_ingest_status).map(([s, n]) => (
          <StatLine key={s} label={INGEST_LABELS[s as keyof typeof INGEST_LABELS] ?? s} value={n} />
        ))}
      </StatCard>
      <StatCard title="Đoạn (chunk)" value={fmt(stats.chunks)}>
        {qdrant ? (
          <>
            <StatLine label="Point trong Qdrant" value={qdrant.points_count} />
            <StatLine label="Vector đã index" value={qdrant.indexed_vectors_count} />
            <StatLine label="Segment" value={qdrant.segments_count} />
            <div className="flex justify-between gap-2">
              <dt>Trạng thái Qdrant</dt>
              <dd>{qdrant.status}</dd>
            </div>
          </>
        ) : (
          <p className="text-red-600">Không kết nối được Qdrant.</p>
        )}
      </StatCard>
    </div>
  )
}

// --- Manual jobs ---------------------------------------------------------------------------

function JobCard({ jobType, lastRun, onStarted }: {
  jobType: JobType
  lastRun: CrawlRun | null | undefined
  onStarted: (runId: string) => void
}) {
  const start = useStartCrawl()
  const [limitText, setLimitText] = useState('')
  const [limitError, setLimitError] = useState<string | null>(null)

  const onSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (start.isPending) return
    let params: Record<string, unknown> | undefined
    if (jobType === 'backfill') {
      const parsed = parseLimit(limitText)
      if (parsed.error) {
        setLimitError(parsed.error)
        return
      }
      if (parsed.limit !== undefined) params = { limit: parsed.limit }
    }
    setLimitError(null)
    start.mutate(
      { jobType, params },
      {
        onSuccess: ({ crawl_run_id }) => {
          onStarted(crawl_run_id)
        },
      },
    )
  }

  const limitId = `limit-${jobType}`
  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-3 rounded-lg border border-slate-200 p-4">
      <div className="min-w-0">
        <p className="font-medium text-slate-800">{JOB_LABELS[jobType]}</p>
        <p className="text-sm text-slate-500">{JOB_DESCRIPTIONS[jobType]}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
        <span>Lần chạy gần nhất:</span>
        {lastRun ? (
          <>
            <RunStatusBadge status={lastRun.status} />
            <span>{formatDateTime(lastRun.started_at)}</span>
          </>
        ) : (
          <span>chưa chạy</span>
        )}
      </div>
      {jobType === 'backfill' && (
        <div>
          <label htmlFor={limitId} className="mb-1 block text-sm text-slate-700">
            Số văn bản tối đa (để trống = không giới hạn)
          </label>
          <input
            id={limitId}
            inputMode="numeric"
            value={limitText}
            onChange={(e) => {
              setLimitText(e.target.value)
              setLimitError(null)
            }}
            placeholder="Ví dụ: 100"
            aria-invalid={limitError !== null}
            className={`${inputClass} sm:max-w-48`}
          />
          {limitError && <ErrorText>{limitError}</ErrorText>}
        </div>
      )}
      <div className="mt-auto flex flex-wrap items-center gap-3">
        <button type="submit" disabled={start.isPending} className={primaryButton}>
          {start.isPending && <Spinner />}
          Chạy ngay
        </button>
        {start.isSuccess && (
          <span className="text-sm text-green-700">Đã bắt đầu chạy.</span>
        )}
      </div>
      {start.isError && !isStaleConflict(start.error, lastRun, start.submittedAt) && (
        <ErrorText>{errorMessage(start.error)}</ErrorText>
      )}
    </form>
  )
}

// --- Crawl runs ----------------------------------------------------------------------------

function RunDetails({ run }: { run: CrawlRun }) {
  const failures = runFailures(run.params)
  const options = runOptions(run.params)
  const stats = statEntries(run.stats)
  return (
    <div className="space-y-3 text-sm">
      {run.error && (
        <div>
          <p className="font-medium text-red-700">Lỗi</p>
          <pre className="mt-1 whitespace-pre-wrap break-words rounded bg-red-50 p-2 font-mono text-xs text-red-800">
            {run.error}
          </pre>
        </div>
      )}
      {failures.length > 0 && (
        <div>
          <p className="font-medium text-slate-700">Văn bản lỗi ({failures.length})</p>
          <ul className="mt-1 space-y-0.5 font-mono text-xs text-slate-700">
            {failures.map((f, i) => (
              <li key={i} className="break-words">
                <span className="font-semibold">{f.source_id}</span>: {f.error}
              </li>
            ))}
          </ul>
        </div>
      )}
      {stats.length > 0 && (
        <p className="text-slate-600">
          {stats.map((s) => `${s.label}: ${fmt(s.value)}`).join(' · ')}
        </p>
      )}
      {options && (
        <div>
          <p className="font-medium text-slate-700">Tham số</p>
          <pre className="mt-1 overflow-x-auto rounded bg-slate-50 p-2 font-mono text-xs text-slate-700">
            {JSON.stringify(options, null, 2)}
          </pre>
        </div>
      )}
    </div>
  )
}

function RunRow({ run, now }: { run: CrawlRun; now: number }) {
  const [open, setOpen] = useState(false)
  const stats = statEntries(run.stats)
  const expandable = hasRunDetails(run) || stats.length > 0
  return (
    <Fragment>
      <tr
        onClick={expandable ? () => setOpen((o) => !o) : undefined}
        className={`text-sm text-slate-700 ${expandable ? 'cursor-pointer hover:bg-slate-50' : ''}`}
      >
        <td className="px-3 py-2 whitespace-nowrap">{JOB_LABELS[run.job_type]}</td>
        <td className="px-3 py-2">
          <RunStatusBadge status={run.status} />
        </td>
        <td className="px-3 py-2 whitespace-nowrap">{formatDateTime(run.started_at)}</td>
        <td className="px-3 py-2 whitespace-nowrap">
          {formatDuration(run.started_at, run.finished_at, now) ?? '—'}
        </td>
        <td className="hidden px-3 py-2 sm:table-cell">
          {stats.length > 0 ? (
            <span className="text-slate-600">
              {stats.map((s) => `${s.label}: ${fmt(s.value)}`).join(' · ')}
            </span>
          ) : (
            <span className="text-slate-400">—</span>
          )}
        </td>
        <td className="px-3 py-2 text-right">
          {expandable && (
            <button
              type="button"
              aria-expanded={open}
              aria-label={open ? 'Thu gọn' : 'Xem chi tiết'}
              onClick={(e) => {
                e.stopPropagation()
                setOpen((o) => !o)
              }}
              className="rounded px-2 text-slate-500 hover:bg-slate-100"
            >
              {run.error ? <span className="mr-1 text-red-600">!</span> : null}
              {open ? '▴' : '▾'}
            </button>
          )}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={6} className="bg-slate-50/60 px-3 py-3">
            <RunDetails run={run} />
          </td>
        </tr>
      )}
    </Fragment>
  )
}

function RunsSection({ jobActive }: { jobActive: boolean }) {
  const [jobType, setJobType] = useState<JobType | null>(null)
  const [page, setPage] = useState(1)
  const runs = useCrawlRuns({ job_type: jobType, page, page_size: RUNS_PAGE_SIZE }, jobActive)
  const items = runs.data?.items ?? []
  const total = runs.data?.total ?? 0
  const pages = totalPages(total, RUNS_PAGE_SIZE)
  const now = useNow(items.some((r) => r.status === 'running'))

  // No clamp to the last page needed: runs are never deleted and a filter change resets page 1.

  const filters: (JobType | null)[] = [null, ...CRAWL_JOB_TYPES]
  return (
    <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
      <div className="space-y-3 border-b border-slate-200 p-4">
        <h2 className="font-semibold text-slate-800">Lịch sử crawl</h2>
        <div role="tablist" aria-label="Loại job" className="flex flex-wrap gap-1">
          {filters.map((jt) => (
            <button
              key={jt ?? 'all'}
              type="button"
              role="tab"
              aria-selected={jobType === jt}
              onClick={() => {
                setJobType(jt)
                setPage(1)
              }}
              className={`rounded-md px-3 py-1.5 text-sm font-medium ${
                jobType === jt ? 'bg-slate-800 text-white' : 'text-slate-600 hover:bg-slate-100'
              }`}
            >
              {jt ? JOB_LABELS[jt] : 'Tất cả'}
            </button>
          ))}
        </div>
      </div>

      {runs.isPending && (
        <p className="flex items-center gap-2 p-4 text-sm text-slate-500">
          <Spinner dark /> Đang tải…
        </p>
      )}
      {runs.isError && !runs.data && (
        <div className="space-y-2 p-4">
          <ErrorText>{errorMessage(runs.error)}</ErrorText>
          <button type="button" onClick={() => void runs.refetch()} className={secondaryButton}>
            Thử lại
          </button>
        </div>
      )}
      {runs.data && total === 0 && (
        <p className="p-6 text-center text-sm text-slate-500">Chưa có lần crawl nào.</p>
      )}
      {runs.data && items.length > 0 && (
        <>
          <div className={`overflow-x-auto ${runs.isPlaceholderData ? 'opacity-60' : ''}`}>
            <table className="w-full text-left">
              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-3 py-2 font-medium">Loại job</th>
                  <th className="px-3 py-2 font-medium">Trạng thái</th>
                  <th className="px-3 py-2 font-medium">Bắt đầu</th>
                  <th className="px-3 py-2 font-medium">Thời lượng</th>
                  <th className="hidden px-3 py-2 font-medium sm:table-cell">Số liệu</th>
                  <th className="px-3 py-2">
                    <span className="sr-only">Chi tiết</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {items.map((run) => (
                  <RunRow key={run.id} run={run} now={now} />
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 px-4 py-3 text-sm text-slate-600">
            <span>{fmt(total)} lần chạy</span>
            {pages > 1 && (
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setPage(page - 1)}
                  disabled={page <= 1}
                  className={secondaryButton}
                >
                  ‹ Trước
                </button>
                <span>
                  Trang {page} / {pages}
                </span>
                <button
                  type="button"
                  onClick={() => setPage(page + 1)}
                  disabled={page >= pages}
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
  )
}

// --- Page ----------------------------------------------------------------------------------

export default function AdminPage() {
  // A run started from this page: poll until its `crawl_runs` row shows up (or time out).
  const [awaitedRunId, setAwaitedRunId] = useState<string | null>(null)
  const stats = useAdminStats(awaitedRunId)
  const lastRuns = stats.data?.last_runs
  const awaitingStart = awaitedRunId !== null && !isRunListed(lastRuns, awaitedRunId)
  const anyRunning =
    lastRuns !== undefined && Object.values(lastRuns).some((r) => r?.status === 'running')
  const invalidateRuns = useInvalidateCrawlRuns()

  useEffect(() => {
    if (awaitedRunId === null) return
    if (!awaitingStart) {
      // The run showed up: refresh the history once (it may already have finished).
      void invalidateRuns()
      return
    }
    const timer = setTimeout(() => setAwaitedRunId(null), AWAIT_START_TIMEOUT_MS)
    return () => clearTimeout(timer)
  }, [awaitingStart, awaitedRunId, invalidateRuns])

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-4 py-8">
      <h1 className="text-xl font-semibold text-slate-800">Admin</h1>

      {stats.isPending && (
        <p className="flex items-center gap-2 text-sm text-slate-500">
          <Spinner dark /> Đang tải thống kê…
        </p>
      )}
      {stats.isError && !stats.data && (
        <div className="space-y-2">
          <ErrorText>{errorMessage(stats.error)}</ErrorText>
          <button type="button" onClick={() => void stats.refetch()} className={secondaryButton}>
            Thử lại
          </button>
        </div>
      )}
      {stats.data && <StatsCards stats={stats.data} />}

      <section className={sectionClass}>
        <h2 className="font-semibold text-slate-800">Chạy job thủ công</h2>
        <p className="mt-1 text-sm text-slate-500">
          Job chạy nền trên server; mỗi job chỉ chạy một lần tại một thời điểm.
        </p>
        <div className="mt-4 grid gap-4 lg:grid-cols-3">
          {CRAWL_JOB_TYPES.map((jt) => (
            <JobCard key={jt} jobType={jt} lastRun={lastRuns?.[jt]} onStarted={setAwaitedRunId} />
          ))}
        </div>
      </section>

      <RunsSection jobActive={awaitingStart || anyRunning} />
    </div>
  )
}
