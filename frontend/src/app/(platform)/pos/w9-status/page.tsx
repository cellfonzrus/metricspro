'use client'
// W-9 STATUS — which wholesale customers can be sold to tax-free, and which certificates lapse when
// (index §POS-19).
//
// Owner 2026-10-31: "build them" — the second of the two reports the wholesale work implies.
//
// NO SECOND VERDICT. Whether a certificate is current is `wholesale_w9.may_sell`'s answer — the
// SAME call the register is blocked by — and the day it lapses is `wholesale_w9.expiry_date`'s. So
// this screen cannot tell a manager a W-9 is good on a day the till refuses the sale, which is the
// one way a report like this earns distrust.
//
// NOTHING DECLARED MEANS NOTHING IDENTIFIED. An org with no wholesale customers is told so in
// words. An empty table reads as "all clear", and that is exactly the shape of the defect the
// owner's 2026-10-30 directive was written about.
//
// RULE FOUR (exports) / RULE FIVE (filters) — ReportShell, so the standard filter bar and the 📤
// Send bar come for free over the rows actually on screen.
import { useCallback, useEffect, useMemo, useState } from 'react'
import ReportShell from '@/components/ReportShell'
import { api } from '@/lib/client'
import { PlatformDetail } from '@/lib/setupNotice'

type Row = {
  customer_id: string; customer_name: string | null; email: string | null; phone: string | null
  w9_status: string | null; blocked: boolean; tax_exempt: boolean
  received_at: string | null; confirmed_by: string | null; doc_status: string | null
  requested_at: string | null; requested_to_email: string | null
  expires_at: string | null; expiry_basis: string | null; expiry_note: string | null
  days_until_expiry: number | null; expiring_soon: boolean; needs_w9_request: boolean
}
type Summary = {
  wholesale: number; current: number; expired: number; missing: number
  expiring_soon: number; blocked: number; tax_exempt: number; never_requested: number
  soonest_days: number | null
}
type Payload = {
  rows: Row[]; summary: Summary; available: boolean; setup_note: string | null
  as_of: string; view: string; within_days: number; form_url: string
}

const EMPTY: Payload = {
  rows: [], available: true, setup_note: null, as_of: '', view: 'attention', within_days: 60,
  form_url: '',
  summary: { wholesale: 0, current: 0, expired: 0, missing: 0, expiring_soon: 0, blocked: 0,
             tax_exempt: 0, never_requested: 0, soonest_days: null },
}

const VIEWS: { key: string; label: string; note: string }[] = [
  { key: 'attention', label: 'Needs attention', note: 'Anything missing, out of date, or lapsing soon.' },
  { key: 'expiring', label: 'Lapsing soon', note: 'Certificates still good, but not for long.' },
  { key: 'missing', label: 'No W-9 on file', note: 'Wholesale customers we have never had a signed form from.' },
  { key: 'expired', label: 'Out of date', note: 'A form arrived once and has since lapsed.' },
  { key: 'all', label: 'Every wholesale customer', note: 'Including the ones in good order.' },
]

const WINDOWS = [30, 60, 90]

const STATUS_WORDS: Record<string, string> = {
  current: 'Current', expired: 'Out of date', missing: 'None on file',
}
const BASIS_WORDS: Record<string, string> = {
  own: 'the form’s own expiry', declared: 'this company’s refresh period',
}

