'use client'
// MANAGEMENT WATCHDOG — one area's report (owner ask 2026-10-05: "under different reports so it is
// easy for the management to review each area and take appropriate action").
//
// ONE dynamic route serves every area, because the area list lives in the backend flag registry
// (app/modules/commcalc/flag_registry.py) and not here. A folder per area would be a second copy of
// that list in the file tree, and the eighth detector would quietly have no page.
//
// Client component + useParams: the same idiom as /hub/[group] and /training/flowcharts/[slug], so
// every dynamic route in this app reads its segment the same way.
//
// THE GRAIN COLUMN IS NOT DECORATION. It is the honest answer to "which transaction was this": a
// `rep_period` finding counted transactions it did not keep, so there is nothing to drill into, and
// the page says so instead of offering an empty drill-down.
import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { api, fmt } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import PageIntro from '@/components/PageIntro'
import { ExportButtons, type ExportPayload } from '@/lib/export'

const SEVERITY_COLORS: Record<string, string> = {
  CRITICAL: '#dc2626', HIGH: '#d97706', MEDIUM: '#2563eb', LOW: '#64748b',
}
// Worst first, the backend's own SEVERITY_RANK order.
const SEVERITY_ORDER = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']

const GRAIN_LABEL: Record<string, string> = {
  transaction: 'One transaction',
  store_day: 'One store-day',
  rep_period: 'A person, over the period',
  store_period: 'A store, over the period',
}

type Finding = {
  id?: string
  flag_type?: string; type_label?: string; grain?: string | null
  severity?: string; source?: string
  store_code?: string; store_address?: string; epay_salesperson?: string
  mdn?: string; imei?: string; amount?: number | null
  transaction_date?: string | null
  description?: string; coaching_note?: string
  status?: string; reviewed_by?: string | null; reviewed_at?: string | null
  action_taken?: string | null
}
type AreaResp = {
  area: string; label: string; blurb: string
  period?: string; open_count?: number
  types?: string[]
  findings?: Finding[]
}

