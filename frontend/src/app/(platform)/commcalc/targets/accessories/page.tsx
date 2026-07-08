'use client'
import { useState, useEffect, Fragment } from 'react'
import { api, ORG_ID, fmt, localToday } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ExportButtons, ExportPayload } from '@/lib/export'

// Accessory Sales target tracker: per store, the monthly accessory-$ target vs achieved MTD vs what's
// still needed (total + per-remaining-day pace), with a behind/on-track flag. Reuses the Daily Targets
// summary endpoint (categories.accessories). Expand a store to see each rep's accessory contribution.
type Acc = { unit: string; monthly: number; achieved_mtd: number; need: number; base_today: number; today_target: number; pace: number; open_days_left: number }

export default function AccessoryTargetsPage() {
  const { period } = usePeriod()
  const [rows, setRows] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState<Record<string, boolean>>({})

  useEffect(() => {
    setLoading(true)
    api(`/api/v1/commcalc/targets/${encodeURIComponent(period)}/summary?org_id=${ORG_ID}&today=${localToday()}&include_untargeted=1`)
      .then((d: any) => setRows((d.stores || []).filter((s: any) => {
        const a = s.categories?.accessories || {}
        // Show a store if it has an accessory target OR any accessory sales achieved this month —
        // so accessory $ is tracked even before per-store targets are configured.
        return (a.monthly || 0) > 0 || (a.achieved_mtd || 0) > 0
      })))
      .catch(console.error).finally(() => setLoading(false))
  }, [period])

  const acc = (s: any): Acc => s.categories?.accessories || { unit: 'dollars', monthly: 0, achieved_mtd: 0, need: 0, base_today: 0, today_target: 0, pace: 0, open_days_left: 0 }
  const pct = (a: Acc) => a.monthly ? Math.min(100, Math.round(100 * a.achieved_mtd / a.monthly)) : 0
  const onTrack = (a: Acc) => a.achieved_mtd >= (a.base_today || 0) - 0.01

  const tot = rows.reduce((t, s) => {
    const a = acc(s); t.monthly += a.monthly; t.achieved += a.achieved_mtd; t.need += Math.max(0, a.need); t.today += a.today_target; return t
  }, { monthly: 0, achieved: 0, need: 0, today: 0 })

  function buildPayload(): ExportPayload {
    return {
      title: 'Accessory Sales Targets', subtitle: period, filename: `accessory-targets_${period.replace(/\s+/g, '-')}`,
      sheets: [{ name: 'By store', rows, columns: [
        { header: 'Store', get: (s: any) => s.address || s.store_code },
        { header: 'Target $', get: (s: any) => acc(s).monthly, money: true },
        { header: 'Achieved MTD $', get: (s: any) => acc(s).achieved_mtd, money: true },
        { header: '% to goal', get: (s: any) => pct(acc(s)) },
        { header: 'Remaining $', get: (s: any) => Math.max(0, acc(s).need), money: true },
        { header: "Today's target $", get: (s: any) => acc(s).today_target, money: true },
        { header: '$/day needed', get: (s: any) => acc(s).pace, money: true },
        { header: 'Open days left', get: (s: any) => acc(s).open_days_left },
        { header: 'Status', get: (s: any) => onTrack(acc(s)) ? 'On track' : 'Behind' },
      ] }],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <a href="/commcalc/targets" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← Daily Targets</a>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 0' }}>🔖 Accessory Sales Targets</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 720 }}>
            Per store: the monthly accessory‑$ goal, achieved so far this month, and what's still needed —
            total remaining, today's target, and the $/day pace for the days left.
          </p>
        </div>
        {rows.length > 0 && <ExportButtons payload={buildPayload} />}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : rows.length === 0 ? (
        <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>
          No accessory targets set for {period}. Set them in Target Settings (accessory $ per store).
        </div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 14, marginBottom: 16 }}>
            <Stat label="Accessory target (all stores)" value={fmt(tot.monthly)} />
            <Stat label="Achieved MTD" value={fmt(tot.achieved)} color="#16a34a" sub={tot.monthly ? `${Math.round(100 * tot.achieved / tot.monthly)}% to goal` : undefined} />
            <Stat label="Still needed" value={fmt(tot.need)} color="#d97706" />
            <Stat label="Needed today" value={fmt(tot.today)} color="var(--accent)" />
          </div>

          <div className="card" style={{ padding: 0, overflow: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 900 }}>
              <thead><tr style={{ background: 'var(--surface2)', fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                {['Store', 'Target', 'Achieved', '% to goal', 'Remaining', "Today's target", '$/day needed', 'Days left', 'Status'].map(h =>
                  <th key={h} style={{ textAlign: h === 'Store' ? 'left' : 'right', padding: '9px 12px', whiteSpace: 'nowrap' }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {rows.map(s => {
                  const a = acc(s); const p = pct(a); const noTarget = (a.monthly || 0) <= 0; const ok = onTrack(a)
                  return (
                    <Fragment key={s.store_code}>
                      <tr onClick={() => setOpen(o => ({ ...o, [s.store_code]: !o[s.store_code] }))}
                        style={{ borderTop: '1px solid var(--border)', cursor: 'pointer', background: ok ? undefined : '#fffaf5' }}>
                        <td style={{ padding: '9px 12px', fontSize: 13, fontWeight: 600 }}>{(s.reps?.length) ? (open[s.store_code] ? '▾ ' : '▸ ') : ''}{s.address || s.store_code}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(a.monthly)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13, color: '#16a34a' }}>{fmt(a.achieved_mtd)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 12 }}>
                          <div style={{ display: 'inline-block', width: 70, height: 7, background: 'var(--surface2)', borderRadius: 4, overflow: 'hidden', verticalAlign: 'middle', marginRight: 6 }}>
                            <div style={{ width: `${p}%`, height: '100%', background: ok ? '#16a34a' : '#d97706' }} />
                          </div>{p}%
                        </td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13, fontWeight: 600 }}>{fmt(Math.max(0, a.need))}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(a.today_target)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(a.pace)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13, color: 'var(--text2)' }}>{a.open_days_left}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 12 }}>
                          {noTarget
                            ? <span style={{ background: 'var(--surface2)', color: 'var(--text2)', borderRadius: 5, padding: '1px 8px', fontWeight: 600 }}>No target</span>
                            : <span style={{ background: ok ? '#dcfce7' : '#ffedd5', color: ok ? '#065f46' : '#9a3412', borderRadius: 5, padding: '1px 8px', fontWeight: 600 }}>{ok ? 'On track' : 'Behind'}</span>}
                        </td>
                      </tr>
                      {open[s.store_code] && (s.reps || []).map((rp: any, i: number) => (
                        <tr key={s.store_code + '_' + i} style={{ background: 'var(--surface2)', fontSize: 12 }}>
                          <td style={{ padding: '5px 12px 5px 30px', color: 'var(--text2)' }}>{rp.rep || '(unnamed)'}</td>
                          <td colSpan={1} />
                          <td style={{ padding: '5px 12px', textAlign: 'right', color: '#16a34a' }}>{fmt(rp.accessories || 0)}</td>
                          <td colSpan={6} style={{ padding: '5px 12px', color: 'var(--text3)' }}>accessory $ contributed</td>
                        </tr>
                      ))}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
          <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 12 }}>
            "Behind" = achieved MTD is under the pace expected by today. "$/day needed" spreads the remaining target over the open days left in the month. Stores with accessory sales but marked <b>No target</b> still appear here so achieved $ is tracked — set a target for them in Target Settings to get pacing. Achieved MTD is accessory sales revenue (matches rep commissions).
          </p>
        </>
      )}
    </div>
  )
}

function Stat({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return <div className="card" style={{ padding: '14px 16px' }}>
    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '.05em' }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, color: color || 'var(--text1)' }}>{value}</div>
    {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
  </div>
}
