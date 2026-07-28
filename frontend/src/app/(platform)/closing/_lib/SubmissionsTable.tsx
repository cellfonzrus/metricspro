'use client'
import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { api, localToday } from '@/lib/client'
import ReportShell from '@/components/ReportShell'
import { ExportColumn } from '@/lib/export'
import StandardFilterBar from '@/components/StandardFilterBar'
import type { EntityOption } from '@/components/EntityPicker'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'

// EVERY submitted daily-closing column, one row per rep-submission (OWNER DIRECTIVE 2026-07-27).
// RULE FIVE (§3d): the standard date-range/store/market/rep filter bar drives BOTH the table and every
// export (RULE FOUR §3c). Reads GET /api/v1/closing/submissions — display/filter/export ONLY, zero
// writes, zero change to close-gate or recon math (the backend re-derives gate_status by reusing the
// exact existing gate helpers, never redefining them).
//
// Money-secrecy note (unchanged from the existing 3-try close flow / Management Review page): the
// coarse gate_status badge (ok/flagged/blocked/recon_pending) is visible to everyone who can see this
// dashboard; the DOLLAR reasons (`gate_reasons`) are populated by the backend ONLY for a company-wide
// caller (same _can_mgmt_review boundary /closing/management already enforces) — a market/store-scope
// viewer here sees the same badge with an empty reasons list, never the true B2B figure.
//
// retail-ops-14 (OWNER DIRECTIVE 2026-07-28, same-day follow-up): the Daily Closing dashboard's
// By-store/By-rep tabs had NO date-range/store/rep filters at all (month + market only) while this
// tab already had the full standard bar — that asymmetry is the owner's complaint. This component now
// OPTIONALLY accepts its filter state (and canonical option lists) as props so the PARENT dashboard can
// render ONE shared <StandardFilterBar> that drives every tab, instead of two competing filter rows.
// Passing no props preserves the original fully-self-contained behavior (own bar, own data-derived
// options) for any other embedding.
export const monthStart = () => localToday().slice(0, 8) + '01'

const GATE_LABEL: Record<string, string> = {
  ok: '✅ OK', flagged: '⚠️ Flagged', blocked: '⛔ Blocked',
  recon_pending: '⏳ Pending', not_computed: '— (range too wide)',
}