export default function W9StatusPage() {
  const [view, setView] = useState('attention')
  const [within, setWithin] = useState(60)
  const [data, setData] = useState<Payload>(EMPTY)
  const [err, setErr] = useState('')
  const [nonce, setNonce] = useState(0)
  const [loadedKey, setLoadedKey] = useState<string | null>(null)

  const key = useMemo(() => JSON.stringify([view, within, nonce]), [view, within, nonce])
  const loading = loadedKey !== key
  const reload = useCallback(() => setNonce(n => n + 1), [])

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const q = new URLSearchParams({ view, within_days: String(within) })
        const r = await api(`/api/v1/pos/reports/w9-status?${q}`)
        if (!alive) return
        setErr('')
        setData({ ...EMPTY, ...r, summary: { ...EMPTY.summary, ...(r?.summary || {}) } })
      } catch (e: unknown) {
        if (!alive) return
        setErr(String((e as { message?: string })?.message || '') || 'Could not load the W-9 status')
        setData(EMPTY)
      }
      if (alive) setLoadedKey(key)
    })()
    return () => { alive = false }
  }, [key, view, within])

  const s = data.summary
  const activeView = VIEWS.find(v => v.key === view) || VIEWS[0]

  const sentence = useMemo(() => {
    const parts: string[] = []
    if (!data.available) {
      parts.push('Wholesale certificates are not switched on for this company yet, so no W-9 can '
        + 'be recorded. This is not a clean bill of health.')
    } else if (s.wholesale === 0) {
      parts.push('This company has no wholesale customers on file, so there is no W-9 to track. A '
        + 'customer becomes wholesale on their own record, and until one does nothing is measured '
        + 'here.')
    }
    if (s.blocked > 0) {
      parts.push(`${s.blocked} of these cannot be sold to right now — an admin can allow a TAXABLE `
        + 'sale past a missing certificate, which is never a tax exemption.')
    }
    if (s.never_requested > 0) {
      parts.push(`${s.never_requested} have never been sent the form. The register’s customer `
        + 'panel emails it with a return link in one press.')
    }
    return parts.length ? parts.join(' ') : null
  }, [data, s])

  const notice = sentence == null ? null : (
    <>
      {sentence}
      {!data.available && (
        <PlatformDetail detail="1107_pos_wholesale_w9" style={{ marginLeft: 6 }} />
      )}
    </>
  )

  return (
    <div style={{ padding: 20, maxWidth: 1400, margin: '0 auto' }}>
      <h1 style={{ fontSize: 20, fontWeight: 800, marginBottom: 4 }}>W-9 status — wholesale customers</h1>
      <p style={{ fontSize: 13, color: 'var(--text3)', marginBottom: 14, maxWidth: 820 }}>
        A wholesale sale is tax-free only while a signed W-9 is on file. This is every wholesale
        customer, whether we hold one, and when the one we hold stops counting.
      </p>

      <div className="card" style={{ padding: 12, marginBottom: 14, display: 'flex', gap: 10,
        alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <label style={{ fontSize: 11.5, color: 'var(--text3)' }}>
          Show
          <select value={view} onChange={e => setView(e.target.value)}
            style={{ display: 'block', padding: '7px 10px', borderRadius: 8,
              border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }}>
            {VIEWS.map(v => <option key={v.key} value={v.key}>{v.label}</option>)}
          </select>
        </label>
        <label style={{ fontSize: 11.5, color: 'var(--text3)' }}>
          Counts as lapsing within
          <select value={within} onChange={e => setWithin(Number(e.target.value))}
            style={{ display: 'block', padding: '7px 10px', borderRadius: 8,
              border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }}>
            {WINDOWS.map(w => <option key={w} value={w}>{w} days</option>)}
          </select>
        </label>
        <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={reload}
          disabled={loading}>{loading ? 'Loading…' : 'Refresh'}</button>
        {data.form_url && (
          <a className="btn btn-secondary" style={{ fontSize: 12 }} href={data.form_url}
            target="_blank" rel="noopener noreferrer">Blank W-9 ↗</a>
        )}
      </div>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
        {[
          { label: 'Wholesale customers', value: String(s.wholesale), tone: 'var(--text)' },
          { label: 'Current', value: String(s.current), tone: 'var(--green)' },
          { label: 'Lapsing soon', value: String(s.expiring_soon), tone: 'var(--amber)' },
          { label: 'Out of date', value: String(s.expired), tone: 'var(--red)' },
          { label: 'None on file', value: String(s.missing), tone: 'var(--red)' },
          { label: 'Cannot sell to', value: String(s.blocked), tone: 'var(--red)' },
        ].map(c => (
          <div key={c.label} className="card" style={{ padding: '10px 14px', minWidth: 120 }}>
            <div style={{ fontSize: 10.5, color: 'var(--text3)', textTransform: 'uppercase',
              letterSpacing: '0.06em' }}>{c.label}</div>
            <div style={{ fontSize: 18, fontWeight: 800, color: c.tone }}>{c.value}</div>
          </div>
        ))}
      </div>

      {err && (
        <div className="card" style={{ padding: 12, marginBottom: 14, fontSize: 13,
          color: 'var(--red)' }}>{err}</div>
      )}

      <ReportShell
        title="W-9 status — wholesale customers"
        subtitle={`${activeView.label} · ${data.rows.length} customer${data.rows.length === 1 ? '' : 's'}`
          + `${data.as_of ? ` · as of ${data.as_of}` : ''}`}
        notice={notice}
        noticeTone={s.blocked > 0 || s.expired > 0 ? 'warn' : 'info'}
        filename="pos-w9-status"
        columns={[
          { header: 'Customer', field: 'customer_name', get: (r: Row) => r.customer_name || '—' },
          { header: 'Email', field: 'email', get: (r: Row) => r.email || '—' },
          { header: 'Phone', field: 'phone', get: (r: Row) => r.phone || '—' },
          { header: 'W-9', field: 'w9_status',
            get: (r: Row) => STATUS_WORDS[r.w9_status || ''] || r.w9_status || '—' },
          { header: 'Sells tax-free', field: 'tax_exempt',
            get: (r: Row) => (r.tax_exempt ? 'Yes' : 'No') },
          { header: 'Can be sold to', field: 'blocked',
            get: (r: Row) => (r.blocked ? 'Blocked' : 'Yes') },
          { header: 'Received', field: 'received_at', type: 'date',
            get: (r: Row) => r.received_at || '—' },
          { header: 'Confirmed by', field: 'confirmed_by', get: (r: Row) => r.confirmed_by || '—' },
          { header: 'Lapses', field: 'expires_at', type: 'date',
            get: (r: Row) => r.expires_at || '—' },
          // NO LAPSE DATE SHOWS NO NUMBER — null, not 0, which would sort beside "lapses today".
          { header: 'Days left', field: 'days_until_expiry', type: 'number',
            get: (r: Row) => r.days_until_expiry },
          { header: 'Judged by', field: 'expiry_basis',
            get: (r: Row) => BASIS_WORDS[r.expiry_basis || ''] || r.expiry_note || '—' },
          { header: 'Form sent', field: 'requested_at', type: 'date',
            get: (r: Row) => (r.requested_at || '').slice(0, 10) || '—' },
          { header: 'Sent to', field: 'requested_to_email',
            get: (r: Row) => r.requested_to_email || '—' },
        ]}
        rows={data.rows} compact stickyHeader
      />

      <p style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 10, maxWidth: 820 }}>
        {activeView.note} A customer&rsquo;s own upload is not a certificate until somebody here
        confirms it arrived, which is why a form that has been sent back still shows as none on file
        until it is checked.
      </p>
    </div>
  )
}
