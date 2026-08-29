'use client'
import { useState, useEffect } from 'react'
import Link from 'next/link'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'

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
    { header: 'Scope', get: (r: any) => r.scope_label || r.scope_key },
    { header: 'Revenue', get: (r: any) => r.revenue, money: true },
    { header: 'Gross Profit', get: (r: any) => r.gross_profit, money: true },
    { header: 'Net Income', get: (r: any) => r.net_income, money: true },
    { header: 'Assets', get: (r: any) => r.assets, money: true },
    { header: 'Balanced', get: (r: any) => (r.balanced ? 'Yes' : 'No') },
  ]
  function overviewSheets() {
    const summary = [
      { k: 'Revenue', v: fmt(consolidated?.revenue || 0) },
      { k: 'Gross Profit', v: fmt(consolidated?.gross_profit || 0) },
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
          ⚠️ The Claude narrative engine is not configured — statements compute with exact deterministic numbers, but without the written analysis. Set <code>ANTHROPIC_API_KEY</code> on the backend to enable narratives.
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
          {consolidated && (
            <div className="card" style={{ padding: 18, marginBottom: 18 }}>
              <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 10 }}>Consolidated (all companies)</div>
              <div style={{ display: 'flex', gap: 28, flexWrap: 'wrap' }}>
                <Tile label="Revenue" v={consolidated.revenue} />
                <Tile label="Gross Profit" v={consolidated.gross_profit} />
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

          {companyScopes.length > 0 && <ScopeTable title="By Company" rows={companyScopes} />}
          {storeScopes.length > 0 && <ScopeTable title="By Store" rows={storeScopes} />}
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

  function load() {
    api(`/api/v1/account/config?org_id=${ORG_ID}`).then((r: any) => {
      setCfg(r)
      setPct(String(Math.round((r?.config?.accessory_cogs_pct ?? 0.2) * 10000) / 100))
      setFees(r?.config?.service_fee_products || [])
      setPayNames(r?.config?.payroll_expense_names || [])
      setDevMode(r?.config?.device_cogs_mode || 'off')
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
              Money-touching — <strong>recompute</strong> each period after saving. Needs migration 621.
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

function ScopeTable({ title, rows }: { title: string; rows: any[] }) {
  return (
    <div className="card" style={{ padding: 0, marginBottom: 18, overflow: 'hidden' }}>
      <div style={{ padding: '10px 16px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>{title}</div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 680 }}>
          <thead>
            <tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
              <th style={{ textAlign: 'left', padding: '8px 16px' }}>Scope</th>
              <th style={{ textAlign: 'right', padding: '8px 12px' }}>Revenue</th>
              <th style={{ textAlign: 'right', padding: '8px 12px' }}>Gross Profit</th>
              <th style={{ textAlign: 'right', padding: '8px 12px' }}>Net Income</th>
              <th style={{ textAlign: 'right', padding: '8px 12px' }}>Assets</th>
              <th style={{ textAlign: 'center', padding: '8px 12px' }}>Bal.</th>
              <th style={{ padding: '8px 16px' }}></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s: any) => (
              <tr key={s.scope_key} style={{ borderTop: '1px solid var(--border)', fontSize: 13 }}>
                <td style={{ padding: '8px 16px', fontWeight: 500 }}>{(s.scope_label || s.scope_key).substring(0, 48)}</td>
                <td style={{ padding: '8px 12px', textAlign: 'right' }}>{fmt(s.revenue || 0)}</td>
                <td style={{ padding: '8px 12px', textAlign: 'right' }}>{fmt(s.gross_profit || 0)}</td>
                <td style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600, color: (s.net_income || 0) >= 0 ? '#16a34a' : '#dc2626' }}>{fmt(s.net_income || 0)}</td>
                <td style={{ padding: '8px 12px', textAlign: 'right' }}>{fmt(s.assets || 0)}</td>
                <td style={{ padding: '8px 12px', textAlign: 'center' }}>{s.balanced ? '✓' : '⚠'}</td>
                <td style={{ padding: '8px 16px', whiteSpace: 'nowrap' }}>
                  <Link href={`/accounts/pl?scope=${encodeURIComponent(s.scope_key)}`} style={{ fontSize: 12, marginRight: 10 }}>P&amp;L</Link>
                  <Link href={`/accounts/balance-sheet?scope=${encodeURIComponent(s.scope_key)}`} style={{ fontSize: 12 }}>BS</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
