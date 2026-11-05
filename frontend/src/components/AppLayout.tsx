import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, Outlet, useNavigate } from 'react-router'
import { useLogout, useMe } from '../hooks/useAuth.ts'
import { errorMessage } from '../lib/errors.ts'

const tabClass = ({ isActive }: { isActive: boolean }) =>
  `rounded-md px-3 py-1.5 text-sm font-medium ${
    isActive ? 'bg-slate-800 text-white' : 'text-slate-600 hover:bg-slate-100'
  }`

function UserMenu() {
  const { data: user } = useMe()
  const logout = useLogout()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  if (!user) return null

  const onLogout = () => {
    logout.mutate(undefined, { onSuccess: () => navigate('/login', { replace: true }) })
  }

  const close = () => setOpen(false)
  const itemClass = 'block w-full px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-100'

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex max-w-[12rem] items-center gap-1 rounded-md px-2 py-1.5 text-sm text-slate-700 hover:bg-slate-100"
      >
        <span className="truncate">{user.email}</span>
        <span aria-hidden>▾</span>
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-20 mt-1 w-48 overflow-hidden rounded-md border border-slate-200 bg-white py-1 shadow-lg"
        >
          <Link role="menuitem" to="/settings" className={itemClass} onClick={close}>
            Cài đặt
          </Link>
          {user.role === 'admin' && (
            <Link role="menuitem" to="/admin" className={itemClass} onClick={close}>
              Admin
            </Link>
          )}
          <button
            role="menuitem"
            type="button"
            onClick={onLogout}
            disabled={logout.isPending}
            className={`${itemClass} border-t border-slate-100 disabled:opacity-50`}
          >
            {logout.isPending ? 'Đang đăng xuất…' : 'Đăng xuất'}
          </button>
          {logout.isError && (
            <p role="alert" className="px-4 pb-2 text-xs text-red-600">
              Đăng xuất thất bại: {errorMessage(logout.error)}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

export default function AppLayout() {
  return (
    <div className="flex min-h-screen flex-col bg-slate-50">
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-white">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-2 px-4 sm:gap-6">
          <Link to="/" className="shrink-0 font-semibold text-slate-800">
            <span className="hidden sm:inline">Chatbot Luật Việt Nam</span>
            <span className="sm:hidden">Luật VN</span>
          </Link>
          <nav className="flex gap-1">
            <NavLink to="/documents" className={tabClass}>
              Văn bản
            </NavLink>
            <NavLink to="/chat" className={tabClass}>
              Hỏi đáp
            </NavLink>
          </nav>
          <div className="ml-auto">
            <UserMenu />
          </div>
        </div>
      </header>
      <main className="flex-1">
        <Outlet />
      </main>
    </div>
  )
}
