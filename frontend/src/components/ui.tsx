/** Small shared UI pieces (Tailwind classes, spinner, error text). */

export const inputClass =
  'w-full min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-200 disabled:bg-slate-50'
export const primaryButton =
  'inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-md bg-slate-800 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-60'
export const secondaryButton =
  'shrink-0 whitespace-nowrap rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-60'
export const dangerButton =
  'shrink-0 whitespace-nowrap rounded-md border border-red-200 bg-white px-3 py-1.5 text-sm text-red-700 hover:bg-red-50 disabled:opacity-60'
export const sectionClass = 'rounded-lg border border-slate-200 bg-white p-5 shadow-sm'

/** White spinner for dark buttons; `dark` for light backgrounds. */
export function Spinner({ dark = false }: { dark?: boolean }) {
  return (
    <span
      aria-hidden
      className={`inline-block size-4 shrink-0 animate-spin rounded-full border-2 ${
        dark ? 'border-slate-300 border-t-slate-600' : 'border-white/40 border-t-white'
      }`}
    />
  )
}

export function ErrorText({ children }: { children: string }) {
  return (
    <p role="alert" className="mt-1 text-sm text-red-600">
      {children}
    </p>
  )
}
