import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'
import { useMe } from '../hooks/useAuth.ts'
import { errorMessage } from '../lib/errors.ts'
import { redirectTarget } from '../lib/redirect.ts'

function FullPageSpinner() {
  return (
    <div className="flex min-h-screen items-center justify-center text-slate-500">
      <span className="size-6 animate-spin rounded-full border-2 border-slate-300 border-t-slate-700" />
      <span className="sr-only">Đang tải…</span>
    </div>
  )
}

function FullPageError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-3 px-4 text-center">
      <p className="text-slate-700">{errorMessage(error)}</p>
      <button
        type="button"
        onClick={onRetry}
        className="rounded-md bg-slate-800 px-4 py-2 text-sm text-white hover:bg-slate-700"
      >
        Thử lại
      </button>
    </div>
  )
}

/** Renders children only for logged-in users; otherwise redirects to /login. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { data: user, isPending, isError, error, refetch } = useMe()
  const location = useLocation()
  if (isPending) return <FullPageSpinner />
  // A failed background refetch keeps the cached user: only block the app when we have none.
  if (isError && !user) return <FullPageError error={error} onRetry={() => void refetch()} />
  if (!user) return <Navigate to="/login" replace state={{ from: location }} />
  return children
}

/** Renders children only for admins; other users go back to the home page. */
export function RequireAdmin({ children }: { children: ReactNode }) {
  const { data: user } = useMe()
  if (user?.role !== 'admin') return <Navigate to="/" replace />
  return children
}

/**
 * For /login and /register: logged-in users are sent to the app — back to the page they were
 * redirected from, if any. This is also what navigates away after a successful login/register
 * (the mutation stores the user in the `me` query).
 */
export function GuestOnly({ children }: { children: ReactNode }) {
  const { data: user, isPending } = useMe()
  const location = useLocation()
  if (isPending) return <FullPageSpinner />
  if (user) return <Navigate to={redirectTarget(location.state)} replace />
  return children
}
