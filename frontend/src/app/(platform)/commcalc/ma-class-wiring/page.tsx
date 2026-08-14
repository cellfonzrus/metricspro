'use client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/client'
import { ReportShell } from '@/components/ReportShell'
import type { ExportColumn } from '@/lib/export'

// ─────────────────────────────────────────────────────────────────────────────────────────────────
// MA PRODUCT CLASS → MONEY  (mig 265).  The control room for the owner-gated wiring.
//
// Two consumers, each with its own dropdown, each defaulting to LEGACY = today's behaviour:
//   • Commission Ledger      — a Daily-Tx line's canonical bucket
//   • What-If carrier income — which lines count as residual / airtime margin
//
// Everything money-visible on this page is shown OLD vs NEW *before* anything is flipped, and
// reverting is the same dropdown. Only CONFIRMED classifications (on /commcalc/ma-product-class)
// classify money; proposals — including the AMBIGUOUS ones — are surfaced here and classify nothing.
// ─────────────────────────────────────────────────────────────────────────────────────────────────

type Mode = 'legacy' | 'class'
type LegRow = { product_class: string; label: string; income_leg: string; default_leg: string }
type Opt = { key: string; label: string }
type Rule = { id?: string; pattern: string; category: string; sign_rule: string; priority: number; match_op: string }
type Conflict = { product_class: string; ledger_category: string; income_leg: string; why: string }
type ClassStatus = {
  rows: number; confirmed: number; proposed: number; ready?: boolean; migration?: string | null
  ambiguous_pending: { product_name: string; product_class: string; note: string }[]
  ambiguous_confirmed: { product_name: string; product_class: string; note: string }[]
  proposed_names: string[]; note: string
}
type Cfg = {
  modes: Record<string, Mode>; consumers: Opt[]; mode_options: Opt[]; default_mode: Mode
  legs: LegRow[]; leg_options: Opt[]; class_status: ClassStatus; classified_names: number
  class_rules: Rule[]; categories: string[]; category_labels: Record<string, string>
  charge_bucket: string; conflicts: Conflict[]; can_edit: boolean
  ready: boolean; migration: string | null; class_migration: string | null; note: string
}
type Proposal = {
  product_class: string; proposed_category: string | null; already_configured: boolean
  current_category: string | null; lines: number; payout_total: number
  today_by_category: Record<string, { lines: number; payout: number }>
  examples: string[]; warning: string | null; kind: string
}
type Delta = {
  totals: { lines: number; moved_lines: number; moved_payout: number; legacy: any; class: any }
  by_month: { period: string; lines: number; moved_lines: number; moved_payout: number; legacy: any; class: any }[]
  movements: { from: string; to: string; lines: number; old_payout: number; new_payout: number; examples: string[] }[]
  drift_rows: number; category_labels: Record<string, string>; note: string
  read: { rows_read: number; truncated: boolean }
}
type ClassSwapMonth = {
  period: string; old_residual: number; old_airtime: number; new_residual: number; new_airtime: number
  delta_residual: number; delta_airtime: number; delta_total: number
  class_excluded_lines: number; class_excluded_discount: number
  class_unclassified_lines: number; class_unclassified_discount: number
  ledger_class_overlap_lines: number; ledger_class_overlap_total: number
  by_class: { product_class: string; leg: string; lines: number; residual: number; airtime: number; excluded_discount: number }[]
}
type Income = {
  class_mode: Mode; class_note: string | null
  class_swap: { active: string; by_month: ClassSwapMonth[]; totals: any; by_class: any[]; note: string }
  class_wiring: any
}

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const money = (n: number) => (n || 0).toLocaleString('en-US', { style: 'currency', currency: 'USD' })
const card: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 10, background: 'var(--surface)', padding: 14, marginBottom: 16 }

