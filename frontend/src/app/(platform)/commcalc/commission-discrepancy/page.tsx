'use client'
// COMMISSION DISCREPANCY HUB (owner directive 2026-09-03, mig 947): one place for "any reports or
// query for the commission not received and the appeals which need to be done."
//
// WHAT IT SHOWS (reuse, never re-derive — duplicate-check gate):
//   · The EXISTING discrepancy_results rows (both engines: Boost discrepancy_engine + B2B↔MA
//     ma_recon source='ma') over a PERIOD RANGE — via GET /commcalc/discrepancy-appeals. This page
//     runs no recon of its own; "Run Detection" stays on the Pay Discrepancy page.
//   · A per-row APPEAL workflow — appeal filed / appeal won / appeal denied / written off, with
//     note + who/when — PATCH /commcalc/discrepancy-appeals/{id}. Transition legality is decided
//     server-side by the pure state machine (discrepancy_appeals.py, harness-proven); the
//     ALLOWED_NEXT map below is its display twin (cell-safety.ts ⇄ notify/render.py convention)
//     and only chooses which buttons to OFFER — the server remains authoritative.
//   · The open-claims CHASE LIST from the mig-098 denied-appeal recovery pipeline
//     (GET /recovery/claims — reused as-is, linked to /commcalc/recovery).
//
// FILTERS: the standard bar (RULE FIVE) — activation-date range + store(s) + rep(s); market is
// omitted because discrepancy rows carry no market field (documented deviation, same as
// commission-legs omits reps). Module-specific extras ride `right`: source / row status / appeal
// state / search. Client-side filtering over the loaded range = what-you-see-is-what-exports.
//
// Money posture: READ-ONLY on money. Appeal actions annotate workflow state only — they never
// touch expected/received/gap/status, and nothing here recomputes a payout.
import { useEffect, useMemo, useState } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, matchesStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
import { ExportButtons, ExportPayload } from '@/lib/export'

type Row = {
  id: number; period: string; imei: string; mdn: string; store: string; rep_username: string
  activation_date: string; activation_type: string; device_model: string; customer_plan: string
  comp_type: string; expected_amount: number; received_amount: number; gap: number; status: string
  notes: string; source: string | null; order_number: string | null
  rule_key: string | null; rule_reason: string | null
  appeal_status: string | null; appeal_note: string | null; appealed_by: string | null; appealed_at: string | null
}
type Summary = {
  by_appeal: Record<string, { count: number; gap: number }>
  open_count: number; open_gap: number; no_rule_count: number; total_rows: number
}
type Resp = { rows: Row[]; summary: Summary; appeals_ready: boolean }
type Claim = { id: string; period_label: string; device_count: number; total_amount: number; status: string; generated_at: string }

// Display twin of discrepancy_appeals.ALLOWED_TRANSITIONS (server-authoritative; buttons only).
const ALLOWED_NEXT: Record<string, string[]> = {
  '': ['appeal_filed', 'written_off'],
  appeal_filed: ['appeal_won', 'appeal_denied', 'written_off', ''],
  appeal_denied: ['appeal_filed', 'written_off', ''],
  appeal_won: [''],
  written_off: ['appeal_filed', ''],
}
const APPEAL_LABEL: Record<string, string> = {
  '': 'Clear', appeal_filed: 'Appeal filed', appeal_won: 'Appeal won',
  appeal_denied: 'Appeal denied', written_off: 'Written off',
}
const APPEAL_COLOR: Record<string, string> = {
  none: '#6b7280', appeal_filed: '#2563eb', appeal_won: '#16a34a',
  appeal_denied: '#dc2626', written_off: '#92400e',
}

function toMonth(label: string): string {
  const months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
  const [mon, yr] = label.split(' ')
  const m = months.indexOf(mon) + 1
  return m ? `${yr}-${String(m).padStart(2, '0')}` : label
}
function monthsBack(ym: string, n: number): string {
  const [y, m] = ym.split('-').map(Number)
  const t = y * 12 + (m - 1) - n
  return `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, '0')}`
}

