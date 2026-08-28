'use client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ReportShell } from '@/components/ReportShell'
import type { ExportColumn } from '@/lib/export'
import StandardFilterBar from '@/components/StandardFilterBar'
import EntityPicker from '@/components/EntityPicker'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'
import { useActiveCarrier } from '@/lib/auth-context'
import { financingVendorLabel, vendorServesCarrier } from '@/lib/carrier-scope'

// FINANCING REPORT — owner directive 2026-08-04: "need another report for tracking the financing, edge in
// case of total and acima in case of boost … should have assignable target for each store in target area".
//
// WHAT A UNIT IS. A financed sale rings as many lines that all carry the same tender (device, rate plan,
// case, protector, fees). This report counts DEVICES, using the same collapse the payout uses — so the
// number here and the number that pays cannot drift.
//
// WHAT THE AMOUNT IS. The POS export's own "Financed Amount" column is not stored in raw_sales (and is
// populated on well under 1% of rows), so the financed amount shown is the device line's Ext Price. The
// page says so out loud rather than implying a number it does not have.

type Row = {
  vendor_key: string; vendor: string; store: string; store_code: string; market: string
  rep: string; units: number; amount: number; transactions: number
  first_date: string | null; last_date: string | null
}
type StoreRow = {
  store_code: string; store: string; market: string; units: number; amount: number
  target_units: number | null; target_source: string; attainment_pct: number | null
  need_units: number | null; projected_units: number | null; pace_per_day: number | null
  on_pace: boolean | null; needed_per_remaining_day: number | null
  by_vendor: { vendor_key: string; vendor: string; units: number; amount: number }[]
  vendor_targets: { vendor_key: string; target_units: number; units: number }[]
}
type Vendor = {
  vendor_key: string; label: string; enabled: boolean; detection_source: string
  detection_status: string; detection_note: string; amount_basis: string
  matchers: { match_field: string; match_op: string; match_value: string; field_warning?: string | null; from_rule_label?: string }[]
  carriers: { carrier_name?: string | null; carrier_id?: string | null }[]
}
type Data = {
  ready: boolean; period: string
  rows: Row[]; by_store: StoreRow[]
  by_vendor: { vendor_key: string; vendor: string; units: number; amount: number; transactions: number; stores: number; detection_status: string; detection_note: string }[]
  totals: { units: number; amount: number; transactions: number }
  tender_values: { value: string; lines: number; transactions: number }[]
  unit_notes: string[]; vendors: Vendor[]; markets: string[]
  configured_vendors: number
  vendors_running_on_defaults?: number
  amount_note: string; attainment_note: string
  source: any
}

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const tile: React.CSSProperties = { flex: '1 1 180px', minWidth: 170, border: '1px solid var(--border)', borderRadius: 10, padding: '10px 14px', background: 'var(--surface)' }
const tileCap: React.CSSProperties = { fontSize: 11.5, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: .4 }
const th: React.CSSProperties = { textAlign: 'left', padding: '8px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '.05em' }
const td: React.CSSProperties = { padding: '7px 12px', fontSize: 13 }

const DETAIL_COLS: ExportColumn[] = [
  { header: 'Vendor', get: r => r.vendor },
  { header: 'Store', get: r => r.store || r.store_code, role: 'store' },
  { header: 'Market', get: r => r.market },
  { header: 'Rep', get: r => r.rep, role: 'rep' },
  { header: 'Financed units', get: r => r.units, type: 'number' },
  { header: 'Transactions', get: r => r.transactions, type: 'number' },
  { header: 'Financed amount', get: r => r.amount, money: true },
  { header: 'First', get: r => r.first_date, type: 'date' },
  { header: 'Last', get: r => r.last_date, type: 'date' },
]

const STORE_COLS: ExportColumn[] = [
  { header: 'Store', get: r => r.store || r.store_code, role: 'store' },
  { header: 'Market', get: r => r.market },
  { header: 'Financed units', get: r => r.units, type: 'number' },
  { header: 'Financed amount', get: r => r.amount, money: true },
  { header: 'Target', get: r => (r.target_units == null ? 'no target' : r.target_units), type: 'text' },
  { header: 'Attainment %', get: r => (r.attainment_pct == null ? '—' : `${r.attainment_pct}%`) },
  { header: 'To go', get: r => (r.need_units == null ? '—' : r.need_units), type: 'text' },
  { header: 'Projected (MTD pace)', get: r => (r.projected_units == null ? '—' : r.projected_units), type: 'text' },
  { header: 'Need / open day', get: r => (r.needed_per_remaining_day == null ? '—' : r.needed_per_remaining_day), type: 'text' },
]

export default function FinancingReportPage() {
  const { period } = usePeriod()
  // Active-carrier lens: for a dual-carrier tenant, show only the active carrier's vendors, relabelled
  // generically (never ACIMA/TW/Edge), and drop the carrier-naming "Carriers" column. Single-carrier
  // tenants are unchanged.
  const { activeCarrier, multi } = useActiveCarrier()
  const vlabel = (key: string, raw: string) => (multi ? financingVendorLabel(key, raw) : raw)
  const [d, setD] = useState<Data | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [tab, setTab] = useState<'stores' | 'detail' | 'vendors'>('stores')
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [vendorSel, setVendorSel] = useState<string[]>([])

  const load = useCallback(() => {
    if (!period) return
    setBusy(true); setErr('')
    api(`/api/v1/commcalc/financing/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      .then(setD)
      .catch(e => setErr(String(e?.message || e)))
      .finally(() => setBusy(false))
  }, [period])
  useEffect(() => { load() }, [load])

  // Vendors visible under the active carrier (carrier-neutral vendors always show); the set of their
  // keys gates the detail rows + vendor tiles so nothing off-carrier is displayed.
  const shownVendors = useMemo(
    () => (d?.vendors || []).filter(v => !multi || vendorServesCarrier(v.carriers, activeCarrier)),
    [d, multi, activeCarrier])
  const allowedKeys = useMemo(() => new Set(shownVendors.map(v => v.vendor_key)), [shownVendors])
  const rows = useMemo(
    () => (multi ? (d?.rows || []).filter(r => allowedKeys.has(r.vendor_key)) : (d?.rows || [])),
    [d, multi, allowedKeys])
  const opts = useMemo(() => optionsFromRows(rows, {
    store: r => r.store || r.store_code, market: r => r.market, rep: r => r.rep,
  }), [rows])
  const vendorOpts = useMemo(
    () => shownVendors.map(v => ({ id: v.vendor_key, label: vlabel(v.vendor_key, v.label) })), [shownVendors, multi])

  // RULE FIVE: the core bar drives the tables AND the exports (what you see is what exports).
  const shown = useMemo(() => {
    const base = filterRows(rows, filt, {
      store: r => r.store || r.store_code, market: r => r.market, rep: r => r.rep,
    })
    return vendorSel.length ? base.filter(r => vendorSel.includes(r.vendor_key)) : base
  }, [rows, filt, vendorSel])

  // The store table follows the same filter set, and its per-store numbers are recomputed from the
  // FILTERED detail rows so a rep/vendor filter cannot leave a total that disagrees with its own rows.
  const shownStores = useMemo(() => {
    // Under the active-carrier lens, recompute per-store totals from the filtered (active-carrier) rows
    // so the store table never sums in the other carrier's financing.
    const narrowed = filt.reps.length > 0 || vendorSel.length > 0 || multi
    const agg: Record<string, { units: number; amount: number }> = {}
    for (const r of shown) {
      const a = agg[r.store_code] || (agg[r.store_code] = { units: 0, amount: 0 })
      a.units += r.units; a.amount += r.amount
    }
    return (d?.by_store || [])
      .filter(s => (!filt.stores.length || filt.stores.some(x => x.toLowerCase() === (s.store || s.store_code).toLowerCase()))
        && (!filt.markets.length || filt.markets.some(x => x.toLowerCase() === (s.market || '').toLowerCase()))
        && (!narrowed || agg[s.store_code]))
      .map(s => {
        if (!narrowed) return s
        const a = agg[s.store_code] || { units: 0, amount: 0 }
        return {
          ...s, units: a.units, amount: Math.round(a.amount * 100) / 100,
          attainment_pct: s.target_units ? Math.round(1000 * a.units / s.target_units) / 10 : null,
          need_units: s.target_units ? Math.max(0, s.target_units - a.units) : null,
        }
      })
  }, [d, shown, filt, vendorSel, multi])

  const t = useMemo(() => shown.reduce((a, r) => ({
    units: a.units + r.units, amount: a.amount + r.amount,
  }), { units: 0, amount: 0 }), [shown])

  const withTarget = shownStores.filter(s => (s.target_units || 0) > 0)
  const hitting = withTarget.filter(s => (s.attainment_pct || 0) >= 100).length
  const unconfigured = shownVendors.filter(v => v.enabled && v.detection_status !== 'configured')

  return (
    <div style={{ maxWidth: 1320 }}>
      <div style={{ marginBottom: 18 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Financing report</h1>
          <Link href="/commcalc/financing/vendors" className="btn btn-secondary" style={{ fontSize: 12 }}>
            ⚙️ Financing vendors
          </Link>
          <Link href="/commcalc/targets/settings" className="btn btn-secondary" style={{ fontSize: 12 }}>
            🎯 Set store targets
          </Link>
        </div>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '6px 0 0' }}>
          {period} · Financed units and financed dollars by vendor, store and rep, against each store's
          monthly financing target. One financed sale = one <b>device</b>, not one receipt line.
        </p>
      </div>

      <StandardFilterBar
        value={filt} onChange={setFilt} periodMode="month"
        storeOptions={opts.stores} marketOptions={opts.markets} repOptions={opts.reps}
        right={<>
          {vendorOpts.length > 0 && (
            <EntityPicker multi options={vendorOpts} value={vendorSel} onChange={setVendorSel}
              placeholder="Vendors…" width={175} ariaLabel="Filter by financing vendor" />
          )}
          <button className="btn btn-secondary" style={{ fontSize: 12 }} disabled={busy} onClick={() => load()}>
            {busy ? '…' : '↻ Reload'}
          </button>
        </>}
      />

      {err && <div className="card" style={{ padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--red)' }}>{err}</div>}

      {unconfigured.length > 0 && (
        <div className="card" style={{ padding: '11px 15px', marginBottom: 14, fontSize: 13,
          background: '#fffbeb', border: '1px solid #fde68a', color: '#92400e' }}>
          <b>⚠️ {unconfigured.length} vendor{unconfigured.length > 1 ? 's are' : ' is'} not mapped yet.</b>{' '}
          {unconfigured.map(v => (multi ? vlabel(v.vendor_key, v.label) : `${v.label}: ${v.detection_note}`)).join(' · ')}{' '}
          <Link href="/commcalc/financing/vendors" style={{ textDecoration: 'underline' }}>Map it now</Link>
          {' '}— until then it counts nothing, and a zero below is “not configured”, not “no financing”.
        </div>
      )}

      {d?.ready && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
          <div style={tile}>
            <div style={tileCap}>Financed units</div>
            <div style={{ fontSize: 24, fontWeight: 800 }}>{t.units}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>{shown.length} vendor · store · rep rows</div>
          </div>
          <div style={tile}>
            <div style={tileCap}>Financed amount</div>
            <div style={{ fontSize: 24, fontWeight: 800 }}>{fmt(t.amount)}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>device price basis</div>
          </div>
          <div style={tile}>
            <div style={tileCap}>Stores hitting target</div>
            <div style={{ fontSize: 24, fontWeight: 800 }}>{withTarget.length ? `${hitting}/${withTarget.length}` : '—'}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>
              {withTarget.length ? 'monthly attainment' : 'no financing targets set yet'}
            </div>
          </div>
          {(d.by_vendor || []).filter(v => !multi || allowedKeys.has(v.vendor_key)).slice(0, 2).map(v => (
            <div key={v.vendor_key} style={tile}>
              <div style={tileCap}>{vlabel(v.vendor_key, v.vendor)}</div>
              <div style={{ fontSize: 24, fontWeight: 800 }}>{v.units}</div>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>{fmt(v.amount)} · {v.stores} store(s)</div>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        {(['stores', 'detail', 'vendors'] as const).map(k => (
          <button key={k} className={tab === k ? 'btn btn-primary' : 'btn'} style={{ fontSize: 12 }}
            onClick={() => setTab(k)}>
            {k === 'stores' ? 'By store vs target' : k === 'detail' ? 'Vendor · store · rep' : 'Vendors & detection'}
          </button>
        ))}
      </div>

      {busy && !d && <div className="card" style={{ padding: 30, textAlign: 'center', color: 'var(--text3)' }}>Loading…</div>}

      {d?.ready && tab === 'stores' && (
        <ReportShell
          title="Financing by store vs target" subtitle={`${period} · ${d.attainment_note}`}
          filename={`financing-by-store-${period}`} columns={STORE_COLS} rows={shownStores} totals
          stickyHeader
        />
      )}

      {d?.ready && tab === 'detail' && (
        <ReportShell
          title="Financing by vendor · store · rep" subtitle={`${period} · ${d.amount_note}`}
          filename={`financing-detail-${period}`} columns={DETAIL_COLS}
          rows={multi ? shown.map(r => ({ ...r, vendor: financingVendorLabel(r.vendor_key, r.vendor) })) : shown} totals
          stickyHeader defaultGroupBy="Store" collapsibleGroups
        />
      )}

      {d?.ready && tab === 'vendors' && (
        <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 900 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
                {/* The "Carriers" column names carriers, so it is dropped under a dual-carrier lens. */}
                {['Vendor', ...(multi ? [] : ['Carriers']), 'How a financed sale is recognised', 'Units', 'Amount'].map(h =>
                  <th key={h} style={th}>{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {shownVendors.map(v => {
                const agg = (d.by_vendor || []).find(x => x.vendor_key === v.vendor_key)
                return (
                  <tr key={v.vendor_key} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={td}>
                      <div style={{ fontWeight: 600 }}>{vlabel(v.vendor_key, v.label)}</div>
                      <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                        {multi ? (v.enabled ? '' : 'disabled') : `${v.vendor_key}${v.enabled ? '' : ' · disabled'}`}
                      </div>
                    </td>
                    {!multi && (
                    <td style={td}>
                      {(v.carriers || []).length
                        ? (v.carriers || []).map((c, i) => <span key={i} style={{ marginRight: 6 }}>{c.carrier_name || c.carrier_id}</span>)
                        : <span style={{ color: 'var(--text3)' }}>any carrier</span>}
                    </td>
                    )}
                    <td style={td}>
                      {/* Under a dual-carrier lens the raw detection rules / notes can name the vendor
                          brand or the other carrier (tender strings like "TW FINANCING", seed notes that
                          mention ACIMA/Boost), so show only a neutral status there. */}
                      {multi
                        ? (v.detection_status === 'configured'
                            ? <span style={{ color: 'var(--text3)' }}>✓ configured</span>
                            : <span style={{ color: '#b45309' }}>⚠️ not configured yet</span>)
                        : (v.matchers || []).length === 0
                        ? <span style={{ color: '#b45309' }}>⚠️ {v.detection_note}</span>
                        : <>
                          {(v.matchers || []).map((m, i) => (
                            <div key={i} style={{ fontSize: 12.5 }}>
                              <code>{m.match_field}</code> {m.match_op} <b>“{m.match_value}”</b>
                              {m.from_rule_label && <span style={{ color: 'var(--text3)' }}> · from pay rule {m.from_rule_label}</span>}
                              {m.field_warning && <div style={{ color: '#b45309', fontSize: 11.5 }}>⚠️ {m.field_warning}</div>}
                            </div>
                          ))}
                          <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 3 }}>{v.detection_note}</div>
                        </>}
                    </td>
                    <td style={td}>{agg?.units ?? 0}</td>
                    <td style={td}>{fmt(agg?.amount || 0)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {/* Raw tender strings can name a vendor brand / carrier ("TW FINANCING") — hide under the lens. */}
          {!multi && (d.tender_values || []).length > 0 && (
            <div style={{ padding: '12px 16px', borderTop: '1px solid var(--border)', fontSize: 12.5, color: 'var(--text2)' }}>
              <b>Tender values actually present in {period}</b> — map a vendor by picking one of these
              rather than typing a guess:{' '}
              {(d.tender_values || []).map(f => (
                <span key={f.value} style={{ display: 'inline-block', marginRight: 10 }}>
                  “{f.value}” <span style={{ color: 'var(--text3)' }}>({f.transactions} txn)</span>
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {d?.ready && (
        <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 14, lineHeight: 1.6 }}>
          {d.amount_note}<br />
          {d.attainment_note}<br />
          Read from {d.source?.rows_read ?? 0} sale line(s) ({d.source?.primary || 'raw sales'} leading).
          Financing units are collapsed to devices with the same rule the payout uses, so this count and
          the paid count agree.
        </div>
      )}
    </div>
  )
}
