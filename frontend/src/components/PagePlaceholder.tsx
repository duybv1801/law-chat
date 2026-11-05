/** Temporary page body until the page's own task (T21–T24) is done. */
export default function PagePlaceholder({ title }: { title: string }) {
  return (
    <div className="mx-auto max-w-7xl px-4 py-8">
      <h1 className="text-xl font-semibold text-slate-800">{title}</h1>
      <p className="mt-2 text-sm text-slate-500">Đang được xây dựng.</p>
    </div>
  )
}
