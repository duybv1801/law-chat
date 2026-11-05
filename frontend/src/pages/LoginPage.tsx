import { Link } from 'react-router'
import AuthForm from '../components/AuthForm.tsx'
import { useLogin } from '../hooks/useAuth.ts'
import { errorMessage } from '../lib/errors.ts'

export default function LoginPage() {
  const login = useLogin()

  return (
    <AuthForm
      title="Đăng nhập"
      submitLabel="Đăng nhập"
      pendingLabel="Đang đăng nhập…"
      isPending={login.isPending}
      error={login.isError ? errorMessage(login.error) : null}
      passwordAutoComplete="current-password"
      // On success GuestOnly redirects back to the page the user came from.
      onSubmit={(values) => login.mutate(values)}
      footer={
        <>
          Chưa có tài khoản?{' '}
          <Link to="/register" className="font-medium text-slate-800 underline">
            Đăng ký
          </Link>
        </>
      }
    />
  )
}