export default function MaClassWiringPage() {
  const [cfg, setCfg] = useState<Cfg | null>(null)
  const [delta, setDelta] = useState<Delta | null>(null)
  const [props_, setProps] = useState<Proposal[]>([])
  const [income, setIncome] = useState<Income | null>(null)
  const [picked, setPicked] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const src = 'ma_daily_tx'

  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(''), 5000) }

  const loadAll = useCallback(async () => {
    try { setCfg(await api('/api/v1/commcalc/ma-class-wiring?source_report=' + src)) } catch (e: any) { flash(e?.message || 'Load failed') }
    try { setDelta(await api('/api/v1/commcalc/ma-class-wiring/ledger-delta?source_report=' + src)) } catch { setDelta(null) }
    try { const d = await api('/api/v1/commcalc/ma-class-wiring/rule-proposals?source_report=' + src); setProps(d?.proposals || []) } catch { setProps([]) }
    try { setIncome(await api('/api/v1/commcalc/whatif/carrier-income?months=6')) } catch { setIncome(null) }
  }, [])
  useEffect(() => { loadAll() }, [loadAll])

  async function setMode(consumer: string, mode: string) {
    setBusy(true)
    try {
      const r = await api('/api/v1/commcalc/ma-class-wiring/mode', { method: 'PUT', body: JSON.stringify({ consumer, mode, source_report: src }) })
      flash(r?.effect || 'Saved'); await loadAll()
    } catch (e: any) { flash(e?.message || 'Could not save — is migration 265 applied, and are you an administrator?') }
    finally { setBusy(false) }
  }
  async function setLeg(product_class: string, income_leg: string) {
    setBusy(true)
    try {
      await api('/api/v1/commcalc/ma-class-wiring/leg', { method: 'PUT', body: JSON.stringify({ product_class, income_leg }) })
      flash('Saved — ' + product_class + ' → ' + income_leg); await loadAll()
    } catch (e: any) { flash(e?.message || 'Could not save') }
    finally { setBusy(false) }
  }
  async function applyRules() {
    const rules = Object.entries(picked).filter(([, c]) => c).map(([product_class, category]) => ({ product_class, category }))
    if (!rules.length) { flash('Pick at least one class → bucket first'); return }
    setBusy(true)
    try {
      const r = await api('/api/v1/commcalc/ma-class-wiring/rule-proposals/apply', { method: 'POST', body: JSON.stringify({ source_report: src, rules }) })
      flash(`Saved ${r?.written_count || 0} rule(s)${r?.rejected?.length ? ` · ${r.rejected.length} rejected` : ''}. ${r?.note || ''}`)
      setPicked({}); await loadAll()
    } catch (e: any) { flash(e?.message || 'Could not save — administrator only') }
    finally { setBusy(false) }
  }

  // ── the CONSUMER-1 delta, flattened per month per bucket ──
  const ledgerRows = useMemo(() => {
    if (!delta) return []
    const out: any[] = []
    for (const m of delta.by_month) {
      const cats = new Set([...Object.keys(m.legacy?.categories || {}), ...Object.keys(m.class?.categories || {})])
      for (const c of Array.from(cats)) {
        const o = m.legacy.categories[c] || { total: 0, count: 0 }
        const n = m.class.categories[c] || { total: 0, count: 0 }
        if (!o.count && !n.count) continue
        out.push({
          month: m.period, bucket: delta.category_labels[c] || c,
          old_lines: o.count, old_total: o.total, new_lines: n.count, new_total: n.total,
          d_lines: n.count - o.count, d_total: Number((n.total - o.total).toFixed(2)),
        })
      }
      const oo = { total: m.legacy.other_total, count: m.legacy.other_count }
      const no = { total: m.class.other_total, count: m.class.other_count }
      if (oo.count || no.count) {
        out.push({
          month: m.period, bucket: 'Unmapped payout (other)', old_lines: oo.count, old_total: oo.total,
          new_lines: no.count, new_total: no.total, d_lines: no.count - oo.count,
          d_total: Number((no.total - oo.total).toFixed(2)),
        })
      }
    }
    return out
  }, [delta])

  const ledgerCols: ExportColumn[] = [
    { header: 'Month', field: 'month', role: 'month', get: r => r.month },
    { header: 'Bucket', field: 'bucket', get: r => r.bucket },
    { header: 'Today — lines', field: 'old_lines', type: 'number', align: 'right', get: r => r.old_lines },
    { header: 'Today — payout', field: 'old_total', money: true, align: 'right', get: r => r.old_total },
    { header: 'With classes — lines', field: 'new_lines', type: 'number', align: 'right', get: r => r.new_lines },
    { header: 'With classes — payout', field: 'new_total', money: true, align: 'right', get: r => r.new_total },
    { header: 'Δ lines', field: 'd_lines', type: 'number', align: 'right', get: r => r.d_lines },
    { header: 'Δ payout', field: 'd_total', money: true, align: 'right', get: r => r.d_total },
  ]

  // ── the CONSUMER-2 delta ──
  const incomeRows = useMemo(() => (income?.class_swap?.by_month || []).map(m => ({
    month: m.period,
    old_residual: m.old_residual, old_airtime: m.old_airtime, old_total: m.old_residual + m.old_airtime,
    new_residual: m.new_residual, new_airtime: m.new_airtime, new_total: m.new_residual + m.new_airtime,
    d_residual: m.delta_residual, d_airtime: m.delta_airtime, d_total: m.delta_total,
    excluded: m.class_excluded_discount, unclassified: m.class_unclassified_discount,
  })), [income])

  const incomeCols: ExportColumn[] = [
    { header: 'Month', field: 'month', role: 'month', get: r => r.month },
    { header: 'Today — residual', field: 'old_residual', money: true, align: 'right', get: r => r.old_residual },
    { header: 'Today — airtime', field: 'old_airtime', money: true, align: 'right', get: r => r.old_airtime },
    { header: 'With classes — residual', field: 'new_residual', money: true, align: 'right', get: r => r.new_residual },
    { header: 'With classes — airtime', field: 'new_airtime', money: true, align: 'right', get: r => r.new_airtime },
    { header: 'Δ residual', field: 'd_residual', money: true, align: 'right', get: r => r.d_residual },
    { header: 'Δ airtime', field: 'd_airtime', money: true, align: 'right', get: r => r.d_airtime },
    { header: 'Left the total (classified)', field: 'excluded', money: true, align: 'right', get: r => r.excluded },
    { header: 'Left the total (unclassified)', field: 'unclassified', money: true, align: 'right', get: r => r.unclassified },
  ]

  const st = cfg?.class_status
  const ro = cfg && !cfg.can_edit

  return (
    <div style={{ padding: 24, maxWidth: 1180 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>🔌 MA Product Class → Money</h1>
      <p style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 10, maxWidth: 980, lineHeight: 1.6 }}>
        Where the MA product classification actually changes a number. Two places, two switches, both
        starting on <b>Legacy</b> — exactly what the system does today. Each switch shows what would move
        <b> before</b> you flip it, and flipping back is the same dropdown (no deploy, no SQL, no recompute).
        Classify names on <a href="/commcalc/ma-product-class" style={{ color: 'var(--accent,#2563eb)' }}>MA Product Name Classification →</a>
      </p>

      {msg && <div style={{ ...card, marginBottom: 12, padding: '9px 13px', fontSize: 13 }}>{msg}</div>}
      {cfg && !cfg.ready && (
        <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '9px 13px', fontSize: 13, marginBottom: 12 }}>
          Run migration <code>{cfg.migration}</code> to save a switch. Until then both consumers read
          <b> Legacy</b> and nothing on this page can change a number.
        </div>
      )}
      {ro && (
        <div style={{ background: '#eff6ff', border: '1px solid #bfdbfe', color: '#1e40af', borderRadius: 8, padding: '9px 13px', fontSize: 13, marginBottom: 12 }}>
          You can read every delta on this page, but only an administrator can flip a switch.
        </div>
      )}

      {/* ── what is CONFIRMED (the money gate) ── */}
      {st && (
        <div style={card}>
          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 8 }}>Only confirmed classifications move money</div>
          <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap', fontSize: 13 }}>
            <div><b style={{ fontSize: 19, color: '#15803d' }}>{st.confirmed}</b> confirmed — these classify</div>
            <div><b style={{ fontSize: 19, color: '#b45309' }}>{st.proposed}</b> proposed — these classify <b>nothing</b></div>
          </div>
          {!!st.ambiguous_pending?.length && (
            <div style={{ background: '#fef2f2', border: '1px solid #fecaca', color: '#b91c1c', borderRadius: 8, padding: '8px 12px', fontSize: 12.5, marginTop: 10, lineHeight: 1.6 }}>
              <b>{st.ambiguous_pending.length} name(s) flagged AMBIGUOUS are still unconfirmed, so they classify nothing:</b>
              <ul style={{ margin: '6px 0 0 18px' }}>
                {st.ambiguous_pending.map(a => (
                  <li key={a.product_name}><code>{a.product_name}</code> → proposed <b>{a.product_class}</b>. {a.note}</li>
                ))}
              </ul>
            </div>
          )}
          {!!st.ambiguous_confirmed?.length && (
            <div style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 8 }}>
              Judgement calls you have already confirmed (these DO classify): {st.ambiguous_confirmed.map(a => <code key={a.product_name} style={{ marginRight: 6 }}>{a.product_name}</code>)}
            </div>
          )}
          {cfg?.class_migration && (
            <div style={{ fontSize: 12.5, color: '#9a3412', marginTop: 8 }}>Migration <code>{cfg.class_migration}</code> has not been run — there are no saved classifications yet.</div>
          )}
        </div>
      )}

      {/* ── the two switches ── */}
      {cfg && (
        <div style={card}>
          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 10 }}>The two switches</div>
          {cfg.consumers.map(c => (
            <div key={c.key} style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', padding: '8px 0', borderTop: '1px solid var(--border)' }}>
              <div style={{ minWidth: 380, fontSize: 13 }}>{c.label}</div>
              <select style={sel} disabled={!!ro || busy || !cfg.ready} value={cfg.modes[c.key] || 'legacy'} onChange={e => setMode(c.key, e.target.value)}>
                {cfg.mode_options.map(o => <option key={o.key} value={o.key}>{o.label}</option>)}
              </select>
              <span style={{ fontSize: 12, fontWeight: 700, color: (cfg.modes[c.key] === 'class') ? '#15803d' : 'var(--text3)' }}>
                {cfg.modes[c.key] === 'class' ? 'LIVE — reading classes' : 'legacy — today’s behaviour'}
              </span>
            </div>
          ))}
          <div style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 10, lineHeight: 1.6 }}>{cfg.note}</div>
          {!!cfg.conflicts?.length && (
            <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '8px 12px', fontSize: 12.5, marginTop: 10 }}>
              {cfg.conflicts.map(c => <div key={c.product_class}>⚠️ {c.why}</div>)}
            </div>
          )}
        </div>
      )}

      {/* ── consumer 1: the rules + the delta ── */}
      <h2 style={{ fontSize: 16, fontWeight: 700, margin: '22px 0 6px' }}>① Commission Ledger — which bucket a line lands in</h2>
      <p style={{ fontSize: 12.5, color: 'var(--text2)', maxWidth: 980, lineHeight: 1.6, marginBottom: 10 }}>
        Today a line is bucketed by keyword rules on its label. A <b>class rule</b> says it once per class
        instead — and a confirmed class always beats a keyword. Rules are inert until the switch above is
        set to <b>Product class</b>, and they only reach the stored ledger on the <b>next</b> refresh/import.
      </p>

      {!!props_.length && (
        <div style={card}>
          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 8 }}>Suggested class → bucket rules (from your own ledger lines)</div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
            <thead><tr style={{ textAlign: 'left', color: 'var(--text3)', fontSize: 11, textTransform: 'uppercase' }}>
              <th style={{ padding: '5px 8px' }}>Class</th><th style={{ padding: '5px 8px' }}>Lines</th>
              <th style={{ padding: '5px 8px', textAlign: 'right' }}>Payout today</th>
              <th style={{ padding: '5px 8px' }}>Sits in today</th><th style={{ padding: '5px 8px' }}>→ Bucket</th>
            </tr></thead>
            <tbody>
              {props_.map(p => (
                <tr key={p.product_class} style={{ borderTop: '1px solid var(--border)', background: p.warning ? '#fff7ed' : undefined }}>
                  <td style={{ padding: '6px 8px', fontWeight: 600 }}>
                    {p.product_class}
                    {p.already_configured && <span style={{ fontSize: 11, color: '#15803d', marginLeft: 6 }}>already a rule → {p.current_category}</span>}
                    {p.warning && <div style={{ fontSize: 11.5, color: '#9a3412', marginTop: 3, maxWidth: 460, lineHeight: 1.5 }}>⚠️ {p.warning}</div>}
                    {!!p.examples.length && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 3 }}>{p.examples.slice(0, 3).join(' · ')}</div>}
                  </td>
                  <td style={{ padding: '6px 8px' }}>{p.lines.toLocaleString()}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}>{money(p.payout_total)}</td>
                  <td style={{ padding: '6px 8px', color: 'var(--text2)' }}>
                    {Object.entries(p.today_by_category).map(([k, v]) => <div key={k}>{cfg?.category_labels[k] || k}: {v.lines} · {money(v.payout)}</div>)}
                  </td>
                  <td style={{ padding: '6px 8px' }}>
                    <select style={sel} disabled={!!ro || busy} value={picked[p.product_class] ?? (p.proposed_category || '')} onChange={e => setPicked(v => ({ ...v, [p.product_class]: e.target.value }))}>
                      <option value="">— don’t create a rule —</option>
                      {(cfg?.categories || []).map(c => <option key={c} value={c}>{cfg?.category_labels[c] || c}</option>)}
                      <option value={cfg?.charge_bucket || 'charge'}>Not a payout (charge)</option>
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button onClick={applyRules} disabled={!!ro || busy} style={{ ...sel, cursor: 'pointer', marginTop: 10, fontWeight: 700, background: 'var(--accent,#2563eb)', color: '#fff', border: 'none', opacity: ro ? .5 : 1 }}>
            Save the picked rules
          </button>
          <span style={{ fontSize: 12, color: 'var(--text3)', marginLeft: 10 }}>Nothing is applied until you press this, and nothing pays differently until the switch is flipped.</span>
        </div>
      )}

      {!!cfg?.class_rules?.length && (
        <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 10 }}>
          Class rules configured: {cfg.class_rules.map(r => <code key={r.pattern} style={{ marginRight: 8 }}>{r.pattern} → {cfg.category_labels[r.category] || r.category}</code>)}
          {' · '}<a href="/commcalc/commission-category-map" style={{ color: 'var(--accent,#2563eb)' }}>edit on Category → Bucket Map</a>
        </div>
      )}

      {delta && (
        <>
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginBottom: 10 }}>
            <div style={{ ...card, marginBottom: 0, minWidth: 190 }}>
              <div style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text3)' }}>Lines that would re-bucket</div>
              <div style={{ fontSize: 19, fontWeight: 700 }}>{delta.totals.moved_lines.toLocaleString()}</div>
              <div style={{ fontSize: 12, color: 'var(--text2)' }}>of {delta.totals.lines.toLocaleString()} ledger lines</div>
            </div>
            <div style={{ ...card, marginBottom: 0, minWidth: 190 }}>
              <div style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text3)' }}>Payout total today</div>
              <div style={{ fontSize: 19, fontWeight: 700 }}>{money(delta.totals.legacy.payout_total)}</div>
            </div>
            <div style={{ ...card, marginBottom: 0, minWidth: 190 }}>
              <div style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text3)' }}>Payout total with classes</div>
              <div style={{ fontSize: 19, fontWeight: 700, color: delta.totals.class.payout_total !== delta.totals.legacy.payout_total ? '#b45309' : undefined }}>{money(delta.totals.class.payout_total)}</div>
            </div>
            {!!delta.drift_rows && (
              <div style={{ ...card, marginBottom: 0, minWidth: 190 }}>
                <div style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text3)' }}>Pre-existing drift</div>
                <div style={{ fontSize: 19, fontWeight: 700, color: '#9a3412' }}>{delta.drift_rows.toLocaleString()}</div>
                <div style={{ fontSize: 12, color: 'var(--text2)' }}>stored ≠ today’s rules — not part of this delta</div>
              </div>
            )}
          </div>
          {!!delta.movements.length && (
            <div style={card}>
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 8 }}>What moves where</div>
              {delta.movements.map(m => (
                <div key={m.from + m.to} style={{ fontSize: 12.5, padding: '4px 0', borderTop: '1px solid var(--border)' }}>
                  <b>{delta.category_labels[m.from] || m.from}</b> → <b>{delta.category_labels[m.to] || m.to}</b>
                  {' · '}{m.lines.toLocaleString()} lines · {money(m.old_payout)} → {money(m.new_payout)}
                  <div style={{ fontSize: 11, color: 'var(--text3)' }}>{m.examples.slice(0, 4).join(' · ')}</div>
                </div>
              ))}
            </div>
          )}
          <ReportShell
            title="① Ledger buckets — today vs with the classes"
            subtitle={delta.note}
            filename="ma-class-ledger-delta"
            columns={ledgerCols}
            rows={ledgerRows}
            defaultGroupBy="month"
            compact
          />
        </>
      )}

      {/* ── consumer 2: the legs + the delta ── */}
      <h2 style={{ fontSize: 16, fontWeight: 700, margin: '26px 0 6px' }}>② What-If carrier income — residual vs airtime margin</h2>
      <p style={{ fontSize: 12.5, color: 'var(--text2)', maxWidth: 980, lineHeight: 1.6, marginBottom: 10 }}>
        Today the residual leg is picked by <b>order type</b> and <b>everything else</b> lands in airtime
        margin — so a device sale, a customer bill payment and a wallet funding are all counted as airtime.
        With classes on, each class feeds the leg you map it to below; anything mapped to <i>not carrier
        income</i>, and anything nobody has classified, <b>leaves the total</b> and is reported in dollars.
      </p>

      {cfg && (
        <div style={card}>
          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 8 }}>Class → carrier-income leg</div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
            <thead><tr style={{ textAlign: 'left', color: 'var(--text3)', fontSize: 11, textTransform: 'uppercase' }}>
              <th style={{ padding: '5px 8px' }}>Class</th><th style={{ padding: '5px 8px' }}>Feeds</th><th style={{ padding: '5px 8px' }}>Default</th>
            </tr></thead>
            <tbody>
              {cfg.legs.map(l => (
                <tr key={l.product_class} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '6px 8px' }}>{l.label} <span style={{ color: 'var(--text3)' }}>({l.product_class})</span></td>
                  <td style={{ padding: '6px 8px' }}>
                    <select style={sel} disabled={!!ro || busy || !cfg.ready} value={l.income_leg} onChange={e => setLeg(l.product_class, e.target.value)}>
                      {cfg.leg_options.map(o => <option key={o.key} value={o.key}>{o.label}</option>)}
                    </select>
                  </td>
                  <td style={{ padding: '6px 8px', color: 'var(--text3)' }}>{l.default_leg}{l.income_leg !== l.default_leg && <b style={{ color: '#b45309' }}> · changed</b>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {income?.class_note && (
        <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '9px 13px', fontSize: 12.5, marginBottom: 12, lineHeight: 1.6 }}>{income.class_note}</div>
      )}
      {income?.class_swap && (
        <>
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginBottom: 10 }}>
            <div style={{ ...card, marginBottom: 0, minWidth: 190 }}>
              <div style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text3)' }}>Residual today → with classes</div>
              <div style={{ fontSize: 17, fontWeight: 700 }}>{money(income.class_swap.totals.old_residual)} → {money(income.class_swap.totals.new_residual)}</div>
              <div style={{ fontSize: 12, color: income.class_swap.totals.delta_residual ? '#b45309' : 'var(--text2)' }}>Δ {money(income.class_swap.totals.delta_residual)}</div>
            </div>
            <div style={{ ...card, marginBottom: 0, minWidth: 190 }}>
              <div style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text3)' }}>Airtime today → with classes</div>
              <div style={{ fontSize: 17, fontWeight: 700 }}>{money(income.class_swap.totals.old_airtime)} → {money(income.class_swap.totals.new_airtime)}</div>
              <div style={{ fontSize: 12, color: income.class_swap.totals.delta_airtime ? '#b45309' : 'var(--text2)' }}>Δ {money(income.class_swap.totals.delta_airtime)}</div>
            </div>
            <div style={{ ...card, marginBottom: 0, minWidth: 210 }}>
              <div style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text3)' }}>Leaves the total</div>
              <div style={{ fontSize: 17, fontWeight: 700 }}>{money(income.class_swap.totals.class_excluded_discount)}</div>
              <div style={{ fontSize: 12, color: 'var(--text2)' }}>classified as not-carrier-income ({income.class_swap.totals.class_excluded_lines} lines)</div>
            </div>
            <div style={{ ...card, marginBottom: 0, minWidth: 210 }}>
              <div style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text3)' }}>Not classified at all</div>
              <div style={{ fontSize: 17, fontWeight: 700, color: income.class_swap.totals.class_unclassified_lines ? '#b91c1c' : undefined }}>{money(income.class_swap.totals.class_unclassified_discount)}</div>
              <div style={{ fontSize: 12, color: 'var(--text2)' }}>{income.class_swap.totals.class_unclassified_lines} lines — classify them or they leave the total</div>
            </div>
          </div>
          {!!income.class_swap.by_class?.length && (
            <div style={card}>
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 8 }}>By class, across the window</div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
                <thead><tr style={{ textAlign: 'left', color: 'var(--text3)', fontSize: 11, textTransform: 'uppercase' }}>
                  <th style={{ padding: '5px 8px' }}>Class</th><th style={{ padding: '5px 8px' }}>Leg</th>
                  <th style={{ padding: '5px 8px', textAlign: 'right' }}>Lines</th>
                  <th style={{ padding: '5px 8px', textAlign: 'right' }}>→ Residual</th>
                  <th style={{ padding: '5px 8px', textAlign: 'right' }}>→ Airtime</th>
                  <th style={{ padding: '5px 8px', textAlign: 'right' }}>Left the total</th>
                </tr></thead>
                <tbody>
                  {income.class_swap.by_class.map((c: any) => (
                    <tr key={c.product_class} style={{ borderTop: '1px solid var(--border)', background: c.leg === 'excluded' ? '#fafafa' : undefined }}>
                      <td style={{ padding: '5px 8px', fontWeight: 600 }}>{c.product_class}</td>
                      <td style={{ padding: '5px 8px', color: 'var(--text2)' }}>{c.leg}</td>
                      <td style={{ padding: '5px 8px', textAlign: 'right' }}>{c.lines.toLocaleString()}</td>
                      <td style={{ padding: '5px 8px', textAlign: 'right' }}>{money(c.residual)}</td>
                      <td style={{ padding: '5px 8px', textAlign: 'right' }}>{money(c.airtime)}</td>
                      <td style={{ padding: '5px 8px', textAlign: 'right', color: c.excluded_discount ? '#9a3412' : undefined }}>{money(c.excluded_discount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <ReportShell
            title="② Carrier income legs — today vs with the classes"
            subtitle={income.class_swap.note}
            filename="ma-class-income-delta"
            columns={incomeCols}
            rows={incomeRows}
            compact
          />
        </>
      )}

      <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 22, maxWidth: 980, lineHeight: 1.6 }}>
        ③ The <b>P&amp;L / gross-profit</b> leg is deliberately NOT wired here. A bill payment and a device
        sale are revenue <i>with a cost</i>, a fee is an expense and a memo is a correction — feeding the
        classes there depends on the device-cost recognition policy and is a separate, owner-gated change.
        Nothing on this page touches the P&amp;L. Rep pay is never affected by any of this: it comes from
        POS sales × Incentive Plans, not from the MA daily file.
      </p>
    </div>
  )
}
