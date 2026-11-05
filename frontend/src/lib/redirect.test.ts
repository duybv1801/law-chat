import { describe, expect, it } from 'vitest'
import { redirectTarget } from './redirect.ts'

describe('redirectTarget', () => {
  it('returns the original in-app location', () => {
    expect(redirectTarget({ from: { pathname: '/chat/abc', search: '?q=1', hash: '#x' } })).toBe(
      '/chat/abc?q=1#x',
    )
  })

  it.each([
    [null],
    [undefined],
    [{}],
    [{ from: { pathname: 'https://evil.example' } }],
    [{ from: { pathname: '//evil.example' } }],
    [{ from: { pathname: '/login' } }],
    [{ from: { pathname: '/register' } }],
    [{ from: { pathname: 42 } }],
  ])('falls back to / for %j', (state) => {
    expect(redirectTarget(state)).toBe('/')
  })
})
