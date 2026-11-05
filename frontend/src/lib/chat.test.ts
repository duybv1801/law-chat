import { describe, expect, it } from 'vitest'
import { ApiError } from '../api/client.ts'
import type { Citation, Message } from '../api/chat.ts'
import type { ModelInfo } from '../api/settings.ts'
import {
  applyEvent,
  chatErrorText,
  citationHeading,
  citeNumber,
  effectiveModelId,
  failed,
  flattenConversations,
  initialStreamState,
  interrupted,
  isSendKey,
  isStreamActive,
  isUncited,
  linkMarkersInTree,
  splitMarkers,
  type MdNode,
  needsSettings,
  showStreamBubble,
  type StreamState,
} from './chat.ts'
import type { SseEvent } from './sse.ts'

const ev = (event: string, data: unknown): SseEvent => ({ event, data: JSON.stringify(data) })

const citation = (over: Partial<Citation> = {}): Citation => ({
  n: 1,
  document_id: 'd1',
  title: 'Bộ luật Lao động',
  so_hieu: '45/2019/QH14',
  doc_type: 'Bộ luật',
  dieu: 'Điều 113',
  dieu_title: 'Nghỉ hằng năm',
  issued_date: '2019-11-20',
  effective_date: '2021-01-01',
  status: 'con_hieu_luc',
  status_label: 'Còn hiệu lực',
  source: 'vbpl',
  source_url: 'https://vbpl.vn/x',
  excerpt: 'Người lao động…',
  uncited: false,
  ...over,
})

const message = (id: string): Message => ({
  id,
  role: 'assistant',
  content: '',
  provider: null,
  model_id: null,
  citations: [],
  refused: false,
  error: null,
  created_at: '2026-11-05T00:00:00Z',
})

function run(events: SseEvent[]): StreamState {
  return events.reduce(applyEvent, initialStreamState('c1', 'Câu hỏi'))
}

describe('applyEvent', () => {
  it('follows start → token* → citations → done', () => {
    const c = citation()
    const s = run([
      ev('start', { message_id: 'm1', model_id: 'gpt' }),
      ev('token', { t: 'Theo ' }),
      ev('token', { t: 'Điều 113 [1].' }),
      ev('citations', { citations: [c], uncited: false }),
      ev('done', { refused: false }),
    ])
    expect(s).toMatchObject({
      messageId: 'm1',
      modelId: 'gpt',
      text: 'Theo Điều 113 [1].',
      citations: [c],
      status: 'done',
      refused: false,
      error: null,
    })
    expect(isStreamActive(s)).toBe(false)
  })

  it('marks streaming after start and keeps partial text on a later error', () => {
    const s1 = run([ev('start', { message_id: 'm1', model_id: 'x' })])
    expect(s1.status).toBe('streaming')
    expect(isStreamActive(s1)).toBe(true)
    const s2 = [ev('token', { t: 'Một phần' }), ev('error', { code: 'rate_limited', message: 'Chậm lại' })].reduce(
      applyEvent,
      s1,
    )
    expect(s2).toMatchObject({ text: 'Một phần', status: 'error', error: { code: 'rate_limited', message: 'Chậm lại' } })
  })

  it('handles an error before start (nothing saved)', () => {
    const s = run([ev('error', { code: 'no_api_key', message: 'Bạn chưa nhập API key cho OpenAI.' })])
    expect(s.messageId).toBeNull()
    expect(s.error?.code).toBe('no_api_key')
  })

  it('falls back to the known text when the error has no message', () => {
    const s = run([ev('error', { code: 'invalid_key' })])
    expect(s.error?.message).toBe(chatErrorText('invalid_key'))
    expect(run([ev('error', {})]).error?.code).toBe('provider_error')
  })

  it('records a refusal and ignores bad payloads / unknown events', () => {
    const s = run([
      ev('start', { message_id: 'm1', model_id: 'x' }),
      { event: 'token', data: 'not json' },
      ev('token', { t: 42 }),
      ev('ping', {}),
      ev('citations', { citations: [] }),
      ev('done', { refused: true }),
    ])
    expect(s).toMatchObject({ text: '', citations: [], refused: true, status: 'done' })
  })
})

describe('interrupted / failed', () => {
  it('turns a stream that ended early into an error', () => {
    const s = interrupted(run([ev('start', { message_id: 'm1', model_id: 'x' })]))
    expect(s.status).toBe('error')
    expect(s.error?.code).toBe('stream_interrupted')
  })

  it('keeps the ApiError code and message', () => {
    const s = failed(initialStreamState('c1', 'q'), new ApiError(404, 'not_found', 'Không thấy'))
    expect(s.error).toEqual({ code: 'not_found', message: 'Không thấy' })
    expect(failed(initialStreamState('c1', 'q'), new Error('x')).error?.code).toBe('network_error')
  })
})

describe('showStreamBubble', () => {
  const connecting = initialStreamState('c1', 'q')
  const started = run([ev('start', { message_id: 'm1', model_id: 'x' })])

  it('shows the local copy only in its conversation until the message is loaded', () => {
    expect(showStreamBubble(null, 'c1', [])).toBe(false)
    expect(showStreamBubble(connecting, 'c1', [])).toBe(true)
    expect(showStreamBubble(connecting, 'c2', [])).toBe(false)
    expect(showStreamBubble(started, 'c1', [message('m0')])).toBe(true)
    expect(showStreamBubble(started, 'c1', undefined)).toBe(true)
    expect(showStreamBubble({ ...started, status: 'done' }, 'c1', [message('m1')])).toBe(false)
  })

  it('hides an error before start (shown as a banner instead)', () => {
    const s = run([ev('error', { code: 'no_model', message: 'x' })])
    expect(showStreamBubble(s, 'c1', [])).toBe(false)
    expect(showStreamBubble({ ...connecting, status: 'cancelled' }, 'c1', [])).toBe(false)
  })
})

