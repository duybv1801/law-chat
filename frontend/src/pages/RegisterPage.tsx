import { Link } from 'react-router'
import { PASSWORD_MIN } from '../api/auth.ts'
import AuthForm from '../components/AuthForm.tsx'
import { useRegister } from '../hooks/useAuth.ts'
import { errorMessage } from '../lib/errors.ts'

export default function RegisterPage() {
  const register = useRegister()

  return (
    <AuthForm
      title="Đăng ký"
      submitLabel="Tạo tài khoản"
      pendingLabel="Đang tạo tài khoản…"
      isPending={register.isPending}
      error={register.isError ? errorMessage(register.error) : null}
      passwordMinLength={PASSWORD_MIN}
      passwordAutoComplete="new-password"
      // On success GuestOnly redirects into the app.
      onSubmit={(values) => register.mutate(values)}
      footer={
        <>
          Đã có tài khoản?{' '}
          <Link to="/login" className="font-medium text-slate-800 underline">
            Đăng nhập
          </Link>
        </>
      }
    />
  )
}
