'use client'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, fmt, getActiveOrg } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import { hasDataGrant } from '@/lib/rbac'
import ReportShell from '@/components/ReportShell'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import EntityPicker, { type EntityOption } from '@/components/EntityPicker'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'

// IMEI ↔ REBATE reconciliation (owner request 2026-07-28: "one more exclusive report which shows the IMEI
// activated and the rebate received against it").
//
// CARRIER- AND TENANT-AGNOSTIC: the backend resolves the source by WHICH DATA EXISTS for the org — the
// master-agent / VidaPay per-activation feed, or the ePay payment-detail rebate classes against B2B sales
// / residual activations — never by a tenant or carrier name. An org with both gets the union, tagged.
//
// The point of the report is the GAPS: an activation with NO rebate against it is a first-class row with
// its own tile and quick facet, not an absent row. The inverse (a rebate whose IMEI has no activation
// here) is the collapsed data-quality section at the bottom.
//
// ACCESS: this report has NO DEFAULT ACCESS (owner directive 2026-07-29). It is gated by the
// 'imei_rebates' DATA_GRANT — super-admins / company-wide ('all') roles / admins pass; everyone else
// needs the grant on their role. The BACKEND is the enforcement (`_require_imei_rebates` → 403 before a
// single row is read); `hasDataGrant` here is the frontend MIRROR, so an ungranted user sees the lock
// note instead of firing a request that can only fail. Because the mirror is optimistic while
// permissions are still loading (an empty perms object reads as scope 'all'), the 403 itself is ALSO
// handled — it renders the same lock note rather than a raw red error string.
//
// RULE FOUR: ReportShell brings Excel / PDF / Print + Send (email & WhatsApp) over the rows on screen.
// RULE FIVE: <StandardFilterBar> carries the core set (period · stores · market · reps); the appended
// facets (rebate status / activation type / platform / financed / feed) are pick-don't-type over the
// values PRESENT IN THE DATA. Every filter is applied SERVER-side so the tiles, the table and the export
// can never disagree.

// Super-admin org resolution: a super-admin is not rewritten by the tenant middleware, so an org-less read
// would default to the house org and a tenant's report would look empty. Same targeted mitigation the
// Sales Report uses until the universal client.ts fix lands; a no-op for everyone else.
const orgParam = () => { const o = getActiveOrg(); return o ? `&org_id=${encodeURIComponent(o)}` : '' }

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const lbl: React.CSSProperties = { fontSize: 12, color: 'var(--text2)', display: 'inline-flex', alignItems: 'center', gap: 5 }
const tile: React.CSSProperties = { flex: 1, minWidth: 165, border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px' }
const tileCap: React.CSSProperties = { fontSize: 11, color: 'var(--text3)', fontWeight: 700, textTransform: 'uppercase' }
const th: React.CSSProperties = { textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }
const td: React.CSSProperties = { padding: '5px 8px', fontSize: 13 }

const STATUS_TINT: Record<string, string> = { none: '#b91c1c', partial: '#b45309', received: '#15803d' }
const STATUS_ICON: Record<string, string> = { none: '⛔', partial: '⚠️', received: '✅' }

function thisMonth() { return new Date().toISOString().slice(0, 7) }

type Row = any

// The backend 403 detail names the grant key verbatim ('imei_rebates'); client.ts `api()` throws an
// Error carrying only that detail string (the status code is not preserved), so the key IS the signal.
const isGateError = (m: string) => /imei_rebates/i.test(m) || /restricted/i.test(m)

function LockNote() {
  return (
    <div className="card" style={{ padding: 18, marginTop: 14, fontSize: 13, lineHeight: 1.7,
      background: 'var(--surface2, #f8fafc)', border: '1px solid var(--border)' }}>
      <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6 }}>🔒 This report is restricted</div>
      Ask an admin to grant <b>“IMEI rebate reconciliation”</b> on your role
      (Roles &amp; Access → your role → sensitive data grants). This report has <b>no default access</b>:
      the per-IMEI activation and rebate detail is restricted for everyone until it is explicitly granted.
      <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8 }}>
        Nothing is wrong with your login — administrators and company-wide roles already have it.
      </div>
    </div>
  )
}

