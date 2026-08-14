'use client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ReportShell } from '@/components/ReportShell'
import type { ExportColumn } from '@/lib/export'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'

// EXPECTED vs EARNED — multi-month commission, months 2..6 (configurable).
//
// OWNER, 2026-08-01: "let the system calculate the expected commission as a separate column but not use
// that to pay out, if the company gets paid the employee commission auto fills from there, there should
// be an option to move the expected commission to the earned column if the system malfunctions or the
// report is not updated on time, this will be done as an edit function gated per permission."
//
// EXPECTED is a COLUMN, NOT A PAYMENT. It is what a month would pay once the carrier pays us, and it is
// never added to anyone's commission, to rep_commissions or to the P&L. EARNED is what actually paid —
// automatically the moment the dealer is shown paid, or because someone with permission promoted it and
// recorded why. Promotions survive every recompute and are never paid at a stale figure.

type Row = {
  rep: string; store: string; label: string; device_category: string
  trans_id: string; mdn: string; imei: string
  sale_period: string; pay_period: string; month_index: number; payout_kind: string
  expected: number; earned: number; expected_in_window: boolean; unearned: number
  status: string; gate_met: boolean; gate_mode: string; gate_kind: string | null
  promoted: boolean; promoted_by: string | null; promoted_at: string | null
  promote_reason: string | null; promote_id: string | null; promote_stale: boolean
  promotable: boolean
}
type Data = {
  period: string
  config: { enabled: boolean; from_month: number; to_month: number; on_expected_change: string; promote_allow_unidentified: boolean }
  can_promote: boolean; can_promote_reason: string
  rows: Row[]
  by_rep: { rep: string; store: string; expected: number; earned: number; unearned: number; months: number; promoted_months: number }[]
  totals: { earned: number; expected_in_window: number; expected_not_yet_earned: number; promoted_amount: number; promotes_applied: number }
  expected_guard: any
  warnings: any[]
  money_note: string; ready: boolean; note: string | null
}

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

const ROW_COLS: ExportColumn[] = [
  { header: 'Rep', get: r => r.rep, role: 'rep' },
  { header: 'Store', get: r => r.store, role: 'store' },
  { header: 'Device / plan', get: r => r.label },
  { header: 'Month', get: r => `M${r.month_index}`, role: 'month' },
  { header: 'Sold in', get: r => r.sale_period },
  { header: 'IMEI', get: r => r.imei },
  { header: 'Mobile #', get: r => r.mdn },
  { header: 'Expected $', get: r => (r.expected_in_window ? r.expected : null), money: true },
  { header: 'Earned $', get: r => r.earned, money: true },
  { header: 'Not yet earned $', get: r => r.unearned, money: true },
  { header: 'Status', get: r => (r.promoted ? 'EARNED — manually promoted' : r.gate_met ? 'Earned' : 'Expected only') },
  { header: 'Promoted by', get: r => r.promoted_by },
  { header: 'Promoted at', get: r => r.promoted_at, type: 'date' },
  { header: 'Reason', get: r => r.promote_reason },
]

const REP_COLS: ExportColumn[] = [
  { header: 'Rep', get: r => r.rep, role: 'rep' },
  { header: 'Store', get: r => r.store, role: 'store' },
  { header: 'Months', get: r => r.months, type: 'number' },
  { header: 'Expected $', get: r => r.expected, money: true },
  { header: 'Earned $', get: r => r.earned, money: true },
  { header: 'Not yet earned $', get: r => r.unearned, money: true },
  { header: 'Manually promoted months', get: r => r.promoted_months, type: 'number' },
]

const AUDIT_COLS: ExportColumn[] = [
  { header: 'Promoted at', get: r => r.promoted_at, type: 'date' },
  { header: 'Promoted by', get: r => r.promoted_by },
  { header: 'Period', get: r => r.pay_period, role: 'month' },
  { header: 'Rep', get: r => r.epay_salesperson, role: 'rep' },
  { header: 'Store', get: r => r.store, role: 'store' },
  { header: 'Trans ID', get: r => r.trans_id },
  { header: 'Mobile #', get: r => r.mdn },
  { header: 'Month', get: r => `M${r.month_index}` },
  { header: 'Approved $', get: r => r.expected_at_promote, money: true },
  { header: 'Reason', get: r => r.reason },
  { header: 'Status', get: r => r.status },
  { header: 'Revoked by', get: r => r.revoked_by },
  { header: 'Revoked at', get: r => r.revoked_at, type: 'date' },
]

