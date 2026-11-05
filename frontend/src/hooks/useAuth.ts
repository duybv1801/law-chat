import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import * as authApi from '../api/auth.ts'
import type { User } from '../api/auth.ts'
import { ApiError } from '../api/client.ts'

export const ME_QUERY_KEY = ['auth', 'me'] as const

/**
 * Forget every cached query of the current user and mark the session as logged out.
 * The `me` query is kept (set to `null`, not removed) so mounted observers update in place.
 */
export function resetAuthState(queryClient: QueryClient, user: User | null = null): void {
  queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== ME_QUERY_KEY[0] })
  queryClient.setQueryData(ME_QUERY_KEY, user)
}

/** Current user, or `null` when not logged in. */
export function useMe() {
  return useQuery<User | null>({
    queryKey: ME_QUERY_KEY,
    queryFn: async () => {
      try {
        return await authApi.getMe()
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return null
        throw err
      }
    },
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
}

function useOnAuthenticated() {
  const queryClient = useQueryClient()
  // Drop data cached for a previous user before storing the new one.
  return (user: User) => resetAuthState(queryClient, user)
}

export function useLogin() {
  const onSuccess = useOnAuthenticated()
  return useMutation({ mutationFn: authApi.login, onSuccess })
}

export function useRegister() {
  const onSuccess = useOnAuthenticated()
  return useMutation({ mutationFn: authApi.register, onSuccess })
}

export function useLogout() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      try {
        await authApi.logout()
      } catch (err) {
        // 401: the session is already gone, which is what logging out wants.
        if (!(err instanceof ApiError && err.status === 401)) throw err
      }
    },
    // Only forget the user once the server has cleared the auth cookies; on failure the session
    // is still valid, so the UI must not pretend the user is logged out.
    onSuccess: () => resetAuthState(queryClient),
  })
}
