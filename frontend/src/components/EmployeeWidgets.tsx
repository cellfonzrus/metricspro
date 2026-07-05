'use client'
import { fmt } from '@/lib/client'
import {
  ResponsiveContainer, AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip,
  CartesianGrid, Cell, ReferenceLine,
} from 'recharts'

// Shared employee widget grid — rendered by BOTH the admin /employee dashboard (pick-anyone) and the
// self-service kiosk /portal (scoped to the signed-in employee). Pure presentation: the caller fetches
// `data` (/core/employee-dashboard), `coach` (/commcalc/coaching) and `repTargets`
// (/commcalc/targets/.../calendar) and passes them in. Widget visibility is driven by data.widgets.

const cell: React.CSSProperties = { padding: '7px 10px', borderBottom: '1px solid var(--border)', fontSize: 13 }
const fmtN = (n: any, d = 0) => (n == null ? '—' : Number(n).toLocaleString('en-US', { maximumFractionDigits: d }))
const shortPeriod = (p: string) => {
  const m = String(p || '').match(/^([A-Za-z]+)\s+(\d{4})$/)
  if (m) return `${m[1].slice(0, 3)} '${m[2].slice(2)}`
  const m2 = String(p || '').match(/^(\d{4})-(\d{2})$/)
  if (m2) { const mn = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][+m2[2]]; return `${mn} '${m2[1].slice(2)}` }
  return p
}

// KPI keys for the report card (whole-number percents vs target).
const KPIS = [
  { k: 'atu', label: 'ATU', t: 55 }, { k: 'protect', label: 'Protect', t: 80 },
  { k: 'byod', label: 'BYOD', t: 35 }, { k: 'familyplan', label: 'Family', t: 45 },
  { k: 'tmr3', label: '3MR', t: 70 }, { k: 'aal', label: 'AAL', t: 5 },
]

// Commission earning components (what was earned, and for what).
const COMP_LINES = [
  { k: 'premium_comm', label: 'Premium activations', acts: 'premium_acts' },
  { k: 'upgrade_comm', label: 'Upgrades', acts: 'upgrade_acts' },
  { k: 'byod_comm', label: 'BYOD', acts: 'byod_acts' },
  { k: 'acc_comm', label: 'Accessories (GP)' },
  { k: 'setup_fee_comm', label: 'Setup fees' },
  { k: 'trade_in_comm', label: 'Trade-in', optional: true },
  { k: 'custom_comm', label: 'Custom', optional: true },
  { k: 'acima_comm', label: 'Acima', optional: true },
]

function Card({ title, icon, children, right }: any) {
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden', boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
      <div style={{ padding: '11px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ fontWeight: 700, fontSize: 14 }}>{icon} {title}</div>
        {right}
      </div>
      <div style={{ padding: 16 }}>{children}</div>
    </div>
  )
}

function Stat({ label, value, color }: { label: string; value: any; color?: string }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: 'var(--text3)' }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color }}>{value}</div>
    </div>
  )
}

// horizontal progress bar (achieved vs target)
function Progress({ pct, color = 'var(--accent)' }: { pct: number; color?: string }) {
  const w = Math.max(0, Math.min(100, pct || 0))
  return (
    <div style={{ height: 7, background: 'var(--surface2)', borderRadius: 99, overflow: 'hidden', marginTop: 5 }}>
      <div style={{ width: `${w}%`, height: '100%', background: color, borderRadius: 99, transition: 'width .3s' }} />
    </div>
  )
}

