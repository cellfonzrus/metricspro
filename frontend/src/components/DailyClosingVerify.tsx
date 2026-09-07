'use client'
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { api, apiUpload, fmt, localToday } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { useAuth } from '@/lib/auth-context'
import StandardFilterBar from '@/components/StandardFilterBar'
import type { EntityOption } from '@/components/EntityPicker'
import type { StandardFilterValue } from '@/lib/standard-filters'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import EnvelopeViewLink from '@/components/EnvelopeViewLink'
import { useReportLabels } from '@/lib/report-labels'

// DM evening verification view — per-store totals, missing-rep check, B2B reconciliation, and
// the DM's confirm/adjust+sign-off. Shared by /closing/verify (Daily Closing module) and the
// legacy /storeops/closing route. Source of truth is GET /closing/summary.
//
// retail-ops-14 (OWNER DIRECTIVE 2026-07-28): DATA PARITY + RULE FIVE filters + RULE FOUR exports.
// Root causes fixed (see docs/handoffs/retail-ops.md for the full writeup):
//   (a) /closing/summary's market filter used to drop any store whose resolved market didn't
//       EXACTLY match — including every unresolved/blank-market store. A market-scoped DM's own
//       market auto-applies below (line ~140), so this silently emptied the page for exactly the
//       callers who use it most. Fixed server-side (bucket-aware "(no market)" matching) — see
//       `_market_bucket` in closing/router.py.
//   (b) The page defaulted to today, showing "No closing-sheet rows" with no hint when the latest
//       submissions were for a prior day. Now auto-resolves to the most recent date WITH rows (via
//       the existing GET /closing/dates) on first load, with a banner explaining the jump.
//   (c) Field-level parity: totals/rep-rows now carry ACIMA + the individual tender buckets +
//       custom tenders (mig 111) + a re-derived close-gate status — all previously visible on the
//       dashboard's "All submissions" tab but entirely absent here. Reuses the backend's existing
//       gate helpers verbatim (never re-implements money math); the same money-secrecy boundary
//       (_can_mgmt_review) that already governs Management Review governs these new fields too.
const GOOGLE_CLOSING_SHEET_URL = 'https://docs.google.com/spreadsheets/d/1e41A9Ug5jaM_ZQGkQbsGncX7WpIwqe7Tf6BpKUoojqI/edit'

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const tin: React.CSSProperties = { ...sel, width: 110 }
const cell: React.CSSProperties = { padding: '6px 9px', borderBottom: '1px solid var(--border)', fontSize: 13 }
const NO_MARKET = '(no market)'

const GATE_LABEL: Record<string, string> = {
  ok: '✅ OK', flagged: '⚠️ Flagged', blocked: '⛔ Blocked',
  recon_pending: '⏳ Pending',
}
const GATE_COLOR: Record<string, React.CSSProperties> = {
  ok: { background: '#e6f7ec', color: '#16794a' },
  flagged: { background: '#fef3e2', color: '#b45309' },
  blocked: { background: '#fde8e8', color: '#b42318' },
  recon_pending: { background: 'var(--surface2)', color: 'var(--text3)' },
}
// `resolved` (OWNER BUG REPORT 2026-07-29, "management removed block still showing on dm verify" +
// senior-review RC-2): gate_status itself is NEVER changed here — it's still the live, unmodified
// re-derivation of declared-vs-B2B (_money_issues, untouched) — but a Blocked/Flagged row that's
// already been DM-verified, auto-accepted (3rd try), or released for correction no longer reads as an
// UNADDRESSED alarm: same status, muted styling + an explicit "(reviewed)" qualifier. Display-layer
// only — never fed back into the gate classification/thresholds.
function GateBadge({ status, resolved }: { status?: string | null; resolved?: boolean }) {
  if (!status || !GATE_LABEL[status]) return null
  const alarm = resolved && (status === 'blocked' || status === 'flagged')
  const style = alarm ? { background: 'var(--surface2)', color: 'var(--text3)' } : GATE_COLOR[status]
  return <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 6, ...style }}>
    {GATE_LABEL[status]}{alarm ? ' (reviewed)' : ''}
  </span>
}

type Form = { dm_store_cash: string; dm_store_cc: string; dm_epay_cash: string; dm_epay_cc: string; dm_acc_sale: string; dm_other: string; dm_ext_cc: string; note: string }

// A rep row's value for one activation-count field_key (mig 501): a standard field_key is a physical
// column on the row, a custom one lives in the `counts` jsonb.
function countVal(r: any, key: string) {
  return key in r ? r[key] : (r.counts?.[key] ?? 0)
}

// A store-card key must include the DATE now that /closing/summary can return a multi-day range
// (retail-ops-14) — the same store_code otherwise collides across two different nights.
function cardKey(s: any): string {
  return `${s.store_code || s.store_name}__${s.close_date || ''}`
}

const csv = (a: string[]) => (a.length ? a.join(',') : undefined)

// ── Missed verifications & chargebacks (OWNER DIRECTIVE 2026-07-22) ──────────────────────────
// A daily_closing that exists but was never DM-verified creates a pending chargeback against the
// DM's COMMISSION (GET/POST /closing/ops-chargebacks/*). Shown at the top of this page — daily
// list + cumulative pending/posted total + Post/Waive (management-gated, backend-enforced; this
// panel only shows the buttons when the server says `can_decide`). retail-ops-14: now honors the
// SAME active filter bar as the rest of the page (previously called with zero filter params).
const badgeStyle: Record<string, React.CSSProperties> = {
  pending: { background: '#fef3e2', color: '#b45309' },
  posted: { background: '#fbe4e4', color: '#b42318' },
  waived: { background: 'var(--surface2)', color: 'var(--text3)' },
}