export default function WatchdogAreaPage() {
  const { area } = useParams<{ area: string }>()
  const { period } = usePeriod()
  const [data, setData] = useState<AreaResp | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [fType, setFType] = useState('')
  const [fSev, setFSev] = useState('')
  const [fStore, setFStore] = useState('')
  const [fRep, setFRep] = useState('')

  useEffect(() => {
    if (!area) return
    setLoading(true)
    setErr('')
    api(`/api/v1/commcalc/watchdog/area/${encodeURIComponent(area)}`
        + `?period=${encodeURIComponent(period || '')}`)
      .then((d: AreaResp) => setData(d))
      .catch(e => { setErr(e?.message || String(e)); setData(null) })
      .finally(() => setLoading(false))
  }, [area, period])

  const findings = data?.findings || []

  const rows = useMemo(() => findings.filter(f =>
    (!fType || f.flag_type === fType)
    && (!fSev || (f.severity || '') === fSev)
    && (!fStore || (f.store_code || f.store_address || '').toLowerCase().includes(fStore.toLowerCase()))
    && (!fRep || (f.epay_salesperson || '').toLowerCase().includes(fRep.toLowerCase()))
  ), [findings, fType, fSev, fStore, fRep])

  // The kinds PRESENT, not the kinds registered: a filter offering a value that matches nothing is
  // the same small lie as a fake zero.
  const typesPresent = useMemo(
    () => Array.from(new Set(findings.map(f => f.flag_type || ''))).filter(Boolean).sort(),
    [findings])
  const sevsPresent = useMemo(
    () => SEVERITY_ORDER.filter(s => findings.some(f => (f.severity || '') === s)),
    [findings])

  // `ExportButtons` takes a FUNCTION returning the payload, and a payload carries `sheets` — the
  // shape every other report on this platform exports in, so the Excel/PDF/CSV output of a watchdog
  // area is indistinguishable from any other report.
  const buildPayload = (): ExportPayload => ({
    title: `Management Watchdog \u2014 ${data?.label || area}`,
    subtitle: data?.period || period || '',
    filename: `watchdog-${area}-${(data?.period || period || '').replace(/\s+/g, '-')}`,
    sheets: [{
      name: (data?.label || 'Findings').slice(0, 28),
      columns: [
        { header: 'Severity', get: (f: Finding) => f.severity || '' },
        { header: 'Finding', get: (f: Finding) => f.type_label || f.flag_type || '' },
        { header: 'What this is about', get: (f: Finding) => GRAIN_LABEL[f.grain || ''] || '' },
        { header: 'Store', get: (f: Finding) => f.store_code || f.store_address || '' },
        { header: 'Person', get: (f: Finding) => f.epay_salesperson || '', role: 'rep' },
        { header: 'Date', get: (f: Finding) => f.transaction_date || '', type: 'date' },
        // `amount` is deliberately passed through as null rather than 0 when a finding has no money
        // on it: an exported 0.00 would read as "nothing at stake", which is not what null means.
        { header: 'Amount', get: (f: Finding) => (f.amount ?? null), money: true, align: 'right' },
        { header: 'What happened', get: (f: Finding) => f.description || '' },
        { header: 'What to do', get: (f: Finding) => f.coaching_note || '' },
        { header: 'Ruled by', get: (f: Finding) => f.reviewed_by || '' },
        { header: 'Decision', get: (f: Finding) => f.action_taken || '' },
      ],
      rows,
    }],
  })

  return (
    <div>
      <PageIntro
        title={data?.label || 'Management Watchdog'}
        right={<Link href="/watchdog" style={{ fontSize: 13, color: 'var(--blue, #2563eb)' }}>
          ← All areas
        </Link>}
        help={data?.blurb}
      />

      {err && (
        <div style={{ border: '1px solid var(--red, #dc2626)', borderRadius: 8, padding: '10px 12px',
                      marginBottom: 14, fontSize: 13, color: 'var(--text2)' }}>
          This area could not be loaded: {err}
        </div>
      )}

      {loading && !data && <p style={{ color: 'var(--text2)', fontSize: 13 }}>Loading…</p>}

      {data && (
        <>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center',
                        marginBottom: 12 }}>
            <select value={fType} onChange={e => setFType(e.target.value)}>
              <option value="">Every kind of finding</option>
              {typesPresent.map(t => {
                const label = findings.find(f => f.flag_type === t)?.type_label || t
                return <option key={t} value={t}>{label}</option>
              })}
            </select>
            <select value={fSev} onChange={e => setFSev(e.target.value)}>
              <option value="">Any severity</option>
              {sevsPresent.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <input placeholder="Store" value={fStore} onChange={e => setFStore(e.target.value)}
                   style={{ width: 130 }} />
            <input placeholder="Person" value={fRep} onChange={e => setFRep(e.target.value)}
                   style={{ width: 130 }} />
            <span style={{ fontSize: 12.5, color: 'var(--text2)' }}>
              {rows.length} of {findings.length} open
            </span>
            <span style={{ marginLeft: 'auto' }}><ExportButtons payload={buildPayload} compact /></span>
          </div>

          {!findings.length && (
            <p style={{ fontSize: 13, color: 'var(--text2)' }}>
              Nothing open in this area for {data.period || period}. This area is watched — the checks
              ran and found nothing, which is not the same as not being measured.
            </p>
          )}

          {!!findings.length && (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ textAlign: 'left', color: 'var(--text2)', fontSize: 11.5,
                               textTransform: 'uppercase', letterSpacing: '0.4px' }}>
                    <th style={{ padding: '6px 8px' }}>Severity</th>
                    <th style={{ padding: '6px 8px' }}>Finding</th>
                    <th style={{ padding: '6px 8px' }}>Store</th>
                    <th style={{ padding: '6px 8px' }}>Person</th>
                    <th style={{ padding: '6px 8px' }}>Date</th>
                    <th style={{ padding: '6px 8px', textAlign: 'right' }}>Amount</th>
                    <th style={{ padding: '6px 8px' }}>What happened, and what to do</th>
                    <th style={{ padding: '6px 8px' }}>Ruled</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((f, i) => {
                    const colour = SEVERITY_COLORS[f.severity || ''] || '#64748b'
                    return (
                      <tr key={f.id || i} style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={{ padding: '8px' }}>
                          <span style={{ fontSize: 10, fontWeight: 700, color: colour,
                                         background: colour + '18', padding: '2px 7px',
                                         borderRadius: 999, whiteSpace: 'nowrap' }}>
                            {f.severity}
                          </span>
                        </td>
                        <td style={{ padding: '8px' }}>
                          <div style={{ fontWeight: 600 }}>{f.type_label || f.flag_type}</div>
                          {/* Says what this finding is ABOUT, so nobody looks for a transaction that
                              was never kept. */}
                          <div style={{ fontSize: 11.5, color: 'var(--text3)' }}>
                            {GRAIN_LABEL[f.grain || ''] || ''}
                          </div>
                        </td>
                        <td style={{ padding: '8px' }}>{f.store_code || f.store_address || '—'}</td>
                        <td style={{ padding: '8px' }}>{f.epay_salesperson || '—'}</td>
                        <td style={{ padding: '8px', whiteSpace: 'nowrap' }}>
                          {f.transaction_date || '—'}
                        </td>
                        <td style={{ padding: '8px', textAlign: 'right',
                                     fontVariantNumeric: 'tabular-nums' }}>
                          {/* null is not 0 — a finding with no amount shows a dash. */}
                          {f.amount === null || f.amount === undefined ? '—' : fmt(f.amount)}
                        </td>
                        <td style={{ padding: '8px', maxWidth: 460 }}>
                          <div>{f.description}</div>
                          {f.coaching_note && (
                            <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 3 }}>
                              {f.coaching_note}
                            </div>
                          )}
                        </td>
                        <td style={{ padding: '8px', fontSize: 12, color: 'var(--text2)' }}>
                          {f.reviewed_by
                            ? <>{f.reviewed_by}{f.action_taken ? ` — ${f.action_taken}` : ''}</>
                            : 'Not yet'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {area === 'voids' && (
            <p style={{ marginTop: 16, fontSize: 13 }}>
              <Link href="/watchdog/void-register" style={{ color: 'var(--blue, #2563eb)' }}>
                Open the full void &amp; return register →
              </Link>
              <span style={{ color: 'var(--text2)', marginLeft: 8 }}>
                every reversed line, not only the ones above the threshold
              </span>
            </p>
          )}
        </>
      )}
    </div>
  )
}
