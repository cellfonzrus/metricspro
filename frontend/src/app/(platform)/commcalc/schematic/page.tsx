'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, getActiveOrg } from '@/lib/client'

// System Schematic — renders the commcalc.data_lineage registry (mig 924/925): how ingested data and derived
// metrics feed each other. Grouped by source item; each edge shows the affected item, where it's visible, a
// code reference, a plain-English effect, and whether it updates automatically. DISPLAY/documentation only.
const orgQ = () => { const o = getActiveOrg(); return o ? `?org_id=${encodeURIComponent(o)}` : '' }

const th: React.CSSProperties = { textAlign: 'left', padding: '7px 9px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { textAlign: 'left', padding: '7px 9px', borderTop: '1px solid var(--border)', fontSize: 12.5, verticalAlign: 'top' }
const mono: React.CSSProperties = { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 11.5, color: 'var(--text2)' }

const KIND_COLOR: Record<string, { bg: string; fg: string }> = {
  ingest: { bg: '#eef2ff', fg: '#3730a3' },
  display: { bg: '#ecfeff', fg: '#155e75' },
  target: { bg: '#f0fdf4', fg: '#166534' },
  recon: { bg: '#fef3c7', fg: '#92400e' },
  pay: { bg: '#fee2e2', fg: '#991b1b' },
}

type Edge = {
  source_key: string; source_label?: string; entry_point?: string
  affected_key: string; affected_label?: string; surface?: string
  kind: string; auto_updated: boolean; effect_code?: string; effect_english?: string
}

export default function SchematicPage() {
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [kind, setKind] = useState('')

  useEffect(() => {
    setLoading(true); setErr(null)
    api(`/api/v1/commcalc/data-lineage${orgQ()}`)
      .then(setData).catch(e => setErr(e?.message || String(e))).finally(() => setLoading(false))
  }, [])

  const bySource: Record<string, Edge[]> = data?.by_source || {}
  const sources = data?.sources || []
  const kinds = useMemo(() => {
    const s = new Set<string>()
    Object.values(bySource).forEach((edges: any) => edges.forEach((e: Edge) => s.add(e.kind)))
    return Array.from(s).sort()
  }, [bySource])

  const ql = q.trim().toLowerCase()
  const match = (e: Edge) => (!kind || e.kind === kind) && (!ql ||
    [e.source_key, e.source_label, e.affected_key, e.affected_label, e.surface, e.effect_code, e.effect_english]
      .some(v => String(v || '').toLowerCase().includes(ql)))

  const visibleSources = sources.filter((s: string) => (bySource[s] || []).some(match))

  return (
    <div style={{ padding: '18px 22px', maxWidth: 1200 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 20, fontWeight: 800, margin: 0 }}>System Schematic</h1>
        <span style={{ fontSize: 12.5, color: 'var(--text3)' }}>How ingested data & metrics feed each other · {data?.count ?? 0} links</span>
      </div>
      <p style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 6, marginBottom: 12, maxWidth: 900 }}>
        Each row is a dependency: a <b>source</b> item → an <b>affected</b> item, with where it&rsquo;s visible, the
        code that implements it, and a plain-English effect. The <b>Auto</b> badge means a change propagates
        automatically; <b>Manual</b> marks a wiring gap to watch. Data from <code style={mono}>commcalc.data_lineage</code>.
      </p>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
        <input style={{ padding: '7px 10px', fontSize: 13, border: '1px solid var(--border)', borderRadius: 8, minWidth: 240 }}
          placeholder="Search source / affected / code / effect…" value={q} onChange={e => setQ(e.target.value)} />
        <select style={{ padding: '7px 10px', fontSize: 13, border: '1px solid var(--border)', borderRadius: 8 }}
          value={kind} onChange={e => setKind(e.target.value)}>
          <option value="">All kinds</option>
          {kinds.map(k => <option key={k} value={k}>{k}</option>)}
        </select>
      </div>

      {err && <div style={{ background: '#fef2f2', border: '1px solid #fecaca', color: '#991b1b', borderRadius: 8, padding: '9px 12px', fontSize: 12.5, marginBottom: 10 }}>❌ {err}</div>}
      {loading && <div style={{ color: 'var(--text3)', fontSize: 13, padding: 20 }}>Loading…</div>}
      {!loading && data?.note && (
        <div style={{ background: '#fffbeb', border: '1px solid #fde68a', color: '#92400e', borderRadius: 8, padding: '9px 12px', fontSize: 12.5, marginBottom: 10 }}>{data.note}</div>
      )}

      {!loading && visibleSources.map((src: string) => {
        const edges = (bySource[src] || []).filter(match)
        const label = edges[0]?.source_label || src
        return (
          <div key={src} style={{ marginBottom: 18, border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
            <div style={{ background: 'var(--surface2, #f8fafc)', padding: '9px 12px', display: 'flex', gap: 8, alignItems: 'baseline' }}>
              <span style={{ fontSize: 14, fontWeight: 800 }}>{label}</span>
              <code style={{ ...mono }}>{src}</code>
              <span style={{ fontSize: 11, color: 'var(--text3)' }}>· {edges.length} link{edges.length === 1 ? '' : 's'}</span>
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ borderCollapse: 'collapse', width: '100%', minWidth: 820 }}>
                <thead><tr>
                  <th style={th}>Affects</th><th style={th}>Where</th><th style={th}>Kind</th>
                  <th style={th}>Updates</th><th style={th}>Code</th><th style={th}>Effect</th>
                </tr></thead>
                <tbody>
                  {edges.map((e, i) => {
                    const kc = KIND_COLOR[e.kind] || { bg: 'var(--surface2)', fg: 'var(--text2)' }
                    return (
                      <tr key={i}>
                        <td style={td}><b>{e.affected_label || e.affected_key}</b><div style={mono}>{e.affected_key}</div></td>
                        <td style={td}>{e.surface || '—'}</td>
                        <td style={td}><span style={{ background: kc.bg, color: kc.fg, borderRadius: 6, padding: '1px 7px', fontSize: 11, fontWeight: 700 }}>{e.kind}</span></td>
                        <td style={td}>
                          {e.auto_updated
                            ? <span style={{ background: '#ecfdf5', color: '#065f46', borderRadius: 6, padding: '1px 7px', fontSize: 11, fontWeight: 700 }}>Auto</span>
                            : <span style={{ background: '#fef2f2', color: '#991b1b', borderRadius: 6, padding: '1px 7px', fontSize: 11, fontWeight: 700 }}>Manual</span>}
                        </td>
                        <td style={{ ...td, ...mono, maxWidth: 240, whiteSpace: 'normal' }}>{e.effect_code || '—'}</td>
                        <td style={{ ...td, maxWidth: 360, whiteSpace: 'normal', color: 'var(--text2)' }}>{e.effect_english || '—'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )
      })}
      {!loading && !data?.note && visibleSources.length === 0 && (
        <div style={{ color: 'var(--text3)', fontSize: 13, padding: 20 }}>No links match your filter.</div>
      )}
    </div>
  )
}
