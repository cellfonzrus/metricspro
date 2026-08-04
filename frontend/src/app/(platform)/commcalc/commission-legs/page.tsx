'use client'
import { useEffect, useState, useCallback } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

// COMMISSION LEGS — 1st Month vs M2–M12 (owner directive 2026-08-04)
//
//   "1st Month commission which is paid the same month of the activation and the other is M2-M12
//    commission, any commission received for an activated number after the activated month will be in
//    this category."
//
// This page is where a tenant RESOLVES the money its carrier files leave ambiguous. Most carrier labels
// name their own month ("New Activation Bounty - Month 3") and are attributed automatically; the ones
// that don't ("Boost Auto Top-Up", "2026 SIM card reimbursement") sit in Unsplit until a human decides,
// because guessing would silently move money between the two columns the owner reads.
//
// Reporting only. Nothing here changes what anybody is PAID — it changes which report column a dollar
// the company already received is displayed in.

type Row = {
  label: string; amount: number; lines: number; sources: string[]; categories: string[]
  bucket: string; leg_month: number | null; why: string; overridden: boolean; override_note: string
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
const inp: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

export default function CommissionLegsPage() {
  const { period } = usePeriod()
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

  useEffect(() => { load() }, [load])
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

  return (
    <div style={{ padding: 24, maxWidth: 1150 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>🧩 Commission Legs — 1st Month vs M2–M12</h1>
      <p style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 10, maxWidth: 900, lineHeight: 1.6 }}>
        Every dollar of commission the company receives belongs to one of two legs: <b>1st Month</b> — it came
        in the same month the number activated — or <b>M2–M12</b> — it came in later, for a number that was
        already active. Most carrier labels say which month they are (&ldquo;… - Month 3&rdquo;) and are sorted
        automatically. The ones that don&apos;t sit in <b>Unsplit</b> until you decide here; nothing is guessed.
        The result drives{' '}
        <a href="/commcalc/gp" style={{ color: 'var(--accent,#2563eb)' }}>Gross Profit</a>,{' '}
        <a href="/commcalc/commission-ledger" style={{ color: 'var(--accent,#2563eb)' }}>Commission Ledger</a>{' '}
        and the{' '}
        <a href="/commcalc/commission-category-map" style={{ color: 'var(--accent,#2563eb)' }}>Category → Bucket Map</a>.
      </p>
      <p style={{ color: 'var(--text3)', fontSize: 12, marginBottom: 12 }}>
        This never changes what anyone is paid — it only decides which column money the company already
        received is shown in.
      </p>

      {(!ready || !mapReady) && (
        <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>
          Run migration <code>274_commission_leg_split.sql</code> to see the full history and to save
          overrides. Until then the page shows the most recent month only and the split falls back to the
          built-in rules (which already handle every label that names its own month).
        </div>
      )}
      {msg && <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>{msg}</div>}

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
        <button style={{ ...inp, cursor: 'pointer' }} onClick={load}>↻ Refresh</button>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><div className="spinner" /></div>
      ) : shown.length === 0 ? (
        <div style={{ color: 'var(--text3)', fontSize: 13 }}>
          No carrier commission labels in this window — import a commission file for {period} first.
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
    </div>
  )
}
