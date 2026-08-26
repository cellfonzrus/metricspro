'use client'
// ATU (autopay) opportunity — card-paying customers who are NOT enrolled, and what that costs.
//
// Owner directive 2026-08-12: "the logic is if the customer is paying with a credit card then the
// customer can save $9 per month for doing auto pay and the store makes 5% extra ATU commission on the
// boost side and 8.5% on the total side — note the saving numbers and the income numbers will be
// entered by the user as they will change."
//
// So the four numbers are INPUTS. They live in commcalc.atu_config (mig 295) and are edited here, and
// every figure on the page re-derives from them. Nothing is hard-coded.
import { useState, useEffect, useCallback } from 'react'
import { api, getActiveOrg } from '@/lib/client'
import { useActiveCarrier } from '@/lib/auth-context'
import { atuActiveCarry } from '@/lib/carrier-scope'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'
import { MultiSelect } from '@/lib/multiselect'

// Same super-admin org-resolution mitigation the Sales and Custom reports carry: these reads send no
// org_id, so a super-admin (whom the tenant middleware does not rewrite) would fall back to the HOUSE
// org and silently read the wrong tenant. No-op for everyone else.
const orgParam = () => { const o = getActiveOrg(); return o ? `&org_id=${encodeURIComponent(o)}` : '' }
// The same value as a LEADING query string, for URLs that carry no other params — `${orgParam()}` alone
// would emit `/atu-config&org_id=…`, which the server parses as part of the path, not a param.
const orgQuery = () => { const p = orgParam(); return p ? '?' + p.slice(1) : '' }