describe('splitMarkers', () => {
  it('splits markers into numbers', () => {
    expect(splitMarkers('Theo luật [1].', null)).toEqual(['Theo luật ', 1, '.'])
    expect(splitMarkers('A [1, 3] B [2;1]', null)).toEqual(['A ', 1, 3, ' B ', 2, 1])
    expect(splitMarkers('[1-3]', null)).toEqual([1, 2, 3])
    expect(splitMarkers('[ 2 – 3 ]', null)).toEqual([2, 3])
  })

  it('keeps only cited numbers once citations are known', () => {
    const valid = new Set([1, 2])
    expect(splitMarkers('x [1][5] y [2, 9]', valid)).toEqual(['x ', 1, '[5] y ', 2])
    expect(splitMarkers('[7]', new Set())).toEqual(['[7]'])
  })

  it('leaves non-markers alone', () => {
    expect(splitMarkers('năm [2019], [a], [1.2], [0]', null)).toEqual(['năm [2019], [a], [1.2], [0]'])
  })

  it('keeps both ends of a reversed or too wide range', () => {
    expect(splitMarkers('[3-1]', null)).toEqual([3, 1])
    expect(splitMarkers('[1-50]', null)).toEqual([1, 50])
  })
})

describe('linkMarkersInTree', () => {
  const text = (value: string): MdNode => ({ type: 'text', value })

  it('links markers in text nodes', () => {
    const tree: MdNode = { type: 'root', children: [{ type: 'paragraph', children: [text('Theo [1].')] }] }
    linkMarkersInTree(tree, null)
    expect(tree.children![0].children).toEqual([
      text('Theo '),
      { type: 'link', url: '#cite-1', children: [text('1')] },
      text('.'),
    ])
  })

  it('leaves code and model-written links untouched', () => {
    const code: MdNode = { type: 'inlineCode', value: 'a[1]' }
    const block: MdNode = { type: 'code', value: 'x = [1]' }
    const link: MdNode = { type: 'link', url: 'https://vbpl.vn', children: [text('[1]')] }
    const tree: MdNode = { type: 'root', children: [{ type: 'paragraph', children: [code, link] }, block] }
    const before = structuredClone(tree)
    linkMarkersInTree(tree, null)
    expect(tree).toEqual(before)
  })
})

describe('citeNumber', () => {
  it('reads only cite links', () => {
    expect(citeNumber('#cite-3')).toBe(3)
    expect(citeNumber('#cite-x')).toBeNull()
    expect(citeNumber('#cite-0')).toBeNull()
    expect(citeNumber('https://example.com')).toBeNull()
    expect(citeNumber(undefined)).toBeNull()
  })
})

describe('citations', () => {
  it('formats the heading from DB metadata', () => {
    expect(citationHeading(citation())).toBe('Bộ luật Lao động (45/2019/QH14) — Điều 113. Nghỉ hằng năm')
    expect(citationHeading(citation({ so_hieu: null, dieu_title: null }))).toBe('Bộ luật Lao động — Điều 113')
    expect(citationHeading(citation({ dieu: null }))).toBe('Bộ luật Lao động (45/2019/QH14)')
  })

  it('detects uncited fallback sources', () => {
    expect(isUncited([])).toBe(false)
    expect(isUncited([citation({ uncited: true }), citation({ n: 2, uncited: true })])).toBe(true)
    expect(isUncited([citation()])).toBe(false)
  })
})

describe('errors', () => {
  it('sends key / model errors to Settings', () => {
    for (const code of ['invalid_key', 'no_api_key', 'key_unreadable', 'no_model']) {
      expect(needsSettings(code)).toBe(true)
    }
    expect(needsSettings('rate_limited')).toBe(false)
    expect(needsSettings(null)).toBe(false)
  })

  it('has a fallback text for unknown codes', () => {
    expect(chatErrorText('???')).toBe(chatErrorText('provider_error'))
  })
})

describe('effectiveModelId', () => {
  const models: ModelInfo[] = [
    { id: 'a', provider: 'openai', label: 'A' },
    { id: 'b', provider: 'anthropic', label: 'B' },
  ]
  it('prefers the pick, then the default, then the first selectable', () => {
    expect(effectiveModelId('b', 'a', models)).toBe('b')
    expect(effectiveModelId(null, 'b', models)).toBe('b')
    expect(effectiveModelId('gone', 'gone', models)).toBe('a')
    expect(effectiveModelId(null, null, [])).toBeNull()
  })
})

describe('flattenConversations', () => {
  it('drops duplicates across shifted pages', () => {
    const c = (id: string) => ({ id, title: id, created_at: '', updated_at: '' })
    expect(flattenConversations([{ items: [c('1'), c('2')] }, { items: [c('2'), c('3')] }]).map((x) => x.id)).toEqual([
      '1',
      '2',
      '3',
    ])
    expect(flattenConversations(undefined)).toEqual([])
  })
})

describe('isSendKey', () => {
  it('sends on Enter only, not Shift+Enter or during IME composition', () => {
    expect(isSendKey({ key: 'Enter', shiftKey: false, isComposing: false })).toBe(true)
    expect(isSendKey({ key: 'Enter', shiftKey: true, isComposing: false })).toBe(false)
    expect(isSendKey({ key: 'Enter', shiftKey: false, isComposing: true })).toBe(false)
    expect(isSendKey({ key: 'a', shiftKey: false, isComposing: false })).toBe(false)
  })
})
