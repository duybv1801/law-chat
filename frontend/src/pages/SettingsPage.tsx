import { useEffect, useState, type FormEvent } from 'react'
import {
  API_KEY_MAX,
  API_KEY_MIN,
  PROVIDER_LABELS,
  PROVIDERS,
  type ApiKeyInfo,
  type Provider,
} from '../api/settings.ts'
import {
  useDeleteApiKey,
  useModels,
  useSaveApiKey,
  useSetDefaultModel,
  useSettings,
} from '../hooks/useSettings.ts'
import { errorMessage } from '../lib/errors.ts'
import {
  apiKeyErrorMessage,
  formatDateTime,
  keysByProvider,
  maskedKey,
  selectableModels,
} from '../lib/settings.ts'

const inputClass =
  'w-full min-w-0 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-200 disabled:bg-slate-50'
const primaryButton =
  'inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-md bg-slate-800 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-60'
const secondaryButton =
  'shrink-0 whitespace-nowrap rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-60'
const dangerButton =
  'shrink-0 whitespace-nowrap rounded-md border border-red-200 bg-white px-3 py-1.5 text-sm text-red-700 hover:bg-red-50 disabled:opacity-60'

function Spinner() {
  return (
    <span
      aria-hidden
      className="size-4 animate-spin rounded-full border-2 border-white/40 border-t-white"
    />
  )
}

function ErrorText({ children }: { children: string }) {
  return (
    <p role="alert" className="mt-1 text-sm text-red-600">
      {children}
    </p>
  )
}

interface KeyFormProps {
  provider: Provider
  autoFocus?: boolean
  onCancel?: () => void
  onSaved: () => void
}

/** Password input + "Lưu và kiểm tra". The key only lives in this component's state until sent. */
function KeyForm({ provider, autoFocus, onCancel, onSaved }: KeyFormProps) {
  const save = useSaveApiKey(provider)
  const [value, setValue] = useState('')
  const [tooShort, setTooShort] = useState(false)
  const inputId = `api-key-${provider}`
  const hasError = tooShort || save.isError

  const onSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (save.isPending) return
    // minLength on the input counts surrounding spaces; the trimmed key is what gets sent.
    const apiKey = value.trim()
    if (apiKey.length < API_KEY_MIN) {
      setTooShort(true)
      return
    }
    save.mutate(apiKey, {
      onSuccess: () => {
        setValue('')
        onSaved()
      },
    })
  }

  return (
    <form onSubmit={onSubmit} className="mt-2">
      <label htmlFor={inputId} className="sr-only">
        API key {PROVIDER_LABELS[provider]}
      </label>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input
          id={inputId}
          type="password"
          autoFocus={autoFocus}
          required
          autoComplete="off"
          spellCheck={false}
          minLength={API_KEY_MIN}
          maxLength={API_KEY_MAX}
          placeholder={`Nhập API key ${PROVIDER_LABELS[provider]}`}
          value={value}
          onChange={(e) => {
            setValue(e.target.value)
            setTooShort(false)
            if (save.isError) save.reset()
          }}
          disabled={save.isPending}
          aria-invalid={hasError}
          aria-describedby={hasError ? `${inputId}-error` : undefined}
          className={inputClass}
        />
        <div className="flex shrink-0 gap-2">
          <button type="submit" disabled={save.isPending} className={primaryButton}>
            {save.isPending && <Spinner />}
            {save.isPending ? 'Đang kiểm tra…' : 'Lưu và kiểm tra'}
          </button>
          {onCancel && (
            <button
              type="button"
              onClick={onCancel}
              disabled={save.isPending}
              className={secondaryButton}
            >
              Huỷ
            </button>
          )}
        </div>
      </div>
      {hasError && (
        <div id={`${inputId}-error`}>
          <ErrorText>
            {tooShort
              ? `API key phải có ít nhất ${API_KEY_MIN} ký tự.`
              : apiKeyErrorMessage(save.error)}
          </ErrorText>
        </div>
      )}
    </form>
  )
}

function SavedKey({ info, onReplace }: { info: ApiKeyInfo; onReplace: () => void }) {
  const remove = useDeleteApiKey(info.provider)
  const [confirming, setConfirming] = useState(false)
  const validatedAt = formatDateTime(info.validated_at)
  const lastUsedAt = formatDateTime(info.last_used_at)

  return (
    <div className="mt-2">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <code className="rounded bg-slate-100 px-2 py-1 font-mono text-sm text-slate-800">
          {maskedKey(info.key_last4)}
        </code>
        <span className="text-xs text-slate-500">
          {validatedAt ? `Đã kiểm tra lúc ${validatedAt}` : 'Chưa kiểm tra với nhà cung cấp'}
          {lastUsedAt && ` · Dùng lần cuối ${lastUsedAt}`}
        </span>
        <div className="ml-auto flex gap-2">
          {confirming ? (
            <>
              <span className="self-center text-sm text-slate-700">Xoá key này?</span>
              <button
                type="button"
                // On success the row switches to the key form; on failure the confirmation stays
                // open with the error so the user can retry or cancel.
                onClick={() => remove.mutate()}
                disabled={remove.isPending}
                className={dangerButton}
              >
                {remove.isPending ? 'Đang xoá…' : 'Xoá'}
              </button>
              <button
                type="button"
                onClick={() => {
                  remove.reset()
                  setConfirming(false)
                }}
                disabled={remove.isPending}
                className={secondaryButton}
              >
                Huỷ
              </button>
            </>
          ) : (
            <>
              <button type="button" onClick={onReplace} className={secondaryButton}>
                Thay key
              </button>
              <button
                type="button"
                onClick={() => {
                  remove.reset()
                  setConfirming(true)
                }}
                className={dangerButton}
              >
                Xoá
              </button>
            </>
          )}
        </div>
      </div>
      {remove.isError && <ErrorText>{`Xoá thất bại: ${errorMessage(remove.error)}`}</ErrorText>}
    </div>
  )
}

