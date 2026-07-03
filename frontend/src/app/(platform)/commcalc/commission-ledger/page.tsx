'use client'
import { useEffect, useState } from 'react'
import { api, apiUpload } from '@/lib/client'

// Canonical Commission Ledger (SAP-style) — normalise ANY carrier's commission/tx file into FIVE canonical
// buckets: Commission / Spiff / Equipment rebate / Residual-monthly / Auto Pay residual. A payout paid over
// many months stays one category but keeps its payment month (shown in the matrix). Negative amounts are
// payouts; positives are bill/activation payments (counted separately, never in the buckets). Templates
// (Total / Boost / your own) namespace the classification rules. Backed by commcalc.commission_ledger +
// commission_category_map (migration 071); the classifier degrades to built-in defaults until 071 is run.

type Summ = {
  source_report: string; period: string; line_count: number; payout_total: number; charge_total: number
  other_total: number; other_count: number
  categories: Record<string, { total: number; count: number }>
  category_labels: Record<string, string>; by_month: Record<string, number>
}
type Tmpl = { key: string; label: string; builtin: boolean; rule_count: number }
type RepRow = { rep: string; lines: number; ledger_payout: number; live_payout: number | null; matched: boolean } & Record<string, number>
type ByRep = { reps: RepRow[]; totals: Record<string, number>; matched_count: number; rep_count: number; category_labels: Record<string, string> }
const CATS = ['commission', 'spiff', 'equipment_rebate', 'residual_monthly', 'autopay_residual']
const money = (n: number) => (n || 0).toLocaleString('en-US', { style: 'currency', currency: 'USD' })
const inp: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const tile: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 10, padding: 14, minWidth: 150, background: 'var(--surface)' }

