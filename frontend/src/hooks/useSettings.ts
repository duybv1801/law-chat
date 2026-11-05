import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as settingsApi from '../api/settings.ts'
import type { Provider, Settings } from '../api/settings.ts'
import { withoutKey, withSavedKey } from '../lib/settings.ts'

export const SETTINGS_QUERY_KEY = ['settings'] as const
export const MODELS_QUERY_KEY = ['models'] as const

export function useSettings() {
  return useQuery({ queryKey: SETTINGS_QUERY_KEY, queryFn: settingsApi.getSettings })
}

export function useModels() {
  return useQuery({
    queryKey: MODELS_QUERY_KEY,
    queryFn: settingsApi.getModels,
    // models.yaml only changes on backend restart.
    staleTime: 10 * 60 * 1000,
  })
}

/** Apply a local update to the cached settings, then refetch to stay in sync with the server. */
function useUpdateSettingsCache() {
  const queryClient = useQueryClient()
  return (update: (s: Settings) => Settings) => {
    queryClient.setQueryData<Settings>(SETTINGS_QUERY_KEY, (s) => (s ? update(s) : s))
    void queryClient.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY })
  }
}

export function useSaveApiKey(provider: Provider) {
  const updateCache = useUpdateSettingsCache()
  return useMutation({
    mutationFn: (apiKey: string) => settingsApi.putApiKey(provider, apiKey),
    onSuccess: (saved) => updateCache((s) => withSavedKey(s, saved)),
  })
}

export function useDeleteApiKey(provider: Provider) {
  const updateCache = useUpdateSettingsCache()
  return useMutation({
    mutationFn: () => settingsApi.deleteApiKey(provider),
    onSuccess: () => updateCache((s) => withoutKey(s, provider)),
  })
}

export function useSetDefaultModel() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: settingsApi.putSettings,
    onSuccess: async (settings) => {
      // A refetch started earlier (e.g. after saving a key) could resolve later with the old
      // default and overwrite this response.
      await queryClient.cancelQueries({ queryKey: SETTINGS_QUERY_KEY })
      queryClient.setQueryData(SETTINGS_QUERY_KEY, settings)
    },
  })
}