export default function SubmissionsTable({
  filterValue, onFilterChange, storeOptions: storeOptionsProp, marketOptions: marketOptionsProp, repOptions: repOptionsProp,
}: {
  /** When provided (by a parent that renders its OWN <StandardFilterBar>), this component skips
   *  rendering its own bar and uses the parent's filter state directly. Omit for standalone use. */
  filterValue?: StandardFilterValue
  onFilterChange?: (v: StandardFilterValue) => void
  /** Canonical option overrides (e.g. the parent's org-scoped /closing/stores + roster fetch) — win
   *  over this component's own data-derived options when given. */
  storeOptions?: string[] | EntityOption[]
  marketOptions?: string[] | EntityOption[]
  repOptions?: EntityOption[]
} = {}) {
  // Own date-range filter (independent of any parent) — string-only state throughout (no
  // `new Date(...)` round-trip), so there's no UTC off-by-one to introduce. Only used when the
  // parent doesn't drive filtering (filterValue omitted).
  const [internalFilt, setInternalFilt] = useState<StandardFilterValue>(() => ({ ...emptyStandardFilter(monthStart()), periodTo: localToday() }))
  const filt = filterValue ?? internalFilt
  const setFilt = onFilterChange ?? setInternalFilt
  const [rawRows, setRawRows] = useState<any[]>([])
  const [meta, setMeta] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  // Anti-clobber: only the LATEST in-flight request may land (the timeclock last-response-wins race
  // class — a fast filter change firing a 2nd request before the 1st's slower response arrives used to
  // let the stale one win and silently show wrong data).
  const reqRef = useRef(0)

  const load = useCallback(() => {
    const myReq = ++reqRef.current
    setLoading(true); setErr('')
    const qs = new URLSearchParams()
    if (filt.period) qs.set('date_from', filt.period)
    if (filt.periodTo) qs.set('date_to', filt.periodTo)
    api(`/api/v1/closing/submissions?${qs.toString()}`)
      .then((d: any) => { if (reqRef.current !== myReq) return; setRawRows(d?.rows || []); setMeta(d) })
      .catch((e: any) => { if (reqRef.current !== myReq) return; setErr(e?.message || String(e)); setRawRows([]); setMeta(null) })
      .finally(() => { if (reqRef.current === myReq) setLoading(false) })
  }, [filt.period, filt.periodTo])
  useEffect(() => { load() }, [load])

  // Store(s) / market / rep(s) picker options — canonical props win when the parent supplies them;
  // otherwise fall back to the already org-scoped, already date-filtered rows just loaded (pick-don't-
  // type §3b; never references data outside the tenant or the range) — the original standalone behavior.
  const acc = useMemo(() => ({
    store: (r: any) => r.store_address, market: (r: any) => r.market, rep: (r: any) => r.employee_name,
  }), [])
  const dataOpts = useMemo(() => optionsFromRows(rawRows, acc), [rawRows, acc])
  const storeOpts = storeOptionsProp ?? dataOpts.stores
  const marketOpts = marketOptionsProp ?? dataOpts.markets
  const repOpts = repOptionsProp ?? dataOpts.reps
  // A parent-supplied `storeOptions` list is CANONICAL (id = store_code, e.g. from GET
  // /closing/stores) — a different value-space than this component's own self-sourced options
  // (id = store_address, from `optionsFromRows` above). `filt.stores` therefore holds store_codes in
  // parent-driven mode. (Gate-1 finding B1, 2026-07-28: filtering everything — including the export —
  // through the shared `filterRows`'s address-keyed accessor made ANY store selection compare a code
  // against an address and match nothing, silently emptying the tab AND its exports.)
  const usingCanonicalStores = !!storeOptionsProp
  // Store/market/rep narrowing happens client-side over the server-returned (date-range-scoped) rows —
  // the SAME rows feed the table AND every export (what-you-see-is-what-exports, §3c). Market/rep go
  // through the shared, platform-core-owned `filterRows` unchanged (their canonical option ids already
  // agree with what a row carries — a market string, an employee's name). Store is handled separately
  // immediately below so it can (a) match the right value-space and (b) mirror the backend's own "an
  // unresolved store is never dropped by a store filter" rule in canonical mode — a bypass the generic
  // shared filter has no way to express selectively.
  const rows = useMemo(() => {
    const afterMarketRep = filterRows(rawRows, filt, { market: acc.market, rep: acc.rep })
    if (!filt.stores.length) return afterMarketRep
    const wanted = new Set(filt.stores.map((s: string) => s.trim().toLowerCase()))
    return afterMarketRep.filter((r: any) => {
      if (usingCanonicalStores) {
        const code = String(r.store_code || '').trim()
        if (!code) return true   // unresolved store (no store_code at all) — never dropped
        return wanted.has(code.toLowerCase())
      }
      return wanted.has(String(r.store_address || '').trim().toLowerCase())
    })
  }, [rawRows, filt, acc, usingCanonicalStores])

  const columns: ExportColumn[] = useMemo(() => [
    // ── Identity ──
    { header: 'Date', field: 'close_date', type: 'date', role: 'date', get: r => r.close_date },
    { header: 'Store', field: 'store_address', role: 'store', get: r => r.store_address },
    { header: 'Market', field: 'market', get: r => r.market },
    { header: 'Employee', field: 'employee_name', role: 'rep', get: r => r.employee_name },
    { header: 'Source', field: 'source', get: r => r.source === 'manual' ? 'In-app form' : 'Sheet upload' },
    // ── Money — per-tender declared amounts (mirrors the POS X-report vocabulary) ──
    { header: 'Cash $', field: 't_cash', money: true, get: r => r.t_cash },
    { header: 'Credit $', field: 't_credit', money: true, get: r => r.t_credit },
    { header: 'External CC $', field: 't_ext_cc', money: true, get: r => r.t_ext_cc },
    { header: 'Gift Card $', field: 't_gift', money: true, get: r => r.t_gift },
    { header: 'Store Account $', field: 't_store_acct', money: true, get: r => r.t_store_acct },
    { header: 'Zelle/CashApp $', field: 't_zelle', money: true, get: r => r.t_zelle },
    { header: 'ACIMA $', field: 't_acima', money: true, get: r => r.t_acima },
    { header: 'Custom tenders', field: 'custom_tenders', get: r => r.custom_tenders },
    { header: 'Total collected $', field: 'total_collected', money: true, get: r => r.total_collected },
    { header: 'Accessory Sale $', field: 'acc_sale', money: true, get: r => r.acc_sale },
    { header: 'ePay on Cash $', field: 'epay_on_cash', money: true, get: r => r.epay_on_cash },
    { header: 'ePay on Credit $', field: 'epay_on_credit', money: true, get: r => r.epay_on_credit },
    { header: 'ePay on ACIMA $', field: 'epay_on_acima', money: true, get: r => r.epay_on_acima },
    // ── Counts ──
    { header: 'Upgrades #', field: 'upgrade_count', type: 'number', align: 'right', get: r => r.upgrade_count },
    { header: 'New Lines #', field: 'new_line_count', type: 'number', align: 'right', get: r => r.new_line_count },
    { header: 'Postpaid #', field: 'postpaid_count', type: 'number', align: 'right', get: r => r.postpaid_count },
    { header: 'Custom counts', field: 'custom_counts', get: r => r.custom_counts },
    // ── Expense ──
    { header: 'Expense $', field: 'expense_amount', money: true, get: r => r.expense_amount },
    { header: 'Expense note', field: 'expense_description', get: r => r.expense_description },
    { header: 'Expense approved', field: 'expense_approved', get: r => r.expense_approved ? 'Yes' : 'No' },
    // ── Status (over/short + block/flag, DM verification, 3-try bookkeeping) ──
    { header: 'Gate status', field: 'gate_status', get: r => GATE_LABEL[r.gate_status] || r.gate_status },
    { header: 'Gate reason(s)', field: 'gate_reasons', get: r => (r.gate_reasons || []).join('; ') },
    { header: 'Attempts', field: 'attempts', type: 'number', align: 'right', get: r => r.attempts },
    { header: 'Sent to review', field: 'auto_accepted', get: r => r.auto_accepted ? 'Yes — 3rd try, still mismatched' : 'No' },
    { header: 'Released for correction', field: 'released_at', get: r => r.released_at ? `Yes — by ${r.released_by || '—'}` : 'No' },
    { header: 'Corrections', field: 'correction_count', type: 'number', align: 'right', get: r => r.correction_count },
    { header: 'DM verified', field: 'dm_verified', get: r => r.dm_verified ? 'Yes' : 'No' },
    { header: 'DM verified by', field: 'dm_verified_by', get: r => r.dm_verified_by },
    { header: 'DM verified at', field: 'dm_verified_at', type: 'date', get: r => r.dm_verified_at ? new Date(r.dm_verified_at).toLocaleString() : '' },
    // ── Meta ──
    // A reference only (storage path) — never the image itself, and never a signed URL that could
    // outlive its 1-hour validity inside a shared export file. In-app photo viewing stays on the
    // existing /closing/verify and /closing/management pages, unchanged.
    { header: 'Envelope photo ref', field: 'envelope_picture', get: r => r.envelope_picture || '' },
    { header: 'Remarks', field: 'remarks', get: r => r.remarks },
    { header: 'Submitted at', field: 'submitted_at', type: 'date', get: r => r.submitted_at ? new Date(r.submitted_at).toLocaleString() : '' },
  ], [])

  return (
    <div>
      {!filterValue && (
        <StandardFilterBar
          value={filt} onChange={setFilt}
          periodMode="range"
          storeOptions={storeOpts} marketOptions={marketOpts} repOptions={repOpts}
          storeLabel="Stores…" marketLabel="Markets…" repLabel="Employees…"
        />
      )}

      {(meta?.status_capped || meta?.truncated) && (
        <div className="card" style={{ padding: '8px 12px', marginBottom: 10, fontSize: 12, background: '#fff8e6', border: '1px solid #f3d98b' }}>
          {meta?.truncated && <div>⚠️ This range has more submissions than can be loaded at once — narrow the date range to see everything.</div>}
          {meta?.status_capped && <div>ℹ️ Gate status was computed for the {meta.status_dates_computed} most recent day(s) in range (of {meta.status_dates_total}) — widen carefully, older days show “not computed” rather than a guess.</div>}
        </div>
      )}
      {meta && meta.can_review === false && (
        <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 10 }}>
          ℹ️ Detailed over/short dollar reasons are visible to company-wide roles only — you see the status badge.
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : err ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {err}</div>
      ) : (
        <ReportShell
          title="Daily Closing — All Submissions"
          subtitle={`${filt.period || '—'} → ${filt.periodTo || '—'}`}
          filename={`daily-closing-submissions_${filt.period || 'start'}_${filt.periodTo || 'end'}`}
          columns={columns} rows={rows} stickyHeader totals
        />
      )}
    </div>
  )
}