export default function EmployeeWidgets({ data, coach, repTargets }: { data: any; coach: any; repTargets: any }) {
  const w = data?.widgets || {}
  const on = (k: string) => !!w[k]
  const c = data?.commission || null

  // commission trend (oldest→newest, last 8 periods)
  const trend = (data?.commission_tracking || []).slice(-8).map((r: any) => ({
    name: shortPeriod(r.period), payout: Number(r.total_payout || 0), tier: Math.round((r.tier || 0) * 100),
  }))
  // kpi bars
  const kpiData = KPIS.map(d => {
    const v = (c?.kpi_values || data?.report_card?.kpi_values || {})[d.k]
    return { name: d.label, value: v == null ? 0 : Number(v), target: d.t, has: v != null, met: v != null && v >= d.t }
  })

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 16, alignItems: 'start' }}>

      {on('phone_priority') && (data?.phone_priority?.length > 0) && (
        <Card title="Sell These Phones Today" icon="📱">
          <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>
            These are in the final stretch of their pay window — prioritize selling them so the store doesn't owe the vendor.
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <tbody>
              {(data.phone_priority || []).map((p: any, i: number) => (
                <tr key={i}>
                  <td style={cell}>{p.device_model || '—'}</td>
                  <td style={{ ...cell, fontFamily: 'monospace', fontSize: 11 }}>{p.imei}</td>
                  <td style={{ ...cell, textAlign: 'right', color: '#d97706', fontWeight: 600 }}>due {p.window_end || p.due_date || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

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

      {on('commission') && (
        <Card title="Commission Earned" icon="💰" right={c ? <span className="badge" style={{ fontSize: 11 }}>tier {Math.round((c.tier || 0) * 100)}%</span> : null}>
          {c ? (
            <>
              <div style={{ textAlign: 'center', marginBottom: 12 }}>
                <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '.05em' }}>Your payout · {data.period}</div>
                <div style={{ fontSize: 34, fontWeight: 800, color: 'var(--accent)', lineHeight: 1.1 }}>{fmt(c.final_payout ?? c.total_payout ?? 0)}</div>
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <tbody>
                  {COMP_LINES.map(line => {
                    const amt = Number(c[line.k] || 0)
                    if (line.optional && !amt) return null
                    const acts = line.acts ? c[line.acts] : null
                    return (
                      <tr key={line.k} style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={{ padding: '5px 4px' }}>{line.label}{acts != null ? <span style={{ color: 'var(--text3)' }}> · {fmtN(acts)}</span> : null}</td>
                        <td style={{ padding: '5px 4px', textAlign: 'right', fontWeight: 600 }}>{fmt(amt)}</td>
                      </tr>
                    )
                  })}
                  <tr style={{ borderTop: '2px solid var(--border)' }}>
                    <td style={{ padding: '6px 4px', color: 'var(--text2)' }}>Subtotal</td>
                    <td style={{ padding: '6px 4px', textAlign: 'right', fontWeight: 700 }}>{fmt(c.subtotal ?? 0)}</td>
                  </tr>
                  <tr>
                    <td style={{ padding: '4px 4px', color: 'var(--text2)' }}>× Tier{c.tier_source ? ` (${c.tier_source})` : ''}</td>
                    <td style={{ padding: '4px 4px', textAlign: 'right' }}>{Math.round((c.tier || 0) * 100)}%</td>
                  </tr>
                  {Number(c.chargeback_deduction || 0) > 0 && (
                    <tr><td style={{ padding: '4px 4px', color: '#b42318' }}>− Chargebacks</td><td style={{ padding: '4px 4px', textAlign: 'right', color: '#b42318' }}>−{fmt(c.chargeback_deduction)}</td></tr>
                  )}
                  <tr style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '6px 4px', fontWeight: 700 }}>Final payout</td>
                    <td style={{ padding: '6px 4px', textAlign: 'right', fontWeight: 800, color: 'var(--accent)' }}>{fmt(c.final_payout ?? c.total_payout ?? 0)}</td>
                  </tr>
                </tbody>
              </table>
            </>
          ) : <div style={{ color: 'var(--text3)', fontSize: 13 }}>No commission for {data.period}.</div>}
        </Card>
      )}

      {on('commission_tracking') && trend.length > 0 && (
        <Card title="Payout Trend" icon="📈" right={<span style={{ fontSize: 11, color: 'var(--text3)' }}>last {trend.length} mo</span>}>
          <ResponsiveContainer width="100%" height={170}>
            <AreaChart data={trend} margin={{ top: 6, right: 6, left: -18, bottom: 0 }}>
              <defs><linearGradient id="payoutFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#2e75b6" stopOpacity={0.35} /><stop offset="100%" stopColor="#2e75b6" stopOpacity={0} />
              </linearGradient></defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
              <XAxis dataKey="name" tick={{ fontSize: 11 }} stroke="var(--text3)" />
              <YAxis tick={{ fontSize: 11 }} stroke="var(--text3)" tickFormatter={(v: number) => `$${v >= 1000 ? (v / 1000).toFixed(0) + 'k' : v}`} />
              <Tooltip formatter={(v: any) => fmt(Number(v))} labelStyle={{ fontSize: 12 }} contentStyle={{ fontSize: 12, borderRadius: 8 }} />
              <Area type="monotone" dataKey="payout" stroke="#2e75b6" strokeWidth={2} fill="url(#payoutFill)" />
            </AreaChart>
          </ResponsiveContainer>
        </Card>
      )}

      {on('report_card') && (
        <Card title="Report Card" icon="🏅" right={<span className="badge" style={{ fontSize: 11 }}>{Math.round((data.report_card.tier || 0) * 100)}% tier</span>}>
          <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', marginBottom: 12 }}>
            <Stat label="KPIs Met" value={`${data.report_card.kpis_met ?? '—'}/${data.report_card.total_kpis ?? 7}`} />
            <Stat label="Commission" value={fmt(data.report_card.commission_earned || 0)} />
            <Stat label="Flags" value={data.report_card.flags_count} color={data.report_card.flags_count ? 'var(--red)' : undefined} />
            <Stat label="Chargebacks" value={data.report_card.chargebacks_count} color={data.report_card.chargebacks_count ? 'var(--red)' : undefined} />
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 4 }}>KPIs vs target</div>
          <ResponsiveContainer width="100%" height={150}>
            <BarChart data={kpiData} margin={{ top: 4, right: 6, left: -22, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
              <XAxis dataKey="name" tick={{ fontSize: 10 }} stroke="var(--text3)" />
              <YAxis tick={{ fontSize: 10 }} stroke="var(--text3)" />
              <Tooltip formatter={(v: any, _n: any, p: any) => [`${Number(v).toFixed(1)}% (target ${p?.payload?.target}%)`, p?.payload?.name]} contentStyle={{ fontSize: 12, borderRadius: 8 }} />
              <Bar dataKey="value" radius={[3, 3, 0, 0]}>
                {kpiData.map((d, i) => <Cell key={i} fill={!d.has ? '#cbd5e1' : d.met ? '#16a34a' : '#dc2626'} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>
      )}

      {on('targets') && (
        <Card title="My Targets" icon="🎯" right={repTargets?.scheduled_hours_total != null ? <span style={{ fontSize: 11, color: 'var(--text3)' }}>{fmtN(repTargets.scheduled_hours_total)}h sched</span> : null}>
          {repTargets?.categories ? (
            <div style={{ display: 'grid', gap: 12 }}>
              {[['activations', 'Activations'], ['upgrades', 'Upgrades'], ['byod', 'BYOD'], ['accessories', 'Accessories']].map(([k, lbl]) => {
                const m = repTargets.categories[k]; if (!m) return null
                const money = m.unit !== 'count'
                const v = (x: any) => x == null ? '—' : money ? fmt(x) : fmtN(x, 1)
                const pct = m.monthly ? (Number(m.achieved_mtd || 0) / Number(m.monthly)) * 100 : 0
                return (
                  <div key={k}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', fontSize: 13 }}>
                      <span style={{ fontWeight: 600 }}>{lbl}</span>
                      <span style={{ color: 'var(--text3)', fontSize: 12 }}>
                        today <b style={{ color: 'var(--accent)' }}>{v(m.today_target)}</b> · pace {v(m.pace)}
                      </span>
                    </div>
                    <Progress pct={pct} color={pct >= 100 ? '#16a34a' : 'var(--accent2, #2e75b6)'} />
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text3)', marginTop: 3 }}>
                      <span>{v(m.achieved_mtd)} of {v(m.monthly)}</span>
                      <span>{m.need > 0 ? <span style={{ color: '#b45309' }}>{v(m.need)} to go</span> : <span style={{ color: 'var(--green)' }}>✓ on track</span>}</span>
                    </div>
                  </div>
                )
              })}
              {repTargets.conversion?.rep && (
                <div style={{ borderTop: '1px solid var(--border)', paddingTop: 8, fontSize: 12, color: 'var(--text2)' }}>
                  Conversion: <b style={{ color: repTargets.conversion.rep.rate >= repTargets.conversion.store.target ? 'var(--green)' : '#dc2626' }}>{repTargets.conversion.rep.rate}%</b>
                  {' '}vs store {repTargets.conversion.store.rate}% (target {repTargets.conversion.store.target}%)
                </div>
              )}
            </div>
          ) : (
            <div style={{ color: 'var(--text3)', fontSize: 13 }}>
              Targets unavailable — ask an admin to set your <b>home store</b> in Employees so your daily goals can be computed.
            </div>
          )}
        </Card>
      )}

      {on('hours') && (
        <Card title="Hours Worked (this month)" icon="⏱️">
          <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', marginBottom: 10 }}>
            <Stat label="Scheduled" value={`${data.hours.scheduled_hours}h`} />
            <Stat label="Actual" value={`${data.hours.actual_hours}h`} />
            <Stat label={`Pay (${fmt(data.hours.pay_rate)}/hr)`} value={fmt(data.hours.actual_pay)} />
          </div>
          <ResponsiveContainer width="100%" height={90}>
            <BarChart layout="vertical" data={[{ name: 'Scheduled', v: data.hours.scheduled_hours }, { name: 'Actual', v: data.hours.actual_hours }]} margin={{ top: 0, right: 10, left: 6, bottom: 0 }}>
              <XAxis type="number" hide /><YAxis type="category" dataKey="name" tick={{ fontSize: 11 }} stroke="var(--text3)" width={66} />
              <Tooltip formatter={(v: any) => `${v}h`} contentStyle={{ fontSize: 12, borderRadius: 8 }} />
              <Bar dataKey="v" radius={[0, 4, 4, 0]} barSize={16}>
                <Cell fill="#94a3b8" /><Cell fill="#2e75b6" />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
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
          <a href="/storeops/timeoff" className="btn btn-primary" style={{ textDecoration: 'none' }}>Request time off →</a>
        </Card>
      )}

      {on('flags') && (
        <Card title="Flags" icon="🚩" right={<span style={{ fontSize: 12, color: data.flags.length ? 'var(--red)' : 'var(--text3)' }}>{data.flags.length}</span>}>
          {data.flags.length ? (
            <div style={{ display: 'grid', gap: 8 }}>
              {data.flags.slice(0, 12).map((f: any, i: number) => {
                const sev = (f.severity || '').toLowerCase()
                const color = sev === 'critical' ? '#dc2626' : sev === 'warning' ? '#d97706' : '#2563eb'
                return (
                  <div key={i} style={{ borderLeft: `4px solid ${color}`, background: 'var(--surface2)', borderRadius: 8, padding: '8px 10px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
                      <span style={{ fontWeight: 600, fontSize: 12, color, textTransform: 'capitalize' }}>{(f.flag_type || 'flag').replace(/_/g, ' ')}</span>
                      {f.amount != null && <span style={{ fontSize: 12, fontWeight: 600 }}>{fmt(Math.abs(f.amount))}</span>}
                    </div>
                    {f.description && <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>{f.description}</div>}
                    <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>
                      {[f.store_address, f.phone_model, f.mdn || f.imei, f.transaction_date].filter(Boolean).join(' · ') || sev}
                    </div>
                  </div>
                )
              })}
              {data.flags.length > 12 && <div style={{ fontSize: 12, color: 'var(--text3)', textAlign: 'center' }}>+{data.flags.length - 12} more</div>}
            </div>
          ) : <div style={{ color: 'var(--text3)', fontSize: 13 }}>No flags. 🎉</div>}
        </Card>
      )}

      {on('chargebacks') && (
        <Card title="Chargebacks" icon="↩️" right={<span style={{ fontSize: 12, color: data.chargebacks.length ? 'var(--red)' : 'var(--text3)' }}>{fmt(data.report_card.chargebacks_total || 0)}</span>}>
          {data.chargebacks.length ? (
            <div style={{ display: 'grid', gap: 8 }}>
              {data.chargebacks.slice(0, 12).map((cb: any, i: number) => (
                <div key={i} style={{ borderLeft: '4px solid #dc2626', background: 'var(--surface2)', borderRadius: 8, padding: '8px 10px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <span style={{ fontWeight: 600, fontSize: 12 }}>{cb.reason || cb.flag_type || 'Chargeback'}</span>
                    <span style={{ fontSize: 12, fontWeight: 600, color: '#dc2626' }}>−{fmt(Math.abs(cb.amount || 0))}</span>
                  </div>
                  {cb.description && <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>{cb.description}</div>}
                  {(cb.mdn || cb.imei) && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{cb.mdn || cb.imei}</div>}
                </div>
              ))}
            </div>
          ) : <div style={{ color: 'var(--text3)', fontSize: 13 }}>No chargebacks. 🎉</div>}
        </Card>
      )}

    </div>
  )
}
