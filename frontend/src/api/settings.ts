/** Models & Settings endpoints (docs/06-api.md, section Models & Settings). */
import { api } from './client.ts'

// Must match backend/app/db/models.py `Provider` / `PROVIDER_LABELS`
// (checked by backend/tests/test_settings.py).
export const PROVIDERS = ['openai', 'anthropic', 'gemini'] as const
export type Provider = (typeof PROVIDERS)[number]

export const PROVIDER_LABELS: Record<Provider, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  gemini: 'Gemini',
}

// Must match backend/app/settings/schemas.py (checked by backend/tests/test_settings.py).
export const API_KEY_MIN = 8
export const API_KEY_MAX = 512

export interface ModelInfo {
  id: string
  provider: Provider
  label: string
}

export interface ApiKeyInfo {
  provider: Provider
  key_last4: string
  validated_at: string | null
  last_used_at: string | null
}

export interface Settings {
  default_provider: Provider | null
  default_model_id: string | null
  api_keys: ApiKeyInfo[]
}

export type ApiKeySaved = Omit<ApiKeyInfo, 'last_used_at'>

export function getModels(): Promise<ModelInfo[]> {
  return api<ModelInfo[]>('/models')
}

export function getSettings(): Promise<Settings> {
  return api<Settings>('/settings')
}

/** `null` clears the default model. */
export function putSettings(defaultModelId: string | null): Promise<Settings> {
  return api<Settings>('/settings', { method: 'PUT', body: { default_model_id: defaultModelId } })
}

/** Validates the key with the provider first; an invalid key → `ApiError(400, 'invalid_key')`. */
export function putApiKey(provider: Provider, apiKey: string): Promise<ApiKeySaved> {
  return api<ApiKeySaved>(`/settings/api-keys/${provider}`, {
    method: 'PUT',
    body: { api_key: apiKey },
  })
}

export function deleteApiKey(provider: Provider): Promise<void> {
  return api<void>(`/settings/api-keys/${provider}`, { method: 'DELETE' })
}
