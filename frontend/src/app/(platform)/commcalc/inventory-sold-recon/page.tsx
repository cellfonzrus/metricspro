'use client'
// INVENTORY vs SOLD — "is the item in inventory already sold?" (owner request 2026-09-12).
//
// Owner: "check against the sales by product to see if the item in inventory is already sold or not
// and if it is alsready sold then it should report those items whic are soled with imei to be
// adjusted and also those items which are in oventory to be cleared out of the inventory."
//
// DISPLAY ONLY. This page reads GET /commcalc/inventory-sold-recon and renders it. It writes no
// adjustment and clears no row — what the report finds is for a human to act on, deliberately, in the
// system of record. That is also why there is no "fix it" button: a one-click write-off of stock is
// exactly the action that must not be one click.
//
// The whole judgement lives server-side in the PURE commcalc/inventory_sold_recon (proof
// backend/harness_inventory_sold_recon.py), so this file can never disagree with the report.
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'

// Each finding cites its EVIDENCE: which sale line / activation line named the unit, and how the
// activation was paired (its own serial, or the mobile number THROUGH a sale line) — never a guess.
type Evidence = { source: string; line: string; pairing: string; via?: string; mobile?: string | null; date?: string | null; note?: string }
type Row = {
  finding: string; device_key: string
  sku: string | null; item: string | null; store: string | null; status: string | null
  received_date: string | null; as_of_date: string | null
  net_units: number; cost: number
  evidence?: Evidence[]; also_activated?: boolean; pairing?: string; sale_state?: string
}
type Totals = {
  to_clear: number; to_clear_cost: number; to_adjust: number
  on_hand_considered: number; devices_sold: number; unkeyed_sale_lines: number
  activated_not_rung_out?: number; activated_not_rung_out_cost?: number; activated_by_mobile?: number
  activated_then_refunded?: number; activations_considered?: number; activations_paired_by_key?: number
  activations_paired_by_mobile?: number; activations_unpairable?: number
}
type SourceBasis = { table: string; read_ok: boolean; truncated?: boolean; rows: number; pairs_on: string; period?: string; carries_device_key?: boolean; carries_mobile?: boolean }
type Basis = {
  sales_read_ok: boolean; sales_truncated: boolean
  inventory_read_ok: boolean; inventory_truncated: boolean; complete: boolean
  activations_read_ok?: boolean
  sources?: { sales: SourceBasis; inventory: SourceBasis; activations: SourceBasis }
}
type Unpairable = { line: string; reason: string; mobile: string | null; serial: string | null; candidates?: string[] }
type Activations = { present: boolean; rows: number; carries_device_key: boolean; carries_mobile: boolean; unpairable: Unpairable[] }
type ReconPayload = { rows?: Row[]; totals?: Totals; basis?: Basis; truncated?: boolean; activations?: Activations }