export default function CommissionLedgerPage() {
  const [tmpls, setTmpls] = useState<Tmpl[]>([])
  // Default to the Boost template (the house/1st-tenant carrier). The page opened on the Total
  // ('ma_daily_tx') template, which made the Boost side show Total's numbers — Total belongs to
  // its own tenant. Switch templates from the picker for other carriers.
  const [src, setSrc] = useState('boost')
  const [period, setPeriod] = useState('')
  const [summ, setSumm] = useState<Summ | null>(null)
  const [labels, setLabels] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [drill, setDrill] = useState<{ cat: string; rows: any[] } | null>(null)
  const [view, setView] = useState<'cat' | 'rep'>('cat')
  const [byRep, setByRep] = useState<ByRep | null>(null)

  async function loadTemplates() {
    try {
      const d = await api('/api/v1/commcalc/commission-ledger/templates')
      setTmpls(d?.templates || [])
      setLabels(d?.category_labels || {})
    } catch (e: any) { setMsg(e?.message || 'Load failed') }
  }
  async function loadSummary() {
    try {
      const qs = `?source_report=${encodeURIComponent(src)}${period ? '&period=' + encodeURIComponent(period) : ''}`
      const d = await api('/api/v1/commcalc/commission-ledger/summary' + qs)
      setSumm(d); setLabels(d?.category_labels || labels)
    } catch (e: any) { setMsg(e?.message || 'Load failed') }
  }
  async function loadByRep() {
    try {
      const qs = `?source_report=${encodeURIComponent(src)}${period ? '&period=' + encodeURIComponent(period) : ''}`
      setByRep(await api('/api/v1/commcalc/commission-ledger/by-rep' + qs))
    } catch (e: any) { setMsg(e?.message || 'Load failed') }
  }
  useEffect(() => { loadTemplates() }, [])            // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { loadSummary(); setDrill(null) }, [src, period])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (view === 'rep') loadByRep() }, [view, src, period])  // eslint-disable-line react-hooks/exhaustive-deps

  function flash(m: string) { setMsg(m); setTimeout(() => setMsg(''), 4000) }

  async function upload(file: File) {
    setBusy(true)
    try {
      const fd = new FormData()
      fd.append('file', file); fd.append('source_report', src); fd.append('period', period)
      const r = await apiUpload('/api/v1/commcalc/commission-ledger/import', fd)
      flash(`Imported ${r?.saved} lines — payouts ${money(r?.summary?.payout_total)}${r?.summary?.other_count ? `, ${r.summary.other_count} unmapped` : ''}`)
      loadSummary()
    } catch (e: any) { flash(e?.message || 'Import failed — is migration 071 applied?') }
    setBusy(false)
  }
  async function openDrill(cat: string) {
    try {
      const qs = `?source_report=${encodeURIComponent(src)}${period ? '&period=' + encodeURIComponent(period) : ''}&category=${cat}&limit=500`
      const d = await api('/api/v1/commcalc/commission-ledger/rows' + qs)
      setDrill({ cat, rows: d?.rows || [] })
    } catch (e: any) { flash(e?.message || 'Drill failed') }
  }

  // month columns present across the buckets
  const months = Array.from(new Set(Object.keys(summ?.by_month || {}).map(k => Number(k.split('|')[1])).filter(m => m > 0))).sort((a, b) => a - b)
  const cell = (cat: string, m: number) => summ?.by_month?.[`${cat}|${m}`] || 0

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>🧾 Commission Ledger</h1>
      <p style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 10 }}>
        Any carrier's commission file → five canonical buckets, classified once and displayed as it's paid.
      </p>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12,
        background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: '10px 14px' }}>
        <b style={{ fontSize: 13 }}>First time?</b>
        <span style={{ fontSize: 13, color: 'var(--text2)' }}>
          1. Pick your carrier → 2. Upload the file → 3. Check the preview → 4. Import.
        </span>
        <a href="/commcalc/commission-ledger/setup" style={{ marginLeft: 'auto', padding: '7px 14px', borderRadius: 8, background: 'var(--accent,#2563eb)', color: '#fff', fontSize: 13, fontWeight: 600, textDecoration: 'none' }}>🧭 Open setup wizard</a>
      </div>
      <p style={{ color: 'var(--text3)', fontSize: 12, marginBottom: 10 }}>
        Already set up? Pick a template + period below and import directly. Adjust the rules on{' '}
        <a href="/commcalc/commission-category-map" style={{ color: 'var(--accent,#2563eb)' }}>Commission Category Map →</a>
      </p>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
        <label style={{ fontSize: 13, color: 'var(--text2)' }}>Template</label>
        <select style={inp} value={src} onChange={e => setSrc(e.target.value)}>
          {tmpls.map(t => <option key={t.key} value={t.key}>{t.label}{t.rule_count ? ` (${t.rule_count} rules)` : ''}</option>)}
        </select>
        <input style={{ ...inp, width: 130 }} placeholder="Period e.g. June 2026" value={period} onChange={e => setPeriod(e.target.value)} />
        <label style={{ ...inp, cursor: busy ? 'wait' : 'pointer', fontWeight: 600 }}>
          {busy ? 'Importing…' : '⬆ Import file'}
          <input type="file" accept=".xls,.xlsx,.csv,.txt" style={{ display: 'none' }} disabled={busy}
            onChange={e => { const f = e.target.files?.[0]; if (f) upload(f); e.currentTarget.value = '' }} />
        </label>
      </div>
      {msg && <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>{msg}</div>}

      {!summ || summ.line_count === 0 ? (
        <div style={{ color: 'var(--text3)', fontSize: 13 }}>No ledger data for this template/period yet — import a file above.</div>
      ) : (
        <>
          {summ.other_count > 0 && (
            <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>
              ⚠️ {summ.other_count} payout line(s) totaling {money(summ.other_total)} are <b>unmapped</b> — add a rule on the Category Map so they land in a bucket.
            </div>
          )}
          <div style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
            {(['cat', 'rep'] as const).map(v => (
              <button key={v} onClick={() => setView(v)} style={{ ...inp, cursor: 'pointer', fontWeight: view === v ? 700 : 400,
                background: view === v ? 'var(--accent,#2563eb)' : 'var(--surface)', color: view === v ? '#fff' : 'inherit' }}>
                {v === 'cat' ? 'By category' : 'By rep'}
              </button>
            ))}
          </div>

          {view === 'cat' && (<>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
            {CATS.map(c => (
              <div key={c} style={{ ...tile, cursor: 'pointer' }} onClick={() => openDrill(c)} title="Click to drill">
                <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 4 }}>{labels[c] || c}</div>
                <div style={{ fontSize: 20, fontWeight: 700 }}>{money(summ.categories[c]?.total || 0)}</div>
                <div style={{ fontSize: 11, color: 'var(--text3)' }}>{summ.categories[c]?.count || 0} lines</div>
              </div>
            ))}
            <div style={{ ...tile, background: 'var(--bg, transparent)' }}>
              <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 4 }}>Total payouts</div>
              <div style={{ fontSize: 20, fontWeight: 700 }}>{money(summ.payout_total)}</div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>{summ.line_count} lines · {money(summ.charge_total)} bill/act. payments</div>
            </div>
          </div>

          {months.length > 0 && (
            <>
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 6 }}>By payment month</div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginBottom: 18 }}>
                <thead>
                  <tr style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 11 }}>
                    <th style={{ textAlign: 'left', padding: '6px 8px' }}>Category</th>
                    {months.map(m => <th key={m} style={{ padding: '6px 8px' }}>M{m}</th>)}
                    <th style={{ padding: '6px 8px' }}>Total</th>
                  </tr>
                </thead>
                <tbody>
                  {CATS.map(c => (
                    <tr key={c} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '6px 8px', fontWeight: 600 }}>{labels[c] || c}</td>
                      {months.map(m => <td key={m} style={{ padding: '6px 8px', textAlign: 'right', color: cell(c, m) ? 'inherit' : 'var(--text3)' }}>{cell(c, m) ? money(cell(c, m)) : '·'}</td>)}
                      <td style={{ padding: '6px 8px', textAlign: 'right', fontWeight: 600 }}>{money(summ.categories[c]?.total || 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {drill && (
            <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <b>{labels[drill.cat] || drill.cat} — {drill.rows.length} lines</b>
                <button onClick={() => setDrill(null)} style={{ ...inp, cursor: 'pointer', fontSize: 11 }}>✕ close</button>
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead><tr style={{ textAlign: 'left', color: 'var(--text3)', fontSize: 11 }}>
                  <th style={{ padding: '4px 6px' }}>Rep</th><th style={{ padding: '4px 6px' }}>Product</th>
                  <th style={{ padding: '4px 6px' }}>Mo</th><th style={{ padding: '4px 6px' }}>Date</th>
                  <th style={{ padding: '4px 6px', textAlign: 'right' }}>Payout</th></tr></thead>
                <tbody>
                  {drill.rows.slice(0, 300).map((r, i) => (
                    <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '4px 6px' }}>{r.rep_user}</td>
                      <td style={{ padding: '4px 6px' }}>{r.product_name}</td>
                      <td style={{ padding: '4px 6px' }}>{r.payment_month || ''}</td>
                      <td style={{ padding: '4px 6px' }}>{r.trans_date}</td>
                      <td style={{ padding: '4px 6px', textAlign: 'right' }}>{money(r.payout_total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          </>)}

          {view === 'rep' && (
            !byRep ? <div style={{ color: 'var(--text3)', fontSize: 13 }}>Loading…</div> :
            byRep.reps.length === 0 ? <div style={{ color: 'var(--text3)', fontSize: 13 }}>No rep-attributed ledger payouts for this template/period.</div> : (
            <>
              <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>
                Each rep&apos;s canonical-ledger payout, with what the live calc actually pays them
                (<a href={`/commcalc/commissions/${encodeURIComponent(period)}`} style={{ color: 'var(--accent,#2563eb)' }}>rep commissions</a>) alongside.
                {' '}{byRep.matched_count}/{byRep.rep_count} reps matched to a live payout{period ? '' : ' — enter a period to join the live payout'}.
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 11 }}>
                    <th style={{ textAlign: 'left', padding: '6px 8px' }}>Rep</th>
                    {CATS.map(c => <th key={c} style={{ padding: '6px 8px' }}>{(byRep.category_labels?.[c] || c).split(' / ')[0]}</th>)}
                    <th style={{ padding: '6px 8px' }}>Ledger payout</th>
                    <th style={{ padding: '6px 8px' }}>Live payout</th>
                  </tr>
                </thead>
                <tbody>
                  {byRep.reps.map((r, i) => (
                    <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '6px 8px', fontWeight: 600 }}>{r.rep}</td>
                      {CATS.map(c => <td key={c} style={{ padding: '6px 8px', textAlign: 'right', color: r[c] ? 'inherit' : 'var(--text3)' }}>{r[c] ? money(r[c]) : '·'}</td>)}
                      <td style={{ padding: '6px 8px', textAlign: 'right', fontWeight: 600 }}>{money(r.ledger_payout)}</td>
                      <td style={{ padding: '6px 8px', textAlign: 'right' }}
                          title={r.matched ? 'from rep_commissions.total_payout' : 'no matching live rep payout for this period'}>
                        {r.live_payout == null ? <span style={{ color: 'var(--text3)' }}>—</span> : money(r.live_payout)}
                      </td>
                    </tr>
                  ))}
                  <tr style={{ borderTop: '2px solid var(--border)', fontWeight: 700 }}>
                    <td style={{ padding: '6px 8px' }}>Total</td>
                    {CATS.map(c => <td key={c} style={{ padding: '6px 8px', textAlign: 'right' }}>{money(byRep.totals[c] || 0)}</td>)}
                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{money(byRep.totals.ledger_payout || 0)}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{money(byRep.totals.live_payout || 0)}</td>
                  </tr>
                </tbody>
              </table>
            </>)
          )}
        </>
      )}
    </div>
  )
}
