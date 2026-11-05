import { useState, type FormEvent, type ReactNode } from 'react'
import { EMAIL_MAX, PASSWORD_MAX, type Credentials } from '../api/auth.ts'

interface AuthFormProps {
  title: string
  submitLabel: string
  pendingLabel: string
  isPending: boolean
  error: string | null
  passwordMinLength?: number
  passwordAutoComplete: 'current-password' | 'new-password'
  onSubmit: (values: Credentials) => void
  footer: ReactNode
}

const inputClass =
  'w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-200'

export default function AuthForm(props: AuthFormProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  const handleSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (props.isPending) return
    props.onSubmit({ email: email.trim(), password })
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
      <div className="w-full max-w-sm">
        <p className="mb-6 text-center text-lg font-semibold text-slate-800">
          Chatbot Luật Việt Nam
        </p>
        <form
          onSubmit={handleSubmit}
          className="space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm"
        >
          <h1 className="text-xl font-semibold text-slate-800">{props.title}</h1>
          <label className="block space-y-1">
            <span className="text-sm text-slate-700">Email</span>
            <input
              type="email"
              required
              autoComplete="email"
              maxLength={EMAIL_MAX}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={inputClass}
            />
          </label>
          <label className="block space-y-1">
            <span className="text-sm text-slate-700">Mật khẩu</span>
            <input
              type="password"
              required
              autoComplete={props.passwordAutoComplete}
              minLength={props.passwordMinLength}
              maxLength={PASSWORD_MAX}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={inputClass}
            />
            {props.passwordMinLength && (
              <span className="text-xs text-slate-500">
                Tối thiểu {props.passwordMinLength} ký tự.
              </span>
            )}
          </label>
          {props.error && (
            <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
              {props.error}
            </p>
          )}
          <button
            type="submit"
            disabled={props.isPending}
            className="w-full rounded-md bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-60"
          >
            {props.isPending ? props.pendingLabel : props.submitLabel}
          </button>
        </form>
        <p className="mt-4 text-center text-sm text-slate-600">{props.footer}</p>
      </div>
    </main>
  )
}
