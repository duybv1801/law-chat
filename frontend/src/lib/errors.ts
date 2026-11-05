import { ApiError, FALLBACK_MESSAGE } from '../api/client.ts'

/** User-facing (Vietnamese) message for any error thrown by the API layer. */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message
  return FALLBACK_MESSAGE
}