function MissedChargebacksPanel({ filt }: { filt: StandardFilterValue }) {
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [msg, setMsg] = useState<Record<string, string>>({})
  const [open, setOpen] = useState(true)
  // Anti-clobber (Gate-1 NIT-1, 2026-07-28): same latest-wins guard the other two fetches in this
  // file already use — a fast filter change firing a 2nd request before a slower 1st one resolves
  // could otherwise let the stale response land last and silently overwrite newer totals/rows.
  const reqRef = useRef(0)

  const load = useCallback(() => {
    const myReq = ++reqRef.current
    setLoading(true)
    const qs = new URLSearchParams()
    if (filt.period) qs.set('date_from', filt.period)
    if (filt.periodTo) qs.set('date_to', filt.periodTo)
    const s = csv(filt.stores); if (s) qs.set('stores', s)
    const m = csv(filt.markets); if (m) qs.set('markets', m)
    const r = csv(filt.reps); if (r) qs.set('reps', r)
    api(`/api/v1/closing/ops-chargebacks/dm-verify?${qs.toString()}`)
      .then(d => { if (reqRef.current === myReq) setData(d) })
      .catch(() => { if (reqRef.current === myReq) setData(null) })
      .finally(() => { if (reqRef.current === myReq) setLoading(false) })
  }, [filt.period, filt.periodTo, filt.stores, filt.markets, filt.reps])
  useEffect(() => { load() }, [load])

  async function decide(id: string, decision: 'posted' | 'waived') {
    if (decision === 'waived' && !window.confirm('Waive this chargeback? It will NOT be deducted from the DM\'s commission.')) return
    if (decision === 'posted' && !window.confirm('Post this chargeback? It will be deducted from the DM\'s commission for that period.')) return
    setBusy(b => ({ ...b, [id]: true })); setMsg(m => ({ ...m, [id]: '' }))
    try {
      await api('/api/v1/closing/ops-chargebacks/decide', { method: 'POST', body: JSON.stringify({ id, decision }) })
      load()
    } catch (e: any) { setMsg(m => ({ ...m, [id]: '❌ ' + (e?.message || e) })) }
    finally { setBusy(b => ({ ...b, [id]: false })) }
  }

  if (loading && !data) return null   // don't flash an empty banner while the very first load runs
  const rows: any[] = data?.rows || []
  const totals = data?.totals || { pending: 0, posted: 0, waived: 0, to_be_foregone: 0 }
  if (rows.length === 0) return null  // no missed verifications ever recorded (within the active filters) -> no banner at all

  return (
    <div className="card" style={{ padding: 14, marginBottom: 16, border: totals.pending > 0 ? '1px solid #f3b4b4' : undefined }}>
      <div role="button" tabIndex={0} onClick={() => setOpen(o => !o)} onKeyDown={e => { if (e.key === 'Enter') setOpen(o => !o) }}
        style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap', cursor: 'pointer' }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 14, fontWeight: 700 }}>⚠️ Missed verifications & chargebacks</span>
          <span style={{ fontSize: 12, color: 'var(--text3)' }}>{rows.length} missed DM verification{rows.length === 1 ? '' : 's'}</span>
        </div>
        <div style={{ display: 'flex', gap: 14, alignItems: 'center', fontSize: 12 }}>
          <span>Pending: <b style={{ color: '#b45309' }}>{fmt(totals.pending)}</b></span>
          <span>Posted: <b style={{ color: '#b42318' }}>{fmt(totals.posted)}</b></span>
          <span>To be foregone from commission: <b>{fmt(totals.to_be_foregone)}</b></span>
          <span style={{ color: 'var(--text3)' }}>{open ? '▲' : '▼'}</span>
        </div>
      </div>
      {open && (
        <div className="table-wrapper" style={{ marginTop: 10 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['Store', 'Date', 'District Manager', 'Amount', 'Status', ''].map(h =>
                <th key={h} style={{ textAlign: 'left', padding: '6px 9px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {rows.map((r: any) => (
                <tr key={r.id}>
                  <td style={cell}>{r.store_code}</td>
                  <td style={cell}>{r.incident_date}</td>
                  <td style={cell}>{r.employee_name || '—'}</td>
                  <td style={cell}>{fmt(r.amount)}</td>
                  <td style={cell}>
                    <span className="badge" style={{ fontSize: 11, padding: '2px 8px', borderRadius: 6, ...badgeStyle[r.status] }}>
                      {r.status === 'posted' ? `POSTED${r.posted_ref ? ` · ${r.posted_ref}` : ''}` : r.status.toUpperCase()}
                    </span>
                    {r.status !== 'pending' && r.decided_by && <span style={{ fontSize: 11, color: 'var(--text3)', marginLeft: 6 }}>by {r.decided_by}</span>}
                  </td>
                  <td style={cell}>
                    {r.status === 'pending' && data?.can_decide && (
                      <div style={{ display: 'flex', gap: 6 }}>
                        <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }} disabled={busy[r.id]}
                          onClick={() => decide(r.id, 'posted')}>{busy[r.id] ? '⏳' : 'Post'}</button>
                        <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }} disabled={busy[r.id]}
                          onClick={() => decide(r.id, 'waived')}>{busy[r.id] ? '⏳' : 'Waive'}</button>
                      </div>
                    )}
                    {msg[r.id] && <div style={{ fontSize: 11, color: '#b91c1c' }}>{msg[r.id]}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export default function DailyClosingVerify() {
  const { user, permissions } = useAuth()
  // Carrier vocabulary (owner 2026-09-04): the bill-pay processor / financing-program names are
  // per-carrier preset DATA (mig 953 — boost renders 'ePay'/'ACIMA' byte-identical to today).
  const { term, colLabel } = useReportLabels()
  const ep = term('processor', 'Bill-pay')
  const fin = term('financing', 'Financing')
  // The external credit machine's tenant-facing name — mig-960 carrier label preset (owner
  // 2026-09-04), tenant-overridable; no preset ⇒ the built-in wording.
  const extCc = colLabel('closing_t_ext_cc', 'External Credit Card')
  const today = localToday()
  // RULE FIVE (§3d): the standard core filter bar — period as a date-RANGE (default From===To, i.e.
  // today, so the DM's evening single-day workflow is unchanged) + store(s)/market(s)/rep(s) multi.
  const [filt, setFilt] = useState<StandardFilterValue>({ period: today, periodTo: today, stores: [], markets: [], reps: [] })
  const [dates, setDates] = useState<any[]>([])
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [forms, setForms] = useState<Record<string, Form>>({})
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [upBusy, setUpBusy] = useState(false)
  const [upMsg, setUpMsg] = useState('')
  const [autoNote, setAutoNote] = useState('')
  // Anti-clobber: only the LATEST in-flight request may land (the timeclock last-response-wins race
  // class — a fast filter change firing a 2nd request before a slower 1st one resolves used to let
  // the stale response land last and silently overwrite newer data).
  const reqRef = useRef(0)
  const autoResolvedRef = useRef(false)

  // A market-scoped DM sees their own market pre-selected — same auto-apply the dashboard uses.
  // Bucket-aware filtering downstream (see closing_summary's `_market_bucket`) means this can no
  // longer silently empty the page the way the pre-fix exact-string match did.
  useEffect(() => {
    const mkt = user?.market
    if (mkt && permissions?.scope === 'market') setFilt(f => (f.markets.length ? f : { ...f, markets: [mkt] }))
  }, [user, permissions])

  // Root cause (b): default to the most recent date WITH rows instead of silently showing an empty
  // "today" — GET /closing/dates already exists for exactly this. Runs once; a user's own date pick
  // afterward is never overridden.
  useEffect(() => {
    api('/api/v1/closing/dates').then(d => {
      const list = Array.isArray(d) ? d : []
      setDates(list)
      if (!autoResolvedRef.current) {
        autoResolvedRef.current = true
        const hasToday = list.some((x: any) => x.date === today)
        if (!hasToday && list.length > 0) {
          setFilt(f => ({ ...f, period: list[0].date, periodTo: list[0].date }))
          setAutoNote(`Showing ${list[0].date} — the most recent closing date with data (today has none submitted yet).`)
        }
      }
    }).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Canonical, org-scoped option sources for the filter bar (pick-don't-type §3b) — NEVER derived
  // from this page's own (possibly empty/filtered) result set, which was the original circularity
  // bug: an empty response meant empty market options meant a DM could never even SEE the "(no
  // market)" bucket to select it back.
  const [pStores, setPStores] = useState<any[]>([])
  const [pEmps, setPEmps] = useState<any[]>([])
  useEffect(() => {
    apiCached('/api/v1/closing/stores', LOOKUP).then((s: any) => setPStores(Array.isArray(s) ? s : [])).catch(() => {})
    apiCached('/api/v1/storeops/employees?all_company=true', LOOKUP).then((r: any) => setPEmps(Array.isArray(r) ? r : (r?.employees || []))).catch(() => {})
  }, [])
  const storeOptions: EntityOption[] = useMemo(
    () => pStores.filter((s: any) => s.store_code).map((s: any) => ({ id: s.store_code, label: s.store_address || s.store_code, sublabel: s.market || undefined })),
    [pStores])
  const marketOptions: EntityOption[] = useMemo(() => {
    const real = Array.from(new Set(pStores.map((s: any) => s.market).filter(Boolean))).sort()
    // Always offered — an SFID-unresolved daily_closing row has no store_code at all, so it can
    // never appear in the canonical store roster above; the bucket must still be pickable.
    return [...real.map((m: string) => ({ id: m, label: m })), { id: NO_MARKET, label: NO_MARKET }]
  }, [pStores])
  const repOptions: EntityOption[] = useMemo(
    () => pEmps.filter((e: any) => (e.name || '').trim()).map((e: any) => ({ id: e.name, label: e.name, sublabel: e.email || undefined })),
    [pEmps])

  // Shared with the narrow single-card refresh below (refreshStore) so the two never drift.
  function buildForm(s: any): Form {
    const v = s.verification || {}
    const t = s.totals || {}
    return {
      dm_store_cash: String(v.dm_store_cash ?? t.store_cash ?? ''),
      dm_store_cc: String(v.dm_store_cc ?? t.store_cc ?? ''),
      // Prefill from the REAL ePay breakdown (t.epay_on_cash/epay_on_cc — era-aware via
      // _row_epay_display, closing/router.py), owner-approved 2026-07-30 ("push"/"go"). The legacy
      // t.epay_cash/epay_cc are hard-zeroed by create_row for modern rows, so prefilling from them
      // defaulted the DM's saved count to $0. A previously SAVED verification value still wins;
      // dm_epay_* is stored on daily_closing_verification only — no recon formula reads it.
      dm_epay_cash: String(v.dm_epay_cash ?? t.epay_on_cash ?? ''),
      dm_epay_cc: String(v.dm_epay_cc ?? t.epay_on_cc ?? ''),
      dm_acc_sale: String(v.dm_acc_sale ?? t.acc_sale ?? ''),
      dm_other: String(v.dm_other ?? t.other_account ?? ''),
      // mig 961 — the EXTERNAL-CREDIT portion OF the corrected card total. Prefilled from the reps'
      // own external figure so the DM confirms rather than re-types; leaving it blank keeps the
      // pre-961 behavior (the corrected card total stays merged). Either way the card TOTAL the
      // platform books is `Store CC` — this box only says how much of it ran on that terminal.
      dm_ext_cc: String(v.dm_ext_cc ?? t.t_ext_cc ?? ''),
      note: v.note || '',
    }
  }

  const load = useCallback(() => {
    if (!filt.period) return
    const myReq = ++reqRef.current
    setLoading(true)
    const qs = new URLSearchParams()
    qs.set('date_from', filt.period)
    qs.set('date_to', filt.periodTo || filt.period)
    const s = csv(filt.stores); if (s) qs.set('stores', s)
    const m = csv(filt.markets); if (m) qs.set('markets', m)
    const r = csv(filt.reps); if (r) qs.set('reps', r)
    api(`/api/v1/closing/summary?${qs.toString()}`)
      .then(d => {
        if (reqRef.current !== myReq) return
        setData(d)
        const f: Record<string, Form> = {}
        ;(d?.stores || []).forEach((s: any) => { f[cardKey(s)] = buildForm(s) })
        setForms(f)
      })
      .catch(console.error)
      .finally(() => { if (reqRef.current === myReq) setLoading(false) })
  }, [filt.period, filt.periodTo, filt.stores, filt.markets, filt.reps])
  useEffect(() => { load() }, [load])

  // OWNER BUG REPORT 2026-07-29 ("DM verify after doing one verification goes into a loop and locks
  // out for over 3-4 minutes"): verify()/approveExpense() used to call the full load() — a fast
  // upsert (POST /closing/verify or /closing/expense/approve) followed by a reload of the ENTIRE
  // active filter/date-range via /closing/summary. In range mode that's up to
  // _SUMMARY_MAX_RANGE_DATES=14 dates, each running 10+ queries (schedules, timelog/B2B money+counts,
  // X-report, verifications, the gate replay) — a single-card action paid for re-deriving every OTHER
  // card too. This refetches ONLY the one (store, close_date) that actually changed and merges it
  // into the existing `data.stores` array in place — never touches the rest of the loaded range. Best
  // effort: on failure it silently leaves the optimistic update in place (the action itself already
  // succeeded server-side; this is just a display refresh).
  async function refreshStore(storeCode: string, closeDate: string) {
    try {
      const qs = new URLSearchParams({ date_from: closeDate, date_to: closeDate, stores: storeCode })
      const d = await api(`/api/v1/closing/summary?${qs.toString()}`)
      const fresh = (d?.stores || []).find((x: any) => x.store_code === storeCode && x.close_date === closeDate)
      if (!fresh) return
      setData((prev: any) => {
        if (!prev) return prev
        const already = (prev.stores || []).some((x: any) => x.store_code === storeCode && x.close_date === closeDate)
        const stores = already
          ? (prev.stores || []).map((x: any) => (x.store_code === storeCode && x.close_date === closeDate) ? fresh : x)
          : [...(prev.stores || []), fresh]
        return { ...prev, stores }
      })
      setForms(p => ({ ...p, [cardKey(fresh)]: buildForm(fresh) }))
    } catch { /* best-effort refresh only — the write already succeeded */ }
  }

  async function upload(file: File) {
    setUpBusy(true); setUpMsg('')
    const fd = new FormData(); fd.append('file', file)
    try {
      const r = await apiUpload('/api/v1/closing/upload', fd)
      setUpMsg(`✅ Loaded ${r.rows_saved} rows across ${r.dates?.length || 0} day(s)${r.unresolved_stores ? ` · ${r.unresolved_stores} rows had an unrecognized SFID` : ''}.`)
      api('/api/v1/closing/dates').then(d => setDates(d || [])).catch(() => {})
      if (r.dates?.length) setFilt(f => ({ ...f, period: r.dates[0], periodTo: r.dates[0] })); else load()
    } catch (e: any) { setUpMsg('❌ ' + (e?.message || e)) }
    finally { setUpBusy(false) }
  }

  async function verify(s: any) {
    const k = cardKey(s)
    const f = forms[k]
    if (!s.store_code) { alert('This store has no resolved store code (unrecognized SFID) — fix the SFID/store mapping first.'); return }
    try {
      await api('/api/v1/closing/verify', { method: 'POST', body: JSON.stringify({
        close_date: s.close_date, store_code: s.store_code, store_name: s.store_name,
        verified: true, verified_by: user?.full_name || 'DM',
        dm_store_cash: num(f.dm_store_cash), dm_store_cc: num(f.dm_store_cc),
        dm_epay_cash: num(f.dm_epay_cash), dm_epay_cc: num(f.dm_epay_cc),
        dm_acc_sale: num(f.dm_acc_sale), dm_other: num(f.dm_other),
        dm_ext_cc: num(f.dm_ext_cc), note: f.note,
      }) })
      // Optimistic local update so the card flips to "Verified" instantly, then a narrow
      // single-store/single-day background refresh (see refreshStore) — never the full load().
      const now = new Date().toISOString()
      setData((prev: any) => prev ? { ...prev, stores: (prev.stores || []).map((x: any) =>
        (x.store_code === s.store_code && x.close_date === s.close_date)
          ? { ...x, verification: { ...(x.verification || {}), verified: true, verified_by: user?.full_name || 'DM', verified_at: now } }
          : x) } : prev)
      refreshStore(s.store_code, s.close_date)
    } catch (e: any) { alert('Verify failed: ' + (e?.message || e)) }
  }

  // DM expense approval — a single checkbox per rep row. Checking it asks "Is this an approved
  // expense?"; on confirm we persist. Unchecking clears the approval. The checkbox is driven by
  // server state, so a cancelled confirm just leaves it as-is — same "narrow refresh, never the
  // whole range" fix as verify() above (the row already carries its own store_code/close_date).
  async function approveExpense(r: any, approved: boolean) {
    if (approved && !window.confirm('Is this an approved expense?')) return
    try {
      await api('/api/v1/closing/expense/approve', { method: 'POST', body: JSON.stringify({
        row_id: r.id, approved, approved_by: user?.full_name || 'DM',
      }) })
      if (r.store_code && r.close_date) refreshStore(r.store_code, r.close_date); else load()
    } catch (e: any) { alert('Approve failed: ' + (e?.message || e)) }
  }

  // Per-line decide for the CATEGORIZED expense lines (mig 506, EEP) — replaces the single checkbox
  // above for new-form entries. Approving/rejecting one line refreshes just that store (same pattern).
  async function decideExpenseLine(r: any, expenseId: string, status: 'approved' | 'rejected') {
    if (status === 'approved' && !window.confirm('Approve this expense line?')) return
    if (status === 'rejected' && !window.confirm('Reject this expense line?')) return
    try {
      await api(`/api/v1/closing/expense/${expenseId}/decide`, { method: 'POST', body: JSON.stringify({
        status, decided_by: user?.full_name || 'DM',
      }) })
      if (r.store_code && r.close_date) refreshStore(r.store_code, r.close_date); else load()
    } catch (e: any) { alert('Decide failed: ' + (e?.message || e)) }
  }

  function setForm(k: string, patch: Partial<Form>) { setForms(p => ({ ...p, [k]: { ...p[k], ...patch } })) }

  const stores: any[] = data?.stores || []
  const verifiedCount = stores.filter(s => s.verification?.verified).length
  const isRange = filt.period !== filt.periodTo

  // ── RULE FOUR exports (§3c) — a non-flat, interactive card page uses ReportExportBar (the shared
  // component the contract names for exactly this case) instead of ReportShell, which would replace
  // this whole UI with a generic table. Two sheets: the per-store verification summary, and every
  // rep row underneath it — what-you-see-is-what-exports (same `stores` the cards below render from,
  // already filtered by the active bar; the money-secrecy boundary is inherited for free since
  // gate_reasons/b2b_cash/b2b_card are already blanked server-side for a non-permitted caller). ──
  const storeColumns: ExportColumn[] = useMemo(() => [
    { header: 'Date', field: 'close_date', type: 'date', role: 'date', get: (r: any) => r.close_date },
    { header: 'Store', field: 'store_address', role: 'store', get: (r: any) => r.store_address || r.store_name },
    { header: 'Market', field: 'market', get: (r: any) => r.market },
    { header: 'Rep submissions', field: 'rep_count', type: 'number', get: (r: any) => r.totals?.rep_count ?? 0 },
    { header: 'No closing submitted', field: 'no_closing_submitted', get: (r: any) => r.no_closing_submitted ? 'Yes' : 'No' },
    { header: 'Missing reps', field: 'missing_reps', get: (r: any) => (r.missing_reps || []).join('; ') },
    { header: 'Store cash $', field: 'store_cash', money: true, get: (r: any) => r.totals?.store_cash },
    { header: 'Store CC $', field: 'store_cc', money: true, get: (r: any) => r.totals?.store_cc },
    { header: `${ep} cash $`, field: 'epay_cash', money: true, get: (r: any) => r.totals?.epay_on_cash },
    { header: `${ep} CC $`, field: 'epay_cc', money: true, get: (r: any) => r.totals?.epay_on_cc },
    { header: `${fin} $`, field: 't_acima', money: true, get: (r: any) => r.totals?.t_acima },
    { header: 'Accessory sale $', field: 'acc_sale', money: true, get: (r: any) => r.totals?.acc_sale },
    { header: 'Other (Zelle/CashApp/Gift/Store Acct) $', field: 'other_account', money: true, get: (r: any) => r.totals?.other_account },
    { header: 'Total collected $', field: 'total_collected', money: true, get: (r: any) => r.totals?.total_collected },
    { header: 'Custom tenders', field: 'custom_tenders', get: (r: any) => (r.totals?.custom_tenders || []).map((c: any) => `${c.label}: ${fmt(c.value)}`).join('; ') },
    { header: 'Gate status', field: 'gate_status', get: (r: any) => (r.gate_status && GATE_LABEL[r.gate_status]) || '' },
    { header: 'Activations var', field: 'act_var', type: 'number', get: (r: any) => r.recon?.act_var },
    { header: 'Upgrades var', field: 'upg_var', type: 'number', get: (r: any) => r.recon?.upg_var },
    { header: 'Count discrepancy', field: 'discrepancy', get: (r: any) => r.recon?.discrepancy ? 'Yes' : 'No' },
    { header: 'Cash recon var $', field: 'cash_var', money: true, get: (r: any) => r.money_recon?.cash?.var },
    { header: 'Cash recon flag', field: 'cash_flag', get: (r: any) => r.money_recon?.cash?.flag ? 'Yes' : (r.money_recon?.cash?.pending ? 'Pending' : 'No') },
    { header: 'Credit recon var $', field: 'credit_var', money: true, get: (r: any) => r.money_recon?.credit?.var },
    { header: 'Credit recon flag', field: 'credit_flag', get: (r: any) => r.money_recon?.credit?.flag ? 'Yes' : (r.money_recon?.credit?.pending ? 'Pending' : 'No') },
    { header: 'DM verified', field: 'dm_verified', get: (r: any) => r.verification?.verified ? 'Yes' : 'No' },
    { header: 'DM verified by', field: 'dm_verified_by', get: (r: any) => r.verification?.verified_by },
    // ── Original vs DM-modified, side by side (owner 2026-09-02): the money columns above are the
    // AUTHORITATIVE store-day figures (DM-corrected once verified — TKT-1030 overlay). These carry
    // the store-entered ORIGINAL aggregate (present only when a DM correction actually applied)
    // and the DM's modified values, so an exported date range shows both. ──
    { header: 'DM corrected', field: 'dm_corrected', get: (r: any) => r.dm_corrected ? 'Yes' : 'No' },
    { header: 'Original store cash $', field: 'orig_store_cash', money: true, get: (r: any) => r.totals_original ? r.totals_original.store_cash : r.totals?.store_cash },
    { header: 'Original store CC $', field: 'orig_store_cc', money: true, get: (r: any) => r.totals_original ? r.totals_original.store_cc : r.totals?.store_cc },
    { header: `Original ${ep} cash $`, field: 'orig_epay_cash', money: true, get: (r: any) => r.totals_original ? r.totals_original.epay_on_cash : r.totals?.epay_on_cash },
    { header: `Original ${ep} CC $`, field: 'orig_epay_cc', money: true, get: (r: any) => r.totals_original ? r.totals_original.epay_on_cc : r.totals?.epay_on_cc },
    { header: 'Original accessory $', field: 'orig_acc_sale', money: true, get: (r: any) => r.totals_original ? r.totals_original.acc_sale : r.totals?.acc_sale },
    { header: 'Original other $', field: 'orig_other', money: true, get: (r: any) => r.totals_original ? r.totals_original.other_account : r.totals?.other_account },
    { header: `Original ${extCc} $`, field: 'orig_ext_cc', money: true, get: (r: any) => r.totals_original ? r.totals_original.t_ext_cc : r.totals?.t_ext_cc },
    { header: 'DM cash $', field: 'dm_store_cash', money: true, get: (r: any) => r.verification?.dm_store_cash },
    { header: 'DM credit $', field: 'dm_store_cc', money: true, get: (r: any) => r.verification?.dm_store_cc },
    { header: `DM ${ep} cash $`, field: 'dm_epay_cash', money: true, get: (r: any) => r.verification?.dm_epay_cash },
    { header: `DM ${ep} CC $`, field: 'dm_epay_cc', money: true, get: (r: any) => r.verification?.dm_epay_cc },
    { header: 'DM accessory $', field: 'dm_acc_sale', money: true, get: (r: any) => r.verification?.dm_acc_sale },
    { header: 'DM other $', field: 'dm_other', money: true, get: (r: any) => r.verification?.dm_other },
    { header: `DM ${extCc} $`, field: 'dm_ext_cc', money: true, get: (r: any) => r.verification?.dm_ext_cc },
    { header: 'DM note', field: 'dm_note', get: (r: any) => r.verification?.note || '' },
  ], [ep, fin, extCc])

  const repColumns: ExportColumn[] = useMemo(() => [
    { header: 'Date', field: 'close_date', type: 'date', role: 'date', get: (r: any) => r._store_close_date },
    { header: 'Store', field: 'store_address', role: 'store', get: (r: any) => r._store_address },
    { header: 'Market', field: 'market', get: (r: any) => r._store_market },
    { header: 'Employee', field: 'employee_name', role: 'rep', get: (r: any) => r.employee_name },
    { header: 'Store cash $', field: 'store_cash', money: true, get: (r: any) => r.store_cash },
    { header: 'Store CC $', field: 'store_cc', money: true, get: (r: any) => r.store_cc },
    { header: `${ep} cash $`, field: 'epay_cash', money: true, get: (r: any) => r._epay_display?.cash },
    { header: `${ep} CC $`, field: 'epay_cc', money: true, get: (r: any) => r._epay_display?.cc },
    { header: `${fin} $`, field: 'acima', money: true, get: (r: any) => r._tenders?.acima },
    { header: 'Gift $', field: 'gift', money: true, get: (r: any) => r._tenders?.gift },
    { header: 'Store Account $', field: 'store_acct', money: true, get: (r: any) => r._tenders?.store_acct },
    { header: 'Custom tenders', field: 'custom_tenders', get: (r: any) => r._custom_tenders_display },
    { header: 'Accessory $', field: 'acc_sale', money: true, get: (r: any) => r.acc_sale },
    { header: 'Other $', field: 'other_account', money: true, get: (r: any) => r.other_account },
    { header: 'Custom counts', field: 'custom_counts', get: (r: any) => r._custom_counts_display },
    { header: 'Expense $', field: 'expense_amount', money: true, get: (r: any) => r.expense_amount },
    { header: 'Expense approved', field: 'expense_approved', get: (r: any) => r.expense_approved ? 'Yes' : 'No' },
    { header: 'Categorized expenses', field: 'expense_lines', get: (r: any) =>
        (r._expense_lines || []).map((e: any) => `${e.category_name}: ${fmt(e.amount)} (${e.status})`).join('; ') },
    { header: 'Gate status', field: 'gate_status', get: (r: any) => (r._gate?.status && GATE_LABEL[r._gate.status]) || '' },
    { header: 'Gate reason(s)', field: 'gate_reasons', get: (r: any) => (r._gate?.reasons || []).join('; ') },
    // Owner 2026-09-02: the envelope PICTURE rides the export — each rep row's photo is already
    // signed by /closing/summary (envelope_url), so the link works straight out of the file.
    { header: 'Envelope photo', field: 'envelope_url', get: (r: any) => r.envelope_url || '' },
  ], [ep, fin])

  const storeExportRows = stores
  const repExportRows = useMemo(() => stores.flatMap((s: any) =>
    (s.reps || []).map((r: any) => ({ ...r, _store_close_date: s.close_date, _store_address: s.store_address || s.store_name, _store_market: s.market }))
  ), [stores])

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>✅ DM Closing Verification</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Verify every evening that each store's closing sheet was submitted, confirm the totals, and reconcile against B2B actual sales.
          </p>
        </div>
        {!loading && stores.length > 0 && (
          <ReportExportBar
            title="Daily Closing — DM Verification"
            subtitle={isRange ? `${filt.period} → ${filt.periodTo}` : filt.period}
            filename={`dm-verify_${filt.period}${isRange ? `_${filt.periodTo}` : ''}`}
            sheets={[
              { name: 'Store Summary', columns: storeColumns, rows: storeExportRows },
              { name: 'Rep Rows', columns: repColumns, rows: repExportRows },
            ]}
          />
        )}
      </div>

      <MissedChargebacksPanel filt={filt} />

      {/* Upload */}
      <div className="card" style={{ padding: 14, marginBottom: 16, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <label className="btn btn-secondary" style={{ fontSize: 13, cursor: 'pointer' }}>
          {upBusy ? '⏳ Uploading…' : '📤 Upload closing sheet'}
          <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) upload(f) }} />
        </label>
        <a href={GOOGLE_CLOSING_SHEET_URL} target="_blank" rel="noreferrer" className="btn btn-secondary" style={{ fontSize: 13 }}>🔗 Open Google sheet ↗</a>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>Export the Google "Envelopes Data" sheet as .xlsx/.csv and upload it here — or open it directly.</span>
        {upMsg && <span style={{ fontSize: 13 }}>{upMsg}</span>}
      </div>

      {autoNote && (
        <div className="card" style={{ padding: '8px 12px', marginBottom: 14, fontSize: 12, background: '#fff8e6', border: '1px solid #f3d98b' }}>
          ℹ️ {autoNote}
        </div>
      )}

      {/* RULE FIVE standardized filter bar — date-range (defaults to a single day) + store(s)/
          market(s)/rep(s), options from canonical org-scoped sources (never this page's own result
          set). Drives the store cards, the chargebacks panel, and the exports above. */}
      <StandardFilterBar
        value={filt} onChange={setFilt}
        periodMode="range"
        storeOptions={storeOptions} marketOptions={marketOptions} repOptions={repOptions}
        storeLabel="Stores…" marketLabel="Markets…" repLabel="Employees…"
        right={dates.length > 0 ? (
          <select style={sel} value="" onChange={e => { if (e.target.value) setFilt(f => ({ ...f, period: e.target.value, periodTo: e.target.value })) }}>
            <option value="">Recent days…</option>
            {dates.map((d: any) => <option key={d.date} value={d.date}>{d.date} ({d.rows})</option>)}
          </select>
        ) : undefined}
      />
      {!loading && stores.length > 0 && (
        <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: -4, marginBottom: 12 }}>
          {verifiedCount}/{stores.length} store-day{stores.length === 1 ? '' : 's'} verified
          {isRange && <span> · {filt.period} → {filt.periodTo}</span>}
        </div>
      )}
      {data?.range_capped && (
        <div className="card" style={{ padding: '8px 12px', marginBottom: 14, fontSize: 12, background: '#fff8e6', border: '1px solid #f3d98b' }}>
          ⚠️ This range has more days than can be loaded at once — narrowed to the {data.dates_computed} most recent of {data.dates_requested} requested. Narrow the date range to see the rest.
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : stores.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          No closing-sheet rows for {isRange ? `${filt.period} → ${filt.periodTo}` : filt.period} matching the active filters. Upload the sheet above, widen the date range, or clear a filter.
        </div>
      ) : stores.map(s => {
        const k = cardKey(s)
        const f = forms[k] || {} as Form
        const t = s.totals || {}
        const ver = s.verification?.verified
        const recon = s.recon
        const expTotal = (s.reps || []).reduce((a: number, r: any) => a + (Number(r.expense_amount) || 0), 0)
        const expPending = (s.reps || []).filter((r: any) => (Number(r.expense_amount) || 0) > 0 && !r.expense_approved).length
        // Config-driven activation-count fields (mig 501) — from totals.counts, so an un-opted tenant
        // still shows exactly the 3 built-in fields (Upgrades / New Lines / Postpaid).
        const countCols: { key: string; label: string }[] = (t.counts || []).map((c: any) => ({ key: c.field_key, label: c.label }))
        const customTenderCols: { key: string; label: string }[] = (t.custom_tenders || []).map((c: any) => ({ key: c.key, label: c.label }))
        // OWNER BUG REPORT 2026-07-29 ("management removed block still showing on dm verify"): the
        // gate_status badge is a live re-derivation of cash/credit vs B2B (never redefined here) — it
        // was already correct, but gave NO indication of two things a DM/management could have already
        // done about a "⛔ Blocked"/"⚠️ Flagged" row: (1) the 3-try submit flow itself auto-accepted the
        // rep's 3rd attempt (letting them finish closing — the "block" on the REP was already lifted,
        // it's now a management-review item, not something the rep needs to redo), or (2) management
        // RELEASED the row for a corrected resubmit (which the rep hasn't sent yet). Without this
        // context a still-"Blocked" badge reads as "nothing was done," even when it was. Read-only —
        // does not touch _money_issues/the gate classification itself.
        const autoAcceptedRep = (s.reps || []).find((r: any) => r.auto_accepted)
        const releasedRep = (s.reps || []).find((r: any) => r.released_at)
        return (
          <div key={k} className="card" style={{ padding: 16, marginBottom: 14, borderLeft: `4px solid ${ver ? 'var(--green, #16794a)' : recon?.discrepancy ? 'var(--amber, #b45309)' : 'var(--border)'}` }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8 }}>
              <div>
                <div style={{ fontSize: 16, fontWeight: 700 }}>{s.store_address || s.store_name}{isRange && <span style={{ fontWeight: 400, fontSize: 13, color: 'var(--text3)' }}> · {s.close_date}</span>}</div>
                <div style={{ fontSize: 12, color: 'var(--text3)' }}>{s.market || '—'} · {t.rep_count || 0} rep submission{(t.rep_count || 0) === 1 ? '' : 's'}{typeof s.worked_count === 'number' ? ` · ${s.worked_count} actually worked` : ''}{s.closer ? ` · closer: ${s.closer}` : ''}{s.closing_mode === 'one_closing' ? ' (one closing/store)' : ''}</div>
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <GateBadge status={s.gate_status} resolved={!!(ver || autoAcceptedRep || releasedRep)} />
                {(s.gate_status === 'blocked' || s.gate_status === 'flagged') && autoAcceptedRep && (
                  <span style={{ fontSize: 11, color: 'var(--text3)' }}
                    title="The rep's 3rd attempt was auto-accepted so they could finish closing — this is now a Management Review item, not something the rep needs to redo.">
                    · auto-accepted (3rd try) — needs Mgmt Review
                  </span>
                )}
                {(s.gate_status === 'blocked' || s.gate_status === 'flagged') && releasedRep && (
                  <span style={{ fontSize: 11, color: 'var(--text3)' }}>
                    · released for correction by {releasedRep.released_by || 'management'}{releasedRep.correction_count ? '' : ' (not yet resubmitted)'}
                  </span>
                )}
                {ver
                  ? <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--green, #16794a)' }}>✅ Verified by {s.verification.verified_by}</span>
                  : <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text3)' }}>Unverified</span>}
                {s.dm_corrected && (
                  <span title="Store-day totals, reconciliation, deposits and the incentive gate all reflect the DM's verified corrections. Per-rep rows below stay as submitted."
                    style={{ fontSize: 11, fontWeight: 700, color: '#1d4ed8', background: '#e0e7ff', padding: '2px 7px', borderRadius: 6 }}>
                    ✎ DM-corrected
                  </span>
                )}
              </div>
            </div>

            {s.no_closing_submitted && (
              <div style={{ marginTop: 8, fontSize: 12, fontWeight: 600, color: '#b42318', background: '#fde8e8', padding: '6px 10px', borderRadius: 8 }}>
                🚫 No closing submitted for this store — but {s.worked_reps?.join(', ') || 'reps'} worked here today.
              </div>
            )}
            {s.missing_reps?.length > 0 && !s.no_closing_submitted && (
              <div style={{ marginTop: 8, fontSize: 12, color: 'var(--amber, #b45309)' }}>
                ⚠️ Worked but no closing submitted: {s.missing_reps.join(', ')}
              </div>
            )}
            {s.cross_login?.length > 0 && (
              <div style={{ marginTop: 6, fontSize: 12, color: '#9a3412', background: '#ffedd5', padding: '6px 10px', borderRadius: 8 }}>
                🔐 Sold under a different login than clock-in: {s.cross_login.map((c: any) => c.logins?.length ? `${c.salesperson} (as ${c.logins.join(', ')})` : c.salesperson).join('; ')}
              </div>
            )}
            {s.scheduled_no_show?.length > 0 && (
              <div style={{ marginTop: 6, fontSize: 11, color: 'var(--text3)' }}>
                Scheduled but didn&apos;t work (not dunned): {s.scheduled_no_show.join(', ')}
              </div>
            )}
            {s.worked_unscheduled?.length > 0 && (
              <div style={{ marginTop: 4, fontSize: 11, color: 'var(--text3)' }}>
                Worked but not on the schedule: {s.worked_unscheduled.join(', ')}
              </div>
            )}

            {/* Totals */}
            <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 12, fontSize: 13 }}>
              <Stat label="Store cash" value={fmt(t.store_cash)} />
              <Stat label="Store CC" value={fmt(t.store_cc)} />
              {/* OWNER BUG REPORT 2026-07-29 (509 Nostrand): these used to read t.epay_cash/epay_cc,
                  a legacy column create_row ALWAYS zeroes for a modern (t_*) submission — the rep's
                  real entered "ePay on Cash/Credit $" breakdown never showed here. epay_on_cash/
                  epay_on_cc are new, display-only totals fields (see _row_epay_display, closing/
                  router.py) — money_recon's own cash/credit math is untouched (still reads
                  totals.epay_cash/epay_cc, byte-identical). */}
              <Stat label={`${ep} cash`} value={fmt(t.epay_on_cash)} />
              <Stat label={`${ep} CC`} value={fmt(t.epay_on_cc)} />
              <Stat label="Acc sale" value={fmt(t.acc_sale)} />
              <Stat label="Other" value={fmt(t.other_account)} />
              {!!t.t_acima && <Stat label={fin} value={fmt(t.t_acima)} />}
              {customTenderCols.map(c => <Stat key={c.key} label={c.label} value={fmt((t.custom_tenders || []).find((x: any) => x.key === c.key)?.value)} />)}
              {typeof t.total_collected === 'number' && <Stat label="Total collected" value={fmt(t.total_collected)} />}
              {expTotal > 0 && <Stat label="Rep expenses" value={fmt(expTotal)} />}
              {countCols.length > 0
                ? countCols.map(c => <Stat key={c.key} label={c.label} value={String((t.counts || []).find((x: any) => x.field_key === c.key)?.value ?? 0)} />)
                : <Stat label="Upg / New / Post" value={`${t.upgrade_count} / ${t.new_line_count} / ${t.postpaid_count}`} />}
            </div>

            {expPending > 0 && (
              <div style={{ marginTop: 8, fontSize: 12, fontWeight: 600, color: '#9a3412', background: '#ffedd5', padding: '6px 10px', borderRadius: 8 }}>
                💵 {expPending} rep expense{expPending === 1 ? '' : 's'} awaiting your approval — open the rep rows below to review and approve.
              </div>
            )}

            {/* B2B reconciliation */}
            {recon && (
              <div style={{ marginTop: 12, padding: '8px 12px', borderRadius: 8, background: recon.discrepancy ? '#fef3e2' : '#e6f7ec', fontSize: 13 }}>
                <strong>B2B reconciliation:</strong>{' '}
                Activations closing {recon.closing_activations} vs B2B {recon.b2b_activations}{' '}
                {recon.act_var !== 0 ? <b style={{ color: 'var(--amber, #b45309)' }}>(Δ{recon.act_var > 0 ? '+' : ''}{recon.act_var})</b> : '✓'}
                {' · '}Upgrades closing {recon.closing_upgrades} vs B2B {recon.b2b_upgrades}{' '}
                {recon.upg_var !== 0 ? <b style={{ color: 'var(--amber, #b45309)' }}>(Δ{recon.upg_var > 0 ? '+' : ''}{recon.upg_var})</b> : '✓'}
                <span style={{ color: 'var(--text3)' }}>{' · '}Acc GP (B2B) {fmt(recon.b2b_acc_gp)}</span>
              </div>
            )}

            {/* Money reconciliation vs the POS X-report (owner 2026-08-20: "make sure the X-report data
                is also pulling in"). money_recon.cash/credit.b2b is the X-report tender when
                tender_source==='x_report' (else the sales feed). `closing` already reflects any DM
                verified correction — the server overlays it before this recon runs (TKT-1030). */}
            {s.money_recon && (s.money_recon.cash || s.money_recon.credit) && (
              <div style={{ marginTop: 10, padding: '8px 12px', borderRadius: 8, background: 'var(--surface2)', fontSize: 13 }}>
                <strong>Money reconciliation</strong>
                <span style={{ color: 'var(--text3)', fontSize: 11, marginLeft: 6 }}>
                  declared{s.dm_corrected ? ' (DM-corrected)' : ''} vs {s.money_recon.tender_source === 'x_report' ? 'POS X-report' : 'sales feed'}
                  {s.money_recon.tenders_available === false ? ' — no X-report tender data for this day (recon pending)' : ''}
                </span>
                <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', marginTop: 6 }}>
                  {(['cash', 'credit'] as const).map((leg) => {
                    const m = s.money_recon[leg]; if (!m) return null
                    return (
                      <div key={leg} style={{ fontSize: 12 }}>
                        <span style={{ textTransform: 'capitalize', fontWeight: 600 }}>{leg}</span>{': '}
                        closing {fmt(m.closing)}
                        {m.pending
                          ? <span style={{ color: 'var(--text3)' }}> · X-report pending</span>
                          : <> vs {s.money_recon.tender_source === 'x_report' ? 'X-report' : 'sales'} {fmt(m.b2b)}{' '}
                              <b style={{ color: m.flag ? 'var(--amber, #b45309)' : 'var(--green, #16794a)' }}>
                                {m.flag ? `Δ${(m.var ?? 0) > 0 ? '+' : ''}${fmt(m.var)}` : '✓'}</b></>}
                      </div>
                    )
                  })}
                  {/* ePay: declared (rep ePay-on-cash + on-credit, DM-corrected) vs the Boost portal
                      (Daily Transaction Detail ingest). Fee is shown for context; it reconciles on the
                      fee-recon report, not here. */}
                  {s.money_recon.epay && (
                    <div style={{ fontSize: 12 }}>
                      <span style={{ fontWeight: 600 }}>{ep}</span>{': '}
                      declared {fmt(s.money_recon.epay.declared)}
                      {s.money_recon.epay.portal_pending
                        ? <span style={{ color: 'var(--text3)' }}> · portal pending</span>
                        : <> vs portal {fmt(s.money_recon.epay.portal)}{' '}
                            <b style={{ color: s.money_recon.epay.flag ? 'var(--amber, #b45309)' : 'var(--green, #16794a)' }}>
                              {s.money_recon.epay.flag ? `Δ${(s.money_recon.epay.var ?? 0) > 0 ? '+' : ''}${fmt(s.money_recon.epay.var)}` : '✓'}</b>
                            {typeof s.money_recon.epay.portal_fee === 'number' && s.money_recon.epay.portal_fee > 0
                              ? <span style={{ color: 'var(--text3)' }}> · fee {fmt(s.money_recon.epay.portal_fee)}</span> : null}</>}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Reps */}
            {(s.reps?.length || 0) > 0 && <button className="btn btn-secondary" style={{ fontSize: 12, marginTop: 12 }} onClick={() => setOpen(o => ({ ...o, [k]: !o[k] }))}>
              {open[k] ? '▾' : '▸'} {s.reps.length} rep row{s.reps.length === 1 ? '' : 's'}
            </button>}
            {open[k] && (s.reps?.length || 0) > 0 && (
              <div className="table-wrapper" style={{ marginTop: 8 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead><tr style={{ background: 'var(--surface2)' }}>
                    {['Employee', 'Store cash', 'Store CC', `${ep} cash`, `${ep} CC`, fin, 'Acc', 'Other', 'Custom tenders',
                      ...(countCols.length > 0 ? countCols.map(c => c.label) : ['Upg', 'New', 'Post']),
                      'Gate', 'Env', 'Expense', 'Approve exp.', 'Categorized expenses'].map((h, i) =>
                      <th key={i} style={{ textAlign: 'left', padding: '6px 9px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {s.reps.map((r: any) => (
                      <tr key={r.id}>
                        <td style={cell}>{r.employee_name || '—'}</td>
                        <td style={cell}>{fmt(r.store_cash)}</td>
                        <td style={cell}>{fmt(r.store_cc)}</td>
                        <td style={cell}>{fmt(r._epay_display?.cash)}</td>
                        <td style={cell}>{fmt(r._epay_display?.cc)}</td>
                        <td style={cell}>{r._tenders?.acima ? fmt(r._tenders.acima) : '—'}</td>
                        <td style={cell}>{fmt(r.acc_sale)}</td>
                        <td style={cell}>{fmt(r.other_account)}</td>
                        <td style={cell}>{r._custom_tenders_display || '—'}</td>
                        {(countCols.length > 0 ? countCols : [{ key: 'upgrade_count', label: 'Upg' }, { key: 'new_line_count', label: 'New' }, { key: 'postpaid_count', label: 'Post' }])
                          .map(c => <td key={c.key} style={cell}>{countVal(r, c.key)}</td>)}
                        <td style={cell}>
                          <GateBadge status={r._gate?.status} resolved={!!(ver || r.auto_accepted || r.released_at)} />
                          {r.auto_accepted && <span style={{ fontSize: 10, color: 'var(--text3)', marginLeft: 4 }} title="3rd-try auto-accept — see Management Review">·3rd try</span>}
                          {r.released_at && <span style={{ fontSize: 10, color: 'var(--text3)', marginLeft: 4 }} title={`Released for correction by ${r.released_by || 'management'}`}>·released</span>}
                        </td>
                        <td style={cell}><EnvelopeViewLink row={r} /></td>
                        <td style={cell}>
                          {(Number(r.expense_amount) || 0) > 0
                            ? <span><b>{fmt(r.expense_amount)}</b>{r.expense_description ? <span style={{ color: 'var(--text3)' }}> · {r.expense_description}</span> : null}</span>
                            : <span style={{ color: 'var(--text3)' }}>—</span>}
                        </td>
                        <td style={cell}>
                          {(Number(r.expense_amount) || 0) > 0
                            ? <label style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, whiteSpace: 'nowrap' }}>
                                <input type="checkbox" checked={!!r.expense_approved} onChange={e => approveExpense(r, e.target.checked)} />
                                {r.expense_approved
                                  ? <span style={{ color: 'var(--green, #16794a)', fontWeight: 600 }}>approved{r.expense_approved_by ? ` · ${r.expense_approved_by}` : ''}</span>
                                  : <span style={{ color: 'var(--text3)' }}>approve</span>}
                              </label>
                            : <span style={{ color: 'var(--text3)' }}>—</span>}
                        </td>
                        <td style={cell}>
                          {(r._expense_lines || []).length === 0
                            ? <span style={{ color: 'var(--text3)' }}>—</span>
                            : <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                {(r._expense_lines || []).map((e: any) => (
                                  <div key={e.id} style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
                                    <b>{fmt(e.amount)}</b> · {e.category_name}
                                    {e.employee_name ? ` (${e.employee_name})` : ''}
                                    {e.description ? <span style={{ color: 'var(--text3)' }}> · {e.description}</span> : null}
                                    {' '}
                                    {e.status === 'approved'
                                      ? <span style={{ color: 'var(--green, #16794a)', fontWeight: 600 }}>✓ approved</span>
                                      : e.status === 'rejected'
                                      ? <span style={{ color: '#b42318', fontWeight: 600 }}>✕ rejected</span>
                                      : <span style={{ display: 'inline-flex', gap: 4 }}>
                                          <button className="btn btn-secondary" style={{ fontSize: 10, padding: '1px 6px' }}
                                            onClick={() => decideExpenseLine(r, e.id, 'approved')}>approve</button>
                                          <button className="btn btn-secondary" style={{ fontSize: 10, padding: '1px 6px' }}
                                            onClick={() => decideExpenseLine(r, e.id, 'rejected')}>reject</button>
                                        </span>}
                                  </div>
                                ))}
                              </div>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* DM verification */}
            {!ver && (
              <div style={{ marginTop: 14, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 8 }}>Confirm totals (prefilled from rep entries — adjust if needed)</div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                  <Lbl t="Store cash"><input style={tin} value={f.dm_store_cash || ''} onChange={e => setForm(k, { dm_store_cash: e.target.value })} /></Lbl>
                  <Lbl t="Store CC"><input style={tin} value={f.dm_store_cc || ''} onChange={e => setForm(k, { dm_store_cc: e.target.value })} /></Lbl>
                  <Lbl t={`${ep} cash`}><input style={tin} value={f.dm_epay_cash || ''} onChange={e => setForm(k, { dm_epay_cash: e.target.value })} /></Lbl>
                  <Lbl t={`${ep} CC`}><input style={tin} value={f.dm_epay_cc || ''} onChange={e => setForm(k, { dm_epay_cc: e.target.value })} /></Lbl>
                  <Lbl t="Acc sale"><input style={tin} value={f.dm_acc_sale || ''} onChange={e => setForm(k, { dm_acc_sale: e.target.value })} /></Lbl>
                  <Lbl t="Other"><input style={tin} value={f.dm_other || ''} onChange={e => setForm(k, { dm_other: e.target.value })} /></Lbl>
                  {/* mig 961: part OF Store CC above, never additional money. Blank = don't split. */}
                  <Lbl t={`${extCc} (of Store CC)`}><input style={tin} value={f.dm_ext_cc || ''} onChange={e => setForm(k, { dm_ext_cc: e.target.value })} /></Lbl>
                </div>
                <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                  <input style={{ ...sel, flex: '1 1 280px' }} placeholder="Note (optional)" value={f.note || ''} onChange={e => setForm(k, { note: e.target.value })} />
                  <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={() => verify(s)}>✅ Mark verified</button>
                </div>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function num(v: string): number | null { const n = Number(String(v).replace(/[$,]/g, '')); return isNaN(n) ? null : n }
const Stat = ({ label, value }: { label: string; value: string }) => (
  <div><div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div><div style={{ fontWeight: 600 }}>{value}</div></div>
)
const Lbl = ({ t, children }: { t: string; children: React.ReactNode }) => (
  <label style={{ fontSize: 11, color: 'var(--text3)' }}><div style={{ marginBottom: 2 }}>{t}</div>{children}</label>
)