export default function ImeiRebatesPage() {
  const { permissions } = useAuth()
  // Frontend MIRROR of backend `_can_view_imei_rebates`. Optimistic while permissions load; the 403
  // below is the authoritative lock.
  const clientGranted = hasDataGrant(permissions, 'imei_rebates')
  const [locked, setLocked] = useState(false)
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter(thisMonth()))
  const [lag, setLag] = useState(6)
  const [basis, setBasis] = useState('both')
  const [status, setStatus] = useState<string[]>([])
  const [actType, setActType] = useState<string[]>([])
  const [platform, setPlatform] = useState<string[]>([])
  const [financed, setFinanced] = useState<string[]>([])
  const [feed, setFeed] = useState<string[]>([])
  const [sort, setSort] = useState('gaps')
  const [d, setD] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [showOrphans, setShowOrphans] = useState(false)

  const load = useCallback(async () => {
    if (!clientGranted) { setLocked(true); setBusy(false); return }   // no grant → don't fire a doomed request
    setBusy(true); setMsg('')
    try {
      const qs = new URLSearchParams({ period: filt.period || thisMonth(), lag_months: String(lag), basis })
      if (filt.stores.length) qs.set('stores', filt.stores.join(','))
      if (filt.reps.length) qs.set('reps', filt.reps.join(','))
      if (filt.markets.length) qs.set('markets', filt.markets.join(','))
      if (status.length) qs.set('status', status.join(','))
      if (actType.length) qs.set('activation_type', actType.join(','))
      if (platform.length) qs.set('platform', platform.join(','))
      if (financed.length) qs.set('financed', financed.join(','))
      if (feed.length) qs.set('source', feed.join(','))
      setD(await api(`/api/v1/commcalc/imei-rebates?${qs.toString()}${orgParam()}`))
    } catch (e: any) {
      const m = String(e?.message || e)
      // A permission refusal is not an error to shout about — show the same lock note, no crash.
      if (isGateError(m)) { setLocked(true); setD(null) } else setMsg('❌ ' + m)
    }
    setBusy(false)
  }, [filt, lag, basis, status, actType, platform, financed, feed, clientGranted])

  useEffect(() => { load() }, [])            // eslint-disable-line react-hooks/exhaustive-deps
  const didMount = useRef(false)
  useEffect(() => {
    if (!didMount.current) { didMount.current = true; return }
    load()
  }, [load])

  // Pick-don't-type options, computed by the backend from the UNFILTERED rows so a picker never collapses
  // to the current selection.
  const storeOpts: EntityOption[] = (d?.store_options || []).map((s: string) => ({ id: s, label: s }))
  const repOpts: EntityOption[] = (d?.rep_options || []).map((s: string) => ({ id: s, label: s }))
  const marketOpts: EntityOption[] = (d?.market_options || []).map((s: string) => ({ id: s, label: s }))
  const statusOpts: EntityOption[] = (d?.status_options || []).map((o: any) => ({ id: o.id, label: o.label }))
  const atOpts: EntityOption[] = (d?.activation_type_options || []).map((s: string) => ({ id: s, label: s }))
  const platOpts: EntityOption[] = (d?.platform_options || []).map((s: string) => ({ id: s, label: s }))
  const finOpts: EntityOption[] = (d?.financed_options || []).map((s: string) => ({ id: s, label: s }))
  const feedOpts: EntityOption[] = (d?.source_options || []).map((s: string) =>
    ({ id: s, label: s === 'ma' ? 'Master-agent feed' : 'ePay feed' }))

  const gated = !!d?.money_gated
  const hasMa = (d?.sources || []).includes('ma')
  const rows: Row[] = d?.rows || []
  const sorted = useMemo(() => {
    const r = [...rows]
    if (sort === 'date') r.sort((a, b) => String(a.activation_date || '').localeCompare(String(b.activation_date || '')))
    else if (sort === 'rebate') r.sort((a, b) => (b.rebate || 0) - (a.rebate || 0))
    else if (sort === 'store') r.sort((a, b) => String(a.store_label || '').localeCompare(String(b.store_label || '')))
    // 'gaps' = the server's own order (partial, then no-rebate, then received) — left untouched.
    return r
  }, [rows, sort])

  // RULE FOUR columns. Money columns are OMITTED entirely when the caller lacks the carrier-residual
  // grant, so a gated $ cannot leak through Excel/PDF/Send either.
  const cols: ExportColumn[] = [
    { header: 'IMEI', field: 'imei', get: (r: Row) => r.imei },
    { header: 'Activated', field: 'activation_date', type: 'date', role: 'date', get: (r: Row) => r.activation_date || '' },
    { header: 'Activation type', field: 'activation_type', get: (r: Row) => [r.activation_type, r.activation_type2].filter(Boolean).join(' · ') },
    { header: 'Device / SKU', field: 'device', get: (r: Row) => r.device || r.sku || '' },
    { header: 'Store', field: 'store', role: 'store', get: (r: Row) => r.store_label || r.store || '' },
    { header: 'Market', field: 'market', get: (r: Row) => r.market || '' },
    { header: 'Rep', field: 'rep', role: 'rep', get: (r: Row) => r.rep || '' },
    { header: 'Rebate status', field: 'rebate_status_label', get: (r: Row) => r.rebate_status_label },
    ...(gated ? [] : [{ header: 'Rebate', field: 'rebate', money: true, get: (r: Row) => r.rebate } as ExportColumn]),
    { header: 'Rebate date', field: 'rebate_date', type: 'date', get: (r: Row) => r.rebate_date || '' },
    { header: 'Rebate source', field: 'rebate_source', get: (r: Row) => r.rebate_label || r.rebate_source || '' },
    ...(hasMa && !gated ? [{ header: 'Spiffs M1–M6', field: 'spiff_total', money: true, get: (r: Row) => r.spiff_total } as ExportColumn] : []),
    ...(gated ? [] : [{ header: 'Other paid', field: 'other_paid', money: true, get: (r: Row) => r.other_paid } as ExportColumn]),
    ...(gated ? [] : [{ header: 'Total received', field: 'total_received', money: true, get: (r: Row) => r.total_received } as ExportColumn]),
    { header: 'Why', field: 'rebate_status_reason', get: (r: Row) => r.rebate_status_reason || '' },
    { header: 'Feed', field: 'source', get: (r: Row) => (r.sources || [r.source]).join(' + ') },
    { header: 'Evidence', field: 'evidence', get: (r: Row) => (r.evidence || []).join(' + ') },
    ...(platOpts.length ? [{ header: 'Platform', field: 'platform', get: (r: Row) => r.platform || '' } as ExportColumn] : []),
    ...(finOpts.length ? [{ header: 'Financed', field: 'financed', get: (r: Row) => r.financed || '' } as ExportColumn] : []),
  ]

  const orphanCols: ExportColumn[] = [
    { header: 'IMEI', get: (o: any) => o.imei },
    { header: 'Rebate date', type: 'date', get: (o: any) => o.date || '' },
    { header: 'Period', get: (o: any) => o.period || '' },
    { header: 'Store', role: 'store', get: (o: any) => o.store || '' },
    { header: 'Rep', role: 'rep', get: (o: any) => o.rep || '' },
    { header: 'Payment type', get: (o: any) => o.label || '' },
    ...(gated ? [] : [{ header: 'Amount', money: true, get: (o: any) => o.amount } as ExportColumn]),
    { header: 'Source', get: (o: any) => o.source || '' },
  ]

  const t = d?.tiles
  const quick = (id: string) => setStatus(status.length === 1 && status[0] === id ? [] : [id])

  const header = (
    <div style={{ marginBottom: 14 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📱 IMEI Rebate Reconciliation</h1>
      <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
        Every IMEI activated in the month and the rebate recorded against it — and, just as importantly,
        the ones with <b>no rebate</b>. The source is whichever activation + rebate feed this tenant
        actually has; nothing here changes what anyone is paid.
      </p>
    </div>
  )

  // NO DEFAULT ACCESS: without the grant the report is not rendered at all — not the filters, not the
  // tiles, not the counts, not the IMEIs. (The backend refuses independently.)
  if (locked) {
    return <div style={{ maxWidth: 1280 }}>{header}<LockNote /></div>
  }

  return (
    <div style={{ maxWidth: 1280 }}>
      {header}

      {/* RULE FIVE core set + the appended module facets. */}
      <StandardFilterBar
        value={filt} onChange={setFilt} periodMode="month"
        storeOptions={storeOpts} repOptions={repOpts} marketOptions={marketOpts}
        storeLabel="Stores / accounts…" repLabel="Reps…"
        right={<>
          <label style={lbl}>Rebate window
            <select style={sel} value={lag} onChange={e => setLag(Number(e.target.value))}>
              {[0, 1, 2, 3, 6, 9, 12].map(n => <option key={n} value={n}>{n === 0 ? 'same month only' : `+${n} mo`}</option>)}
            </select>
          </label>
          <label style={lbl}>Activation basis
            <select style={sel} value={basis} onChange={e => setBasis(e.target.value)}>
              <option value="both">sales + residual</option>
              <option value="sales">POS sales only</option>
              <option value="residual">residual only</option>
            </select>
          </label>
          {statusOpts.length > 0 && (
            <EntityPicker multi options={statusOpts} value={status} onChange={setStatus}
              placeholder="Rebate status…" width={165} ariaLabel="Filter by rebate status" />
          )}
          {atOpts.length > 0 && (
            <EntityPicker multi options={atOpts} value={actType} onChange={setActType}
              placeholder="Activation type…" width={170} ariaLabel="Filter by activation type" />
          )}
          {platOpts.length > 0 && (
            <EntityPicker multi options={platOpts} value={platform} onChange={setPlatform}
              placeholder="Platform…" width={140} ariaLabel="Filter by platform" />
          )}
          {finOpts.length > 0 && (
            <EntityPicker multi options={finOpts} value={financed} onChange={setFinanced}
              placeholder="Financed…" width={130} ariaLabel="Filter by financed" />
          )}
          {feedOpts.length > 1 && (
            <EntityPicker multi options={feedOpts} value={feed} onChange={setFeed}
              placeholder="Feed…" width={165} ariaLabel="Filter by source feed" />
          )}
          <label style={lbl}>Sort
            <select style={sel} value={sort} onChange={e => setSort(e.target.value)}>
              <option value="gaps">gaps first</option>
              <option value="date">activation date</option>
              <option value="rebate">rebate $ (high→low)</option>
              <option value="store">store</option>
            </select>
          </label>
          <button className="btn btn-secondary" style={{ fontSize: 12 }} disabled={busy} onClick={() => load()}>
            {busy ? '…' : '↻ Reload'}
          </button>
        </>}
      />

      {msg && <div className="card" style={{ padding: 12, marginBottom: 12, fontSize: 13 }}>{msg}</div>}

      {d?.ready && <>
        {/* Tiles — the GAP bucket is a first-class headline, not a footnote. */}
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
          <div style={tile}>
            <div style={tileCap}>Activations</div>
            <div style={{ fontSize: 22, fontWeight: 800 }}>{t.activations}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>{d.period} · {d.source === 'none' ? 'no feed' : d.source} feed</div>
          </div>
          <button onClick={() => quick('none')} style={{ ...tile, textAlign: 'left', cursor: 'pointer',
            background: status.includes('none') ? 'var(--surface2)' : 'transparent',
            borderColor: t.no_rebate.count ? '#fca5a5' : 'var(--border)' }}>
            <div style={tileCap}>⛔ No rebate</div>
            <div style={{ fontSize: 22, fontWeight: 800, color: t.no_rebate.count ? STATUS_TINT.none : undefined }}>{t.no_rebate.count}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>
              {gated ? 'amounts hidden for your role'
                : t.no_rebate.estimated_amount != null ? `≈ ${fmt(t.no_rebate.estimated_amount)} exposure (estimate)` : (t.no_rebate.estimate_basis ? 'no basis to estimate' : 'none')}
            </div>
          </button>
          <button onClick={() => quick('partial')} style={{ ...tile, textAlign: 'left', cursor: 'pointer',
            background: status.includes('partial') ? 'var(--surface2)' : 'transparent',
            borderColor: t.partial.count ? '#fcd34d' : 'var(--border)' }}>
            <div style={tileCap}>⚠️ Partial / mismatch</div>
            <div style={{ fontSize: 22, fontWeight: 800, color: t.partial.count ? STATUS_TINT.partial : undefined }}>{t.partial.count}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>{gated ? '—' : `${fmt(t.partial.amount)} net`}</div>
          </button>
          <button onClick={() => quick('received')} style={{ ...tile, textAlign: 'left', cursor: 'pointer',
            background: status.includes('received') ? 'var(--surface2)' : 'transparent' }}>
            <div style={tileCap}>✅ Rebate received</div>
            <div style={{ fontSize: 22, fontWeight: 800, color: STATUS_TINT.received }}>{t.with_rebate.count}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>{gated ? '—' : fmt(t.with_rebate.amount)}</div>
          </button>
          <div style={tile}>
            <div style={tileCap}>Total received</div>
            <div style={{ fontSize: 22, fontWeight: 800 }}>{gated ? '—' : fmt(t.total_received)}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>
              {gated ? 'hidden' : <>rebate {fmt(t.rebate_total)}{hasMa && <> · spiffs {fmt(t.spiff_total)}</>} · other {fmt(t.other_total)}</>}
            </div>
          </div>
        </div>

        {t.no_rebate.estimate_basis && !gated && (
          <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10 }}>
            Exposure basis: {t.no_rebate.estimate_basis}.
          </div>
        )}

        {/* What the report MEANS — stated on the page and carried into every export subtitle. */}
        <div className="card" style={{ padding: '10px 14px', marginBottom: 12, fontSize: 12, color: 'var(--text2)', lineHeight: 1.6 }}>
          <div><b>Definition.</b> {d.definition_note}</div>
          {d.window_note && <div><b>Rebate window.</b> {d.window_note}</div>}
          {d.sign_note && <div><b>Sign.</b> {d.sign_note}.</div>}
        </div>

        {d.note && (
          <div className="card" style={{ padding: 12, marginBottom: 12, fontSize: 13, background: '#fffbeb', border: '1px solid #fde68a' }}>
            ⚠️ {d.note}
          </div>
        )}
        {d.truncated && (
          <div style={{ fontSize: 12, color: '#b45309', marginBottom: 8 }}>
            Showing the first {rows.length} of {d.total_rows} matching activations — the tiles above still
            describe all {d.total_rows}. Narrow the filters to see the rest.
          </div>
        )}

        <ReportShell
          title={`IMEI Rebate Reconciliation — ${d.period}`}
          subtitle={`${rows.length} activation(s) · ${d.definition_note}${d.sign_note ? ` · ${d.sign_note}` : ''}`}
          filename={`imei-rebates-${String(d.period).replace(/\s+/g, '-')}`}
          columns={cols}
          rows={sorted}
          totals
          stickyHeader
          rowStyle={(r: Row) => (r.rebate_status === 'none' ? { background: 'rgba(239,68,68,0.06)' }
            : r.rebate_status === 'partial' ? { background: 'rgba(245,158,11,0.07)' } : undefined)}
        />

        {/* The INVERSE gap — collapsed by default; a data-quality signal, not an accusation. */}
        {d.orphans?.length > 0 && (
          <div className="card" style={{ padding: 0, marginTop: 14 }}>
            <button onClick={() => setShowOrphans(v => !v)}
              style={{ width: '100%', textAlign: 'left', padding: '10px 14px', background: 'transparent', border: 0, cursor: 'pointer', fontWeight: 700, fontSize: 13 }}>
              {showOrphans ? '▾' : '▸'} Rebates with no matching activation ({d.orphans.length}
              {gated ? '' : ` · ${fmt(t.orphan.amount)}`})
            </button>
            {showOrphans && (
              <div style={{ borderTop: '1px solid var(--border)' }}>
                <div style={{ padding: '8px 14px', fontSize: 12, color: 'var(--text3)' }}>
                  {d.orphan_note}
                  <span style={{ float: 'right' }}>
                    <ReportExportBar title={`IMEI rebates without an activation — ${d.period}`}
                      filename={`imei-rebates-orphans-${String(d.period).replace(/\s+/g, '-')}`}
                      columns={orphanCols} rows={d.orphans} />
                  </span>
                </div>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead><tr style={{ background: 'var(--surface2)' }}>
                    {['IMEI', 'Rebate date', 'Period', 'Store', 'Rep', 'Payment type', 'Amount'].map(h => <th key={h} style={th}>{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {d.orphans.map((o: any) => (
                      <tr key={o.imei} style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={{ ...td, fontFamily: 'ui-monospace, monospace' }}>{o.imei}</td>
                        <td style={td}>{o.date || '—'}</td>
                        <td style={td}>{o.period || '—'}</td>
                        <td style={td}>{o.store || '—'}</td>
                        <td style={td}>{o.rep || '—'}</td>
                        <td style={td}>{o.label || '—'}</td>
                        <td style={{ ...td, fontWeight: 600 }}>{gated ? '—' : fmt(o.amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {d.orphan_truncated && (
                  <div style={{ padding: '8px 14px', fontSize: 12, color: '#b45309' }}>
                    Only the first 1,000 are listed.
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 14, lineHeight: 1.6 }}>
          Status key: <b style={{ color: STATUS_TINT.received }}>{STATUS_ICON.received} Received</b> — a rebate
          line credits this IMEI and nothing reverses it. <b style={{ color: STATUS_TINT.none }}>{STATUS_ICON.none} No
          rebate</b> — no rebate line exists against it in the window (the gap this report exists to
          surface). <b style={{ color: STATUS_TINT.partial }}>{STATUS_ICON.partial} Partial / mismatch</b> — rebate
          lines exist but the money does not stand up: partly or wholly reversed, or net negative. Nothing on
          this page changes anyone&apos;s pay; it reports what the carrier/processor already recorded.
        </div>
      </>}

      {!d && !busy && !msg && <div className="card" style={{ padding: 14, fontSize: 13 }}>Loading…</div>}
    </div>
  )
}
