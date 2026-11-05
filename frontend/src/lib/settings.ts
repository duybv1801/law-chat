/** Pure helpers for the Settings page (and the chat model picker in T23). */
import { ApiError } from '../api/client.ts'
import type { ApiKeyInfo, ModelInfo, Provider, Settings } from '../api/settings.ts'
import { errorMessage } from './errors.ts'

/** Display form of a stored key: only the last 4 characters are ever known to the frontend. */
export function maskedKey(last4: string): string {
  return `••••${last4}`
}

/** Index the user's keys by provider. */
export function keysByProvider(apiKeys: ApiKeyInfo[]): Partial<Record<Provider, ApiKeyInfo>> {
  const result: Partial<Record<Provider, ApiKeyInfo>> = {}
  for (const key of apiKeys) result[key.provider] = key
  return result
}

/** Models the user can pick: those whose provider has a saved key (order kept from the API). */
export function selectableModels(models: ModelInfo[], apiKeys: ApiKeyInfo[]): ModelInfo[] {
  const providers = new Set(apiKeys.map((k) => k.provider))
  return models.filter((m) => providers.has(m.provider))
}

/** Settings after a key was saved: insert or replace that provider's entry, sorted by provider like the API. */
export function withSavedKey(settings: Settings, saved: Omit<ApiKeyInfo, 'last_used_at'>): Settings {
  const entry: ApiKeyInfo = { ...saved, last_used_at: null }
  const others = settings.api_keys.filter((k) => k.provider !== saved.provider)
  const api_keys = [...others, entry].sort((a, b) =>
    a.provider < b.provider ? -1 : a.provider > b.provider ? 1 : 0,
  )
  return { ...settings, api_keys }
}

/**
 * Settings after a key was deleted. Mirrors the backend: the default model is cleared when it
 * belongs to that provider.
 */
export function withoutKey(settings: Settings, provider: Provider): Settings {
  const clearDefault = settings.default_provider === provider
  return {
    default_provider: clearDefault ? null : settings.default_provider,
    default_model_id: clearDefault ? null : settings.default_model_id,
    api_keys: settings.api_keys.filter((k) => k.provider !== provider),
  }
}

const pad = (n: number) => String(n).padStart(2, '0')

/** `dd/mm/yyyy HH:mm` in local time; `null` or an unparsable value → `null`. */
export function formatDateTime(iso: string | null): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export const INVALID_KEY_MESSAGE =
  'API key không hợp lệ hoặc đã bị thu hồi. Vui lòng kiểm tra lại key và thử lại.'

/**
 * Message shown under the key input when saving fails. The backend's `invalid_key` message is
 * worded for the chat screen ("Vào Cài đặt để cập nhật"), so it is replaced here.
 */
export function apiKeyErrorMessage(err: unknown): string {
  if (err instanceof ApiError && err.code === 'invalid_key') return INVALID_KEY_MESSAGE
  return errorMessage(err)
}
