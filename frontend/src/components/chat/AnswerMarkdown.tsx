import { memo, useMemo } from 'react'
import Markdown, { type Components, type Options } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { citeNumber, remarkCiteMarkers } from '../../lib/chat.ts'

const proseClass = [
  'text-sm leading-relaxed text-slate-800 break-words',
  '[&>*:first-child]:mt-0 [&>*:last-child]:mb-0',
  '[&_p]:my-2 [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-1',
  '[&_h1]:mb-2 [&_h1]:mt-3 [&_h1]:text-base [&_h1]:font-semibold',
  '[&_h2]:mb-2 [&_h2]:mt-3 [&_h2]:text-base [&_h2]:font-semibold',
  '[&_h3]:mb-1 [&_h3]:mt-3 [&_h3]:font-semibold [&_strong]:font-semibold',
  '[&_blockquote]:my-2 [&_blockquote]:border-l-4 [&_blockquote]:border-slate-200 [&_blockquote]:pl-3 [&_blockquote]:text-slate-600',
  '[&_code]:rounded [&_code]:bg-slate-100 [&_code]:px-1 [&_code]:text-[0.8125rem]',
  '[&_pre]:my-2 [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-slate-100 [&_pre]:p-3 [&_pre_code]:bg-transparent [&_pre_code]:p-0',
  '[&_table]:my-2 [&_table]:block [&_table]:overflow-x-auto [&_table]:border-collapse [&_td]:border [&_td]:border-slate-200 [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:border-slate-200 [&_th]:bg-slate-50 [&_th]:px-2 [&_th]:py-1 [&_th]:text-left',
].join(' ')

const chipClass =
  'mx-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded bg-sky-100 px-1 align-text-top text-[11px] font-semibold leading-none text-sky-800'

interface Props {
  text: string
  /** Cited source numbers; `null` while streaming (chips shown, not clickable yet). */
  valid: ReadonlySet<number> | null
  onCite?: (n: number) => void
}

/**
 * Answer text as markdown, with `[n]` markers as chips. Links written by the model are shown as
 * plain text: every link the user can follow comes from DB metadata (CLAUDE.md rule 2).
 * Raw HTML is not rendered (react-markdown default) and images are dropped.
 */
function AnswerMarkdown({ text, valid, onCite }: Props) {
  const remarkPlugins = useMemo<Options['remarkPlugins']>(() => [remarkGfm, [remarkCiteMarkers, valid]], [valid])
  const components = useMemo<Components>(
    () => ({
      a: ({ href, children }) => {
        const n = citeNumber(href)
        if (n === null) return <span>{children}</span>
        if (!onCite || valid === null) return <span className={chipClass}>{n}</span>
        return (
          <button
            type="button"
            onClick={() => onCite(n)}
            className={`${chipClass} cursor-pointer hover:bg-sky-200 focus:outline-none focus:ring-2 focus:ring-sky-400`}
            aria-label={`Xem nguồn ${n}`}
          >
            {n}
          </button>
        )
      },
    }),
    [onCite, valid],
  )
  return (
    <div className={proseClass}>
      <Markdown remarkPlugins={remarkPlugins} components={components} disallowedElements={['img']}>
        {text}
      </Markdown>
    </div>
  )
}

export default memo(AnswerMarkdown)
