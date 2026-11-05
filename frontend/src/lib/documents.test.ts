import { describe, expect, it } from 'vitest'
import { listQueryString, MAX_UPLOAD_BYTES, STATUSES } from '../api/documents.ts'
import {
  fileExtension,
  formatDate,
  formatDieu,
  formatFileSize,
  formFromPreview,
  parseListParams,
  pollInterval,
  POLL_INTERVAL_MS,
  STATUS_BADGE_CLASS,
  totalPages,
  uploadFileError,
  uploadFormData,
  warningMessages,
  withListParams,
  type UploadForm,
} from './documents.ts'

describe('formatDate', () => {
  it('formats ISO dates as dd/mm/yyyy without timezone shifts', () => {
    expect(formatDate('2019-11-20')).toBe('20/11/2019')
    expect(formatDate('2026-01-01T00:30:00+07:00')).toBe('01/01/2026')
  })
  it('returns null for missing or invalid values', () => {
    expect(formatDate(null)).toBeNull()
    expect(formatDate(undefined)).toBeNull()
    expect(formatDate('')).toBeNull()
    expect(formatDate('20/11/2019')).toBeNull()
  })
})

describe('status badges', () => {
  it('has a colour for every status, as in docs/07-frontend.md', () => {
    expect(Object.keys(STATUS_BADGE_CLASS).sort()).toEqual([...STATUSES].sort())
    expect(STATUS_BADGE_CLASS.con_hieu_luc).toContain('green')
    expect(STATUS_BADGE_CLASS.het_hieu_luc_mot_phan).toContain('yellow')
    expect(STATUS_BADGE_CLASS.het_hieu_luc_toan_bo).toContain('red')
    expect(STATUS_BADGE_CLASS.chua_co_hieu_luc).toContain('blue')
    expect(STATUS_BADGE_CLASS.khong_xac_dinh).toContain('slate')
  })
})

describe('formatDieu', () => {
  it('joins the article and its title', () => {
    expect(formatDieu({ dieu: 'Điều 5', dieu_title: 'Phạm vi điều chỉnh' })).toBe(
      'Điều 5. Phạm vi điều chỉnh',
    )
    expect(formatDieu({ dieu: 'Điều 6', dieu_title: null })).toBe('Điều 6')
  })
})

describe('pollInterval', () => {
  it('polls only while a document is pending or processing', () => {
    expect(pollInterval(undefined)).toBe(false)
    expect(pollInterval([])).toBe(false)
    expect(pollInterval([{ ingest_status: 'done' }, { ingest_status: 'failed' }])).toBe(false)
    expect(pollInterval([{ ingest_status: 'done' }, { ingest_status: 'pending' }])).toBe(
      POLL_INTERVAL_MS,
    )
    expect(pollInterval([{ ingest_status: 'processing' }])).toBe(POLL_INTERVAL_MS)
    expect(POLL_INTERVAL_MS).toBe(3000)
  })
})

describe('list params', () => {
  it('reads defaults from an empty URL', () => {
    expect(parseListParams(new URLSearchParams())).toEqual({
      scope: 'all',
      q: '',
      doc_type: '',
      status: '',
      page: 1,
      page_size: 20,
    })
  })

  it('reads valid values and ignores invalid ones', () => {
    const ok = parseListParams(
      new URLSearchParams('scope=mine&q=đất đai&doc_type=Luật&status=con_hieu_luc&page=3'),
    )
    expect(ok).toMatchObject({
      scope: 'mine',
      q: 'đất đai',
      doc_type: 'Luật',
      status: 'con_hieu_luc',
      page: 3,
    })
    const bad = parseListParams(new URLSearchParams('scope=x&status=y&page=-2'))
    expect(bad).toMatchObject({ scope: 'all', status: '', page: 1 })
    expect(parseListParams(new URLSearchParams('page=1.5')).page).toBe(1)
    expect(parseListParams(new URLSearchParams('page=abc')).page).toBe(1)
  })

  it('resets the page on a filter change and drops defaults from the URL', () => {
    const start = new URLSearchParams('scope=mine&page=4&doc=abc')
    const next = withListParams(start, { status: 'het_hieu_luc_toan_bo' })
    expect(next.get('page')).toBeNull()
    expect(next.get('status')).toBe('het_hieu_luc_toan_bo')
    expect(next.get('scope')).toBe('mine')
    expect(next.get('doc')).toBe('abc') // unrelated params are kept
    expect(withListParams(next, { scope: 'all' }).get('scope')).toBeNull()
    expect(withListParams(next, { status: '' }).has('status')).toBe(false)
  })

  it('trims the search and keeps the page when nothing really changed', () => {
    const start = new URLSearchParams('q=luật&page=3')
    const same = withListParams(start, { q: 'luật ' })
    expect(same.get('q')).toBe('luật')
    expect(same.get('page')).toBe('3')
    expect(withListParams(new URLSearchParams('page=2'), { q: '   ' }).toString()).toBe('page=2')
    expect(parseListParams(new URLSearchParams('q=%20đất%20')).q).toBe('đất')
  })

  it('keeps the other filters when only the page changes', () => {
    const next = withListParams(new URLSearchParams('q=thuế'), { page: 2 })
    expect(next.get('page')).toBe('2')
    expect(next.get('q')).toBe('thuế')
  })

  it('builds the API query string, leaving out empty filters and trimming the search', () => {
    const qs = listQueryString({
      scope: 'vbpl',
      q: '  luật đất đai ',
      doc_type: '',
      status: '',
      page: 2,
      page_size: 20,
    })
    expect(Object.fromEntries(new URLSearchParams(qs))).toEqual({
      scope: 'vbpl',
      q: 'luật đất đai',
      page: '2',
      page_size: '20',
    })
  })

  it('computes the number of pages', () => {
    expect(totalPages(0, 20)).toBe(1)
    expect(totalPages(20, 20)).toBe(1)
    expect(totalPages(21, 20)).toBe(2)
  })
})