export default function CommissionDiscrepancyHub() {
  const { period } = usePeriod()
  const cur = toMonth(period)

  const [from, setFrom] = useState(cur)
  const [to, setTo] = useState(cur)
  const [data, setData] = useState<Resp | null>(null)
  const [claims, setClaims] = useState<Claim[]>([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState<number | null>(null)

  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [srcFilter, setSrcFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('open')
  const [appealFilter, setAppealFilter] = useState('')
  const [search, setSearch] = useState('')

  // Follow the app-wide header period when the user hasn't loaded a custom range yet.
  useEffect(() => { setFrom(cur); setTo(cur) }, [cur])

  const load = async (f = from, t = to) => {
    setLoading(true); setErr('')
    try {
      const r = await api(`/api/v1/commcalc/discrepancy-appeals?org_id=${ORG_ID}` +
        `&period_from=${encodeURIComponent(f)}&period_to=${encodeURIComponent(t)}`)
      setData(r)
    } catch (e: any) { setErr(e.message || 'Failed to load') }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [from, to]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    api(`/api/v1/recovery/claims?org_id=${ORG_ID}`)
      .then((r: any) => setClaims((r?.claims || r || []).filter?.((c: Claim) => c.status === 'draft' || c.status === 'submitted') || []))
      .catch(() => setClaims([]))
  }, [])

  const rows = data?.rows || []
  const filtered = useMemo(() => rows.filter(r => {
    if (!matchesStandardFilter(r, filt, {
      store: x => x.store, rep: x => x.rep_username, date: x => x.activation_date,
    })) return false
    if (srcFilter === 'boost' && r.source && r.source !== 'boost') return false
    if (srcFilter === 'ma' && r.source !== 'ma') return false
    if (statusFilter && r.status !== statusFilter) return false
    if (appealFilter === 'none' && r.appeal_status) return false
    if (appealFilter && appealFilter !== 'none' && r.appeal_status !== appealFilter) return false
    if (search) {
      const s = search.toLowerCase()
      if (![r.imei, r.mdn, r.device_model, r.rep_username, r.order_number, r.notes, r.rule_reason]
        .some(v => (v || '').toLowerCase().includes(s))) return false
    }
    return true
  }), [rows, filt, srcFilter, statusFilter, appealFilter, search])

  const setAppeal = async (row: Row, next: string) => {
    let note: string | null = ''
    if (next) {
      note = window.prompt(`${APPEAL_LABEL[next]} — add a note (optional):`, row.appeal_note || '')
      if (note === null) return
    } else if (!window.confirm('Clear the appeal state on this row?')) return
    setSaving(row.id); setMsg('')
    try {
      const r = await api(`/api/v1/commcalc/discrepancy-appeals/${row.id}?org_id=${ORG_ID}`, {
        method: 'PATCH', body: JSON.stringify({ appeal_status: next, appeal_note: note }),
      })
      setData(d => d ? {
        ...d,
        rows: d.rows.map(x => x.id === row.id ? {
          ...x, appeal_status: r.appeal_status, appeal_note: r.appeal_note,
          appealed_by: r.appealed_by, appealed_at: r.appealed_at,
        } : x),
      } : d)
      setMsg(next ? `Row marked "${APPEAL_LABEL[next]}".` : 'Appeal state cleared.')
    } catch (e: any) { setErr(e.message || 'Could not save appeal state') }
    finally { setSaving(null) }
  }

  const sum = data?.summary
  const buckets = sum?.by_appeal || {}
  const card = (label: string, value: string, sub: string, color: string) => (
    <div className="card" style={{ padding: '12px 16px', minWidth: 150, flex: '1 1 150px' }}>
      <div style={{ fontSize: 11.5, color: 'var(--text2)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.04em' }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, color, margin: '2px 0' }}>{value}</div>
      <div style={{ fontSize: 11.5, color: 'var(--text3)' }}>{sub}</div>
    </div>
  )

  function buildPayload(): ExportPayload {
    return {
      title: 'Commission Discrepancy', subtitle: `${from} → ${to} · commission not received + appeals`,
      filename: `commission-discrepancy-${from}-${to}`,
      sheets: [{
        name: 'Discrepancy', rows: filtered, columns: [
          { header: 'Period', get: (r: Row) => r.period },
          { header: 'Source', get: (r: Row) => r.source || 'boost' },
          { header: 'Store', get: (r: Row) => r.store },
          { header: 'Rep', get: (r: Row) => r.rep_username },
          { header: 'Activated', get: (r: Row) => r.activation_date },
          { header: 'Type', get: (r: Row) => r.comp_type },
          { header: 'IMEI', get: (r: Row) => r.imei },
          { header: 'MDN', get: (r: Row) => r.mdn },
          { header: 'Device', get: (r: Row) => r.device_model },
          { header: 'Expected', get: (r: Row) => r.expected_amount, money: true },
          { header: 'Received', get: (r: Row) => r.received_amount, money: true },
          { header: 'Gap', get: (r: Row) => r.gap, money: true },
          { header: 'Status', get: (r: Row) => r.status },
          { header: 'Why (rule / notes)', get: (r: Row) => r.rule_reason || r.notes || '' },
          { header: 'Appeal', get: (r: Row) => APPEAL_LABEL[r.appeal_status || ''] === 'Clear' ? '' : APPEAL_LABEL[r.appeal_status || ''] },
          { header: 'Appeal note', get: (r: Row) => r.appeal_note || '' },
          { header: 'Appealed at', get: (r: Row) => r.appealed_at || '' },
        ],
      }],
    }
  }

  const selStyle: React.CSSProperties = { padding: '6px 9px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

  return (
    <div style={{ padding: 24, maxWidth: 1500, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 4 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>⚖️ Commission Discrepancy</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 13.5, margin: '4px 0 0', maxWidth: 860, lineHeight: 1.55 }}>
            Commission <b>not received</b> from the carrier, and the <b>appeals</b> that need to be done.
            Rows come from the existing discrepancy engines (Boost + B2B↔MA) — run detection on{' '}
            <a href="/commcalc/discrepancy" style={{ color: 'var(--accent)' }}>Pay Discrepancy</a>; mark each
            row&apos;s appeal here (who/when is recorded).
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>From{' '}
            <input type="month" value={from} onChange={e => setFrom(e.target.value)} style={selStyle} /></label>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>To{' '}
            <input type="month" value={to} onChange={e => setTo(e.target.value)} style={selStyle} /></label>
          <button className="btn" onClick={() => { setFrom(monthsBack(cur, 2)); setTo(cur) }} style={{ fontSize: 12 }}>Last 3 months</button>
          {filtered.length > 0 && <ExportButtons payload={buildPayload} />}
        </div>
      </div>

      {err && <div style={{ background: '#fef2f2', color: '#991b1b', padding: 12, borderRadius: 8, margin: '12px 0', fontSize: 13.5 }}>{err}</div>}
      {msg && <div style={{ background: '#f0fdf4', color: '#166534', padding: 10, borderRadius: 8, margin: '12px 0', fontSize: 13 }}>{msg}</div>}
      {data && !data.appeals_ready && (
        <div style={{ background: '#fffbeb', color: '#92400e', padding: 12, borderRadius: 8, margin: '12px 0', fontSize: 13 }}>
          Appeal tracking is not enabled on this database yet — run migration
          {' '}<code>947_commission_discrepancy_hub.sql</code>. The not-received report still works below.
        </div>
      )}

      {/* Headline cards — whole loaded range (pure server summary), before client filters. */}
      <div style={{ display: 'flex', gap: 12, margin: '14px 0', flexWrap: 'wrap' }}>
        {card('Not received (open)', fmt(sum?.open_gap || 0), `${sum?.open_count || 0} rows`, '#dc2626')}
        {card('Appeal filed', fmt(buckets.appeal_filed?.gap || 0), `${buckets.appeal_filed?.count || 0} rows`, APPEAL_COLOR.appeal_filed)}
        {card('Appeal won', fmt(buckets.appeal_won?.gap || 0), `${buckets.appeal_won?.count || 0} rows`, APPEAL_COLOR.appeal_won)}
        {card('Written off', fmt(buckets.written_off?.gap || 0), `${buckets.written_off?.count || 0} rows`, APPEAL_COLOR.written_off)}
        {card('No business rule', String(sum?.no_rule_count || 0), 'rows with no configured explanation', '#7c3aed')}
      </div>

      {/* Open recovery claims — the chase list (mig-098 pipeline, reused; managed on Appeal Recovery). */}
      {claims.length > 0 && (
        <div className="card" style={{ padding: '10px 16px', margin: '0 0 14px', fontSize: 13 }}>
          <b>📮 Open recovery claims to chase:</b>{' '}
          {claims.slice(0, 4).map(c => (
            <span key={c.id} style={{ marginRight: 14 }}>
              {c.period_label} — {c.device_count} devices, {fmt(c.total_amount)} <i style={{ color: 'var(--text2)' }}>({c.status})</i>
            </span>
          ))}
          <a href="/commcalc/recovery" style={{ color: 'var(--accent)', fontWeight: 600 }}>Appeal Recovery →</a>
        </div>
      )}

      <StandardFilterBar
        value={filt}
        onChange={setFilt}
        periodMode="range"
        show={{ period: true, stores: true, markets: false, reps: true }}
        optionsUrl={`/api/v1/core/filter-options?org_id=${ORG_ID}`}
        right={
          <>
            <select value={srcFilter} onChange={e => setSrcFilter(e.target.value)} style={selStyle} aria-label="Engine source">
              <option value="">All sources</option>
              <option value="boost">Carrier engine</option>
              <option value="ma">B2B ↔ MA recon</option>
            </select>
            <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} style={selStyle} aria-label="Row status">
              <option value="">All statuses</option>
              {['open', 'pending', 'lagged', 'info', 'resolved', 'disputed'].map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <select value={appealFilter} onChange={e => setAppealFilter(e.target.value)} style={selStyle} aria-label="Appeal state">
              <option value="">Any appeal state</option>
              <option value="none">No appeal yet</option>
              <option value="appeal_filed">Appeal filed</option>
              <option value="appeal_won">Appeal won</option>
              <option value="appeal_denied">Appeal denied</option>
              <option value="written_off">Written off</option>
            </select>
            <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search IMEI / MDN / device / reason…"
              style={{ ...selStyle, minWidth: 220 }} aria-label="Search rows" />
          </>
        }
      />

      <div className="card" style={{ marginTop: 14, overflowX: 'auto' }}>
        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 50 }}><div className="spinner" /></div>
        ) : filtered.length === 0 ? (
          <div style={{ padding: 24, fontSize: 13.5, color: 'var(--text2)' }}>
            No rows match. Widen the month range, clear filters, or run detection on the{' '}
            <a href="/commcalc/discrepancy" style={{ color: 'var(--accent)' }}>Pay Discrepancy</a> page.
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
            <thead>
              <tr style={{ textAlign: 'left', color: 'var(--text2)' }}>
                {['Period', 'Store', 'Rep', 'Activated', 'Type', 'Device / IMEI', 'Expected', 'Received', 'Gap', 'Why (rule / notes)', 'Appeal', 'Actions']
                  .map(h => <th key={h} style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }}>{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, 500).map(r => {
                const ap = r.appeal_status || ''
                return (
                  <tr key={r.id} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding: '7px 10px', whiteSpace: 'nowrap' }}>{r.period}</td>
                    <td style={{ padding: '7px 10px' }}>{r.store}</td>
                    <td style={{ padding: '7px 10px' }}>{r.rep_username}</td>
                    <td style={{ padding: '7px 10px', whiteSpace: 'nowrap' }}>{r.activation_date}</td>
                    <td style={{ padding: '7px 10px' }}>{r.comp_type}</td>
                    <td style={{ padding: '7px 10px' }}>{r.device_model}<div style={{ color: 'var(--text3)', fontSize: 11 }}>{r.imei || r.mdn}</div></td>
                    <td style={{ padding: '7px 10px', textAlign: 'right' }}>{fmt(r.expected_amount)}</td>
                    <td style={{ padding: '7px 10px', textAlign: 'right' }}>{fmt(r.received_amount)}</td>
                    <td style={{ padding: '7px 10px', textAlign: 'right', fontWeight: 700, color: r.gap > 0 ? '#dc2626' : 'inherit' }}>{fmt(r.gap)}</td>
                    <td style={{ padding: '7px 10px', maxWidth: 240 }}>
                      <span title={r.rule_reason || r.notes || ''}>{(r.rule_reason || r.notes || '—').slice(0, 80)}</span>
                    </td>
                    <td style={{ padding: '7px 10px', whiteSpace: 'nowrap' }}>
                      <span style={{ color: APPEAL_COLOR[ap || 'none'], fontWeight: 600 }}>
                        {ap ? APPEAL_LABEL[ap] : '—'}
                      </span>
                      {r.appealed_at && <div style={{ fontSize: 10.5, color: 'var(--text3)' }} title={`by ${r.appealed_by || '?'}${r.appeal_note ? ` — ${r.appeal_note}` : ''}`}>
                        {String(r.appealed_at).slice(0, 10)}{r.appeal_note ? ' · 📝' : ''}</div>}
                    </td>
                    <td style={{ padding: '7px 10px', whiteSpace: 'nowrap' }}>
                      {data?.appeals_ready && (ALLOWED_NEXT[ap] || []).map(n => (
                        <button key={n || 'clear'} disabled={saving === r.id} onClick={() => setAppeal(r, n)}
                          style={{ fontSize: 11, marginRight: 4, padding: '3px 7px', borderRadius: 6, cursor: 'pointer',
                            border: '1px solid var(--border)', background: 'var(--surface)',
                            color: n ? APPEAL_COLOR[n] : 'var(--text2)' }}>
                          {APPEAL_LABEL[n]}
                        </button>
                      ))}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
        {filtered.length > 500 && (
          <div style={{ padding: 10, fontSize: 12, color: 'var(--text2)' }}>Showing 500 of {filtered.length} rows — narrow the filters or export for the full set.</div>
        )}
      </div>

      <div style={{ marginTop: 16, fontSize: 12.5, color: 'var(--text2)', lineHeight: 1.7 }}>
        Related: <a href="/commcalc/discrepancy" style={{ color: 'var(--accent)' }}>Pay Discrepancy (run detection, phantom payments)</a> ·{' '}
        <a href="/commcalc/recovery" style={{ color: 'var(--accent)' }}>Appeal Recovery (denied-appeal claw-back claims)</a> ·{' '}
        <a href="/commcalc/expected-commission" style={{ color: 'var(--accent)' }}>Expected vs Earned</a> ·{' '}
        <a href="/commcalc/ma-overview-recon" style={{ color: 'var(--accent)' }}>MA Overview cross-check</a> ·{' '}
        <a href="/commcalc/commission-plans" style={{ color: 'var(--accent)' }}>Commission plan coverage exceptions</a>
      </div>
    </div>
  )
}
