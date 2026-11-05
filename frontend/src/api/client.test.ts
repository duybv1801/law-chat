import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, apiFetch, setUnauthorizedHandler } from './client.ts'

type Handler = (url: string, init: RequestInit) => Response | Promise<Response>

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const unauthorized = () => json(401, { error: { code: 'not_authenticated', message: 'Chưa đăng nhập' } })

let fetchMock: ReturnType<typeof vi.fn>

function mockFetch(handler: Handler) {
  fetchMock = vi.fn((url: string, init: RequestInit) => Promise.resolve(handler(url, init)))
  vi.stubGlobal('fetch', fetchMock)
}

const calledUrls = () => fetchMock.mock.calls.map((c) => c[0] as string)

beforeEach(() => setUnauthorizedHandler(null))
afterEach(() => vi.unstubAllGlobals())

describe('api client', () => {
  it('sends cookies, CSRF header and JSON body', async () => {
    mockFetch(() => json(200, { ok: true }))
    await api('/things', { method: 'POST', body: { a: 1 } })
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    const headers = new Headers(init.headers)
    expect(url).toBe('/api/things')
    expect(init.credentials).toBe('include')
    expect(headers.get('X-Requested-With')).toBe('fetch')
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(init.body).toBe('{"a":1}')
  })

  it('does not set JSON content type for FormData', async () => {
    mockFetch(() => json(200, {}))
    const form = new FormData()
    form.append('f', 'x')
    await api('/upload', { method: 'POST', body: form })
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(new Headers(init.headers).has('Content-Type')).toBe(false)
    expect(init.body).toBe(form)
  })

  it('returns undefined for 204', async () => {
    mockFetch(() => new Response(null, { status: 204 }))
    await expect(api('/x', { method: 'DELETE' })).resolves.toBeUndefined()
  })

  it('throws ApiError with backend code and message', async () => {
    mockFetch(() => json(400, { error: { code: 'invalid_key', message: 'Key không hợp lệ' } }))
    const err = await api('/x').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 400, code: 'invalid_key', message: 'Key không hợp lệ' })
  })

  it('falls back to a generic message for non-JSON errors', async () => {
    mockFetch(() => new Response('<html>Bad gateway</html>', { status: 502 }))
    const err = (await api('/x').catch((e: unknown) => e)) as ApiError
    expect(err.status).toBe(502)
    expect(err.code).toBe('http_error')
    expect(err.message).toMatch(/lỗi/)
  })

  it('maps network failures to ApiError(network_error)', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))))
    await expect(api('/x')).rejects.toMatchObject({ status: 0, code: 'network_error' })
  })

  it('rethrows aborts unchanged', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new DOMException('aborted', 'AbortError'))))
    await expect(api('/x')).rejects.toMatchObject({ name: 'AbortError' })
  })
})

describe('refresh on 401', () => {
  it('refreshes once and retries the request', async () => {
    let authed = false
    mockFetch((url) => {
      if (url === '/api/auth/refresh') {
        authed = true
        return new Response(null, { status: 204 })
      }
      return authed ? json(200, { id: 1 }) : unauthorized()
    })
    await expect(api('/settings')).resolves.toEqual({ id: 1 })
    expect(calledUrls()).toEqual(['/api/settings', '/api/auth/refresh', '/api/settings'])
  })

  it('shares one refresh between concurrent requests', async () => {
    let authed = false
    let releaseRefresh: () => void = () => {}
    mockFetch((url) => {
      if (url === '/api/auth/refresh') {
        return new Promise<Response>((resolve) => {
          releaseRefresh = () => {
            authed = true
            resolve(new Response(null, { status: 204 }))
          }
        })
      }
      return authed ? json(200, { url }) : unauthorized()
    })
    const pending = Promise.all([api('/a'), api('/b'), api('/c')])
    await vi.waitFor(() => expect(calledUrls()).toContain('/api/auth/refresh'))
    await new Promise((r) => setTimeout(r, 0))
    releaseRefresh()
    await expect(pending).resolves.toHaveLength(3)
    expect(calledUrls().filter((u) => u === '/api/auth/refresh')).toHaveLength(1)
  })

  it('calls the unauthorized handler when refresh fails', async () => {
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)
    mockFetch(() => unauthorized())
    await expect(api('/settings')).rejects.toMatchObject({ status: 401, code: 'not_authenticated' })
    expect(calledUrls()).toEqual(['/api/settings', '/api/auth/refresh'])
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('calls the unauthorized handler once for concurrent requests', async () => {
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)
    let releaseRefresh: () => void = () => {}
    mockFetch((url) => {
      if (url === '/api/auth/refresh') {
        return new Promise<Response>((resolve) => {
          releaseRefresh = () => resolve(unauthorized())
        })
      }
      return unauthorized()
    })
    const pending = Promise.allSettled([api('/a'), api('/b'), api('/c')])
    await vi.waitFor(() => expect(calledUrls()).toContain('/api/auth/refresh'))
    await new Promise((r) => setTimeout(r, 0))
    releaseRefresh()
    const results = await pending
    expect(results.every((r) => r.status === 'rejected')).toBe(true)
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('treats a network failure during refresh as a network error, not a logout', async () => {
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) =>
        url === '/api/auth/refresh'
          ? Promise.reject(new TypeError('Failed to fetch'))
          : Promise.resolve(unauthorized()),
      ),
    )
    await expect(api('/settings')).rejects.toMatchObject({ status: 0, code: 'network_error' })
    expect(onUnauthorized).not.toHaveBeenCalled()
  })

  it('treats a server error during refresh as that error, not a logout', async () => {
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)
    mockFetch((url) => (url === '/api/auth/refresh' ? json(503, {}) : unauthorized()))
    await expect(api('/settings')).rejects.toMatchObject({ status: 503 })
    expect(onUnauthorized).not.toHaveBeenCalled()
  })

  it('does not refresh for auth endpoints', async () => {
    mockFetch(() =>
      json(401, { error: { code: 'invalid_credentials', message: 'Sai email hoặc mật khẩu' } }),
    )
    await expect(
      api('/auth/login', { method: 'POST', body: { email: 'a@b.c', password: 'x' } }),
    ).rejects.toMatchObject({ code: 'invalid_credentials' })
    expect(calledUrls()).toEqual(['/api/auth/login'])
  })

  it('returns the raw response from apiFetch (for streams)', async () => {
    mockFetch(() => new Response('event: done\n\n', { status: 200 }))
    const res = await apiFetch('/stream', { method: 'POST' })
    await expect(res.text()).resolves.toBe('event: done\n\n')
  })
})
