'use client'
import { useCallback, useEffect, useState } from 'react'
import { api, fmt } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

// VENDOR REBATE HISTORY — what the carrier OWES, landed per line (index §27, mig 1005).
//
// THE ONE THING THIS SCREEN MUST NOT DO IS ADD ITS TWO MONEY COLUMNS TOGETHER.
// The feed is EARNED, not collected. On the first real export every one of 47,252 rows carries
// Collected $0.00 while Balance carries the whole $6,748,358.09 — the carrier has told us what it
// owes and nothing about what it has paid. Whether an earned-but-uncollected rebate books as a
// receivable is an OPEN OWNER DECISION, so until it is made nothing here reaches the P&L, the
// Balance Sheet, gross profit or commission payout, and the banner says so in those words rather
// than leaving the reader to assume. `booked_to: []` comes from the backend as the machine-readable
// form of the same statement (vendor_rebate_feed.BOOKS_TO).
//
// DUPLICATE CHECK: this is not a second rebate report. /commcalc/imei-rebates answers "which
// activation got a rebate PAID against it" from the feeds that prove PAYMENT. This answers "what is
// still owed on the statement we were sent". Folding this feed into that one would make it claim
// rebates were received when the file says $0.00 was collected — the VIP-payable mistake, where
// earned and settled were answered by one number and it was wrong for both.
//
// RULE TWO: no carrier, POS or vendor name appears here. Store names, vendor-account names and
// component names are DATA, rendered from the rows.

type Bucket = { key: string; rows: number; earned: number; collected: number; outstanding: number }
type Totals = {
  rows: number; earned: number; collected: number; outstanding: number
  balance_reported: number; balance_disagrees_by: number
  reversal_rows: number; invoices: number; devices: number; components: number
  stores: string[]; vendor_accounts: string[]; sold_from: string | null; sold_to: string | null
  booked_to: string[]; posture: string
}

const card: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px', background: 'var(--bg1)' }

