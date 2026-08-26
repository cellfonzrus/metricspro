'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api, getActiveOrg } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

// Activations report — the b2b "Activation Details" basis of truth (owner 2026-08-26). One row per distinct
// device (Serial#); Total Activation EXCLUDES Upgrade (b2b-consistent, LuxeLink 687 / Nova 250), with a
// toggle to include it. The real export has no Store column — geography is Division / Region / District /
// Dealer Code — so this page rolls up by MARKET (LuxeLink vs Nova) and by STORE, and shows the automatic
// reconciliation against the sales-derived count. DISPLAY-ONLY.
const orgQ = () => { const o = getActiveOrg(); return o ? `?org_id=${encodeURIComponent(o)}` : '' }

const th: React.CSSProperties = { textAlign: 'right', padding: '7px 8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }
const thL: React.CSSProperties = { ...th, textAlign: 'left' }
const td: React.CSSProperties = { textAlign: 'right', padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 12.5, whiteSpace: 'nowrap' }
const tdL: React.CSSProperties = { ...td, textAlign: 'left' }
const int = (n: number) => String(Math.round(n || 0))

type Row = {
  store?: string; market?: string; total_activation: number; total_with_upgrade: number
  activation: number; port: number; byod: number; tablet: number; home_internet: number; edge: number; upgrade: number; other?: number
}

export default function ActivationsPage() {
  const { period } = usePeriod()
  const [data, setData] = useState<any>(null)
  const [recon, setRecon] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [inclUpg, setInclUpg] = useState(false)
  const [tab, setTab] = useState<'market' | 'store'>('market')
  const [srcCfg, setSrcCfg] = useState<any>(null)   // metric-source-config (the no-SQL basis toggle)
  const [savingSrc, setSavingSrc] = useState(false)
  const [dmap, setDmap] = useState<any>(null)       // dealer-code → store mapping status
  const [mapBusy, setMapBusy] = useState('')        // dealer_code currently being saved

  const load = useCallback(() => {
    if (!period) return
    setLoading(true); setErr(null)
    Promise.all([
      api(`/api/v1/commcalc/activation-counts/${encodeURIComponent(period)}${orgQ()}${orgQ() ? '&' : '?'}include_upgrade=${inclUpg}`),
      api(`/api/v1/commcalc/metric-recon/${encodeURIComponent(period)}${orgQ()}${orgQ() ? '&' : '?'}metric=activations`).catch(() => null),
      api(`/api/v1/commcalc/metric-source-config${orgQ()}`).catch(() => null),
      api(`/api/v1/commcalc/dealer-code-map/${encodeURIComponent(period)}${orgQ()}`).catch(() => null),
    ]).then(([ac, rc, sc, dm]: any[]) => {
      setData(ac); setRecon(rc); setDmap(dm)
      const act = (sc?.metrics || []).find((m: any) => m.metric === 'activations')
      setSrcCfg(act || null)
    }).catch(e => setErr(e?.message || String(e))).finally(() => setLoading(false))
  }, [period, inclUpg])

  // Map one Dealer Code to a store (writes commcalc.store_aliases via the shared endpoint) → the activations
  // then merge onto that store's named row. Empty store_code clears nothing here (delete is via Store Matching).
  const mapDealer = async (code: string, storeCode: string) => {
    if (!storeCode) return
    setMapBusy(code)
    try {
      await api('/api/v1/commcalc/store-aliases' + orgQ(), {
        method: 'POST', body: JSON.stringify({ alias: code, store_code: storeCode, source: 'manual' }),
      })
      load()
    } catch (e: any) { setErr(e?.message || String(e)) } finally { setMapBusy('') }
  }

  useEffect(() => { load() }, [load])

  // Flip Executive MTD / Sales Report onto (or off) the Activation Details basis — no SQL for the tenant.
  const setBasis = async (toActivationDetails: boolean) => {
    setSavingSrc(true)
    try {
      await api('/api/v1/commcalc/metric-source-config' + orgQ(), {
        method: 'PUT',
        body: JSON.stringify({
          metric: 'activations',
          source: toActivationDetails ? 'activation_details' : 'sales_agg',
          enabled: toActivationDetails,
          reconcile_with: 'sales_agg',
        }),
      })
      load()
    } catch (e: any) { setErr(e?.message || String(e)) } finally { setSavingSrc(false) }
  }

  const totalKey = inclUpg ? 'total_with_upgrade' : 'total_activation'
  const rows: Row[] = (tab === 'market' ? data?.markets : data?.stores) || []
  const grand = data?.total || {}
  const onAD = !!(srcCfg && srcCfg.enabled && srcCfg.source === 'activation_details')

  const COLS: { key: keyof Row; label: string }[] = [
    { key: totalKey as keyof Row, label: inclUpg ? 'Total (incl. Upgrade)' : 'Total Activation' },
    { key: 'activation', label: 'New Activation' },
    { key: 'port', label: 'Port' },
    { key: 'byod', label: 'BYOD' },
    { key: 'tablet', label: 'Tablet' },
    { key: 'home_internet', label: 'Home Internet' },
    { key: 'edge', label: 'Edge' },
    { key: 'upgrade', label: 'Upgrade' },
  ]

  return (
    <div style={{ padding: '18px 22px', maxWidth: 1180 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap', marginBottom: 4 }}>
        <h1 style={{ fontSize: 20, fontWeight: 800, margin: 0 }}>Activations</h1>
        <span style={{ fontSize: 12.5, color: 'var(--text3)' }}>
          b2b Activation Details — distinct devices (Serial#), {period || '—'}
        </span>
      </div>
      <p style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 6, marginBottom: 12, maxWidth: 820 }}>
        Total Activation counts one row per device and excludes Upgrade (the b2b-consistent definition). The
        export has no Store column, so activations roll up by <b>Market</b> (e.g. LuxeLink vs Nova) and by
        <b> Store</b> (Dealer Code / District). Toggle Upgrade in or out below.
      </p>

      {/* Controls */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
        <div style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
          {(['market', 'store'] as const).map(t => (
            <button key={t} onClick={() => setTab(t)}
              style={{ padding: '6px 14px', fontSize: 12.5, fontWeight: 700, border: 'none', cursor: 'pointer',
                background: tab === t ? 'var(--accent, #2563eb)' : 'transparent', color: tab === t ? '#fff' : 'var(--text2)' }}>
              By {t === 'market' ? 'Market' : 'Store'}
            </button>
          ))}
        </div>
        <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center', fontSize: 12.5, cursor: 'pointer' }}>
          <input type="checkbox" checked={inclUpg} onChange={e => setInclUpg(e.target.checked)} />
          Include Upgrades in the total
        </label>
        <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={load}>↻ Refresh</button>
      </div>

      {err && <div style={{ background: '#fef2f2', border: '1px solid #fecaca', color: '#991b1b', borderRadius: 8, padding: '9px 12px', fontSize: 12.5, marginBottom: 10 }}>❌ {err}</div>}
      {loading && <div style={{ color: 'var(--text3)', fontSize: 13, padding: 20 }}>Loading…</div>}
      {!loading && data?.note && (
        <div style={{ background: '#fffbeb', border: '1px solid #fde68a', color: '#92400e', borderRadius: 8, padding: '9px 12px', fontSize: 12.5, marginBottom: 10 }}>{data.note}</div>
      )}

      {/* Grand total chips */}
      {!loading && !data?.note && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
          <Chip label="Total Activation (excl. Upgrade)" value={int(grand.total_activation)} strong />
          <Chip label="Incl. Upgrade" value={int(grand.total_with_upgrade)} />
          <Chip label="Upgrades" value={int(grand.upgrade)} />
          <Chip label="Devices counted" value={int(data?.counted)} />
        </div>
      )}

      {/* Breakdown table */}
      {!loading && !data?.note && (
        <div style={{ overflowX: 'auto', border: '1px solid var(--border)', borderRadius: 10 }}>
          <table style={{ borderCollapse: 'collapse', width: '100%', minWidth: 720 }}>
            <thead>
              <tr>
                <th style={thL}>{tab === 'market' ? 'Market' : 'Store'}</th>
                {tab === 'store' && <th style={thL}>Market</th>}
                {COLS.map(c => <th key={String(c.key)} style={th}>{c.label}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td style={tdL}>{(tab === 'market' ? r.market : r.store) || '—'}</td>
                  {tab === 'store' && <td style={tdL}>{r.market || '—'}</td>}
                  {COLS.map(c => (
                    <td key={String(c.key)} style={{ ...td, fontWeight: c.key === totalKey ? 700 : 400 }}>{int((r as any)[c.key])}</td>
                  ))}
                </tr>
              ))}
              <tr>
                <td style={{ ...tdL, fontWeight: 800, borderTop: '2px solid var(--border)' }}>TOTAL</td>
                {tab === 'store' && <td style={{ ...tdL, borderTop: '2px solid var(--border)' }} />}
                {COLS.map(c => (
                  <td key={String(c.key)} style={{ ...td, fontWeight: 800, borderTop: '2px solid var(--border)' }}>{int((grand as any)[c.key])}</td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {/* Reconciliation panel */}
      {recon && !recon?.note && (
        <div style={{ marginTop: 18 }}>
          <h2 style={{ fontSize: 15, fontWeight: 800, margin: '0 0 6px' }}>Reconciliation</h2>
          <ReconPanel recon={recon} />
        </div>
      )}

      {/* Dealer Code → Store mapping: link the b2b numeric Dealer Codes to stores so activations merge onto
          the named store row instead of showing as a numeric ID. Only shows when there are unmapped codes. */}
      {dmap && (dmap.unmapped > 0) && (
        <div style={{ marginTop: 18 }}>
          <h2 style={{ fontSize: 15, fontWeight: 800, margin: '0 0 6px' }}>
            Dealer Code → Store <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12.5 }}>({dmap.unmapped} unmapped)</span>
          </h2>
          <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 8, maxWidth: 820 }}>
            These Dealer Codes from the Activation Details report aren&rsquo;t linked to a store yet, so their
            activations show under the numeric ID. Pick the matching store to merge them onto the named row.
          </div>
          <div style={{ overflowX: 'auto', border: '1px solid var(--border)', borderRadius: 10 }}>
            <table style={{ borderCollapse: 'collapse', width: '100%', minWidth: 640 }}>
              <thead><tr>
                <th style={thL}>Dealer Code</th><th style={th}>Activations</th>
                <th style={thL}>District / Region</th><th style={thL}>Map to store</th>
              </tr></thead>
              <tbody>
                {(dmap.codes || []).filter((c: any) => !c.mapped).map((c: any) => (
                  <tr key={c.dealer_code}>
                    <td style={tdL}><b>{c.dealer_code}</b></td>
                    <td style={td}>{int(c.activations)}</td>
                    <td style={tdL}>{[c.district, c.region].filter(Boolean).join(' · ') || '—'}</td>
                    <td style={tdL}>
                      <select disabled={mapBusy === c.dealer_code} defaultValue=""
                        onChange={e => mapDealer(c.dealer_code, e.target.value)}
                        style={{ padding: '5px 8px', fontSize: 12.5, border: '1px solid var(--border)', borderRadius: 8, minWidth: 260 }}>
                        <option value="">{mapBusy === c.dealer_code ? 'Saving…' : 'Select a store…'}</option>
                        {(dmap.stores || []).map((s: any) => (
                          <option key={s.store_code} value={s.store_code}>{s.address || s.store_code}</option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Source-of-truth (no-SQL) basis toggle */}
      <div style={{ marginTop: 18, border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px' }}>
        <div style={{ fontSize: 13, fontWeight: 800, marginBottom: 4 }}>Activation basis for Executive MTD / Sales Report</div>
        <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 8, maxWidth: 760 }}>
          {onAD
            ? 'Executive MTD & the Sales Report are using the Activation Details report as the basis of truth (Total Activation excludes Upgrade). The two sources reconcile automatically above.'
            : 'Executive MTD & the Sales Report currently derive activations from the sales feed. Switch them to the Activation Details basis of truth — no SQL needed. Reconcile above first to confirm the two sources line up.'}
        </div>
        <button className="btn" disabled={savingSrc}
          style={{ fontSize: 12.5, fontWeight: 700, background: onAD ? '#f3f4f6' : 'var(--accent, #2563eb)', color: onAD ? 'var(--text2)' : '#fff' }}
          onClick={() => setBasis(!onAD)}>
          {savingSrc ? 'Saving…' : onAD ? 'Switch back to the sales feed basis' : 'Use Activation Details as the basis of truth'}
        </button>
      </div>
    </div>
  )
}

function Chip({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: '8px 14px', minWidth: 120,
      background: strong ? '#eff6ff' : 'var(--surface)' }}>
      <div style={{ fontSize: 11, color: 'var(--text3)' }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 800 }}>{value}</div>
    </div>
  )
}

function ReconPanel({ recon }: { recon: any }) {
  const s = recon.status as string
  const ok = s === 'match'
  const color = ok ? { bg: '#ecfdf5', bd: '#6ee7b7', fg: '#065f46' } : { bg: '#fffbeb', bd: '#fde68a', fg: '#92400e' }
  const t = recon.totals || {}
  const c = recon.counts || {}
  const rem = recon.remediation
  return (
    <div style={{ background: color.bg, border: `1px solid ${color.bd}`, color: color.fg, borderRadius: 8, padding: '10px 13px', fontSize: 12.5 }}>
      <div style={{ fontWeight: 800, marginBottom: 4 }}>
        {ok ? '✓ Sources match — ingest proven good' : s === 'mismatch' ? '⚠️ Sources disagree' : 'ℹ︎ ' + s}
      </div>
      <div style={{ marginBottom: rem ? 6 : 0 }}>
        Activation Details (basis of truth): <b>{int(t.primary)}</b> · sales feed: <b>{int(t.secondary)}</b> · delta: <b>{int(t.delta)}</b>.
        {' '}{int(c.matched)} stores match, {int(c.mismatched)} differ, {int(c.missing_in_primary)} missing in Activation Details, {int(c.missing_in_secondary)} missing in the feed.
      </div>
      {rem && (
        <div style={{ marginBottom: 6 }}>
          <b>Suggested:</b> {rem.reason}{rem.assigned_user ? ` (assignee: ${rem.assigned_user})` : ''}
        </div>
      )}
      {(recon.stores || []).length > 0 && (
        <div style={{ overflowX: 'auto', marginTop: 4 }}>
          <table style={{ borderCollapse: 'collapse', width: '100%', minWidth: 420, background: 'var(--surface)', color: 'var(--text)' }}>
            <thead><tr>
              <th style={thL}>Store</th><th style={th}>Activation Details</th><th style={th}>Sales feed</th><th style={th}>Δ</th><th style={thL}>Issue</th>
            </tr></thead>
            <tbody>
              {(recon.stores || []).slice(0, 40).map((r: any, i: number) => (
                <tr key={i}>
                  <td style={tdL}>{r.store}</td>
                  <td style={td}>{int(r.primary)}</td>
                  <td style={td}>{int(r.secondary)}</td>
                  <td style={{ ...td, fontWeight: 700 }}>{int(r.delta)}</td>
                  <td style={tdL}>{r.kind}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
