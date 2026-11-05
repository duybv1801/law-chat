import { describe, expect, it } from 'vitest'
import { ApiError, FALLBACK_MESSAGE } from '../api/client.ts'
import type { ApiKeyInfo, ModelInfo, Settings } from '../api/settings.ts'
import {
  apiKeyErrorMessage,
  formatDateTime,
  INVALID_KEY_MESSAGE,
  keysByProvider,
  maskedKey,
  selectableModels,
  withoutKey,
  withSavedKey,
} from './settings.ts'

const key = (provider: ApiKeyInfo['provider'], last4 = 'abcd'): ApiKeyInfo => ({
  provider,
  key_last4: last4,
  validated_at: '2026-11-05T03:04:00Z',
  last_used_at: null,
})

const models: ModelInfo[] = [
  { id: 'openai-main', provider: 'openai', label: 'GPT' },
  { id: 'anthropic-main', provider: 'anthropic', label: 'Claude' },
  { id: 'gemini-main', provider: 'gemini', label: 'Gemini' },
  { id: 'openai-mini', provider: 'openai', label: 'GPT mini' },
]

describe('maskedKey', () => {
  it('shows only the last 4 characters', () => {
    expect(maskedKey('abcd')).toBe('••••abcd')
  })
})

describe('keysByProvider', () => {
  it('indexes keys by provider', () => {
    const result = keysByProvider([key('openai', '1111'), key('gemini', '2222')])
    expect(result.openai?.key_last4).toBe('1111')
    expect(result.gemini?.key_last4).toBe('2222')
    expect(result.anthropic).toBeUndefined()
  })
})

describe('selectableModels', () => {
  it('returns nothing without keys', () => {
    expect(selectableModels(models, [])).toEqual([])
  })

  it('keeps only models of providers with a key, in API order', () => {
    expect(selectableModels(models, [key('openai')]).map((m) => m.id)).toEqual([
      'openai-main',
      'openai-mini',
    ])
    expect(
      selectableModels(models, [key('gemini'), key('anthropic')]).map((m) => m.id),
    ).toEqual(['anthropic-main', 'gemini-main'])
  })
})

describe('settings cache updates', () => {
  const base: Settings = {
    default_provider: 'openai',
    default_model_id: 'openai-main',
    api_keys: [key('openai', '1111'), key('gemini', '2222')],
  }

  it('adds a new key', () => {
    const next = withSavedKey(base, { provider: 'anthropic', key_last4: '3333', validated_at: null })
    expect(next.api_keys.map((k) => k.provider)).toEqual(['anthropic', 'gemini', 'openai'])
    expect(base.api_keys).toHaveLength(2)
  })

  it('replaces an existing key and resets last_used_at', () => {
    const used = { ...base, api_keys: [{ ...key('openai', '1111'), last_used_at: '2026-11-01T00:00:00Z' }] }
    const next = withSavedKey(used, { provider: 'openai', key_last4: '9999', validated_at: null })
    expect(next.api_keys).toEqual([
      { provider: 'openai', key_last4: '9999', validated_at: null, last_used_at: null },
    ])
  })

  it('clears the default model when deleting its provider key', () => {
    const next = withoutKey(base, 'openai')
    expect(next.default_provider).toBeNull()
    expect(next.default_model_id).toBeNull()
    expect(next.api_keys.map((k) => k.provider)).toEqual(['gemini'])
  })

  it('keeps the default model when deleting another provider key', () => {
    const next = withoutKey(base, 'gemini')
    expect(next.default_model_id).toBe('openai-main')
    expect(next.api_keys.map((k) => k.provider)).toEqual(['openai'])
  })
})

describe('formatDateTime', () => {
  it('formats as dd/mm/yyyy HH:mm in local time', () => {
    const d = new Date(2026, 0, 2, 3, 4)
    expect(formatDateTime(d.toISOString())).toBe('02/01/2026 03:04')
  })

  it.each([[null], [''], ['not-a-date']])('returns null for %j', (value) => {
    expect(formatDateTime(value)).toBeNull()
  })
})

describe('apiKeyErrorMessage', () => {
  it('rewords invalid_key for the settings page', () => {
    const err = new ApiError(400, 'invalid_key', 'API key không hợp lệ. Vào Cài đặt để cập nhật.')
    expect(apiKeyErrorMessage(err)).toBe(INVALID_KEY_MESSAGE)
  })

  it('keeps the backend message for other errors', () => {
    expect(apiKeyErrorMessage(new ApiError(429, 'too_many_requests', 'Thử lại sau'))).toBe(
      'Thử lại sau',
    )
    expect(apiKeyErrorMessage(new Error('x'))).toBe(FALLBACK_MESSAGE)
  })
})
