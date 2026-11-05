/**
 * Where to go after login: the page the user was redirected from (router state), if it is an
 * in-app path. Anything else (external URL, protocol-relative `//host`, auth pages) → `/`.
 */
export function redirectTarget(state: unknown): string {
  const from = (state as { from?: { pathname?: unknown; search?: unknown; hash?: unknown } } | null)
    ?.from
  const pathname = typeof from?.pathname === 'string' ? from.pathname : ''
  if (!pathname.startsWith('/') || pathname.startsWith('//')) return '/'
  if (pathname === '/login' || pathname === '/register') return '/'
  const search = typeof from?.search === 'string' ? from.search : ''
  const hash = typeof from?.hash === 'string' ? from.hash : ''
  return `${pathname}${search}${hash}`
}
