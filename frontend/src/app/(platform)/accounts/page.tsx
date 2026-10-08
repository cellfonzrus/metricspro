'use client'
import { Fragment, useState, useEffect } from 'react'
import Link from 'next/link'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import NarrativeBanner from '@/components/NarrativeBanner'
import {
  allSections, drillFilter, drillRootLevels, expenseTieOut, isStoreScope, levelTotals,
  lineTieOut, marginIdentity, marketsForScope, nextRung, SECTION_TITLE,
  scopeBalanceSheetHref, scopeDetailHref, scopeDisplay, scopeExpenses,
  type DrillLevel, type ScopeRow, type Statement,
} from './_components/scopeFinancials'
import { plQuery } from './_components/plStatement'

/** The headline figures of one drill rung, as `levelTotals` reports them. */
type LevelTotals = ReturnType<typeof levelTotals>

export default function AccountsDashboard() {
  const { period } = usePeriod()
  const [data, setData] = useState<any>({ computed: false, scopes: [], companies: [] })
  const [loading, setLoading] = useState(true)
  const [computing, setComputing] = useState(false)
  const [msg, setMsg] = useState('')
  const [health, setHealth] = useState<any>({})

  function load() {
    setLoading(true)
    Promise.all([
      api(`/api/v1/account/overview/${encodeURIComponent(period)}?org_id=${ORG_ID}`).catch(() => ({ computed: false, scopes: [] })),
      api(`/api/v1/account/health`).catch(() => ({})),
    ]).then(([o, h]: any) => { setData(o); setHealth(h) }).catch(console.error).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [period])

  async function compute() {
    setComputing(true); setMsg('Building the chart of accounts + statements…')
    try {
      const r = await api(`/api/v1/account/compute/${encodeURIComponent(period)}?org_id=${ORG_ID}`, { method: 'POST' })
      setMsg(`Computed ${r.snapshots} snapshots across ${r.scopes} scopes (${r.companies} companies, ${r.stores} stores) — engine: ${r.engine}.`)
      load()
    } catch (e: any) { setMsg('Compute failed: ' + (e?.message || e)) }
    setComputing(false)
  }

  const consolidated = data.scopes?.find((s: any) => s.scope_key === 'consolidated')
  const companyScopes = (data.scopes || []).filter((s: any) => s.scope_key?.startsWith('company:'))
  const storeScopes = (data.scopes || []).filter((s: any) => s.scope_key?.startsWith('store:'))

  // RULE FOUR (§3c) — tiles doctrine: this hub is a dashboard with detail tables (By Company / By
  // Store), so it exports a {Metric,Value} summary sheet PLUS those tables. DISPLAY/EXPORT ONLY.
  const scopeCols: ExportColumn[] = [
    { header: 'Scope', get: (r: any) => scopeDisplay(r) },
    { header: 'Revenue', get: (r: any) => r.revenue, money: true },
    { header: 'Gross Profit', get: (r: any) => r.gross_profit, money: true },
    // Owner request 2026-09-21. The statement's OWN expense total for this scope (backend
    // `analysis.pl_totals`), never re-derived here. ABSENT (no computed P&L) exports BLANK — the
    // money formatter already refuses to write $0.00 for a figure nobody measured.
    { header: 'Expenses', get: (r: any) => (scopeExpenses(r).reported ? r.expenses : null), money: true },
    { header: 'Net Income', get: (r: any) => r.net_income, money: true },
    { header: 'Assets', get: (r: any) => r.assets, money: true },
    { header: 'Balanced', get: (r: any) => (r.balanced ? 'Yes' : 'No') },
  ]
  function overviewSheets() {
    const summary = [
      { k: 'Revenue', v: fmt(consolidated?.revenue || 0) },
      { k: 'Gross Profit', v: fmt(consolidated?.gross_profit || 0) },
      { k: 'Expenses', v: scopeExpenses(consolidated).reported ? fmt(scopeExpenses(consolidated).amount) : 'not reported' },
      { k: 'Net Income', v: fmt(consolidated?.net_income || 0) },
      { k: 'Total Assets', v: fmt(consolidated?.assets || 0) },
      { k: 'Balance sheet balances', v: consolidated?.balanced ? 'Yes' : 'No' },
    ]
    const sheets: { name: string; columns: ExportColumn[]; rows: any[] }[] = [
      { name: 'Summary', columns: [{ header: 'Metric', get: (r: any) => r.k }, { header: 'Value', get: (r: any) => r.v }], rows: summary },
    ]
    if (companyScopes.length) sheets.push({ name: 'By Company', columns: scopeCols, rows: companyScopes })
    if (storeScopes.length) sheets.push({ name: 'By Store', columns: scopeCols, rows: storeScopes })
    return sheets
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>💼 Account Module</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {period} · P&amp;L + Balance Sheet, per company &amp; consolidated · cash basis
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {msg && <span style={{ fontSize: 12, color: 'var(--text2)', maxWidth: 380 }}>{msg}</span>}
          {data.computed && <ReportExportBar title={`Account Overview — ${period}`}
            subtitle={`${period} · consolidated + per company & store · cash basis`}
            filename={`account-overview-${String(period).replace(/\s+/g, '-')}`} sheets={overviewSheets()} />}
          <button className="btn btn-primary" onClick={compute} disabled={computing}>
            {computing ? '⏳ Computing…' : '⚙️ Compute statements'}
          </button>
        </div>
      </div>

      {!health.engine_configured && (
        <div className="card" style={{ padding: 12, marginBottom: 16, background: '#fffbeb', border: '1px solid #fde68a', fontSize: 13, color: '#92400e' }}>
          ⚠️ The written analysis isn&apos;t switched on yet — statements compute with exact deterministic numbers, but without the written narrative. Contact support to enable it.
        </div>
      )}

      <AccountConfigCard />

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : !data.computed ? (
        <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>
          No statements computed for {period} yet. Click <strong>Compute statements</strong> above.
          <div style={{ marginTop: 8, fontSize: 13 }}>First, assign stores to companies on the <Link href="/accounts/companies">Companies</Link> page.</div>
        </div>
      ) : (
        <>
          {/* NARRATIVE BANNER — deterministic "consolidated P&L vs last month" above the statements.
              Computed from the same account_statements shown below, so it can never disagree with them. */}
          <NarrativeBanner url={`/api/v1/account/overview/${encodeURIComponent(period)}/narrative?org_id=${ORG_ID}`} />
          {consolidated && (
            <div className="card" style={{ padding: 18, marginBottom: 18 }}>
              <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 10 }}>Consolidated (all companies)</div>
              <div style={{ display: 'flex', gap: 28, flexWrap: 'wrap' }}>
                <Tile label="Revenue" v={consolidated.revenue} />
                <Tile label="Gross Profit" v={consolidated.gross_profit} />
                <ExpenseTile row={consolidated} />
                <Tile label="Net Income" v={consolidated.net_income} accent />
                <Tile label="Total Assets" v={consolidated.assets} />
                <div style={{ alignSelf: 'center' }}>
                  <span style={{ fontSize: 12, padding: '3px 9px', borderRadius: 999, fontWeight: 600,
                    background: consolidated.balanced ? '#dcfce7' : '#fee2e2', color: consolidated.balanced ? '#166534' : '#991b1b' }}>
                    {consolidated.balanced ? '✓ Balance sheet balances' : '⚠ Not balanced — enter cash/opening balances'}
                  </span>
                </div>
              </div>
              <div style={{ marginTop: 12, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                <Link className="btn" href={`/accounts/pl?scope=consolidated`}>📈 View P&amp;L</Link>
                <Link className="btn" href={`/accounts/balance-sheet?scope=consolidated`}>⚖️ View Balance Sheet</Link>
                <Link className="btn" href={`/accounts/inventory`}>📦 Inventory Values</Link>
                <Link className="btn" href={`/accounts/journal`}>📒 Journal</Link>
              </div>
            </div>
          )}

          {companyScopes.length > 0 && <ScopeTable title="By Company" rows={companyScopes} period={period} />}
          {storeScopes.length > 0 && <ScopeTable title="By Store" rows={storeScopes} period={period} />}
        </>
      )}
    </div>
  )
}