describe('upload file checks', () => {
  it('extracts the extension in lower case', () => {
    expect(fileExtension('Luat.Dat.Dai.PDF')).toBe('.pdf')
    expect(fileExtension('noext')).toBe('')
    expect(fileExtension('.hidden')).toBe('')
  })

  it('accepts PDF / DOCX / TXT up to 20 MB', () => {
    expect(uploadFileError({ name: 'a.pdf', size: 10 })).toBeNull()
    expect(uploadFileError({ name: 'a.DOCX', size: MAX_UPLOAD_BYTES })).toBeNull()
    expect(uploadFileError({ name: 'a.txt', size: 1 })).toBeNull()
  })

  it('rejects other formats, empty and too large files', () => {
    expect(uploadFileError({ name: 'a.doc', size: 10 })).toMatch(/PDF, DOCX hoặc TXT/)
    expect(uploadFileError({ name: 'a.pdf', size: 0 })).toBe('File rỗng.')
    expect(uploadFileError({ name: 'a.pdf', size: MAX_UPLOAD_BYTES + 1 })).toMatch(/20 MB/)
  })

  it('formats file sizes', () => {
    expect(formatFileSize(512)).toBe('512 B')
    expect(formatFileSize(1536)).toBe('1,5 KB')
    expect(formatFileSize(3 * 1024 * 1024)).toBe('3,0 MB')
  })
})

describe('upload form', () => {
  it('pre-fills from the detected metadata', () => {
    const form = formFromPreview(
      {
        so_hieu: '45/2019/QH14',
        doc_type: 'Luật',
        issued_date: '2019-11-20',
        issued_date_source: 'passed',
        title: 'Bộ luật Lao động',
      },
      'bll.pdf',
    )
    expect(form).toEqual({
      title: 'Bộ luật Lao động',
      so_hieu: '45/2019/QH14',
      doc_type: 'Luật',
      issued_date: '2019-11-20',
      effective_date: '',
      status: 'khong_xac_dinh',
    })
  })

  it('falls back to the file name for the title and blanks missing values', () => {
    const form = formFromPreview({ title: null, issued_date: 'not-a-date' }, 'Quy chế nội bộ.docx')
    expect(form).toMatchObject({ title: 'Quy chế nội bộ', so_hieu: '', doc_type: '', issued_date: '' })
    expect(formFromPreview({}, 'x').title).toBe('x')
  })

  it('builds the multipart body, leaving out blank optional fields', () => {
    const file = new File(['abc'], 'a.txt', { type: 'text/plain' })
    const form: UploadForm = {
      title: '  Quy chế  ',
      so_hieu: ' ',
      doc_type: 'Quyết định',
      issued_date: '2024-01-02',
      effective_date: '',
      status: 'con_hieu_luc',
    }
    const body = uploadFormData(file, form)
    expect(body.get('file')).toBeInstanceOf(File)
    expect(body.get('title')).toBe('Quy chế')
    expect(body.has('so_hieu')).toBe(false)
    expect(body.get('doc_type')).toBe('Quyết định')
    expect(body.get('issued_date')).toBe('2024-01-02')
    expect(body.has('effective_date')).toBe(false)
    expect(body.get('status')).toBe('con_hieu_luc')
  })

  it('describes parser warnings in Vietnamese', () => {
    expect(warningMessages([{ code: 'partially_scanned', scanned_pages: [11, 12] }])).toEqual([
      'Trang 11, 12 là ảnh scan, nội dung các trang này sẽ không được đọc.',
    ])
    expect(warningMessages([{ code: 'partially_scanned' }])[0]).toMatch(/Một số trang/)
    expect(warningMessages([{ code: 'unknown' }])).toEqual([])
  })
})