const thisMonth = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}` }
const money = (n: number | undefined) => '$' + Math.round(n || 0).toLocaleString('en-US')
const inp: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', width: 96 }

type StoreRow = { store: string; market?: string; card_acts: number; card_atu: number; card_open: number; attach_pct: number; rtr_open: number; carry_forgone: number }

export default function AtuOpportunityPage() {
  // Active-carrier lens: show only the active carrier's ATU rate/base/carry. The backend keeps
  // returning BOTH carriers' figures (boost_carry_monthly + total_carry_monthly); we pick one and
  // NEVER show the combined carry_monthly. Single-carrier tenants are unchanged.
  const { activeCarrier } = useActiveCarrier()
  const isTotal = activeCarrier === 'total'
  const [period, setPeriod] = useState(thisMonth())
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [selStores, setSelStores] = useState<string[]>([])
  const [selMarkets, setSelMarkets] = useState<string[]>([])
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [cfg, setCfg] = useState<any>(null)
  const [showCfg, setShowCfg] = useState(false)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  const run = useCallback(() => {
    setLoading(true)
    const qs = new URLSearchParams({ period, start, end, stores: selStores.join(','), markets: selMarkets.join(',') })
    api(`/api/v1/commcalc/atu-opportunity?${qs.toString()}${orgParam()}`)
      .then((r: any) => { setData(r); if (!cfg) setCfg(r.config) })
      .catch((e: any) => setData({ error: String(e?.message || e) }))
      .finally(() => setLoading(false))
  }, [period, start, end, selStores, selMarkets, cfg])
  useEffect(() => { run() }, [run])

  async function saveCfg() {
    setSaving(true); setMsg('')
    try { await api(`/api/v1/commcalc/atu-config${orgQuery()}`, { method: 'POST', body: JSON.stringify(cfg) }); setMsg('Saved ✓'); run() }
    catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) }
    setSaving(false)
  }

  const s = data?.summary
  const c = s?.customers, m = s?.money, rc = s?.recharge
  const stores: StoreRow[] = data?.stores || []
  // The active carrier's commission carry — NEVER the combined carry_monthly (which sums both carriers).
  const activeCarry = atuActiveCarry(m, activeCarrier)

  const cols: ExportColumn[] = [
    { header: 'Store', field: 'store', get: (r: StoreRow) => r.store },
    { header: 'Market', field: 'market', get: (r: StoreRow) => r.market || '' },
    { header: 'Card activations', field: 'card_acts', get: (r: StoreRow) => r.card_acts, type: 'number', align: 'right' },
    { header: 'On autopay', field: 'card_atu', get: (r: StoreRow) => r.card_atu, type: 'number', align: 'right' },
    { header: 'Open', field: 'card_open', get: (r: StoreRow) => r.card_open, type: 'number', align: 'right' },
    { header: 'Attach %', field: 'attach_pct', get: (r: StoreRow) => r.attach_pct, type: 'number', align: 'right' },
    { header: 'Open recharge / mo', field: 'rtr_open', get: (r: StoreRow) => r.rtr_open, money: true, type: 'money', align: 'right' },
    { header: 'Commission forgone / mo', field: 'carry_forgone', get: (r: StoreRow) => r.carry_forgone, money: true, type: 'money', align: 'right' },
  ]

  const tile = (label: string, value: string, sub: string, tone?: 'warn' | 'good') => (
    <div className="card" style={{ padding: '14px 16px', flex: '1 1 168px', minWidth: 168 }}>
      <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.06em', color: 'var(--text3)', fontWeight: 700 }}>{label}</div>
      <div style={{ fontSize: 25, fontWeight: 700, marginTop: 5, fontVariantNumeric: 'tabular-nums', color: tone === 'warn' ? '#b45309' : tone === 'good' ? '#0f766e' : 'var(--text)' }}>{value}</div>
      <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>
    </div>
  )

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Autopay Opportunity</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: '68ch' }}>
            Customers who pay by card already have an instrument on file, so enrolling them in autopay is a
            question rather than a sale. This is how many are still unenrolled, and what that costs each month.
          </p>
        </div>
        <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setShowCfg(v => !v)}>
          {showCfg ? '▾' : '▸'} Assumptions
        </button>
      </div>

      {/* RULE FIVE — one universal filter bar driving every tile, the table and the exports. */}
      <div className="card" style={{ padding: 12, marginBottom: 14, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Period{' '}
          <input style={{ ...inp, width: 104 }} value={period} onChange={e => setPeriod(e.target.value)} placeholder="YYYY-MM" />
        </label>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>From{' '}
          <input type="date" style={{ ...inp, width: 140 }} value={start} onChange={e => setStart(e.target.value)} />
        </label>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>To{' '}
          <input type="date" style={{ ...inp, width: 140 }} value={end} onChange={e => setEnd(e.target.value)} />
        </label>
        <MultiSelect allLabel="All stores" width={150} value={selStores} options={data?.filter_options?.stores || []} onChange={setSelStores} searchable />
        <MultiSelect allLabel="All markets" width={140} value={selMarkets} options={data?.filter_options?.markets || []} onChange={setSelMarkets} />
        {(selStores.length || selMarkets.length || start || end) ? (
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => { setSelStores([]); setSelMarkets([]); setStart(''); setEnd('') }}>Clear filters</button>
        ) : null}
        {loading && <span style={{ fontSize: 12, color: 'var(--text3)' }}>loading…</span>}
      </div>

      {showCfg && cfg && (
        <div className="card" style={{ padding: 14, marginBottom: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>Assumptions — yours to set</div>
          <p style={{ fontSize: 12.5, color: 'var(--text2)', margin: '0 0 10px', maxWidth: '76ch' }}>
            Every figure on this page derives from these four numbers. They are saved for your whole company.
            Set them to your current carrier terms — nothing here is fixed in the code.
          </p>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            {(([['saving_per_month', 'Customer saving / mo'], ['boost_rate_pct', 'Boost ATU rate %'],
               ['total_rate_pct', 'Total ATU rate %'], ['total_recharge_base', 'Total recharge base $/mo']] as [string, string][])
              // Show only the ACTIVE carrier's rate/base — for every tenant, so a non-Boost store never
              // sees a "Boost ATU rate %" field. 'saving_per_month' is carrier-neutral.
              .filter(([k]) => k === 'saving_per_month'
                || (isTotal ? k.startsWith('total_') : k.startsWith('boost_'))))
              .map(([k, label]) => (
                <label key={k} style={{ fontSize: 12, color: 'var(--text2)', display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {label}
                  <input type="number" min={0} step="0.1" style={inp} value={cfg[k] ?? 0}
                    onChange={e => setCfg({ ...cfg, [k]: e.target.value === '' ? 0 : Number(e.target.value) })} />
                </label>
              ))}
            <button className="btn btn-primary" style={{ fontSize: 12 }} onClick={saveCfg} disabled={saving}>{saving ? '…' : '💾 Save'}</button>
            {msg && <span style={{ fontSize: 12, color: 'var(--text2)' }}>{msg}</span>}
          </div>
          {data && !data.table_present && (
            <div style={{ fontSize: 12, color: '#b45309', marginTop: 8 }}>
              ⚠️ Showing built-in defaults — run migration <code>295_atu_opportunity_config.sql</code> to save your own.
            </div>
          )}
        </div>
      )}

      {data?.error && <div className="card" style={{ padding: 16, color: '#b45309' }}>⚠️ {data.error}</div>}

      {s && (
        <>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
            {tile('Card customers', (c.card || 0).toLocaleString(), 'distinct lines')}
            {tile('On autopay', (c.card_on_atu || 0).toLocaleString(), `${c.card_attach_pct}% attach`, 'good')}
            {tile('Open position', (c.card_open || 0).toLocaleString(), 'card on file, not enrolled', 'warn')}
            {tile('Commission forgone', money(activeCarry), 'per month, recurring', 'warn')}
            {tile('Annualised', money(activeCarry * 12), 'this cohort held flat', 'warn')}
            {tile('Customer savings lost', money(m.customer_savings_monthly), 'per month, to customers')}
          </div>

          <div className="card" style={{ padding: 14, marginBottom: 14, fontSize: 13, color: 'var(--text2)' }}>
            <b style={{ color: 'var(--text)' }}>{money(rc.card_open)}</b> of card-tendered recharge each month comes from
            customers who are not on autopay — <b style={{ color: 'var(--text)' }}>{m.pct_of_card_recharge_forgone}%</b> of
            the card recharge base. Converting them earns {isTotal ? cfg?.total_rate_pct : cfg?.boost_rate_pct}% of it every month for the life of
            the line, and hands each customer back {money(cfg?.saving_per_month)}.
            {c.noncard > 0 && (
              <> Cash customers attach at <b style={{ color: 'var(--text)' }}>{c.noncard_attach_pct}%</b> versus{' '}
                <b style={{ color: 'var(--text)' }}>{c.card_attach_pct}%</b> on card — the case for working the card book is the
                cost to close, not a better conversion rate.</>
            )}
            {/* Total-side caveat (recharges settle through VidaPay) — only under the Total lens. */}
            {isTotal && !s.totals_measurable?.total && (
              <div style={{ marginTop: 8, color: '#b45309' }}>
                ⚠️ {s.totals_measurable?.total_note}
              </div>
            )}
          </div>

          {/* RULE FOUR — what you see is what exports. */}
          {stores.length > 0 && (
            <ReportShell
              title={`Autopay opportunity — ${period}`}
              filename={`atu-opportunity-${period}`}
              columns={cols}
              rows={stores}
              totals
              stickyHeader
            />
          )}

          <div className="card" style={{ padding: 14, marginTop: 14 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', marginBottom: 6 }}>What this does not measure</div>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, color: 'var(--text2)', display: 'flex', flexDirection: 'column', gap: 4 }}>
              {(data.caveats || []).map((x: string, i: number) => <li key={i}>{x}</li>)}
            </ul>
          </div>
        </>
      )}
    </div>
  )
}