// Per-org accounting config (mig 611). MONEY-TOUCHING: the accessory COGS % moves Accessory cost /
// Gross Profit, so saving prompts a recompute. Empty/default = 0.20 for every tenant (Boost byte-identical).
function AccountConfigCard() {
  const [cfg, setCfg] = useState<any>(null)
  const [pct, setPct] = useState('')
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState(false)
  const [open, setOpen] = useState(false)

  // Service-fee products (mig 613): which sale lines are FEE INCOME to the store. RULE THREE —
  // picked from what this tenant's sales actually carry, never typed.
  const [fees, setFees] = useState<string[]>([])
  const [feeQ, setFeeQ] = useState('')

  // Owner ruling K2 (mig 621): which expense names ARE payroll. Listing one makes payroll
  // AUTHORITATIVE for the period and SUPPRESSES the StoreOps shifts x rate estimate — the fix for a
  // tenant that keys payroll by hand and was getting BOTH. RULE THREE: picked from this tenant's own
  // expense names, never typed.
  const [payNames, setPayNames] = useState<string[]>([])
  const [payQ, setPayQ] = useState('')
  // Owner ruling K3 (mig 621): device COGS recognition. 'off' keeps the legacy POS basis.
  const [devMode, setDevMode] = useState('off')
  // Owner directive 2026-09-04 (mig 954) — the TENANT-SETUP mapping: which feed answers "what do we
  // owe the distributor", and which balance-sheet line it books to (a per-tenant cost-centre choice).
  // '' = not declared here → the org's CARRIER preset decides (lazy auto-assign at onboarding).
  const [payBasis, setPayBasis] = useState('')
  const [payLine, setPayLine] = useState('')

  function load() {
    api(`/api/v1/account/config?org_id=${ORG_ID}`).then((r: any) => {
      setCfg(r)
      setPct(String(Math.round((r?.config?.accessory_cogs_pct ?? 0.2) * 10000) / 100))
      setFees(r?.config?.service_fee_products || [])
      setPayNames(r?.config?.payroll_expense_names || [])
      setDevMode(r?.config?.device_cogs_mode || 'off')
      setPayBasis(r?.distributor_payable?.org_basis || '')
      setPayLine(r?.distributor_payable?.org_line || '')
    }).catch(() => {})
  }
  useEffect(() => { load() }, [])

  async function save() {
    const v = parseFloat(pct)
    if (isNaN(v) || v < 0 || v > 100) { setMsg('Enter a percent between 0 and 100.'); return }
    setSaving(true); setMsg('')
    try {
      await api(`/api/v1/account/config?org_id=${ORG_ID}`, {
        method: 'PUT',
        body: JSON.stringify({
          accessory_cogs_pct: v / 100, service_fee_products: fees,
          payroll_expense_names: payNames, device_cogs_mode: devMode,
          distributor_payable_basis: payBasis || null, distributor_payable_line: payLine || null,
        }),
      })
      setMsg('Saved. Recompute this period’s statements for it to take effect.'); load()
    } catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) }
    setSaving(false)
  }

  function toggleFee(p: string) {
    setFees(f => f.includes(p) ? f.filter(x => x !== p) : [...f, p])
  }

  function togglePay(p: string) {
    setPayNames(f => f.includes(p) ? f.filter(x => x !== p) : [...f, p])
  }

  if (!cfg) return null
  return (
    <div className="card" style={{ padding: 12, marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <button onClick={() => setOpen(o => !o)} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 13, fontWeight: 700, padding: 0, color: 'var(--text)' }}>
          {open ? '▾' : '▸'} ⚙️ Accounting settings
        </button>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>
          Accessory COGS: <strong>{Math.round((cfg.config?.accessory_cogs_pct ?? 0.2) * 10000) / 100}%</strong>
          {cfg.is_default && <span style={{ marginLeft: 6, color: 'var(--text3)' }}>(default)</span>}
          {fees.length > 0 && <span style={{ marginLeft: 10 }}>· Service-fee products: <strong>{fees.length}</strong></span>}
          {payNames.length > 0 && <span style={{ marginLeft: 10 }}>· Payroll names: <strong>{payNames.length}</strong></span>}
          <span style={{ marginLeft: 10 }}>· Device COGS: <strong>{devMode}</strong></span>
          <span style={{ marginLeft: 10 }}>· Distributor payable: <strong>{cfg.distributor_payable?.resolved_basis || 'off'}</strong>
            {cfg.distributor_payable?.resolved_line && <> → <strong>{cfg.distributor_payable.resolved_line}</strong></>}</span>
        </span>
      </div>
      {open && (
        <div style={{ marginTop: 10 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <label style={{ fontSize: 13 }}>Accessory COGS %
              <input type="number" step="0.01" min={0} max={100} value={pct} onChange={e => setPct(e.target.value)}
                style={{ marginLeft: 8, width: 90, padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }} />
            </label>
            <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '⏳…' : 'Save'}</button>
            {msg && <span style={{ fontSize: 12, color: 'var(--text2)' }}>{msg}</span>}
            <span style={{ fontSize: 12, color: 'var(--text3)' }}>
              Accessory cost is booked as this fraction of gross accessory sales (money-touching — recompute after saving).
            </span>
          </div>

          {/* Service-fee income (mig 613). A fee the store CHARGES is revenue; the bill payment it rides
              on is pass-through and must never be picked. Options come from this tenant's own sales. */}
          <div style={{ marginTop: 14, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>Service-fee products → P&amp;L “Service fee income”</div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>
              Pick the sale lines that are a <strong>fee your store charges</strong> (e.g. a bill-payment service
              charge). Each is booked as revenue at full price with no cost. Do <strong>not</strong> pick the bill
              payment or refill itself — that is the customer’s money passing through, not income.
            </div>
            {fees.length > 0 && (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
                {fees.map(p => (
                  <button key={p} onClick={() => toggleFee(p)} title="Remove"
                    style={{ fontSize: 12, padding: '4px 9px', borderRadius: 999, cursor: 'pointer',
                             border: '1px solid var(--border)', background: 'var(--surface2, var(--surface))' }}>
                    {p} ✕
                  </button>
                ))}
              </div>
            )}
            <input value={feeQ} onChange={e => setFeeQ(e.target.value)} placeholder="Search this tenant's products…"
              style={{ width: '100%', maxWidth: 460, padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }} />
            <div style={{ maxHeight: 190, overflowY: 'auto', marginTop: 8, border: '1px solid var(--border)', borderRadius: 7 }}>
              {(cfg.service_fee_product_options || [])
                .filter((p: string) => !feeQ || p.toLowerCase().includes(feeQ.toLowerCase()))
                .slice(0, 200)
                .map((p: string) => (
                  <label key={p} style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '5px 9px', fontSize: 12.5, cursor: 'pointer' }}>
                    <input type="checkbox" checked={fees.includes(p)} onChange={() => toggleFee(p)} />
                    <span>{p}</span>
                  </label>
                ))}
              {!(cfg.service_fee_product_options || []).length &&
                <div style={{ padding: '8px 9px', fontSize: 12, color: 'var(--text3)' }}>No sales products found for this tenant yet.</div>}
            </div>
          </div>

          {/* Owner ruling K2 (2026-08-10) — payroll authority. If your payroll is TYPED into the
              expense sheet, say so here; otherwise the books add an estimate from the schedule on top
              of it and the same wages get counted twice. */}
          <div style={{ marginTop: 14, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>Which expenses are payroll?</div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>
              Tick the expense names you use for <strong>wages and salaries</strong>. When any of them has
              an amount for a month, that is treated as the real payroll for that month and the estimate
              calculated from the schedule (hours × pay rate) is <strong>switched off</strong> — so the same
              wages are never counted twice. Leave this empty to keep the old behaviour.
            </div>
            {payNames.length > 0 && (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
                {payNames.map(p => (
                  <button key={p} onClick={() => togglePay(p)} title="Remove"
                    style={{ fontSize: 12, padding: '4px 9px', borderRadius: 999, cursor: 'pointer',
                             border: '1px solid var(--border)', background: 'var(--surface2, var(--surface))' }}>
                    {p} ✕
                  </button>
                ))}
              </div>
            )}
            <input value={payQ} onChange={e => setPayQ(e.target.value)} placeholder="Search this tenant's expense names…"
              style={{ width: '100%', maxWidth: 460, padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }} />
            <div style={{ maxHeight: 170, overflowY: 'auto', marginTop: 8, border: '1px solid var(--border)', borderRadius: 7 }}>
              {(cfg.payroll_expense_name_options || [])
                .filter((p: string) => !payQ || p.toLowerCase().includes(payQ.toLowerCase()))
                .slice(0, 200)
                .map((p: string) => (
                  <label key={p} style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '5px 9px', fontSize: 12.5, cursor: 'pointer' }}>
                    <input type="checkbox" checked={payNames.includes(p)} onChange={() => togglePay(p)} />
                    <span>{p}</span>
                  </label>
                ))}
              {!(cfg.payroll_expense_name_options || []).length &&
                <div style={{ padding: '8px 9px', fontSize: 12, color: 'var(--text3)' }}>No expense names found for this tenant yet.</div>}
            </div>
          </div>

          {/* Owner ruling K3 (2026-08-10) — device COGS recognition. */}
          <div style={{ marginTop: 14, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>Where does the cost of phones come from?</div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>
              Your point-of-sale records the phone cost <strong>after</strong> the carrier subsidy, which can make
              it look like a phone cost nothing — or less than nothing. Reading the cost from the
              <strong> distributor’s invoice</strong> instead puts the real handset cost on the P&amp;L.
            </div>
            <select value={devMode} onChange={e => setDevMode(e.target.value)}
              style={{ padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', maxWidth: 460, width: '100%' }}>
              <option value="off">Point of sale only (current default)</option>
              <option value="auto">Distributor invoice, fall back to point of sale — recommended</option>
              <option value="invoice">Distributor invoice only (never fall back)</option>
              <option value="pos">Point of sale only (explicit)</option>
            </select>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 6 }}>
              Money-touching — <strong>recompute</strong> each period after saving.
            </div>
          </div>

          {/* Owner directive 2026-09-04 (mig 954) — the distributor-payable tenant mapping. Set at
              TENANT SETUP so a new tenant reports correctly from day one; left blank it follows the
              carrier picked during onboarding. RULE THREE: both fields are pickers. */}
          <div style={{ marginTop: 14, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>What do we still owe the distributor for phones?</div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>
              Your open balance with the distributor comes from one of two places, depending on how your
              distributor bills you. Leave this on <strong>“follow our carrier”</strong> and it is chosen from the
              carrier picked when the tenant was set up — change it only if your books work differently.
              The figure is worked out <strong>as of the date of the statement</strong>: today for the month in
              progress, the month end for a closed month.
            </div>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-start' }}>
              <label style={{ fontSize: 12.5 }}>
                <div style={{ marginBottom: 4, color: 'var(--text2)' }}>Where the balance comes from</div>
                <select value={payBasis} onChange={e => setPayBasis(e.target.value)}
                  style={{ padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', minWidth: 330 }}>
                  <option value="">Follow our carrier{cfg.distributor_payable?.carrier_preset_basis ? ` (${cfg.distributor_payable.carrier_preset_basis})` : ''}</option>
                  <option value="asset_ledger">The consignment ledger’s open balance</option>
                  <option value="marketplace_due">Marketplace orders still inside their due date</option>
                  <option value="off">Do not put a distributor balance on the balance sheet</option>
                </select>
              </label>
              <label style={{ fontSize: 12.5 }}>
                <div style={{ marginBottom: 4, color: 'var(--text2)' }}>Which balance-sheet line it goes on</div>
                <select value={payLine} onChange={e => setPayLine(e.target.value)}
                  style={{ padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', minWidth: 330 }}>
                  <option value="">Standard line for that source{cfg.distributor_payable?.resolved_line ? ` (${cfg.distributor_payable.resolved_line})` : ''}</option>
                  {(cfg.distributor_payable?.line_options || []).map((o: any) => (
                    <option key={o.key} value={o.key}>{o.label}</option>
                  ))}
                </select>
              </label>
            </div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8 }}>
              Currently in effect: <strong>{cfg.distributor_payable?.resolved_basis || 'off'}</strong>
              {cfg.distributor_payable?.resolved_line && <> on <strong>{cfg.distributor_payable.resolved_line}</strong></>}
              {cfg.distributor_payable?.source && <> — from {cfg.distributor_payable.source}</>}.
              Different companies assign this to different cost centres, so the line is yours to choose.
              Money-touching — <strong>recompute</strong> each period after saving.
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function Tile({ label, v, accent }: { label: string; v: number; accent?: boolean }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color: accent ? (v >= 0 ? 'var(--green, #16a34a)' : 'var(--red, #dc2626)') : 'var(--text)' }}>{fmt(v || 0)}</div>
    </div>
  )
}

// ── NOT-REPORTED, never $0.00 ────────────────────────────────────────────────────────────────────
// A scope with no computed P&L has no expense figure. Printing "$0.00" there would assert that this
// store spent nothing, which is a different and much stronger claim than "we have not computed it".
// A MEASURED zero still prints $0.00.
function ExpenseCell({ row }: { row: ScopeRow }) {
  const e = scopeExpenses(row)
  if (!e.reported) {
    return <span title="No computed P&L for this scope and period — nothing to report, which is not the same as zero."
      style={{ fontSize: 11, color: 'var(--text3)', background: 'var(--surface2, #f1f5f9)', padding: '1px 6px', borderRadius: 999 }}>not reported</span>
  }
  return <>{fmt(e.amount)}</>
}

function ExpenseTile({ row }: { row: ScopeRow }) {
  const e = scopeExpenses(row)
  return (
    <div>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: 0.4 }}>Expenses</div>
      <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--text)' }}>
        {e.reported ? fmt(e.amount) : <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text3)' }}>not reported</span>}
      </div>
    </div>
  )
}

function ScopeTable({ title, rows, period }: { title: string; rows: ScopeRow[]; period: string }) {
  // Which row is expanded. One at a time: the panel is a focused read, and only the open row fetches.
  const [openKey, setOpenKey] = useState<string | null>(null)
  return (
    <div className="card" style={{ padding: 0, marginBottom: 18, overflow: 'hidden' }}>
      <div style={{ padding: '10px 16px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>{title}</div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}>
          <thead>
            <tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
              <th style={{ textAlign: 'left', padding: '8px 16px' }}>Scope</th>
              <th style={{ textAlign: 'right', padding: '8px 12px' }}>Revenue</th>
              <th style={{ textAlign: 'right', padding: '8px 12px' }}>Gross Profit</th>
              {/* Owner request 2026-09-21. The P&L's own expense total for this scope + period
                  (Operating Expenses + Other) — the figure the statement subtracts from gross
                  profit to reach net income, so the three columns read across as an equation. */}
              <th style={{ textAlign: 'right', padding: '8px 12px' }}
                  title="Operating Expenses + Other, exactly as the P&L reports them for this scope and period. Gross Profit − Expenses = Net Income.">
                Expenses
              </th>
              <th style={{ textAlign: 'right', padding: '8px 12px' }}>Net Income</th>
              <th style={{ textAlign: 'right', padding: '8px 12px' }}>Assets</th>
              <th style={{ textAlign: 'center', padding: '8px 12px' }}>Bal.</th>
              <th style={{ padding: '8px 16px' }}></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s: ScopeRow) => {
              const open = openKey === s.scope_key
              // THE display name, from the backend's one home (scopeFinancials, §13b.1) — never
              // `scope_label || scope_key`, which rendered `company:<uuid>` for a stale label.
              const label = scopeDisplay(s)
              return (
                <Fragment key={s.scope_key}>
                  <tr style={{ borderTop: '1px solid var(--border)', fontSize: 13, background: open ? 'var(--surface2, #f8fafc)' : undefined }}>
                    <td style={{ padding: '8px 16px', fontWeight: 500 }}>
                      {/* THE DRILL-DOWN HANDLE — same mechanism for every scope row, no per-tenant
                          or per-store branching. Expanding reads the canonical stored statement. */}
                      <button onClick={() => setOpenKey(open ? null : s.scope_key)}
                        aria-expanded={open}
                        title={open ? 'Hide the detail' : 'Show this scope\u2019s expense detail'}
                        style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 11, marginRight: 6, padding: 0, color: 'var(--text2)' }}>
                        {open ? '\u25be' : '\u25b8'}
                      </button>
                      {label.substring(0, 48)}
                    </td>
                    <td style={{ padding: '8px 12px', textAlign: 'right' }}>{fmt(s.revenue || 0)}</td>
                    <td style={{ padding: '8px 12px', textAlign: 'right' }}>{fmt(s.gross_profit || 0)}</td>
                    <td style={{ padding: '8px 12px', textAlign: 'right' }}><ExpenseCell row={s} /></td>
                    <td style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600, color: (s.net_income || 0) >= 0 ? '#16a34a' : '#dc2626' }}>{fmt(s.net_income || 0)}</td>
                    <td style={{ padding: '8px 12px', textAlign: 'right' }}>{fmt(s.assets || 0)}</td>
                    <td style={{ padding: '8px 12px', textAlign: 'center' }}>{s.balanced ? '\u2713' : '\u26a0'}</td>
                    <td style={{ padding: '8px 16px', whiteSpace: 'nowrap' }}>
                      <Link href={scopeDetailHref(s.scope_key)} style={{ fontSize: 12, marginRight: 10 }}>P&amp;L</Link>
                      <Link href={scopeBalanceSheetHref(s.scope_key)} style={{ fontSize: 12 }}>BS</Link>
                    </td>
                  </tr>
                  {open && (
                    <tr style={{ background: 'var(--surface2, #f8fafc)' }}>
                      <td colSpan={8} style={{ padding: '0 16px 14px' }}>
                        {/* Keyed on the row AND the period: opening a different scope, or
                            changing the month, gives a FRESH panel at the top of its drill
                            path. React remounting is the reset — never a setState in an
                            effect, which cascades renders (react-hooks/set-state-in-effect). */}
                        <ScopeDrillDown key={`${s.scope_key}|${period}`} row={s} period={period} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── THE DRILL-DOWN: the SAME statement the P&L page shows, down to the line level ────────────────
// Owner report 2026-10-07, verbatim: *"the details are missing and the details with the drop down
// does not tie any information which appears on the summary line, details should be same as on the
// p&l page, the expenses should be able to drill down, each line should be able to drill down to get
// to each level and finally down to the line level."*
//
// What this panel is, and what it deliberately is NOT:
//   · It renders the server's own `GET /account/pl/{period}` answer, requested through the ONE
//     frontend query helper `plQuery` — byte for byte the request the /accounts/pl page makes for the
//     same scope and filter. It is NOT a second computation, and it adds up nothing.
//   · Every rung down — company → market → store — is that SAME request with one more thing in the
//     filter. So a rung ties to the rung above it by construction: both are `statement_filter`
//     summing the same per-store snapshots, never two different summations.
//   · Below the store rung are the statement's own LINES, and below each line its own drill rows,
//     which the backend now records at the store grain (`coa.accrue_detail`). Before this release
//     every per-store snapshot stored an EMPTY drill-down, which is why the dropdown showed nothing
//     that tied to the summary line.
//   · ALL FOUR sections are shown (Revenue, COGS, Operating Expenses, Other) with their subtotals
//     and Gross Profit / Net Operating Income / Net Income — "the same as on the p&l page". The
//     earlier panel showed only expenses.
// RULE TWO: a scope key, a market name and a store address are opaque strings; nothing branches on a
// tenant, carrier, company or store.
function ScopeDrillDown({ row, period }: { row: ScopeRow; period: string }) {
  const [path, setPath] = useState<DrillLevel[]>(() => drillRootLevels(row.scope_key, scopeDisplay(row)))
  const [st, setSt] = useState<Statement | null>(null)
  const [filteredStores, setFilteredStores] = useState<string[]>([])
  const [state, setState] = useState<'loading' | 'ready' | 'uncomputed' | 'error'>('loading')
  const [err, setErr] = useState('')
  const [markets, setMarkets] = useState<string[]>([])
  const [childTotals, setChildTotals] = useState<Record<string, LevelTotals>>({})
  const [openLines, setOpenLines] = useState<Record<string, boolean>>({})

  // A drill path belongs to ONE scope and ONE month: the parent keys this panel on both, so a
  // different row or a different period REMOUNTS it at the root rather than resetting state from
  // inside an effect (which cascades renders).
  const filt = drillFilter(path)
  const rung = nextRung(path)
  // The filter as PRIMITIVES, so an effect can depend on the values rather than on a fresh object
  // every render.
  const fScope = filt.scope
  const fStores = filt.stores.join('|')
  const fMarkets = filt.markets.join('|')

  // THE level's own statement — the canonical read, one request per level, only the open one.
  useEffect(() => {
    let live = true
    api(plQuery(period, fScope, fStores ? fStores.split('|') : [], fMarkets ? fMarkets.split('|') : [], ORG_ID))
      .then((d: { computed?: boolean; statement?: Statement; filtered_stores?: string[] } | null) => {
        if (!live) return
        setFilteredStores(d?.filtered_stores || [])
        if (!d?.computed) { setSt(null); setState('uncomputed'); return }
        setSt(d.statement || null); setState('ready')
      })
      .catch((e: unknown) => { if (live) { setErr(e instanceof Error ? e.message : String(e)); setState('error') } })
    return () => { live = false }
  }, [period, fScope, fStores, fMarkets])

  // The market vocabulary — the canonical UNION market index, the same authority the P&L's own
  // market filter resolves through. Fetched once; a failure leaves the market rung empty rather
  // than offering a market the filter could not bind.
  useEffect(() => {
    if (rung !== 'market' || markets.length > 0) return
    let live = true
    api(`/api/v1/core/markets?org_id=${ORG_ID}`)
      .then((d: { markets?: string[] } | null) => { if (live) setMarkets(d?.markets || []) })
      .catch(() => { if (live) setMarkets([]) })
    return () => { live = false }
  }, [rung, markets.length])

  // Each child rung's own headline figures — again the canonical read, one request per child, so a
  // child row can never disagree with what opening it shows.
  const children: DrillLevel[] = rung === 'market'
    ? marketsForScope(markets, null).map(m => ({ kind: 'market' as const, key: m, label: m }))
    : rung === 'store'
      ? filteredStores.map(s => ({ kind: 'store' as const, key: s, label: s }))
      : []
  const childKeys = children.map(c => `${c.kind}:${c.key}`).join('||')

  useEffect(() => {
    if (!childKeys) return
    let live = true
    // Rebuilt from the serialized keys, so this effect depends on VALUES only. Each child's figures
    // are the same canonical read, one rung deeper — `drillFilter` composes the request, here over
    // the minimal path (root scope + this child), which is what the full path resolves to anyway.
    const kids: DrillLevel[] = childKeys.split('||').map(k => {
      const i = k.indexOf(':')
      return { kind: k.slice(0, i) as DrillLevel['kind'], key: k.slice(i + 1), label: k.slice(i + 1) }
    })
    Promise.all(kids.map(c => {
      const f = drillFilter([{ kind: 'scope', key: fScope, label: '' }, c])
      return api(plQuery(period, f.scope, f.stores, f.markets, ORG_ID))
        .then((d: { computed?: boolean; statement?: Statement } | null) =>
          [`${c.kind}:${c.key}`, levelTotals(d?.computed ? (d.statement || null) : null)] as const)
        .catch(() => [`${c.kind}:${c.key}`, levelTotals(null)] as const)
    })).then(pairs => {
      if (!live) return
      setChildTotals(prev => {
        const next = { ...prev }
        for (const [k, v] of pairs) next[k] = v
        return next
      })
    })
    return () => { live = false }
  }, [period, fScope, childKeys])

  const secs = allSections(st)
  const col = scopeExpenses(row)
  const tie = expenseTieOut(col, st)
  const ident = marginIdentity(row)
  const atRoot = path.length === drillRootLevels(row.scope_key, scopeDisplay(row)).length

  return (
    <div className="card" style={{ padding: 14, background: 'var(--surface, #fff)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12, flexWrap: 'wrap', marginBottom: 8 }}>
        <div style={{ fontSize: 12, fontWeight: 700 }}>
          {/* The breadcrumb IS the way back up. Clicking a crumb truncates the path to it. */}
          {path.map((p, i) => (
            <Fragment key={`${p.kind}:${p.key}`}>
              {i > 0 && <span style={{ color: 'var(--text3)', margin: '0 5px' }}>›</span>}
              {i === path.length - 1
                ? <span>{p.label}</span>
                : <button onClick={() => setPath(path.slice(0, i + 1))}
                    style={{ border: 'none', background: 'none', padding: 0, cursor: 'pointer', color: 'var(--link, #2563eb)', fontSize: 12, fontWeight: 700 }}>
                    {p.label}
                  </button>}
            </Fragment>
          ))}
          <span style={{ color: 'var(--text3)', fontWeight: 400 }}> · {period}</span>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <Link className="btn" style={{ fontSize: 12, padding: '3px 10px' }} href={scopeDetailHref(row.scope_key)}>Full P&amp;L</Link>
          <Link className="btn" style={{ fontSize: 12, padding: '3px 10px' }} href={scopeBalanceSheetHref(row.scope_key)}>Balance Sheet</Link>
          {isStoreScope(row.scope_key) && (
            <Link className="btn" style={{ fontSize: 12, padding: '3px 10px' }} href={`/accounts/cash-flow?scope=${encodeURIComponent(row.scope_key)}`}>Cash Flow</Link>
          )}
        </div>
      </div>

      {state === 'loading' && <div style={{ padding: 12, color: 'var(--text3)', fontSize: 12 }}>Reading the statement…</div>}
      {state === 'error' && <div style={{ padding: 12, color: '#991b1b', fontSize: 12 }}>Could not read this statement: {err}</div>}
      {state === 'uncomputed' && (
        <div style={{ padding: 12, color: 'var(--text3)', fontSize: 12 }}>
          No P&amp;L computed for this selection and period — there is nothing to report, which is not the same as $0.00.
          Use <strong>Compute statements</strong> above.
        </div>
      )}

      {/* ── THE RUNG BELOW: markets, then stores. Each row is the same canonical read, one level
             deeper, so the rows add up to the statement printed under them. ── */}
      {children.length > 0 && (
        <div style={{ marginBottom: 12, border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
          <div style={{ padding: '6px 10px', fontSize: 11, textTransform: 'uppercase', fontWeight: 700, color: 'var(--text2)', borderBottom: '1px solid var(--border)' }}>
            {rung === 'market' ? 'By market — open one to reach its stores' : 'By store — open one to reach its lines'}
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ fontSize: 10.5, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '5px 10px' }}>{rung === 'market' ? 'Market' : 'Store'}</th>
                <th style={{ textAlign: 'right', padding: '5px 8px' }}>Revenue</th>
                <th style={{ textAlign: 'right', padding: '5px 8px' }}>Gross Profit</th>
                <th style={{ textAlign: 'right', padding: '5px 8px' }}>Expenses</th>
                <th style={{ textAlign: 'right', padding: '5px 8px' }}>Net Income</th>
              </tr>
            </thead>
            <tbody>
              {children.map(c => {
                const t = childTotals[`${c.kind}:${c.key}`]
                return (
                  <tr key={`${c.kind}:${c.key}`} style={{ borderTop: '1px solid var(--border)', fontSize: 12 }}>
                    <td style={{ padding: '5px 10px' }}>
                      <button onClick={() => setPath([...path, c])}
                        style={{ border: 'none', background: 'none', padding: 0, cursor: 'pointer', color: 'var(--link, #2563eb)', fontSize: 12, textAlign: 'left' }}>
                        ▸ {c.label}
                      </button>
                    </td>
                    {/* Absent is NOT zero: a level with no computed statement says so. */}
                    {t === undefined
                      ? <td colSpan={4} style={{ padding: '5px 8px', textAlign: 'right', color: 'var(--text3)', fontSize: 11 }}>reading…</td>
                      : !t.reported
                        ? <td colSpan={4} style={{ padding: '5px 8px', textAlign: 'right', color: 'var(--text3)', fontSize: 11 }}>not reported</td>
                        : <>
                            <td style={{ padding: '5px 8px', textAlign: 'right' }}>{fmt(t.revenue)}</td>
                            <td style={{ padding: '5px 8px', textAlign: 'right' }}>{fmt(t.gross_profit)}</td>
                            <td style={{ padding: '5px 8px', textAlign: 'right' }}>{fmt(t.expenses)}</td>
                            <td style={{ padding: '5px 8px', textAlign: 'right', fontWeight: 600, color: t.net_income >= 0 ? '#16a34a' : '#dc2626' }}>{fmt(t.net_income)}</td>
                          </>}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {state === 'ready' && (
        <>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <tbody>
              {secs.length === 0 && (
                <tr><td style={{ padding: '8px 4px', fontSize: 12, color: 'var(--text3)' }}>
                  The computed P&amp;L carries no lines for this selection.
                </td></tr>
              )}
              {secs.map(sec => (
                <Fragment key={sec.type}>
                  <tr>
                    <td colSpan={2} style={{ padding: '8px 4px 4px', fontSize: 11, fontWeight: 700, textTransform: 'uppercase', color: 'var(--text2)' }}>
                      {SECTION_TITLE[String(sec.type)] || sec.type}
                    </td>
                  </tr>
                  {(sec.lines || []).map((l, i) => {
                    const lk = `${sec.type}:${l.key || l.label || i}`
                    const drill = Object.entries(l.detail || {})
                    const lt = lineTieOut(l)
                    const open = !!openLines[lk]
                    return (
                      <Fragment key={lk}>
                        <tr style={{ borderTop: '1px solid var(--border)' }}>
                          <td style={{ padding: '6px 4px', fontSize: 12.5 }}>
                            {/* EVERY line drills — down to the rows behind it. A line with no rows
                                says so rather than offering an empty dropdown. */}
                            {drill.length > 0 ? (
                              <button onClick={() => setOpenLines({ ...openLines, [lk]: !open })}
                                aria-expanded={open}
                                title={open ? 'Hide the rows behind this line' : 'Show the rows behind this line'}
                                style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 11, marginRight: 6, padding: 0, color: 'var(--text2)' }}>
                                {open ? '▾' : '▸'}
                              </button>
                            ) : <span style={{ display: 'inline-block', width: 17 }} />}
                            {l.label}
                            {drill.length > 0 && (
                              <span style={{ marginLeft: 6, fontSize: 10.5, color: 'var(--text3)' }}>({drill.length})</span>
                            )}
                            {/* Ruling K3(b): a DECLARED zero must say so — the reader cannot tell a
                                measured $0 from an unmeasured one by looking at the number. */}
                            {l.note && (
                              <div style={{ marginTop: 3, fontSize: 11, lineHeight: 1.45, color: 'var(--text3)', maxWidth: 560 }}>
                                <span style={{ marginRight: 5, fontSize: 10, color: '#92400e', background: '#fef3c7', padding: '1px 5px', borderRadius: 999, whiteSpace: 'nowrap' }}>not measured</span>
                                {l.note}
                              </div>
                            )}
                          </td>
                          <td style={{ padding: '6px 4px', textAlign: 'right', fontSize: 12.5, color: l.amount ? 'var(--text)' : 'var(--text3)' }}>
                            {l.amount ? fmt(l.amount) : '—'}
                          </td>
                        </tr>
                        {open && drill.map(([dl, dv]) => (
                          <tr key={lk + ':' + dl}>
                            <td style={{ padding: '3px 4px 3px 43px', fontSize: 11.5, color: 'var(--text2)' }}>↳ {dl}</td>
                            <td style={{ padding: '3px 4px', textAlign: 'right', fontSize: 11.5, color: 'var(--text2)' }}>{fmt(Number(dv))}</td>
                          </tr>
                        ))}
                        {/* The line-level tie-out is STATED, never assumed. Since the backend records
                            drill rows at the store grain, the rows and the line come from one
                            accumulation — a mismatch means a stale snapshot, and the reader is told. */}
                        {open && lt.checked && !lt.agree && (
                          <tr>
                            <td colSpan={2} style={{ padding: '2px 4px 6px 43px', fontSize: 11, color: '#991b1b' }}>
                              ⚠ These rows add to {fmt(lt.detail)} against {fmt(Number(l.amount || 0))} on the line ({fmt(lt.delta)} apart) — recompute this period, then re-open.
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    )
                  })}
                  <tr style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '6px 4px', fontSize: 11.5, fontWeight: 600, color: 'var(--text2)' }}>
                      Subtotal — {SECTION_TITLE[String(sec.type)] || sec.type}
                    </td>
                    <td style={{ padding: '6px 4px', textAlign: 'right', fontSize: 12.5, fontWeight: 600 }}>{fmt(Number(sec.subtotal || 0))}</td>
                  </tr>
                </Fragment>
              ))}
              {/* The page's own totals, in the page's own order — so this IS the P&L, not a summary of it. */}
              <tr style={{ borderTop: '2px solid var(--border)' }}>
                <td style={{ padding: '7px 4px', fontSize: 12.5, fontWeight: 700 }}>Gross Profit</td>
                <td style={{ padding: '7px 4px', textAlign: 'right', fontSize: 12.5, fontWeight: 700 }}>{fmt(Number(st?.gross_profit || 0))}</td>
              </tr>
              <tr>
                <td style={{ padding: '5px 4px', fontSize: 12.5, fontWeight: 700 }}>Net Operating Income</td>
                <td style={{ padding: '5px 4px', textAlign: 'right', fontSize: 12.5, fontWeight: 700 }}>{fmt(Number(st?.net_operating_income || 0))}</td>
              </tr>
              <tr>
                <td style={{ padding: '5px 4px', fontSize: 13, fontWeight: 700 }}>Net Income</td>
                <td style={{ padding: '5px 4px', textAlign: 'right', fontSize: 13, fontWeight: 700, color: Number(st?.net_income || 0) >= 0 ? '#16a34a' : '#dc2626' }}>{fmt(Number(st?.net_income || 0))}</td>
              </tr>
            </tbody>
          </table>

          {/* The tie-out is stated, never assumed. The hub column and this panel come from the same
              snapshot through the same definition, so a mismatch means something upstream changed
              between the two reads — and the reader is told, not shown a quiet second number. It is
              checked only at the ROOT of the drill: a market or a store is a part, not the whole. */}
          <div style={{ marginTop: 8, fontSize: 11.5, color: atRoot && tie.checked && !tie.agree ? '#991b1b' : 'var(--text3)' }}>
            {atRoot && tie.checked && tie.agree && <>✓ Expenses here tie to the Expenses column above, and to the P&amp;L for this scope — one statement, read once.</>}
            {atRoot && tie.checked && !tie.agree && <>⚠ The expenses here sum to {fmt(tie.detail)} against {fmt(col.amount)} in the column ({fmt(tie.delta)} apart) — the snapshot changed between the two reads. Recompute, then re-open.</>}
            {atRoot && !tie.checked && <>Figures read from the stored statement for this scope.</>}
            {!atRoot && <>Figures read from the same stored per-store statements the P&amp;L page reads, filtered to this selection — the rungs above add up to this one.</>}
            {atRoot && ident.checked && !ident.agree && (
              <div style={{ color: '#991b1b', marginTop: 3 }}>
                ⚠ Gross Profit − Expenses does not equal Net Income for this scope ({fmt(ident.delta)} apart).
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
