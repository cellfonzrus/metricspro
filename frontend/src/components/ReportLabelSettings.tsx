'use client'
// ReportLabelSettings — "a place to fix the column labels as per the carrier" (owner 2026-09-02).
//
// The panel edits THIS org's report-label OVERRIDES (PUT /commcalc/report-labels → mig-068
// ui_label_override rows, scope 'report_col'/'report_banner'). It shows, per labelable column:
// the built-in default, the carrier preset the org inherits (house data rows, mig 945 — what a
// new tenant gets automatically when it picks its carrier), and the org's own override input.
// Clearing an override reverts to inheritance (carrier preset, then built-in) — never to blank.
// Also hosts the banner terminology toggle (e.g. the b2bsoft-MTD unrecognized-contract-type
// warning): Auto = follow the carrier preset; Always / Never = tenant override. DISPLAY-ONLY —
// no data path, bucket rule, or pay number changes when a label changes.
import { useMemo, useState } from 'react'
import { api, getActiveOrg } from '@/lib/client'
import { useReportLabels } from '@/lib/report-labels'

const orgQS = () => { const o = getActiveOrg(); return o ? `?org_id=${encodeURIComponent(o)}` : '' }

export default function ReportLabelSettings({ onClose, onSaved }: { onClose: () => void; onSaved?: () => void }) {
  const { data, reload, activeCarrier } = useReportLabels()
  const [edits, setEdits] = useState<Record<string, string>>({})          // column key → typed label
  const [bannerEdits, setBannerEdits] = useState<Record<string, string>>({}) // banner key → 'on'|'off'|'' (auto)
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState(false)

  const preset = useMemo(() => data?.presets?.[activeCarrier] || { columns: {}, banners: {} }, [data, activeCarrier])
  const overrides = data?.overrides || { columns: {}, banners: {} }

  if (!data) return <div className="card" style={{ padding: 16, marginBottom: 12 }}>Loading labels…</div>

  const save = async () => {
    setSaving(true); setMsg('Saving…')
    try {
      const columns: Record<string, string> = {}
      for (const [k, v] of Object.entries(edits)) columns[k] = v.trim()
      const banners: Record<string, string | null> = {}
      for (const [k, v] of Object.entries(bannerEdits)) banners[k] = v === '' ? null : v
      await api(`/api/v1/commcalc/report-labels${orgQS()}`, {
        method: 'PUT', body: JSON.stringify({ columns, banners }),
      })
      setEdits({}); setBannerEdits({}); setMsg('✅ Saved.')
      reload(); onSaved?.()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setSaving(false) }
  }

  const dirty = Object.keys(edits).length > 0 || Object.keys(bannerEdits).length > 0

  return (
    <div className="card" style={{ padding: 16, marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
        <div style={{ fontWeight: 700, fontSize: 14 }}>🏷️ Report column labels</div>
        <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text3)' }}>✕</button>
      </div>
      <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 10px', maxWidth: 760 }}>
        Column headers on the activation reports (Executive MTD, Activations), per carrier. Your carrier&apos;s
        preset applies automatically{data.carriers.length ? <> (carrier: <b>{activeCarrier}</b>)</> : null}; type a
        label to override it for your organization, or clear the box to go back to the preset / default.
        Display only — no report math or pay changes.
      </p>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', fontSize: 12.5, minWidth: 560 }}>
          <thead>
            <tr style={{ color: 'var(--text3)', textAlign: 'left' }}>
              <th style={{ padding: '4px 10px 4px 0' }}>Column</th>
              <th style={{ padding: '4px 10px' }}>Default</th>
              <th style={{ padding: '4px 10px' }}>Carrier preset</th>
              <th style={{ padding: '4px 10px' }}>Your label</th>
            </tr>
          </thead>
          <tbody>
            {data.editable_columns.map(({ key, default: dflt }) => {
              const pre = preset.columns[key] || ''
              const ovr = key in edits ? edits[key] : (overrides.columns[key] || '')
              return (
                <tr key={key} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '4px 10px 4px 0', fontFamily: 'monospace', color: 'var(--text2)' }}>{key}</td>
                  <td style={{ padding: '4px 10px' }}>{dflt}</td>
                  <td style={{ padding: '4px 10px', color: pre ? 'var(--text)' : 'var(--text3)' }}>{pre || '—'}</td>
                  <td style={{ padding: '4px 10px' }}>
                    <input value={ovr} placeholder={pre || dflt}
                      onChange={(e) => setEdits((s) => ({ ...s, [key]: e.target.value }))}
                      style={{ fontSize: 12.5, padding: '3px 8px', border: '1px solid var(--border)', borderRadius: 6, width: 180 }} />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {data.banner_keys.length > 0 && (
        <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>Report warnings</div>
          {data.banner_keys.map(({ key, title }) => {
            const pre = preset.banners[key] || ''
            const cur = key in bannerEdits ? bannerEdits[key] : (overrides.banners[key] || '')
            return (
              <label key={key} style={{ display: 'flex', gap: 10, alignItems: 'center', fontSize: 12.5, margin: '4px 0' }}>
                <select value={cur} onChange={(e) => setBannerEdits((s) => ({ ...s, [key]: e.target.value }))}
                  style={{ fontSize: 12.5, padding: '3px 6px', border: '1px solid var(--border)', borderRadius: 6 }}>
                  <option value="">Auto (carrier preset{pre ? `: ${pre}` : ''})</option>
                  <option value="on">Always show</option>
                  <option value="off">Never show</option>
                </select>
                <span style={{ color: 'var(--text2)' }}>{title}</span>
              </label>
            )
          })}
        </div>
      )}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 12 }}>
        <button onClick={save} disabled={!dirty || saving} className="btn"
          style={{ fontSize: 12.5, padding: '5px 14px', cursor: dirty ? 'pointer' : 'default' }}>
          {saving ? 'Saving…' : 'Save labels'}
        </button>
        {msg && <span style={{ fontSize: 12.5 }}>{msg}</span>}
      </div>
    </div>
  )
}