function Tile({ label, value, tone, sub }: { label: string; value: string; tone?: string; sub?: string }) {
  return (
    <div className="card" style={{ flex: '1 1 190px', minWidth: 180 }}>
      <div style={{ fontSize: 11.5, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: .4 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color: tone || 'var(--text)' }}>{value}</div>
      {sub && <div style={{ fontSize: 11.5, color: 'var(--text3)' }}>{sub}</div>}
    </div>
  )
}

export default function ExpectedCommissionPage() {
  const { period } = usePeriod()
  const [tab, setTab] = useState<'rows' | 'audit'>('rows')
  const [data, setData] = useState<Data | null>(null)
  const [audit, setAudit] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [promoting, setPromoting] = useState<Row | null>(null)
  const [reason, setReason] = useState('')

  const load = useCallback(() => {
    if (!period) return
    setBusy(true); setErr('')
    api(`/api/v1/commcalc/expected-commission/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      .then(setData)
      .catch(e => setErr(String(e?.message || e)))
      .finally(() => setBusy(false))
  }, [period])

  const loadAudit = useCallback(() => {
    api(`/api/v1/commcalc/expected-commission/promotes?org_id=${ORG_ID}`)
      .then(setAudit).catch(() => {})
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => { if (tab === 'audit') loadAudit() }, [tab, loadAudit])

  async function doPromote() {
    if (!promoting) return
    if (!reason.trim()) { setMsg('A reason is required — this writes a money audit record.'); return }
    setMsg('')
    try {
      const r = await api(`/api/v1/commcalc/expected-commission/promote?org_id=${ORG_ID}`, {
        method: 'POST',
        body: JSON.stringify({ period, trans_id: promoting.trans_id, mdn: promoting.mdn,
                               month_index: promoting.month_index, reason: reason.trim() }),
      })
      setMsg(r?.note || 'Recorded.')
      setPromoting(null); setReason(''); load()
    } catch (e: any) { setMsg(e.message) }
  }

  async function doRevoke(id: string) {
    setMsg('')
    try {
      const r = await api(`/api/v1/commcalc/expected-commission/revoke?org_id=${ORG_ID}`,
        { method: 'POST', body: JSON.stringify({ id }) })
      setMsg(r?.note || 'Revoked.')
      load(); loadAudit()
    } catch (e: any) { setMsg(e.message) }
  }

  const acc = useMemo(() => ({ store: (r: any) => r.store, rep: (r: any) => r.rep }), [])
  const shownRows = useMemo(() => filterRows(data?.rows || [], filt, acc), [data, filt, acc])
  const shownReps = useMemo(() => filterRows(data?.by_rep || [], filt, acc), [data, filt, acc])
  const opts = useMemo(() => optionsFromRows(data?.rows || [], acc), [data, acc])
  const t = data?.totals

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Expected vs Earned — multi-month incentive</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 940 }}>
          {period} · months <b>{data?.config?.from_month ?? 2}–{data?.config?.to_month ?? 6}</b> ·{' '}
          <b>Expected is a column, not a payment.</b> It is what a month will pay once the carrier pays
          us; it is never added to anyone&rsquo;s incentive. <b>Earned</b> fills in automatically the
          moment the dealer is shown paid.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
        <button className={`btn ${tab === 'rows' ? 'btn-primary' : ''}`} onClick={() => setTab('rows')}>📊 Expected vs earned</button>
        <button className={`btn ${tab === 'audit' ? 'btn-primary' : ''}`} onClick={() => setTab('audit')}>🧾 Promotion audit trail</button>
      </div>

      {err && <div className="card" style={{ borderLeft: '4px solid var(--red)', marginBottom: 14, fontSize: 13 }}>{err}</div>}
      {msg && <div className="card" style={{ borderLeft: '4px solid var(--blue)', marginBottom: 14, fontSize: 13 }}>{msg}</div>}

      {tab === 'rows' && (
        <>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
            <Tile label="Earned this period" value={fmt(t?.earned || 0)} tone="var(--green)"
                  sub="what actually pays" />
            <Tile label="Expected (months in window)" value={fmt(t?.expected_in_window || 0)}
                  sub="a column — pays nobody" />
            <Tile label="Expected, not yet earned" value={fmt(t?.expected_not_yet_earned || 0)}
                  tone="var(--amber)" sub="waiting on the carrier" />
            <Tile label="Manually promoted" value={fmt(t?.promoted_amount || 0)}
                  sub={`${t?.promotes_applied || 0} month(s), each with a named approver`} />
          </div>

          <div className="card" style={{ marginBottom: 14, fontSize: 12.5, borderLeft: '4px solid var(--blue)' }}>
            {data?.money_note}
          </div>

          {(data?.warnings || []).length > 0 && (
            <div className="card" style={{ marginBottom: 14, borderLeft: '4px solid var(--amber)', fontSize: 12.5 }}>
              <div style={{ fontWeight: 700, marginBottom: 4 }}>Things you should know about the promotions</div>
              {(data?.warnings || []).map((w: any, i: number) => (
                <div key={i} style={{ color: w.type === 'promote_expected_changed' ? 'var(--red)' : 'var(--text2)' }}>
                  • {w.detail}
                </div>
              ))}
            </div>
          )}

          <div className="card" style={{ marginBottom: 14 }}>
            <StandardFilterBar
              value={filt} onChange={setFilt} periodMode="none"
              show={{ period: false, stores: true, markets: false, reps: true }}
              storeOptions={opts.stores} repOptions={opts.reps}
              right={<button className="btn btn-secondary" onClick={load} disabled={busy}>{busy ? 'Loading…' : 'Refresh'}</button>}
            />
            <div style={{ fontSize: 11.5, color: 'var(--text3)' }}>
              Market is not offered here — the installment ledger carries no market column, so a market
              filter would be a guess rather than a filter.
            </div>
          </div>

          {data && !data.can_promote && (
            <div className="card" style={{ marginBottom: 14, borderLeft: '4px solid var(--text3)', fontSize: 12.5, color: 'var(--text2)' }}>
              You can see this report but not change it. Moving an expected amount into earned is a money
              action gated by the <b>commission_promote</b> permission
              {data.can_promote_reason === 'unidentified_caller'
                ? ' and must be done by a signed-in user, so the record names who approved it.' : '.'}
            </div>
          )}

          <div style={{ marginBottom: 14 }}>
            <ReportShell
              title={`By rep — ${period}`}
              subtitle="Expected and earned side by side. Only 'Earned' is money."
              filename={`expected-vs-earned-by-rep-${period}`}
              columns={REP_COLS} rows={shownReps} compact
            />
          </div>

          <ReportShell
            title={`Every multi-month installment — ${period}`}
            subtitle={`Months ${data?.config?.from_month ?? 2}–${data?.config?.to_month ?? 6} carry an expected amount. Sorted by what is still owed.`}
            filename={`expected-vs-earned-${period}`}
            columns={ROW_COLS} rows={shownRows} compact stickyHeader
            rowStyle={(r: any) => (r.promote_stale ? { background: '#fef2f2' }
              : r.promoted ? { background: '#f0fdf4' } : undefined)}
          >
            {data?.can_promote && (
              <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>
                To move a month into <b>Earned</b>, pick it below. You will be asked for a reason — it is
                recorded with your name and survives every recalculation.
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 8 }}>
                  <select style={sel} value={promoting ? `${promoting.trans_id}|${promoting.mdn}|${promoting.month_index}` : ''}
                    onChange={e => {
                      const v = e.target.value
                      setPromoting(v ? (shownRows.find((r: any) => `${r.trans_id}|${r.mdn}|${r.month_index}` === v) || null) : null)
                    }}>
                    <option value="">— pick a month waiting on the carrier —</option>
                    {shownRows.filter((r: any) => r.promotable && !r.promoted).map((r: any) => (
                      <option key={`${r.trans_id}|${r.mdn}|${r.month_index}`} value={`${r.trans_id}|${r.mdn}|${r.month_index}`}>
                        {r.rep} · M{r.month_index} · {r.label || r.imei || r.mdn} · expected {fmt(r.expected)}
                      </option>
                    ))}
                  </select>
                  <input style={{ ...sel, width: 320 }} placeholder="reason (required — e.g. carrier report late)"
                    value={reason} onChange={e => setReason(e.target.value)} />
                  <button className="btn btn-primary" disabled={!promoting || !reason.trim()} onClick={doPromote}>
                    Move to Earned{promoting ? ` — ${fmt(promoting.expected)}` : ''}
                  </button>
                </div>
              </div>
            )}
          </ReportShell>
        </>
      )}

      {tab === 'audit' && (
        <>
          <div className="card" style={{ marginBottom: 14, fontSize: 12.5, borderLeft: '4px solid var(--blue)' }}>
            Every promotion ever made, including revoked ones — a record you can delete would not be a
            record. {audit?.active ?? 0} active · {audit?.revoked ?? 0} revoked.
          </div>
          <ReportShell
            title="Promotion audit trail"
            subtitle="Who moved which month into earned, when, and why."
            filename="expected-commission-promotions"
            columns={AUDIT_COLS} rows={audit?.promotes || []} compact stickyHeader
            onRowClick={(r: any) => {
              if (data?.can_promote && String(r.status) === 'active'
                  && confirm(`Revoke this promotion for M${r.month_index}? The month goes back to waiting on the carrier.`)) {
                doRevoke(r.id)
              }
            }}
          />
        </>
      )}
    </div>
  )
}
