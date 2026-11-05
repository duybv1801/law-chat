/** Auth endpoints (docs/06-api.md, section Auth). */
import { api } from './client.ts'

export type UserRole = 'user' | 'admin'

export interface User {
  id: string
  email: string
  role: UserRole
}

export interface Credentials {
  email: string
  password: string
}

// Must match backend/app/auth/schemas.py (checked by backend/tests/test_auth.py).
export const EMAIL_MAX = 254
export const PASSWORD_MIN = 8
export const PASSWORD_MAX = 128

export function register(body: Credentials): Promise<User> {
  return api<User>('/auth/register', { method: 'POST', body })
}

export function login(body: Credentials): Promise<User> {
  return api<User>('/auth/login', { method: 'POST', body })
}

export function logout(): Promise<void> {
  return api<void>('/auth/logout', { method: 'POST' })
}

export function getMe(): Promise<User> {
  return api<User>('/auth/me')
}