const money = (n: number) =>
  `$${Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

const CLEAR = 'sold_not_cleared'
const ACTIVATED = 'activated_not_rung_out'   // the auto-check against the activation feed (owner 2026-09-20)

export default function InventorySoldReconPage() {
  const [rows, setRows] = useState<Row[]>([])
  const [totals, setTotals] = useState<Totals | null>(null)
  const [basis, setBasis] = useState<Basis | null>(null)
  const [acts, setActs] = useState<Activations | null>(null)
  const [truncated, setTruncated] = useState(false)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  // `tick` is what a Retry bumps; the fetch itself lives in the effect, and every setState happens in
  // an async callback rather than synchronously in the effect body.
  const [tick, setTick] = useState(0)
  useEffect(() => {
    let alive = true
    api('/api/v1/commcalc/inventory-sold-recon?limit=500')
      .then((r: ReconPayload) => {
        if (!alive) return
        setRows(r?.rows || []); setTotals(r?.totals || null)
        setBasis(r?.basis || null); setActs(r?.activations || null); setTruncated(!!r?.truncated); setErr('')
      })
      // NEVER `.catch(() => {})`. A swallowed error here renders as "no discrepancies", which is the
      // most dangerous possible lie this page could tell.
      .catch((e: unknown) => {
        if (alive) setErr((e instanceof Error && e.message) || 'Could not load the reconciliation.')
      })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [tick])
  const load = () => { setLoading(true); setErr(''); setTick(t => t + 1) }

  const clearRows = rows.filter(r => r.finding === CLEAR)
  const activatedRows = rows.filter(r => r.finding === ACTIVATED)
  const adjustRows = rows.filter(r => r.finding !== CLEAR && r.finding !== ACTIVATED)

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto', padding: '4px 0 32px' }}>
      <h1 style={{ fontSize: 20, fontWeight: 800, margin: '0 0 4px' }}>Inventory vs Sold</h1>
      <div style={{ fontSize: 13, color: 'var(--text3)', marginBottom: 16 }}>
        Every device your on-hand snapshot says is in stock, checked against what the sales file says was
        sold <b>and</b> against the activation report. A unit that was sold and then <b>refunded</b> nets out and is
        <b> not</b> listed as sold — unless the activation report says it went to a customer, in which case it is
        listed as activated but still on hand.
      </div>

      {loading && <div style={{ fontSize: 13, color: 'var(--text3)' }}>Loading…</div>}

      {!!err && (
        <div style={{ background: '#fef2f2', border: '1px solid #fca5a5', borderRadius: 10,
          padding: 14, fontSize: 13, color: '#991b1b' }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>❌ {err}</div>
          <div style={{ marginBottom: 10 }}>
            This is the page failing to reach the server — it does <b>not</b> mean your inventory is clean.
          </div>
          <button className="btn btn-secondary" onClick={load}>Retry</button>
        </div>
      )}

      {!loading && !err && totals && (<>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: 12, marginBottom: 16 }}>
          <Tile label="To clear out of inventory" value={String(totals.to_clear)} tone={totals.to_clear ? 'warn' : 'ok'} />
          <Tile label="Value sitting on the shelf that isn't there" value={money(totals.to_clear_cost)} tone={totals.to_clear_cost ? 'warn' : 'ok'} />
          <Tile label="Sold, no inventory row — to adjust" value={String(totals.to_adjust)} tone={totals.to_adjust ? 'info' : 'ok'} />
          <Tile label="Activated but still on hand" value={acts?.present ? String(totals.activated_not_rung_out ?? 0) : 'no activation report'} tone={(totals.activated_not_rung_out ?? 0) ? 'warn' : 'ok'} />
          <Tile label="On-hand units checked" value={String(totals.on_hand_considered)} tone="ok" />
        </div>

        {/* THE BASIS, PER SOURCE — what each side is and what it can pair on. A report that does not say
            what it read is a number acted on without its basis. */}
        {basis?.sources && (
          <div style={{ background: 'var(--card,#fff)', border: '1px solid var(--border)', borderRadius: 10, padding: 12, fontSize: 12.5, marginBottom: 16, lineHeight: 1.6 }}>
            {(['sales', 'inventory', 'activations'] as const).map(k => {
              const sb = basis.sources?.[k]
              if (!sb) return null
              return (
                <div key={k}>
                  <b style={{ textTransform: 'capitalize' }}>{k}:</b> <code>{sb.table}</code> — {sb.read_ok ? `${sb.rows.toLocaleString()} rows read${sb.truncated ? ' (capped — a floor)' : ''}` : <span style={{ color: '#991b1b' }}>read did not complete{k === 'activations' ? ' — no activation report is loaded; checked against sales only' : ''}</span>}{sb.period ? ` · ${sb.period}` : ''}
                  {k === 'activations' && sb.read_ok && <> · carries a device key: <b>{sb.carries_device_key ? 'yes' : 'no'}</b> · carries a mobile number: <b>{sb.carries_mobile ? 'yes' : 'no'}</b></>}
                  <div style={{ color: 'var(--text3)' }}>pairs on: {sb.pairs_on}</div>
                </div>
              )
            })}
          </div>
        )}

        {/* WHAT THIS ANSWER IS BASED ON. A capped or failed read makes every number above a FLOOR,
            and a report that does not say so is a number somebody acts on without knowing its basis. */}
        {basis && !basis.complete && (
          <div style={{ background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 10,
            padding: 12, fontSize: 12.5, color: '#92400e', marginBottom: 16 }}>
            <b>These are minimums, not totals.</b>{' '}
            {!basis.sales_read_ok && 'The sales read did not complete. '}
            {!basis.inventory_read_ok && 'The inventory read did not complete. '}
            {(basis.sales_truncated || basis.inventory_truncated) && 'More rows exist than were read. '}
            There may be more discrepancies than are listed here.
          </div>
        )}
        {totals.unkeyed_sale_lines > 0 && (
          <div style={{ fontSize: 12.5, color: 'var(--text3)', marginBottom: 16 }}>
            {totals.unkeyed_sale_lines.toLocaleString()} sale line{totals.unkeyed_sale_lines === 1 ? '' : 's'} carried
            no usable IMEI or serial, so no specific unit can be proven sold from them. They are excluded, not guessed at.
          </div>
        )}

        <Section
          title="🧹 In inventory, but already sold — clear these out"
          empty="Nothing on the shelf is already sold. "
          rows={clearRows} showDetails />
        <Section
          title="📲 Activated, but never rung out of inventory — check these"
          empty={acts?.present ? 'No on-hand unit appears in the activation report without a sale. ' : 'No activation report is loaded, so this check could not run. '}
          rows={activatedRows} showDetails showPairing />
        {acts && acts.unpairable.length > 0 && (
          <div style={{ background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 10, padding: 12, fontSize: 12.5, color: '#92400e', marginBottom: 24 }}>
            <b>{acts.unpairable.length} activation line{acts.unpairable.length === 1 ? '' : 's'} could not be paired to a unit</b> — listed, never guessed
            {' '}({totals.activations_paired_by_key ?? 0} paired by IMEI/serial, {totals.activations_paired_by_mobile ?? 0} by mobile number through a sale line):
            <ul style={{ margin: '6px 0 0 18px' }}>
              {acts.unpairable.slice(0, 50).map(u => <li key={u.line}>line <code>{u.line}</code>{u.mobile ? ` · mobile …${u.mobile.slice(-4)}` : ''}: {u.reason}{u.candidates?.length ? ` (${u.candidates.join(', ')})` : ''}</li>)}
              {acts.unpairable.length > 50 && <li>… and {acts.unpairable.length - 50} more</li>}
            </ul>
          </div>
        )}
        <Section
          title="🔧 Sold with an IMEI your inventory never knew — adjust these"
          empty="Every sold unit was accounted for in inventory. "
          rows={adjustRows} showDetails={false} />

        {truncated && (
          <div style={{ fontSize: 12.5, color: 'var(--text3)', marginTop: 12 }}>
            Showing the 500 largest by cost. More rows exist.
          </div>
        )}

        <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 20, lineHeight: 1.6 }}>
          This page reports; it does not change anything. Clearing a unit or posting an adjustment is done
          deliberately in the system of record — a one-click write-off of stock is exactly the action that
          should not be one click.
        </div>
      </>)}
    </div>
  )
}

function Tile({ label, value, tone }: { label: string; value: string; tone: 'ok' | 'warn' | 'info' }) {
  const c = tone === 'warn' ? { bg: '#fffbeb', bd: '#fde68a', fg: '#92400e' }
          : tone === 'info' ? { bg: '#eff6ff', bd: '#bfdbfe', fg: '#1e40af' }
          : { bg: 'var(--card, #fff)', bd: 'var(--border)', fg: 'var(--text2)' }
  return (
    <div style={{ background: c.bg, border: `1px solid ${c.bd}`, borderRadius: 10, padding: '12px 14px' }}>
      <div style={{ fontSize: 11.5, color: c.fg, opacity: 0.85 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 800, color: c.fg, marginTop: 2 }}>{value}</div>
    </div>
  )
}

const PAIRING: Record<string, string> = { device_key: 'by IMEI / serial', mobile_number_via_sales: 'by mobile number, through a sale line' }
const SALE_STATE: Record<string, string> = { no_sale_line: 'no sale line names this unit', sold_then_refunded: 'sold, then refunded back into stock' }

function Section({ title, empty, rows, showDetails, showPairing }:
                 { title: string; empty: string; rows: Row[]; showDetails: boolean; showPairing?: boolean }) {
  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{ fontWeight: 700, fontSize: 14, margin: '0 0 8px' }}>
        {title} <span style={{ fontWeight: 400, color: 'var(--text3)' }}>({rows.length})</span>
      </div>
      {rows.length === 0 ? (
        <div style={{ fontSize: 13, color: 'var(--text3)' }}>
          ✅ {empty}<span style={{ opacity: 0.8 }}>Checked against the sales file currently loaded.</span>
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
            <thead>
              <tr style={{ textAlign: 'left', color: 'var(--text3)' }}>
                <th style={th}>IMEI / serial</th>
                {showDetails && <th style={th}>Item</th>}
                {showDetails && <th style={th}>SKU</th>}
                {showDetails && <th style={th}>Store</th>}
                {showDetails && <th style={th}>Status</th>}
                {showDetails && <th style={th}>Received</th>}
                <th style={{ ...th, textAlign: 'right' }}>Net sold</th>
                {showDetails && <th style={{ ...th, textAlign: 'right' }}>Cost</th>}
                {showPairing && <th style={th}>Paired</th>}
                <th style={th}>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={`${r.finding}:${r.device_key}`} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ ...td, fontFamily: 'ui-monospace, monospace' }}>{r.device_key}</td>
                  {showDetails && <td style={td}>{r.item || '—'}</td>}
                  {showDetails && <td style={td}>{r.sku || '—'}</td>}
                  {showDetails && <td style={td}>{r.store || '—'}</td>}
                  {showDetails && <td style={td}>{r.status || '—'}</td>}
                  {showDetails && <td style={td}>{(r.received_date || '').slice(0, 10) || '—'}</td>}
                  <td style={{ ...td, textAlign: 'right' }}>{r.net_units}</td>
                  {showDetails && <td style={{ ...td, textAlign: 'right' }}>{money(r.cost)}</td>}
                  {showPairing && <td style={td}>{PAIRING[r.pairing || ''] || r.pairing || '—'}{r.sale_state ? <div style={{ color: 'var(--text3)' }}>{SALE_STATE[r.sale_state] || r.sale_state}</div> : null}</td>}
                  <td style={{ ...td, color: 'var(--text3)' }}>
                    {(r.evidence || []).map((e, i) => <div key={i}>{e.source === 'activation' ? '📲 activation' : '🧾 sale'} <code>{e.line}</code>{e.via ? ` (${e.via})` : ''}{e.note ? ` — ${e.note}` : ''}</div>)}
                    {r.also_activated && r.finding === CLEAR && <div>📲 also in the activation report</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

const th: React.CSSProperties = { padding: '6px 10px', fontWeight: 600, whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '7px 10px', verticalAlign: 'top' }
