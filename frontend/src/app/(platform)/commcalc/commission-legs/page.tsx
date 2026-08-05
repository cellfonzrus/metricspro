'use client'
import { useEffect, useState, useCallback, useMemo, Fragment } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import StandardFilterBar from '@/components/StandardFilterBar'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import { TrendChart } from '@/components/TrendChart'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'

// COMMISSION LEGS — what we made, by month-of-life (owner directives 2026-08-04 + 2026-08-05)
//
//   2026-08-04: "1st Month commission which is paid the same month of the activation and the other is
//    M2-M12 commission, any commission received for an activated number after the activated month will
//    be in this category."
//   2026-08-05: "we need to see what we made in M1 and other months and how much is on ATU and how
//    much is on residual."
//
// TWO TABS, one job each:
//   💰 What we made  — the BREAKOUT: every stream of carrier money (commission, comprehensive comp,
//                      MI, ATU, VidaPay airtime margin) split into M1, M2, M3 … individually, plus
//                      Unsplit, with a month-over-month trend. Read-only.
//   🧩 Map a label   — the admin surface that RESOLVES what the carrier files leave ambiguous. Most
//                      carrier labels name their own month ("New Activation Bounty - Month 3") and are
//                      attributed automatically; the ones that don't sit in Unsplit until a human
//                      decides, because guessing would silently move money between two columns the
//                      owner reads. Behaviour here is unchanged from what shipped.
//
// Reporting only. Nothing on this page changes what anybody is PAID — it changes which report column a
// dollar the company already received is displayed in.

type Row = {
  label: string; amount: number; lines: number; sources: string[]; categories: string[]
  bucket: string; leg_month: number | null; why: string; overridden: boolean; override_note: string
}
type Cell = { legs: Record<string, number>; m1: number; m2_12: number; unsplit: number; total: number; lines: number }
type Stream = {
  key: string; label: string; group: string; in_total: boolean; source: string; splits_on: string
  periods: Record<string, Cell>
  legs: Record<string, number>; m1: number; m2_12: number; unsplit: number; total: number
  meta?: { unsplit_fields?: string[]; airtime_on_residual_orders?: number }
}
type GroupTotal = {
  group: string; label: string; by_period: Record<string, number>
  legs: Record<string, number>; m1: number; m2_12: number; unsplit: number; total: number
}

