import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as chatApi from '../api/chat.ts'

export const CHAT_QUERY_KEY = ['chat'] as const
export const CONVERSATIONS_KEY = [...CHAT_QUERY_KEY, 'conversations'] as const
export const messagesKey = (conversationId: string) =>
  [...CHAT_QUERY_KEY, 'messages', conversationId] as const

/** Conversation list, newest first, 20 per page ("Tải thêm"). */
export function useConversations() {
  return useInfiniteQuery({
    queryKey: CONVERSATIONS_KEY,
    queryFn: ({ pageParam, signal }) => chatApi.listConversations(pageParam, signal),
    initialPageParam: 1,
    getNextPageParam: (last, pages) =>
      pages.length * chatApi.PAGE_SIZE < last.total ? pages.length + 1 : undefined,
  })
}

export function useMessages(conversationId: string | null) {
  return useQuery({
    queryKey: messagesKey(conversationId ?? ''),
    queryFn: ({ signal }) => chatApi.listMessages(conversationId!, signal),
    enabled: conversationId != null,
  })
}

export function useCreateConversation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: chatApi.createConversation,
    onSuccess: (conversation) => {
      // Nothing to load yet: avoids a request (and a flash of "loading") right after creating it.
      queryClient.setQueryData(messagesKey(conversation.id), [])
      void queryClient.invalidateQueries({ queryKey: CONVERSATIONS_KEY })
    },
  })
}

export function useRenameConversation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      chatApi.renameConversation(id, title),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: CONVERSATIONS_KEY }),
  })
}

export function useDeleteConversation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: chatApi.deleteConversation,
    onSuccess: (_, id) => {
      const key = messagesKey(id)
      // The open conversation's query is still observed until the page navigates away:
      // removing it now would make the observer refetch it (404). It is garbage-collected
      // once unobserved.
      const query = queryClient.getQueryCache().find({ queryKey: key, exact: true })
      if (query && query.getObserversCount() > 0) void queryClient.cancelQueries({ queryKey: key })
      else queryClient.removeQueries({ queryKey: key })
      return queryClient.invalidateQueries({ queryKey: CONVERSATIONS_KEY })
    },
  })
}
