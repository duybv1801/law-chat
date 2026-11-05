import { useEffect, useRef } from 'react'
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'
import * as documentsApi from '../api/documents.ts'
import type { IngestStatus, ListParams } from '../api/documents.ts'
import { isIngestActive, pollInterval, POLL_INTERVAL_MS } from '../lib/documents.ts'

export const DOCUMENTS_QUERY_KEY = ['documents'] as const

const LISTS_KEY = [...DOCUMENTS_QUERY_KEY, 'list'] as const
const listKey = (params: ListParams) => [...LISTS_KEY, params] as const
const detailKey = (id: string) => [...DOCUMENTS_QUERY_KEY, 'detail', id] as const

/** Document list; polls every 3 s while a listed document is pending / processing. */
export function useDocumentList(params: ListParams) {
  return useQuery({
    queryKey: listKey(params),
    queryFn: ({ signal }) => documentsApi.listDocuments(params, signal),
    placeholderData: keepPreviousData,
    refetchInterval: (query) => pollInterval(query.state.data?.items),
  })
}

/** Document detail (drawer); polls while it is being processed. */
export function useDocument(id: string | null) {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: detailKey(id ?? ''),
    queryFn: ({ signal }) => documentsApi.getDocument(id!, signal),
    enabled: id !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.ingest_status
      return status && isIngestActive(status) ? POLL_INTERVAL_MS : false
    },
  })

  // Processing finished while the drawer was open: refresh the lists too (they may not poll).
  const status: IngestStatus | undefined = query.data?.ingest_status
  const prevStatus = useRef(status)
  useEffect(() => {
    const prev = prevStatus.current
    prevStatus.current = status
    if (prev && isIngestActive(prev) && status && !isIngestActive(status)) {
      void queryClient.invalidateQueries({ queryKey: LISTS_KEY })
    }
  }, [status, queryClient])

  return query
}

export function usePreviewUpload() {
  return useMutation({
    mutationFn: ({ file, signal }: { file: File; signal?: AbortSignal }) =>
      documentsApi.previewUpload(file, signal),
  })
}

export function useUploadDocument() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: documentsApi.uploadDocument,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: LISTS_KEY }),
  })
}

/**
 * Delete a document. The detail cache is NOT removed here: the drawer still observes it and
 * would refetch the deleted document (404). The drawer calls `forgetDocument` when it unmounts.
 */
export function useDeleteDocument() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: documentsApi.deleteDocument,
    onSuccess: (_data, id) => {
      void queryClient.cancelQueries({ queryKey: detailKey(id) })
      void queryClient.invalidateQueries({ queryKey: LISTS_KEY })
    },
  })
}

/** Drop a deleted document's detail cache (call once nothing observes it any more). */
export function forgetDocument(queryClient: QueryClient, id: string) {
  queryClient.removeQueries({ queryKey: detailKey(id), exact: true })
}
