import { useCallback } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as adminApi from '../api/admin.ts'
import type { CrawlRunParams, JobType } from '../api/admin.ts'
import { adminPollInterval, isRunListed } from '../lib/admin.ts'

export const ADMIN_QUERY_KEY = ['admin'] as const

const STATS_KEY = [...ADMIN_QUERY_KEY, 'stats'] as const
const RUNS_KEY = [...ADMIN_QUERY_KEY, 'crawl-runs'] as const

/** `/admin/stats`; polls while a job is running (or a just-started run is awaited). */
export function useAdminStats(awaitedRunId: string | null) {
  return useQuery({
    queryKey: STATS_KEY,
    queryFn: ({ signal }) => adminApi.getAdminStats(signal),
    refetchInterval: (query) => {
      const lastRuns = query.state.data?.last_runs
      const awaitingStart = awaitedRunId !== null && !isRunListed(lastRuns, awaitedRunId)
      return adminPollInterval(lastRuns && Object.values(lastRuns), awaitingStart)
    },
  })
}

/**
 * One page of `crawl_runs`; polls while a listed run is running, or while `jobActive` (a run
 * is awaited or `/admin/stats` reports a running job that may not be on this page yet).
 */
export function useCrawlRuns(params: CrawlRunParams, jobActive: boolean) {
  return useQuery({
    queryKey: [...RUNS_KEY, params],
    queryFn: ({ signal }) => adminApi.listCrawlRuns(params, signal),
    placeholderData: keepPreviousData,
    refetchInterval: (query) => adminPollInterval(query.state.data?.items, jobActive),
  })
}

/** Refetch the crawl history (e.g. once an awaited run shows up in `/admin/stats`). */
export function useInvalidateCrawlRuns() {
  const queryClient = useQueryClient()
  return useCallback(() => queryClient.invalidateQueries({ queryKey: RUNS_KEY }), [queryClient])
}

export function useStartCrawl() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ jobType, params }: { jobType: JobType; params?: Record<string, unknown> }) =>
      adminApi.startCrawl(jobType, params),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ADMIN_QUERY_KEY }),
  })
}
