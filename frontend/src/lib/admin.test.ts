import { describe, expect, it } from 'vitest'
import { CRAWL_JOB_TYPES, CRAWL_RUN_STATUSES, listCrawlRuns } from '../api/admin.ts'
import { ApiError } from '../api/client.ts'
import {
  ADMIN_POLL_INTERVAL_MS,
  adminPollInterval,
  formatDuration,
  hasRunDetails,
  isRunListed,
  isStaleConflict,
  JOB_LABELS,
  LIMIT_ERROR,
  parseLimit,
  RUN_STATUS_BADGE_CLASS,
  RUN_STATUS_LABELS,
  runFailures,
  runOptions,
  statEntries,
} from './admin.ts'

describe('labels', () => {
  it('cover every job type and run status', () => {
    for (const jt of CRAWL_JOB_TYPES) expect(JOB_LABELS[jt]).toBeTruthy()
    for (const s of CRAWL_RUN_STATUSES) {
      expect(RUN_STATUS_LABELS[s]).toBeTruthy()
      expect(RUN_STATUS_BADGE_CLASS[s]).toBeTruthy()
    }
  })
})

describe('statEntries', () => {
  it('keeps non-zero counters, known ones first in CrawlStats order', () => {
    expect(statEntries({ zzz: 2, failed: 1, fetched: 10, created: 0, updated: 3 })).toEqual([
      { key: 'fetched', label: 'Đã tải', value: 10 },
      { key: 'updated', label: 'Cập nhật', value: 3 },
      { key: 'failed', label: 'Lỗi', value: 1 },
      { key: 'zzz', label: 'zzz', value: 2 },
    ])
  })

  it('ignores non-numbers and handles empty stats', () => {
    expect(statEntries({ fetched: '3', created: null })).toEqual([])
    expect(statEntries({})).toEqual([])
  })
})

describe('formatDuration', () => {
  const start = '2026-11-06T10:00:00Z'
  it('formats seconds, minutes, hours', () => {
    expect(formatDuration(start, '2026-11-06T10:00:45Z')).toBe('45 giây')
    expect(formatDuration(start, '2026-11-06T10:03:12Z')).toBe('3 phút 12 giây')
    expect(formatDuration(start, '2026-11-06T10:03:00Z')).toBe('3 phút')
    expect(formatDuration(start, '2026-11-06T12:05:30Z')).toBe('2 giờ 5 phút')
    expect(formatDuration(start, '2026-11-06T12:00:20Z')).toBe('2 giờ')
  })

  it('uses now for a running job and never goes negative', () => {
    const now = Date.parse('2026-11-06T10:01:05Z')
    expect(formatDuration(start, null, now)).toBe('1 phút 5 giây')
    expect(formatDuration(start, null, Date.parse(start) - 5000)).toBe('0 giây')
  })

  it('returns null for invalid dates', () => {
    expect(formatDuration('nope', null, 0)).toBeNull()
  })
})

describe('run details', () => {
  const params = {
    limit: 100,
    checkpoint: { group: 'Luật', page: 2 },
    failures: [{ source_id: '123', error: 'timeout' }, 'junk', { source_id: 7 }],
  }

  it('extracts failures defensively', () => {
    expect(runFailures(params)).toEqual([
      { source_id: '123', error: 'timeout' },
      { source_id: '7', error: '' },
    ])
    expect(runFailures({ failures: 'x' })).toEqual([])
    expect(runFailures({})).toEqual([])
  })

  it('options are params without failures', () => {
    expect(runOptions(params)).toEqual({ limit: 100, checkpoint: { group: 'Luật', page: 2 } })
    expect(runOptions({ failures: [] })).toBeNull()
  })

  it('hasRunDetails', () => {
    expect(hasRunDetails({ error: null, params: {} })).toBe(false)
    expect(hasRunDetails({ error: 'boom', params: {} })).toBe(true)
    expect(hasRunDetails({ error: null, params: { overlap_days: 7 } })).toBe(true)
  })
})

describe('parseLimit', () => {
  it('empty means no limit', () => {
    expect(parseLimit('')).toEqual({})
    expect(parseLimit('   ')).toEqual({})
  })

  it('accepts positive integers', () => {
    expect(parseLimit(' 100 ')).toEqual({ limit: 100 })
  })

  it.each(['0', '-1', '1.5', 'abc', '1e3', '99999999999999999999'])('rejects %s', (v) => {
    expect(parseLimit(v)).toEqual({ error: LIMIT_ERROR })
  })
})

describe('adminPollInterval', () => {
  it('polls while a run is running or a start is awaited', () => {
    expect(adminPollInterval([{ status: 'success' }, null], false)).toBe(false)
    expect(adminPollInterval(undefined, false)).toBe(false)
    expect(adminPollInterval([null, { status: 'running' }], false)).toBe(ADMIN_POLL_INTERVAL_MS)
    expect(adminPollInterval([], true)).toBe(ADMIN_POLL_INTERVAL_MS)
  })
})

describe('listCrawlRuns', () => {
  it('builds the query string', async () => {
    const calls: string[] = []
    const realFetch = globalThis.fetch
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      calls.push(String(input))
      return new Response(JSON.stringify({ items: [], total: 0 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }) as typeof fetch
    try {
      await listCrawlRuns({ job_type: null, page: 2, page_size: 20 })
      await listCrawlRuns({ job_type: 'daily_new', page: 1, page_size: 20 })
    } finally {
      globalThis.fetch = realFetch
    }
    expect(calls[0]).toMatch(/\/admin\/crawl-runs\?page=2&page_size=20$/)
    expect(calls[1]).toMatch(/\/admin\/crawl-runs\?page=1&page_size=20&job_type=daily_new$/)
  })
})

describe('isRunListed', () => {
  it('finds a run among the latest runs', () => {
    const lastRuns = { backfill: { id: 'a' }, daily_new: null }
    expect(isRunListed(lastRuns, 'a')).toBe(true)
    expect(isRunListed(lastRuns, 'b')).toBe(false)
    expect(isRunListed(undefined, 'a')).toBe(false)
  })
})

describe('isStaleConflict', () => {
  const conflict = new ApiError(409, 'job_running', 'Job này đang chạy')
  const clickedAt = Date.parse('2026-11-06T10:00:00Z')
  it('hides job_running once the run finished after the click', () => {
    expect(isStaleConflict(conflict, { finished_at: '2026-11-06T10:05:00Z' }, clickedAt)).toBe(true)
    expect(isStaleConflict(conflict, { finished_at: '2026-11-06T09:00:00Z' }, clickedAt)).toBe(false)
    expect(isStaleConflict(conflict, { finished_at: null }, clickedAt)).toBe(false)
    expect(isStaleConflict(conflict, undefined, clickedAt)).toBe(false)
  })
  it('keeps other errors', () => {
    const other = new ApiError(422, 'validation_error', 'x')
    expect(isStaleConflict(other, { finished_at: '2026-11-06T10:05:00Z' }, clickedAt)).toBe(false)
  })
})
