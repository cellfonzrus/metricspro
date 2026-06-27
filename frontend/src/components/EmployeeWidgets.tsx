'use client'
import { fmt } from '@/lib/client'
import Link from 'next/link'

// Shared employee widget grid — rendered by BOTH the admin /employee dashboard (pick-anyone) and the
// self-service kiosk /portal (scoped to the signed-in employee). Pure presentation: the caller fetches
// `data` (/core/employee-dashboard), `coach` (/commcalc/coaching) and `repTargets`
// (/commcalc/targets/.../calendar) and passes them in. Widget visibility is driven by data.widgets.

const cell: React.CSSProperties = { padding: '7px 10px', borderBottom: '1px solid var(--border)', fontSize: 13 }

// KPI keys for the report card (whole-number percents vs target).
const KPIS = [
  { k: 'atu', label: 'ATU', t: 55 }, { k: 'protect', label: 'Protect', t: 80 },
  { k: 'byod', label: 'BYOD', t: 35 }, { k: 'familyplan', label: 'Family', t: 45 },
  { k: 'tmr3', label: '3MR', t: 70 }, { k: 'aal', label: 'AAL', t: 5 },
]

function Card({ title, icon, children, right }: any) {
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '11px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ fontWeight: 700, fontSize: 14 }}>{icon} {title}</div>
        {right}
      </div>
      <div style={{ padding: 16 }}>{children}</div>
    </div>
  )
}

