/**
 * Fetch wrapper for the backend API (docs/07-frontend.md).
 *
 * - Always sends cookies (`credentials: 'include'`) and the CSRF header `X-Requested-With: fetch`.
 * - On a 401 it calls `/api/auth/refresh` once (shared between concurrent requests) and retries
 *   the original request one time. Auth endpoints themselves are never retried.
 * - Errors are thrown as `ApiError` carrying the backend's `{error: {code, message}}`.
 */

export const API_BASE = '/api'

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

export const FALLBACK_MESSAGE = 'Đã có lỗi xảy ra. Vui lòng thử lại.'
const NETWORK_MESSAGE = 'Không kết nối được máy chủ. Vui lòng kiểm tra mạng và thử lại.'

// Paths (relative to API_BASE) whose 401 must not trigger a refresh + retry.
const NO_REFRESH_PATHS = ['/auth/login', '/auth/register', '/auth/refresh', '/auth/logout']

let refreshInFlight: Promise<boolean> | null = null
let unauthorizedHandler: (() => void) | null = null

/** Called when the session cannot be recovered (refresh failed). Used to reset auth state. */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler
}

function buildInit(init: RequestInit): RequestInit {
  const headers = new Headers(init.headers)
  headers.set('X-Requested-With', 'fetch')
  const body = init.body
  if (body != null && typeof body === 'string' && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  return { ...init, headers, credentials: 'include' }
}

async function rawFetch(path: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(`${API_BASE}${path}`, buildInit(init))
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    throw new ApiError(0, 'network_error', NETWORK_MESSAGE)
  }
}

/**
 * Refresh the session once, sharing the in-flight request between concurrent callers.
 *
 * Resolves `true` when refreshed and `false` when the session is gone (401/403) — in that case
 * the unauthorized handler is called exactly once for all waiting callers. Network failures and
 * server errors are rethrown as `ApiError`: they say nothing about the session being expired.
 */
export function refreshSession(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = rawFetch('/auth/refresh', { method: 'POST' })
      .then(async (res) => {
        if (res.ok) return true
        if (res.status === 401 || res.status === 403) {
          unauthorizedHandler?.()
          return false
        }
        throw await toApiError(res)
      })
      .finally(() => {
        refreshInFlight = null
      })
  }
  return refreshInFlight
}

/** Turn a non-OK response into an `ApiError` using the unified error format. */
export async function toApiError(res: Response): Promise<ApiError> {
  let code = 'http_error'
  let message = FALLBACK_MESSAGE
  try {
    const data: unknown = await res.json()
    const error = (data as { error?: { code?: unknown; message?: unknown } } | null)?.error
    if (typeof error?.code === 'string') code = error.code
    if (typeof error?.message === 'string' && error.message) message = error.message
  } catch {
    // Body is not JSON (e.g. proxy error page): keep the fallback message.
  }
  return new ApiError(res.status, code, message)
}

/**
 * Low-level request returning the raw `Response` (needed for SSE streams).
 * Handles refresh-on-401; throws `ApiError` for any non-OK final response.
 */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  let res = await rawFetch(path, init)
  if (res.status === 401 && !NO_REFRESH_PATHS.includes(path)) {
    if (await refreshSession()) {
      res = await rawFetch(path, init)
    }
  }
  if (!res.ok) throw await toApiError(res)
  return res
}

export interface RequestOptions extends Omit<RequestInit, 'body'> {
  /** JSON-serialisable body, or a `FormData` for uploads. */
  body?: unknown
}

/** JSON request helper. Returns `undefined` for 204 responses. */
export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, ...rest } = options
  const init: RequestInit = { ...rest }
  if (body instanceof FormData) init.body = body
  else if (body !== undefined) init.body = JSON.stringify(body)
  const res = await apiFetch(path, init)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}
