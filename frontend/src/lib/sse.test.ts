import { describe, expect, it } from 'vitest'
import { SseParser } from './sse.ts'

describe('SseParser', () => {
  it('parses sse-starlette output (CRLF, ping comments)', () => {
    const p = new SseParser()
    const body =
      'event: start\r\ndata: {"message_id": "m1"}\r\n\r\n' +
      ': ping - 2026-11-05\r\n\r\n' +
      'event: token\r\ndata: {"t": "Xin"}\r\n\r\n'
    expect(p.push(body)).toEqual([
      { event: 'start', data: '{"message_id": "m1"}' },
      { event: 'token', data: '{"t": "Xin"}' },
    ])
  })

  it('handles events and CRLF split across chunks', () => {
    const p = new SseParser()
    expect(p.push('event: tok')).toEqual([])
    expect(p.push('en\r')).toEqual([])
    expect(p.push('\ndata: {"t":"a"}\r')).toEqual([])
    expect(p.push('\n\r')).toEqual([])
    expect(p.push('\n')).toEqual([{ event: 'token', data: '{"t":"a"}' }])
  })

  it('supports LF, CR, multi-line data and the default event name', () => {
    const p = new SseParser()
    expect(p.push('data: a\ndata: b\n\nevent: done\rdata:x\r\r')).toEqual([
      { event: 'message', data: 'a\nb' },
    ])
    // The last "\r" could start a "\r\n": only the end of the stream settles it.
    expect(p.finish()).toEqual([{ event: 'done', data: 'x' }])
  })

  it('ignores events without data and unknown fields', () => {
    const p = new SseParser()
    expect(p.push('event: start\n\nid: 1\nretry: 10\nevent: done\ndata: {}\n\n')).toEqual([
      { event: 'done', data: '{}' },
    ])
  })

  it('drops an incomplete trailing event', () => {
    const p = new SseParser()
    expect(p.push('event: done\ndata: {}\n')).toEqual([])
    expect(p.finish()).toEqual([])
  })
})