const LEG_LABEL: Record<string, string> = { m1: '1st Month', trailing: 'M2–M12', unsplit: 'Unsplit' }
const LEG_TINT: Record<string, string> = { m1: '#065f46', trailing: '#1d4ed8', unsplit: '#9a3412' }
const WHY_TEXT: Record<string, string> = {
  label_override: 'you set this label explicitly',
  month_in_label: 'the label names its own month',
  no_month_in_label: 'the label never states a month — nothing was guessed',
  activation_date: "the subscriber's activation date",
  no_activation_date: 'no usable activation date on the line',
  activation_split_disabled: 'residual splitting is switched off for this org',
}
const GROUP_TINT: Record<string, string> = {
  commission: '#065f46', comp: '#6d28d9', residual: '#1d4ed8', reference: '#9a3412',
}
const inp: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const th: React.CSSProperties = { padding: '5px 8px', fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', textAlign: 'right', whiteSpace: 'nowrap' }
const thL: React.CSSProperties = { ...th, textAlign: 'left' }
const td: React.CSSProperties = { padding: '5px 8px', fontSize: 12.5, textAlign: 'right', whiteSpace: 'nowrap' }
const tdL: React.CSSProperties = { ...td, textAlign: 'left' }
const shortP = (p: string) => (p || '').replace(/^(\w{3})\w*\s(\d{4})$/, '$1 $2')
const legHead = (k: string) => (k === 'unsplit' ? 'Unsplit' : `M${k}`)

export default function CommissionLegsPage() {
  const { period } = usePeriod()
  const [tab, setTab] = useState<'made' | 'map'>('made')

  // ── tab 1: the breakout ──────────────────────────────────────────────────────────────────────
  const [bo, setBo] = useState<any>(null)
  const [boLoading, setBoLoading] = useState(true)
  const [win, setWin] = useState(6)
  const [pivot, setPivot] = useState<'legs' | 'months'>('legs')
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())

  // ── tab 2: the label map (unchanged) ─────────────────────────────────────────────────────────
  const [rows, setRows] = useState<Row[]>([])
  const [cfg, setCfg] = useState<any>(null)
  const [resolved, setResolved] = useState<any>(null)
  const [ready, setReady] = useState(true)
  const [mapReady, setMapReady] = useState(true)
  const [unsplitTotal, setUnsplitTotal] = useState(0)
  const [months, setMonths] = useState(6)
  const [onlyUnsplit, setOnlyUnsplit] = useState(false)
  const [q, setQ] = useState('')
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(true)

  const marketsKey = filt.markets.join(',')
  const storesKey = filt.stores.join(',')

  const loadBreakout = useCallback(async () => {
    setBoLoading(true)
    try {
      const qs = new URLSearchParams({ period, months: String(win), org_id: ORG_ID })
      if (marketsKey) qs.set('market', marketsKey)
      if (storesKey) qs.set('store', storesKey)
      setBo(await api(`/api/v1/commcalc/commission-received-breakout?${qs.toString()}`))
    } catch (e: any) { setBo(null); setMsg(e?.message || 'Load failed') }
    finally { setBoLoading(false) }
  }, [period, win, marketsKey, storesKey])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const d = await api(`/api/v1/commcalc/commission-leg-labels?period=${encodeURIComponent(period)}&months=${months}&org_id=${ORG_ID}`)
      setRows(d?.labels || []); setResolved(d?.config || null)
      setReady(d?.rollup_ready !== false); setMapReady(d?.map_ready !== false)
      setUnsplitTotal(d?.unsplit_total || 0)
    } catch (e: any) { setMsg(e?.message || 'Load failed') }
    finally { setLoading(false) }
  }, [period, months])

  useEffect(() => { loadBreakout() }, [loadBreakout])
  useEffect(() => { if (tab === 'map') load() }, [tab, load])
  useEffect(() => { api(`/api/v1/commcalc/commission-leg-config?org_id=${ORG_ID}`).then(setCfg).catch(() => setCfg(null)) }, [])

  function flash(m: string) { setMsg(m); setTimeout(() => setMsg(''), 4000) }

  async function setBucket(label: string, bucket: string) {
    try {
      await api(`/api/v1/commcalc/commission-leg-labels?org_id=${ORG_ID}`, {
        method: 'POST', body: JSON.stringify({ label, bucket }),
      })
      flash(bucket ? `“${label}” → ${LEG_LABEL[bucket]}` : `“${label}” back to automatic`)
      load()
    } catch (e: any) { flash(e?.message || 'Save failed — is migration 274 applied?') }
  }

  const shown = rows.filter(r =>
    (!onlyUnsplit || r.bucket === 'unsplit') &&
    (!q.trim() || r.label.toLowerCase().includes(q.trim().toLowerCase())))
  const tot = (b: string) => rows.filter(r => r.bucket === b).reduce((s, r) => s + (r.amount || 0), 0)

  // ── breakout derivations ─────────────────────────────────────────────────────────────────────
  const streams: Stream[] = useMemo(() => bo?.streams || [], [bo])
  const periods: string[] = useMemo(() => bo?.periods || [], [bo])
  const groups: GroupTotal[] = useMemo(() => bo?.groups || [], [bo])
  const legCols: string[] = useMemo(() => {
    const ns = (bo?.leg_columns || []).map((n: number) => String(n))
    return streams.some(s => s.legs?.unsplit) ? [...ns, 'unsplit'] : ns
  }, [bo, streams])
  const refStreams = streams.filter(s => !s.in_total)
  const airtime = streams.find(s => s.key === 'ma_airtime')
  const namedUnsplit = streams.find(s => (s.meta?.unsplit_fields || []).length > 0)

  const trendData = periods.map(p => {
    const row: any = { name: shortP(p) }
    groups.forEach(g => { row[g.group] = g.by_period?.[p] || 0 })
    const comm = streams.filter(s => s.in_total && s.group === 'commission')
    row.m1 = comm.reduce((a, s) => a + (s.periods[p]?.m1 || 0), 0)
    row.m2_12 = comm.reduce((a, s) => a + (s.periods[p]?.m2_12 || 0), 0)
    return row
  })

  // RULE FOUR — what you see is what exports. Both pivots ship, each as its own sheet.
  const legSheetCols: ExportColumn[] = [
    { header: 'Money', field: 'label', get: (r: any) => r.label },
    { header: 'Group', field: 'group', get: (r: any) => r.group },
    ...legCols.map(k => ({ header: legHead(k), field: `leg_${k}`, money: true, get: (r: any) => r[`leg_${k}`] || 0 })),
    { header: '1st Month', field: 'm1', money: true, get: (r: any) => r.m1 },
    { header: 'M2–M12', field: 'm2_12', money: true, get: (r: any) => r.m2_12 },
    { header: 'Total', field: 'total', money: true, get: (r: any) => r.total },
  ]
  const legSheetRows = streams.map(s => ({
    label: s.in_total ? s.label : `${s.label} (cross-check — in no total)`,
    group: bo?.group_labels?.[s.group] || s.group,
    ...Object.fromEntries(legCols.map(k => [`leg_${k}`, s.legs?.[k] || 0])),
    m1: s.m1, m2_12: s.m2_12, total: s.total,
  }))
  const monthSheetCols: ExportColumn[] = [
    { header: 'Money', field: 'label', get: (r: any) => r.label },
    { header: 'Group', field: 'group', get: (r: any) => r.group },
    ...periods.map(p => ({ header: p, field: `p_${p}`, money: true, get: (r: any) => r[`p_${p}`] || 0 })),
    { header: 'Total', field: 'total', money: true, get: (r: any) => r.total },
  ]
  const monthSheetRows = streams.map(s => ({
    label: s.in_total ? s.label : `${s.label} (cross-check — in no total)`,
    group: bo?.group_labels?.[s.group] || s.group,
    ...Object.fromEntries(periods.map(p => [`p_${p}`, s.periods[p]?.total || 0])),
    total: s.total,
  }))

  return (
    <div style={{ padding: 24, maxWidth: 1400 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧩 Commission Legs — what we made, by month</h1>
        <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 999, background: 'rgba(34,197,94,.12)', color: '#16a34a' }}>
          READ-ONLY · changes no pay
        </span>
      </div>
      <p style={{ color: 'var(--text2)', fontSize: 13, margin: '8px 0 12px', maxWidth: 980, lineHeight: 1.6 }}>
        Every dollar of carrier money the company receives belongs to a <b>month of life</b> of the number it was
        paid on. <b>M1</b> means it arrived in the same month the number activated; <b>M2, M3 …</b> mean it arrived
        that many months later, for a number that was already active. Money whose source never states a month sits
        in <b>Unsplit</b> — nothing is ever guessed.
      </p>

      <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
        {([['made', '💰 What we made'], ['map', '🧩 Map a label’s leg']] as const).map(([k, l]) => (
          <button key={k} className={tab === k ? 'btn' : 'btn btn-secondary'} style={{ fontSize: 13 }}
            onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>

      {msg && <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>{msg}</div>}

      {/* ═══════════════ TAB 1 — WHAT WE MADE ═══════════════ */}
      {tab === 'made' && (
        <>
          <StandardFilterBar
            value={filt}
            onChange={setFilt}
            periodMode="none"
            show={{ period: false, stores: true, markets: true, reps: false }}
            optionsUrl={`/api/v1/core/filter-options?org_id=${ORG_ID}`}
            right={
              <>
                {/* The month itself comes from the app-wide period picker in the header — the same one
                    every commcalc report uses. This control sets how far BACK from it the trend reaches.
                    "Rep" is not offered: carrier money arrives per STORE or company-wide, never per rep. */}
                <label style={{ fontSize: 12, color: 'var(--text2)' }}>Window</label>
                <select style={inp} value={win} onChange={e => setWin(Number(e.target.value))}>
                  {[3, 6, 12, 24].map(m => <option key={m} value={m}>last {m} months to {period}</option>)}
                </select>
                <select style={inp} value={pivot} onChange={e => setPivot(e.target.value as 'legs' | 'months')}>
                  <option value="legs">Columns: M1, M2, M3 …</option>
                  <option value="months">Columns: month received</option>
                </select>
                <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={loadBreakout}>↻ Refresh</button>
                <ReportExportBar
                  title={`Commission received by leg — ${periods[0] || ''} to ${periods[periods.length - 1] || period}`}
                  subtitle={bo?.basis}
                  filename={`commission_received_legs_${(period || '').replace(/\W+/g, '_').toLowerCase()}`}
                  sheets={[
                    { name: 'By month of life', columns: legSheetCols, rows: legSheetRows },
                    { name: 'By month received', columns: monthSheetCols, rows: monthSheetRows },
                  ]}
                />
              </>
            }
          />

          {boLoading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><div className="spinner" /></div>
          ) : !bo || !streams.length ? (
            <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 16, fontSize: 13, color: 'var(--text2)', lineHeight: 1.7 }}>
              No carrier money in this window yet. Import a commission file for {period} — or, if this tenant is
              paid only through its Commission Plans, this is correct and there is nothing to show here.
              {(bo?.gaps || []).length > 0 && (
                <ul style={{ marginTop: 8 }}>
                  {(bo.gaps || []).map((g: any) => (
                    <li key={g.stream}><b>{g.what}</b> — {g.why} Import it via {g.how}.</li>
                  ))}
                </ul>
              )}
            </div>
          ) : (
            <>
              {bo.identity_ok === false && (
                <div style={{ background: '#fef2f2', border: '1px solid #fca5a5', color: '#991b1b', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12, fontWeight: 600 }}>
                  ⚠ A row&apos;s parts do not add back to its own total — treat this split as unreliable and report it.
                </div>
              )}
              {(bo.notes || []).map((n: string) => (
                <div key={n} style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '8px 12px', fontSize: 12.5, marginBottom: 10, lineHeight: 1.6 }}>⚠ {n}</div>
              ))}
              {(bo.gaps || []).map((g: any) => (
                <div key={g.stream} style={{ background: '#fffbeb', border: '1px solid #fde047', color: '#92400e', borderRadius: 8, padding: '8px 12px', fontSize: 12.5, marginBottom: 10, lineHeight: 1.6 }}>
                  <b>Not ingested:</b> {g.what}. {g.why} Import it via {g.how}.
                </div>
              ))}

              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
                {groups.map(g => (
                  <div key={g.group} style={{ border: '1px solid var(--border)', borderRadius: 10, padding: '10px 14px', minWidth: 210 }}>
                    <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase' }}>{g.label}</div>
                    <div style={{ fontSize: 21, fontWeight: 700, color: GROUP_TINT[g.group] }}>{fmt(g.total)}</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                      1st month {fmt(g.m1)} · M2–M12 {fmt(g.m2_12)}{g.unsplit ? ` · unsplit ${fmt(g.unsplit)}` : ''}
                    </div>
                  </div>
                ))}
              </div>

              <div className="table-wrapper" style={{ border: '1px solid var(--border)', borderRadius: 10, overflowX: 'auto', marginBottom: 14 }}>
                <table style={{ borderCollapse: 'collapse', width: '100%' }}>
                  <thead>
                    <tr>
                      <th style={thL}>Money</th>
                      <th style={thL}>What decides the month</th>
                      {pivot === 'legs'
                        ? legCols.map(k => <th key={k} style={th}>{legHead(k)}</th>)
                        : periods.map(p => <th key={p} style={th}>{shortP(p)}</th>)}
                      <th style={{ ...th, fontWeight: 700 }}>Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {groups.map(g => (
                      <Fragment key={g.group}>
                        {streams.filter(s => s.in_total && s.group === g.group).map(s => (
                          <tr key={s.key} style={{ borderTop: '1px solid var(--border)' }}>
                            <td style={{ ...tdL, fontWeight: 600 }}>{s.label}</td>
                            <td style={{ ...tdL, color: 'var(--text3)', fontSize: 11, maxWidth: 320, whiteSpace: 'normal' }}>{s.splits_on}</td>
                            {pivot === 'legs'
                              ? legCols.map(k => (
                                <td key={k} style={{ ...td, color: k === 'unsplit' && s.legs?.[k] ? '#b45309' : undefined }}>
                                  {s.legs?.[k] ? fmt(s.legs[k]) : '·'}
                                </td>))
                              : periods.map(p => (
                                <td key={p} style={td}>{s.periods[p]?.total ? fmt(s.periods[p].total) : '·'}</td>))}
                            <td style={{ ...td, fontWeight: 700 }}>{fmt(s.total)}</td>
                          </tr>
                        ))}
                        <tr style={{ borderTop: '1px solid var(--border)', background: 'var(--surface2)' }}>
                          <td style={{ ...tdL, fontWeight: 700, color: GROUP_TINT[g.group] }} colSpan={2}>{g.label} — total</td>
                          {pivot === 'legs'
                            ? legCols.map(k => <td key={k} style={{ ...td, fontWeight: 600 }}>{g.legs?.[k] ? fmt(g.legs[k]) : '·'}</td>)
                            : periods.map(p => <td key={p} style={{ ...td, fontWeight: 600 }}>{g.by_period?.[p] ? fmt(g.by_period[p]) : '·'}</td>)}
                          <td style={{ ...td, fontWeight: 700 }}>{fmt(g.total)}</td>
                        </tr>
                      </Fragment>
                    ))}
                    {refStreams.map(s => (
                      <tr key={s.key} style={{ borderTop: '2px solid var(--border)', background: '#fffbeb' }}>
                        <td style={{ ...tdL, fontWeight: 600, color: '#92400e' }}>
                          {s.label} <span style={{ fontSize: 10, fontWeight: 500 }}>(cross-check — in no total)</span>
                        </td>
                        <td style={{ ...tdL, color: 'var(--text3)', fontSize: 11, maxWidth: 320, whiteSpace: 'normal' }}>{s.splits_on}</td>
                        {pivot === 'legs'
                          ? legCols.map(k => <td key={k} style={{ ...td, color: '#92400e' }}>{s.legs?.[k] ? fmt(s.legs[k]) : '·'}</td>)
                          : periods.map(p => <td key={p} style={{ ...td, color: '#92400e' }}>{s.periods[p]?.total ? fmt(s.periods[p].total) : '·'}</td>)}
                        <td style={{ ...td, fontWeight: 700, color: '#92400e' }}>{fmt(s.total)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div style={{ fontSize: 11.5, color: 'var(--text3)', lineHeight: 1.6, marginBottom: 14, maxWidth: 1040 }}>
                {bo.basis}
                {namedUnsplit && (
                  <> The Unsplit figure on <b>{namedUnsplit.label}</b> is{' '}
                    {(namedUnsplit.meta!.unsplit_fields || []).map(f => f.replace(/_/g, ' ')).join(', ')} — the
                    carrier states those as their own figures, so they are not first-month commission. They are
                    still inside the Total on the right, which is why the 1st Month figure here equals what the
                    carrier portal calls Commissions Paid.</>
                )}
              </div>

              {refStreams.length > 0 && (
                <div style={{ background: '#fffbeb', border: '1px solid #fde047', color: '#92400e', borderRadius: 8, padding: '10px 12px', fontSize: 12.5, marginBottom: 16, lineHeight: 1.7 }}>
                  <b>Two different “residual” readings, and they are not the same money.</b> {bo.divergence_note}
                  {typeof airtime?.meta?.airtime_on_residual_orders === 'number' && (
                    <> Of the airtime margin above, <b>{fmt(airtime.meta.airtime_on_residual_orders)}</b> sits on
                      residual-order lines — that is exactly where the two readings overlap.</>
                  )}
                </div>
              )}

              {periods.length > 1 && (
                <div className="card" style={{ padding: '12px 12px 6px', marginBottom: 20 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, paddingLeft: 6 }}>
                    📈 What we made — last {periods.length} months
                    {filt.markets.length ? ` · ${filt.markets.join(', ')}` : ''}
                    {filt.stores.length ? ` · ${filt.stores.length} store(s)` : ''}
                  </div>
                  <TrendChart data={trendData} height={230}
                    series={[
                      { key: 'm1', name: '1st month', color: '#16a34a', money: true },
                      { key: 'm2_12', name: 'M2–M12', color: '#2e75b6', money: true },
                      ...groups.filter(g => g.group === 'residual').map(g => ({
                        key: g.group, name: g.label, color: '#f59e0b', money: true, dashed: true,
                      })),
                    ]} />
                </div>
              )}

              <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                Rules in use: {(bo.config?.resolved_from || '').replace(/_/g, ' ')}
                {bo.carrier_mode ? ` · carrier mode: ${bo.carrier_mode}` : ''}
                {bo.config?.label_overrides ? ` · ${bo.config.label_overrides} label override(s)` : ''}
                {bo.config?.mi_split_by_activation === false ? ' · residual split off' : ''}
                {' · '}<a href="/commcalc/gp" style={{ color: 'var(--accent,#2563eb)' }}>Gross Profit</a>
                {' · '}<a href="/commcalc/ma-overview-recon" style={{ color: 'var(--accent,#2563eb)' }}>MA Overview cross-check</a>
                {' · '}<a href="/commcalc/commission-ledger" style={{ color: 'var(--accent,#2563eb)' }}>Commission Ledger</a>
              </div>
            </>
          )}
        </>
      )}

      {/* ═══════════════ TAB 2 — MAP A LABEL (behaviour unchanged) ═══════════════ */}
      {tab === 'map' && (
        <>
          <p style={{ color: 'var(--text3)', fontSize: 12, marginBottom: 12, maxWidth: 980, lineHeight: 1.6 }}>
            Most carrier labels say which month they are (&ldquo;… - Month 3&rdquo;) and are sorted automatically. The
            ones that don&apos;t sit in <b>Unsplit</b> until you decide here; nothing is guessed. This never changes
            what anyone is paid — it only decides which column money the company already received is shown in.
          </p>

          {(!ready || !mapReady) && (
            <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>
              Run migration <code>274_commission_leg_split.sql</code> to see the full history and to save
              overrides. Until then the page shows the most recent month only and the split falls back to the
              built-in rules (which already handle every label that names its own month).
            </div>
          )}

          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
            {['m1', 'trailing', 'unsplit'].map(b => (
              <div key={b} style={{ border: '1px solid var(--border)', borderRadius: 10, padding: '10px 14px', minWidth: 170 }}>
                <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase' }}>{LEG_LABEL[b]}</div>
                <div style={{ fontSize: 20, fontWeight: 700, color: LEG_TINT[b] }}>{fmt(tot(b))}</div>
                <div style={{ fontSize: 11, color: 'var(--text3)' }}>{rows.filter(r => r.bucket === b).length} label(s)</div>
              </div>
            ))}
          </div>

          {unsplitTotal !== 0 && (
            <div style={{ background: '#fffbeb', border: '1px solid #fde047', color: '#92400e', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 14, lineHeight: 1.6 }}>
              <b>{fmt(unsplitTotal)}</b> is sitting in Unsplit. Those labels never state a month-of-life, so the
              reports show them separately instead of quietly folding them into one of the two columns. Pick a leg
              below and they move.
            </div>
          )}

          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
            <label style={{ fontSize: 13, color: 'var(--text2)' }}>Window</label>
            <select style={inp} value={months} onChange={e => setMonths(Number(e.target.value))}>
              {[1, 3, 6, 12].map(m => <option key={m} value={m}>last {m} month{m > 1 ? 's' : ''} to {period}</option>)}
            </select>
            <input style={{ ...inp, width: 220 }} placeholder="find a label…" value={q} onChange={e => setQ(e.target.value)} />
            <label style={{ fontSize: 13, color: 'var(--text2)', display: 'flex', alignItems: 'center', gap: 6 }}>
              <input type="checkbox" checked={onlyUnsplit} onChange={e => setOnlyUnsplit(e.target.checked)} />
              only the ones needing a decision
            </label>
            <div style={{ flex: 1 }} />
            <ReportExportBar
              title={`Carrier labels by leg — ${period}`}
              filename={`commission_leg_labels_${(period || '').replace(/\W+/g, '_').toLowerCase()}`}
              columns={[
                { header: 'Carrier label', field: 'label', get: (r: any) => r.label },
                { header: 'Where it comes from', field: 'sources', get: (r: any) => (r.sources || []).join(' · ') },
                { header: 'Lines', field: 'lines', get: (r: any) => r.lines },
                { header: 'Amount', field: 'amount', money: true, get: (r: any) => r.amount },
                { header: 'Leg', field: 'bucket', get: (r: any) => LEG_LABEL[r.bucket] || r.bucket },
                { header: 'Month', field: 'leg_month', get: (r: any) => r.leg_month ?? '' },
                { header: 'Why', field: 'why', get: (r: any) => WHY_TEXT[r.why] || r.why },
              ]}
              rows={shown}
            />
            <button style={{ ...inp, cursor: 'pointer' }} onClick={load}>↻ Refresh</button>
          </div>

          {loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><div className="spinner" /></div>
          ) : shown.length === 0 ? (
            <div style={{ color: 'var(--text3)', fontSize: 13, lineHeight: 1.7, maxWidth: 900 }}>
              No carrier commission labels in this window. Labels come from the ePay Commission Payment Detail and
              Comprehensive Compensation reports; a VidaPay/Total tenant has none — its leg is the COLUMN on the MA
              Commission Details export, which needs no mapping. See <b>💰 What we made</b>.
            </div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ textAlign: 'left', color: 'var(--text3)', fontSize: 11, textTransform: 'uppercase' }}>
                  <th style={{ padding: '6px 8px' }}>Carrier label</th>
                  <th style={{ padding: '6px 8px' }}>Where it comes from</th>
                  <th style={{ padding: '6px 8px', textAlign: 'right' }}>Lines</th>
                  <th style={{ padding: '6px 8px', textAlign: 'right' }}>Amount</th>
                  <th style={{ padding: '6px 8px' }}>Leg</th>
                  <th style={{ padding: '6px 8px' }}>Why</th>
                </tr>
              </thead>
              <tbody>
                {shown.map(r => (
                  <tr key={r.label} style={{ borderTop: '1px solid var(--border)', background: r.bucket === 'unsplit' ? '#fffbeb' : undefined }}>
                    <td style={{ padding: '5px 8px', fontWeight: 500 }}>{r.label}</td>
                    <td style={{ padding: '5px 8px', color: 'var(--text3)', fontSize: 11 }}>
                      {r.sources.map(x => x === 'payment_detail' ? 'ePay Payment Detail' : x === 'comp_report' ? 'Comprehensive Comp' : x).join(' · ')}
                      {r.categories.length ? ` · ${r.categories.join(', ')}` : ''}
                    </td>
                    <td style={{ padding: '5px 8px', textAlign: 'right' }}>{r.lines.toLocaleString()}</td>
                    <td style={{ padding: '5px 8px', textAlign: 'right', fontWeight: 600 }}>{fmt(r.amount)}</td>
                    <td style={{ padding: '5px 8px' }}>
                      <select style={{ ...inp, padding: '2px 6px', fontSize: 12, color: LEG_TINT[r.bucket], fontWeight: 600 }}
                        value={r.overridden ? r.bucket : ''}
                        onChange={e => setBucket(r.label, e.target.value)}>
                        <option value="">Automatic — {LEG_LABEL[r.bucket]}</option>
                        <option value="m1">1st Month</option>
                        <option value="trailing">M2–M12</option>
                        <option value="unsplit">Leave unsplit</option>
                      </select>
                    </td>
                    <td style={{ padding: '5px 8px', color: 'var(--text3)', fontSize: 11 }}>
                      {WHY_TEXT[r.why] || r.why}{r.leg_month ? ` · month ${r.leg_month}` : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {(resolved || cfg?.resolved) && (
            <details style={{ border: '1px solid var(--border)', borderRadius: 10, background: 'var(--surface)', padding: 12, marginTop: 20 }}>
              <summary style={{ cursor: 'pointer', fontWeight: 700, fontSize: 14 }}>⚙️ How each money source is split</summary>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5, marginTop: 10 }}>
                <tbody>
                  {((resolved || cfg?.resolved)?.sources || []).map((s: any) => (
                    <tr key={s.source} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '5px 8px', fontWeight: 600, width: '38%' }}>{s.source}</td>
                      <td style={{ padding: '5px 8px', color: 'var(--text2)' }}>{s.splits_on}</td>
                      <td style={{ padding: '5px 8px', color: s.splittable ? '#065f46' : '#9a3412', fontWeight: 600 }}>
                        {s.splittable ? 'splittable' : 'not splittable from this source'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8, lineHeight: 1.6 }}>
                Rules in use: <b>{(resolved || cfg?.resolved)?.resolved_from?.replace(/_/g, ' ')}</b>
                {cfg?.carrier_mode ? ` · carrier mode: ${cfg.carrier_mode}` : ''}
                {' · '}money whose source states no month goes to <b>{LEG_LABEL[(resolved || cfg?.resolved)?.unlabeled_bucket] || 'Unsplit'}</b>.
                <br />
                Note: the ePay Commission Payment Detail export carries an &ldquo;Activation Date&rdquo; column, but the
                carrier ships it empty — so the month written into the payment type is the only activation month that
                source actually gives us. Residual (MI/ATU) is different: it carries a real activation date, so it is
                split by date.
              </div>
            </details>
          )}
        </>
      )}
    </div>
  )
}
