import { useEffect, useRef, useState, type DragEvent, type FormEvent } from 'react'
import {
  DOC_TYPES,
  SHORT_FIELD_MAX,
  STATUS_LABELS,
  STATUSES,
  TITLE_MAX,
  UPLOAD_EXTENSIONS,
  type ParseWarning,
  type Status,
} from '../../api/documents.ts'
import { usePreviewUpload, useUploadDocument } from '../../hooks/useDocuments.ts'
import {
  formatFileSize,
  formFromPreview,
  uploadFileError,
  uploadFormData,
  warningMessages,
  type UploadForm,
} from '../../lib/documents.ts'
import { errorMessage } from '../../lib/errors.ts'
import { ErrorText, inputClass, primaryButton, secondaryButton, sectionClass, Spinner } from '../ui.tsx'

const ACCEPT = UPLOAD_EXTENSIONS.join(',')
const labelClass = 'mb-1 block text-sm font-medium text-slate-700'

interface Preview {
  form: UploadForm
  warnings: ParseWarning[]
  fallbackDate: boolean
}

/** Upload area: pick / drop a file → preview (pre-filled metadata form) → upload. */
export default function UploadPanel({ onUploaded }: { onUploaded: (id: string) => void }) {
  const preview = usePreviewUpload()
  const upload = useUploadDocument()
  const inputRef = useRef<HTMLInputElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const [data, setData] = useState<Preview | null>(null)
  const [dragging, setDragging] = useState(false)
  const [uploadedTitle, setUploadedTitle] = useState<string | null>(null)

  useEffect(() => () => abortRef.current?.abort(), [])

  const reset = () => {
    abortRef.current?.abort()
    abortRef.current = null
    preview.reset()
    upload.reset()
    setFile(null)
    setFileError(null)
    setData(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  const pickFile = (picked: File | undefined) => {
    if (!picked || upload.isPending) return
    reset()
    setUploadedTitle(null)
    const error = uploadFileError(picked)
    if (error) {
      setFileError(error)
      return
    }
    setFile(picked)
    const controller = new AbortController()
    abortRef.current = controller
    // Callbacks passed to `mutate` only fire for the latest call: a stale preview is dropped.
    preview.mutate(
      { file: picked, signal: controller.signal },
      {
        onSuccess: (res) =>
          setData({
            form: formFromPreview(res.detected, picked.name),
            warnings: res.warnings,
            fallbackDate: res.detected.issued_date_source === 'fallback' && !!res.detected.issued_date,
          }),
      },
    )
  }

  const onDrop = (e: DragEvent<HTMLElement>) => {
    e.preventDefault()
    setDragging(false)
    pickFile(e.dataTransfer.files[0])
  }

  const setField = <K extends keyof UploadForm>(key: K, value: UploadForm[K]) =>
    setData((d) =>
      d
        ? {
            ...d,
            form: { ...d.form, [key]: value },
            // The user picked the date themselves: it is no longer a guess.
            fallbackDate: key === 'issued_date' ? false : d.fallbackDate,
          }
        : d,
    )

  const onSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (!file || !data || upload.isPending) return
    if (!data.form.title.trim()) return
    upload.mutate(uploadFormData(file, data.form), {
      onSuccess: (res) => {
        setUploadedTitle(data.form.title.trim())
        reset()
        onUploaded(res.id)
      },
    })
  }

  const previewError =
    preview.isError && !(preview.error instanceof DOMException) ? errorMessage(preview.error) : null
  const form = data?.form
  const warnings = data ? warningMessages(data.warnings) : []

  return (
    <section className={sectionClass}>
      <h2 className="font-semibold text-slate-800">Tải văn bản lên</h2>
      <p className="mt-1 text-sm text-slate-500">
        PDF, DOCX hoặc TXT, tối đa 20 MB. Văn bản bạn tải lên chỉ mình bạn xem và hỏi đáp được.
      </p>

      {!file && (
        <div
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`mt-4 flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-8 text-center ${
            dragging ? 'border-slate-500 bg-slate-50' : 'border-slate-300'
          }`}
        >
          <p className="text-sm text-slate-600">Kéo thả file vào đây hoặc</p>
          <button type="button" onClick={() => inputRef.current?.click()} className={secondaryButton}>
            Chọn file
          </button>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT}
            className="sr-only"
            tabIndex={-1}
            aria-label="Chọn file văn bản"
            onChange={(e) => pickFile(e.target.files?.[0])}
          />
        </div>
      )}

      {fileError && <ErrorText>{fileError}</ErrorText>}
      {uploadedTitle && !file && (
        <p role="status" className="mt-3 text-sm text-green-700">
          Đã tải lên “{uploadedTitle}”. Văn bản đang được xử lý, trạng thái sẽ tự cập nhật.
        </p>
      )}

      {file && (
        <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md bg-slate-50 px-3 py-2 text-sm">
          <span className="min-w-0 truncate font-medium text-slate-800" title={file.name}>
            {file.name}
          </span>
          <span className="text-slate-500">{formatFileSize(file.size)}</span>
          {preview.isPending && (
            <span className="flex items-center gap-2 text-slate-500">
              <Spinner dark /> Đang đọc file…
            </span>
          )}
          <button
            type="button"
            onClick={reset}
            disabled={upload.isPending}
            className="ml-auto text-sm text-slate-600 underline hover:text-slate-800 disabled:opacity-60"
          >
            Chọn file khác
          </button>
        </div>
      )}
      {previewError && <ErrorText>{previewError}</ErrorText>}

      {form && (
        <form onSubmit={onSubmit} className="mt-4 space-y-4">
          <p className="text-sm text-slate-500">
            Thông tin dưới đây được đoán từ nội dung file. Vui lòng kiểm tra lại trước khi tải lên.
          </p>
          {warnings.map((w) => (
            <p key={w} className="rounded-md bg-yellow-50 px-3 py-2 text-sm text-yellow-800">
              {w}
            </p>
          ))}

          <div>
            <label htmlFor="up-title" className={labelClass}>
              Tên văn bản <span className="text-red-600">*</span>
            </label>
            <input
              id="up-title"
              required
              maxLength={TITLE_MAX}
              value={form.title}
              onChange={(e) => setField('title', e.target.value)}
              disabled={upload.isPending}
              className={inputClass}
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <div>
              <label htmlFor="up-so-hieu" className={labelClass}>
                Số hiệu
              </label>
              <input
                id="up-so-hieu"
                maxLength={SHORT_FIELD_MAX}
                placeholder="VD: 45/2019/QH14"
                value={form.so_hieu}
                onChange={(e) => setField('so_hieu', e.target.value)}
                disabled={upload.isPending}
                className={inputClass}
              />
            </div>
            <div>
              <label htmlFor="up-doc-type" className={labelClass}>
                Loại văn bản
              </label>
              <input
                id="up-doc-type"
                list="doc-type-options"
                maxLength={SHORT_FIELD_MAX}
                value={form.doc_type}
                onChange={(e) => setField('doc_type', e.target.value)}
                disabled={upload.isPending}
                className={inputClass}
              />
              <datalist id="doc-type-options">
                {DOC_TYPES.map((t) => (
                  <option key={t} value={t} />
                ))}
              </datalist>
            </div>
            <div>
              <label htmlFor="up-status" className={labelClass}>
                Tình trạng
              </label>
              <select
                id="up-status"
                value={form.status}
                onChange={(e) => setField('status', e.target.value as Status)}
                disabled={upload.isPending}
                className={inputClass}
              >
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {STATUS_LABELS[s]}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="up-issued" className={labelClass}>
                Ngày ban hành
              </label>
              <input
                id="up-issued"
                type="date"
                value={form.issued_date}
                onChange={(e) => setField('issued_date', e.target.value)}
                disabled={upload.isPending}
                className={inputClass}
              />
              {data.fallbackDate && form.issued_date && (
                <p className="mt-1 text-xs text-yellow-700">
                  Ngày này được đoán từ nội dung, có thể chưa chính xác.
                </p>
              )}
            </div>
            <div>
              <label htmlFor="up-effective" className={labelClass}>
                Ngày hiệu lực
              </label>
              <input
                id="up-effective"
                type="date"
                value={form.effective_date}
                onChange={(e) => setField('effective_date', e.target.value)}
                disabled={upload.isPending}
                className={inputClass}
              />
            </div>
          </div>

          {upload.isError && <ErrorText>{`Tải lên thất bại: ${errorMessage(upload.error)}`}</ErrorText>}

          <div className="flex gap-2">
            <button
              type="submit"
              disabled={upload.isPending || !form.title.trim()}
              className={primaryButton}
            >
              {upload.isPending && <Spinner />}
              {upload.isPending ? 'Đang tải lên…' : 'Tải lên'}
            </button>
            <button type="button" onClick={reset} disabled={upload.isPending} className={secondaryButton}>
              Huỷ
            </button>
          </div>
        </form>
      )}
    </section>
  )
}