export default function EmployeeWidgets({ data, coach, repTargets }: { data: any; coach: any; repTargets: any }) {
  const w = data?.widgets || {}
  const on = (k: string) => !!w[k]

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 16, alignItems: 'start' }}>

      {coach && (
        <Card title="Coaching — what's costing you" icon="🎓">
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
            {(coach.kpis || []).map((kpi: any) => (
              <span key={kpi.kpi} style={{ fontSize: 11, padding: '3px 8px', borderRadius: 99, fontWeight: 600,
                background: kpi.met ? '#e6f7ec' : '#fde8e8', color: kpi.met ? '#16794a' : '#b42318' }}>
                {kpi.met ? '✓' : '✗'} {kpi.label} {kpi.actual}/{kpi.target}
              </span>
            ))}
          </div>
          {coach.tier < 1
            ? <div style={{ fontSize: 13 }}>💸 <b>{fmt(coach.at_risk)}</b> at risk — short on <b>{(coach.short_kpis || []).join(', ') || '—'}</b>{coach.need_for_full ? <> · hit <b>{coach.need_for_full}</b> more KPI(s) for full payout</> : null}.</div>
            : <div style={{ fontSize: 13, color: 'var(--green, #16794a)' }}>✅ Full tier — all KPIs on target.</div>}
          {coach.chargeback_deducted > 0 && <div style={{ fontSize: 13, color: '#b42318', marginTop: 4 }}>🔻 {fmt(coach.chargeback_deducted)} chargebacks deducted ({coach.chargeback_count}).</div>}
          <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 6 }}>On the table this period: <b style={{ color: coach.money_on_table > 0 ? '#b42318' : 'inherit' }}>{fmt(coach.money_on_table)}</b></div>
          {(coach.coaching_notes || []).length > 0 && <ul style={{ margin: '6px 0 0', paddingLeft: 16, fontSize: 12, color: 'var(--text3)' }}>{coach.coaching_notes.map((n: string, i: number) => <li key={i}>{n}</li>)}</ul>}
        </Card>
      )}

      {on('report_card') && (
        <Card title="Report Card" icon="🏅" right={<span className="badge" style={{ fontSize: 11 }}>{Math.round((data.report_card.tier || 0) * 100)}% tier</span>}>
          <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', marginBottom: 12 }}>
            <div><div style={{ fontSize: 11, color: 'var(--text3)' }}>KPIs Met</div><div style={{ fontSize: 20, fontWeight: 700 }}>{data.report_card.kpis_met ?? '—'}/{data.report_card.total_kpis ?? 7}</div></div>
            <div><div style={{ fontSize: 11, color: 'var(--text3)' }}>Commission</div><div style={{ fontSize: 20, fontWeight: 700 }}>{fmt(data.report_card.commission_earned || 0)}</div></div>
            <div><div style={{ fontSize: 11, color: 'var(--text3)' }}>Flags</div><div style={{ fontSize: 20, fontWeight: 700, color: data.report_card.flags_count ? 'var(--red)' : 'var(--text)' }}>{data.report_card.flags_count}</div></div>
            <div><div style={{ fontSize: 11, color: 'var(--text3)' }}>Chargebacks</div><div style={{ fontSize: 20, fontWeight: 700, color: data.report_card.chargebacks_count ? 'var(--red)' : 'var(--text)' }}>{data.report_card.chargebacks_count}</div></div>
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {KPIS.map(d => {
              const v = (data.report_card.kpi_values || {})[d.k]
              const met = v != null && v >= d.t
              return <span key={d.k} style={{ fontSize: 11, padding: '3px 8px', borderRadius: 999, background: v == null ? 'var(--surface2)' : met ? '#dcfce7' : '#fee2e2', color: v == null ? 'var(--text3)' : met ? '#166534' : '#991b1b', fontWeight: 600 }}>{d.label} {v != null ? v.toFixed(0) + '%' : '—'}</span>
            })}
          </div>
        </Card>
      )}

      {on('commission') && (
        <Card title="Commission Earned" icon="💰">
          {data.commission ? (
            <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
              <div><div style={{ fontSize: 11, color: 'var(--text3)' }}>Total Payout</div><div style={{ fontSize: 22, fontWeight: 700 }}>{fmt(data.commission.final_payout ?? data.commission.total_payout ?? 0)}</div></div>
              <div><div style={{ fontSize: 11, color: 'var(--text3)' }}>Tier</div><div style={{ fontSize: 22, fontWeight: 700 }}>{Math.round((data.commission.tier || 0) * 100)}%</div></div>
              <div><div style={{ fontSize: 11, color: 'var(--text3)' }}>Premium / Upg / BYOD</div><div style={{ fontSize: 14, fontWeight: 600, marginTop: 4 }}>{data.commission.premium_acts || 0} / {data.commission.upgrade_acts || 0} / {data.commission.byod_acts || 0}</div></div>
            </div>
          ) : <div style={{ color: 'var(--text3)', fontSize: 13 }}>No commission for {data.period}.</div>}
        </Card>
      )}

      {on('targets') && (
        <Card title="Targets" icon="🎯" right={<Link href="/commcalc/targets/my" style={{ fontSize: 12 }}>Open →</Link>}>
          {repTargets?.categories ? (
            <>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead><tr style={{ color: 'var(--text3)', fontSize: 11 }}>
                  {['', 'Today', 'Month', 'Done'].map((h, i) => <th key={i} style={{ textAlign: i ? 'right' : 'left', padding: '3px 6px' }}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {[['activations', 'Activations'], ['upgrades', 'Upgrades'], ['byod', 'BYOD'], ['accessories', 'Accessories']].map(([k, lbl]) => {
                    const m = repTargets.categories[k]; if (!m) return null
                    const money = m.unit !== 'count'
                    const v = (x: any) => x == null ? '—' : money ? fmt(x) : Math.round(x)
                    return <tr key={k} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '4px 6px', fontWeight: 600 }}>{lbl}</td>
                      <td style={{ padding: '4px 6px', textAlign: 'right', color: 'var(--accent)', fontWeight: 700 }}>{v(m.today_target)}</td>
                      <td style={{ padding: '4px 6px', textAlign: 'right' }}>{v(m.monthly)}</td>
                      <td style={{ padding: '4px 6px', textAlign: 'right', color: 'var(--text3)' }}>{v(m.achieved_mtd)}</td>
                    </tr>
                  })}
                </tbody>
              </table>
              {repTargets.rep_share != null && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6 }}>Your share: {Math.round(repTargets.rep_share * 100)}% of {repTargets.store_code} scheduled hours</div>}
            </>
          ) : (
            <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
              <div><div style={{ fontSize: 11, color: 'var(--text3)' }}>Accessory Target</div><div style={{ fontSize: 20, fontWeight: 700 }}>{data.targets.acc_target != null ? fmt(data.targets.acc_target) : '—'}</div></div>
              <div><div style={{ fontSize: 11, color: 'var(--text3)' }}>Accessory Achieved</div><div style={{ fontSize: 20, fontWeight: 700 }}>{data.targets.acc_comm != null ? fmt(data.targets.acc_comm) : '—'}</div></div>
            </div>
          )}
        </Card>
      )}

      {on('hours') && (
        <Card title="Hours Worked (this month)" icon="⏱️">
          <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
            <div><div style={{ fontSize: 11, color: 'var(--text3)' }}>Scheduled</div><div style={{ fontSize: 20, fontWeight: 700 }}>{data.hours.scheduled_hours}h</div></div>
            <div><div style={{ fontSize: 11, color: 'var(--text3)' }}>Actual</div><div style={{ fontSize: 20, fontWeight: 700 }}>{data.hours.actual_hours}h</div></div>
            <div><div style={{ fontSize: 11, color: 'var(--text3)' }}>Pay ({fmt(data.hours.pay_rate)}/hr)</div><div style={{ fontSize: 20, fontWeight: 700 }}>{fmt(data.hours.actual_pay)}</div></div>
          </div>
        </Card>
      )}

      {on('schedule') && (
        <Card title="Upcoming Schedule (7 days)" icon="📅">
          {data.schedule.length ? (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <tbody>{data.schedule.map((s: any) => (
                <tr key={s.id}><td style={cell}>{s.shift_date}</td><td style={cell}>{(s.start_time || '').slice(0, 5)}–{(s.end_time || '').slice(0, 5)}</td><td style={cell}>{s.store_code}</td><td style={{ ...cell, textAlign: 'right' }}>{s.scheduled_hours}h</td></tr>
              ))}</tbody>
            </table>
          ) : <div style={{ color: 'var(--text3)', fontSize: 13 }}>No shifts in the next 7 days.</div>}
        </Card>
      )}

      {on('timeoff') && (
        <Card title="Time Off" icon="🌴">
          <Link href="/storeops/timeoff" className="btn btn-primary" style={{ textDecoration: 'none' }}>Request time off →</Link>
        </Card>
      )}

      {on('commission_tracking') && (
        <Card title="Commission Tracking" icon="📈">
          {data.commission_tracking.length ? (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr><th style={{ ...cell, textAlign: 'left', color: 'var(--text2)' }}>Period</th><th style={{ ...cell, textAlign: 'right', color: 'var(--text2)' }}>Tier</th><th style={{ ...cell, textAlign: 'right', color: 'var(--text2)' }}>Payout</th></tr></thead>
              <tbody>{data.commission_tracking.slice(-8).reverse().map((c: any, i: number) => (
                <tr key={i}><td style={cell}>{c.period}</td><td style={{ ...cell, textAlign: 'right' }}>{Math.round((c.tier || 0) * 100)}%</td><td style={{ ...cell, textAlign: 'right', fontWeight: 600 }}>{fmt(c.total_payout || 0)}</td></tr>
              ))}</tbody>
            </table>
          ) : <div style={{ color: 'var(--text3)', fontSize: 13 }}>No commission history.</div>}
        </Card>
      )}

      {on('flags') && (
        <Card title="Flags" icon="🚩" right={<span style={{ fontSize: 12, color: data.flags.length ? 'var(--red)' : 'var(--text3)' }}>{data.flags.length}</span>}>
          {data.flags.length ? (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <tbody>{data.flags.slice(0, 12).map((f: any, i: number) => (
                <tr key={i}><td style={cell}>{(f.flag_type || '').replace(/_/g, ' ')}</td><td style={cell}>{f.mdn || f.imei || '—'}</td><td style={{ ...cell, textAlign: 'right' }}>{f.amount ? fmt(Math.abs(f.amount)) : '—'}</td></tr>
              ))}</tbody>
            </table>
          ) : <div style={{ color: 'var(--text3)', fontSize: 13 }}>No flags. 🎉</div>}
        </Card>
      )}

      {on('chargebacks') && (
        <Card title="Chargebacks" icon="↩️" right={<span style={{ fontSize: 12, color: data.chargebacks.length ? 'var(--red)' : 'var(--text3)' }}>{fmt(data.report_card.chargebacks_total || 0)}</span>}>
          {data.chargebacks.length ? (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <tbody>{data.chargebacks.slice(0, 12).map((c: any, i: number) => (
                <tr key={i}><td style={cell}>{c.reason || c.flag_type || 'Chargeback'}</td><td style={cell}>{c.mdn || c.imei || '—'}</td><td style={{ ...cell, textAlign: 'right' }}>{fmt(Math.abs(c.amount || 0))}</td></tr>
              ))}</tbody>
            </table>
          ) : <div style={{ color: 'var(--text3)', fontSize: 13 }}>No chargebacks. 🎉</div>}
        </Card>
      )}

    </div>
  )
}