function Tile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: 'owed' | 'paid' | 'plain' }) {
  const color = tone === 'owed' ? '#b45309' : tone === 'paid' ? '#15803d' : 'var(--text1)'
  return (
    <div style={card}>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: .3 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color, marginTop: 2 }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

function Breakdown({ title, rows, note }: { title: string; rows: Bucket[]; note?: string }) {
  if (!rows.length) return null
  return (
    <div style={{ marginTop: 22 }}>
      <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6 }}>{title}</div>
      {note && <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 6 }}>{note}</div>}
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 13 }}>
          <thead>
            <tr style={{ textAlign: 'left', color: 'var(--text3)' }}>
              <th style={{ padding: '6px 8px' }}>&nbsp;</th>
              <th style={{ padding: '6px 8px', textAlign: 'right' }}>Rows</th>
              <th style={{ padding: '6px 8px', textAlign: 'right' }}>Earned</th>
              <th style={{ padding: '6px 8px', textAlign: 'right' }}>Collected</th>
              <th style={{ padding: '6px 8px', textAlign: 'right' }}>Outstanding</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(b => (
              <tr key={b.key} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '6px 8px' }}>{b.key}</td>
                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{b.rows.toLocaleString()}</td>
                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{fmt(b.earned)}</td>
                <td style={{ padding: '6px 8px', textAlign: 'right', color: b.collected ? '#15803d' : 'var(--text3)' }}>{fmt(b.collected)}</td>
                <td style={{ padding: '6px 8px', textAlign: 'right', fontWeight: 600, color: '#b45309' }}>{fmt(b.outstanding)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function VendorRebatesPage() {
  const { period } = usePeriod()
  const [data, setData] = useState<any>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [scoped, setScoped] = useState(false)   // false = every landed row, ignoring the period bar

  const load = useCallback(() => {
    setLoading(true); setErr('')
    const q = scoped && period ? `?period=${encodeURIComponent(period)}` : ''
    api(`/api/v1/commcalc/vendor-rebates${q}`)
      .then((r: any) => setData(r))
      .catch((e: any) => setErr(e?.message || String(e)))
      .finally(() => setLoading(false))
  }, [period, scoped])
  useEffect(() => { load() }, [load])

  const t: Totals | null = data?.totals || null
  const booked = (t?.booked_to || []).length

  return (
    <div style={{ padding: 24, maxWidth: 1200 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Vendor Rebate History</h1>
      <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
        Every rebate and commission line the carrier statement says it owes — one row per rebate
        component per line, landed exactly as uploaded.
      </p>

      {/* The posture banner. It is not decoration: the whole risk of this feed is a reader assuming
          "earned" means "received", and this is the sentence that stops them. */}
      <div style={{ ...card, marginTop: 14, background: booked ? '#fffbeb' : '#f0f9ff',
                    borderColor: booked ? '#fcd34d' : '#bae6fd' }}>
        <strong style={{ fontSize: 13 }}>Earned, not collected — nothing here is booked.</strong>
        <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 4 }}>
          These rows record what the carrier <strong>owes</strong>. They do not prove payment, and they are
          deliberately not wired into the P&amp;L, the Balance Sheet, gross profit or commission payout while
          it is still an open decision whether an earned-but-uncollected rebate books as a receivable or
          waits for a payment file. <em>Earned</em> and <em>Collected</em> are shown side by side below and
          are never added together.
          {booked > 0 && <> <strong>This feed now books to: {(t?.booked_to || []).join(', ')}.</strong></>}
        </div>
      </div>

      <div style={{ display: 'flex', gap: 12, alignItems: 'center', margin: '14px 0 0', flexWrap: 'wrap' }}>
        <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
          <input type="checkbox" checked={scoped} onChange={e => setScoped(e.target.checked)} />
          Limit to the selected period{period ? ` (${period})` : ''}
        </label>
        <button className="btn btn-secondary" onClick={load}>↻ Refresh</button>
        <a className="btn btn-secondary" href="/commcalc/implementation">Import / map this report ↗</a>
      </div>

      {loading && <div style={{ marginTop: 16, color: 'var(--text2)' }}>Loading…</div>}
      {err && <div style={{ marginTop: 16, color: '#dc2626', fontSize: 13 }}>❌ {err}</div>}

      {/* A table that does not exist yet is a migration away, not a bug — say which one. */}
      {data && data.available === false && (
        <div style={{ ...card, marginTop: 16, background: '#fffbeb', borderColor: '#fcd34d' }}>
          <strong style={{ fontSize: 13 }}>Nothing landed yet.</strong>
          <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 4 }}>{data.note}</div>
        </div>
      )}

      {t && data?.available !== false && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 12, marginTop: 16 }}>
            <Tile label="Earned (owed to us)" value={fmt(t.earned)} tone="owed"
                  sub={`${t.rows.toLocaleString()} line(s)`} />
            <Tile label="Collected" value={fmt(t.collected)} tone="paid"
                  sub={t.collected === 0 ? 'the statement reports no payment at all' : undefined} />
            <Tile label="Still outstanding" value={fmt(t.outstanding)} tone="owed"
                  sub="earned − collected, recomputed here" />
            <Tile label="Invoices" value={t.invoices.toLocaleString()}
                  sub={`${t.devices.toLocaleString()} device(s) · ${t.components} component type(s)`} />
            <Tile label="Reversal lines" value={t.reversal_rows.toLocaleString()}
                  sub="quantity −1; already netted into earned" />
            <Tile label="Covers" value={t.sold_from && t.sold_to ? `${t.sold_from} → ${t.sold_to}` : '—'}
                  sub={t.stores.length === 1 ? t.stores[0] : `${t.stores.length} store(s)`} />
          </div>

          {/* A feed whose own Balance column disagrees with its own arithmetic is a fact about the
              file, so it is surfaced rather than quietly reconciled away. */}
          {Math.abs(t.balance_disagrees_by) >= 0.01 && (
            <div style={{ ...card, marginTop: 12, background: '#fef2f2', borderColor: '#fca5a5' }}>
              <strong style={{ fontSize: 13 }}>This file disagrees with itself by {fmt(t.balance_disagrees_by)}.</strong>
              <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 4 }}>
                Its own Balance column totals {fmt(t.balance_reported)}, while earned − collected is {fmt(t.outstanding)}.
                Reported, not averaged away — the difference is a property of the statement we were sent.
              </div>
            </div>
          )}

          {(data.status_counts || []).length > 0 && (
            <div style={{ marginTop: 22 }}>
              <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6 }}>Settlement state</div>
              <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 6 }}>
                “Not reported” means the statement carried no Collected value at all — which is not the same
                as being told nothing was paid, and the two are never merged.
              </div>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                {(data.status_counts as any[]).map(s => (
                  <div key={s.status} style={{ ...card, minWidth: 190 }}>
                    <div style={{ fontSize: 12, fontWeight: 600 }}>{String(s.status).replace(/_/g, ' ')}</div>
                    <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 2 }}>
                      {Number(s.rows).toLocaleString()} line(s) · {fmt(s.earned)} earned
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <Breakdown title="By month" rows={data.by_period || []} />
          <Breakdown title="By store" rows={data.by_store || []} />
          <Breakdown title="By vendor account / rebate program" rows={data.by_vendor_account || []}
                     note="The statement's own account name. One export can carry several — they are not all the same program." />
          <Breakdown title="Top rebate components" rows={data.by_component || []} />

          {data.device_cost && (
            <div style={{ ...card, marginTop: 22 }}>
              <div style={{ fontWeight: 700, fontSize: 14 }}>Device cost on these lines — reference only</div>
              <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 4 }}>
                {fmt(data.device_cost.cost)} across {Number(data.device_cost.devices).toLocaleString()} distinct devices,
                counted <strong>once per device</strong>. The statement repeats each device&apos;s cost on every one of its
                rebate component rows, so adding the column up row by row multiplies it — on the first real file that
                reads 7.6× high. This figure books nothing and is not device COGS; it is here so the number can be
                sanity-checked rather than re-derived by hand.
                {data.device_cost.rows_without_imei > 0 && <> {Number(data.device_cost.rows_without_imei).toLocaleString()} line(s)
                carry no device identifier and are excluded rather than guessed at.</>}
              </div>
            </div>
          )}

          <div style={{ marginTop: 22, fontSize: 12, color: 'var(--text3)' }}>
            Showing aggregates over {t.rows.toLocaleString()} landed line(s).
            {t.vendor_accounts.length > 1 && ` Vendor accounts present: ${t.vendor_accounts.join(', ')}.`}
          </div>
        </>
      )}
    </div>
  )
}