function ProviderKeyRow({ provider, info }: { provider: Provider; info: ApiKeyInfo | undefined }) {
  const [replacing, setReplacing] = useState(false)
  const showForm = !info || replacing

  return (
    <li className="py-4 first:pt-0 last:pb-0">
      <p className="font-medium text-slate-800">{PROVIDER_LABELS[provider]}</p>
      {showForm ? (
        <KeyForm
          // Remount on mode change so a stale error / value from the other mode is dropped.
          key={info ? 'replace' : 'new'}
          provider={provider}
          autoFocus={replacing}
          onCancel={info ? () => setReplacing(false) : undefined}
          onSaved={() => setReplacing(false)}
        />
      ) : (
        <SavedKey info={info} onReplace={() => setReplacing(true)} />
      )}
    </li>
  )
}

function DefaultModelSection({ apiKeys, defaultModelId }: {
  apiKeys: ApiKeyInfo[]
  defaultModelId: string | null
}) {
  const models = useModels()
  const setDefault = useSetDefaultModel()
  const options = selectableModels(models.data ?? [], apiKeys)
  // The saved default may be missing from a cached (stale) models list; still show it.
  const missingDefault =
    models.data && defaultModelId && !options.some((m) => m.id === defaultModelId)
      ? defaultModelId
      : null
  const { refetch: refetchModels } = models

  useEffect(() => {
    if (missingDefault) void refetchModels()
  }, [missingDefault, refetchModels])

  if (models.isPending) return <p className="text-sm text-slate-500">Đang tải danh sách model…</p>
  if (models.isError) return <ErrorText>{errorMessage(models.error)}</ErrorText>
  if (options.length === 0 && !missingDefault) {
    return (
      <p className="text-sm text-slate-500">
        Thêm API key cho ít nhất một nhà cung cấp để chọn model mặc định.
      </p>
    )
  }

  return (
    <div>
      <label htmlFor="default-model" className="sr-only">
        Model mặc định
      </label>
      <div className="flex items-center gap-3">
        <select
          id="default-model"
          value={defaultModelId ?? ''}
          disabled={setDefault.isPending}
          onChange={(e) => setDefault.mutate(e.target.value || null)}
          className={`${inputClass} max-w-sm`}
        >
          <option value="">— Chưa chọn —</option>
          {missingDefault && <option value={missingDefault}>{missingDefault}</option>}
          {options.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label} ({PROVIDER_LABELS[m.provider]})
            </option>
          ))}
        </select>
        {setDefault.isPending && <span className="text-sm text-slate-500">Đang lưu…</span>}
        {setDefault.isSuccess && setDefault.data.default_model_id === defaultModelId && (
          <span className="text-sm text-green-700">Đã lưu</span>
        )}
      </div>
      {setDefault.isError && <ErrorText>{errorMessage(setDefault.error)}</ErrorText>}
    </div>
  )
}

const sectionClass = 'rounded-lg border border-slate-200 bg-white p-5 shadow-sm'

export default function SettingsPage() {
  const settings = useSettings()
  const keys = settings.data ? keysByProvider(settings.data.api_keys) : {}

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-4 py-8">
      <h1 className="text-xl font-semibold text-slate-800">Cài đặt</h1>

      {settings.isPending && <p className="text-sm text-slate-500">Đang tải…</p>}
      {settings.isError && !settings.data && (
        <div className="space-y-2">
          <ErrorText>{errorMessage(settings.error)}</ErrorText>
          <button type="button" onClick={() => void settings.refetch()} className={secondaryButton}>
            Thử lại
          </button>
        </div>
      )}

      {settings.data && (
        <>
          <section className={sectionClass}>
            <h2 className="font-semibold text-slate-800">API key</h2>
            <p className="mt-1 text-sm text-slate-500">
              Key được mã hoá khi lưu và không bao giờ hiển thị lại.
            </p>
            <ul className="mt-4 divide-y divide-slate-100">
              {PROVIDERS.map((provider) => (
                <ProviderKeyRow
                  key={provider}
                  provider={provider}
                  info={keys[provider]}
                />
              ))}
            </ul>
          </section>

          <section className={sectionClass}>
            <h2 className="font-semibold text-slate-800">Model mặc định</h2>
            <p className="mt-1 mb-3 text-sm text-slate-500">
              Dùng cho hội thoại mới. Chỉ hiện model của nhà cung cấp đã có key.
            </p>
            <DefaultModelSection
              apiKeys={settings.data.api_keys}
              defaultModelId={settings.data.default_model_id}
            />
          </section>
        </>
      )}
    </div>
  )
}
