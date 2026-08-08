'use client'
// POS module — Phase 1: POS Configuration engine UI (ported from the standalone
// pos-system app's app/settings/PosConfigSection.tsx). Scope dimension changed from
// location_id uuid to store_code TEXT (null = org default); data access rewired from
// direct Supabase to the FastAPI /pos router (GET/PUT/DELETE /api/v1/pos/settings).
import { useState } from 'react'
import { api } from '@/lib/client'
import { POS_SETTING_SECTIONS, POS_SETTING_DEFS, resolvePosConfig } from '@/lib/pos-config'
import type { PosSettingDef, PosSettingRow } from '@/lib/pos-config'

export interface PosStore { store_code: string; address?: string | null; market?: string | null }

export function storeLabel(s: PosStore): string {
  return s.address ? `${s.store_code} — ${s.address}` : s.store_code
}

/** Surface the router's pos_settings 403 as a human sentence; otherwise prefix the raw message. */
export function friendlyError(err: unknown, fallback: string): string {
  const msg = err instanceof Error ? err.message : String(err)
  if (/not allow|forbidden|403/i.test(msg)) return 'Your role does not allow editing POS settings.'
  return `${fallback}: ${msg}`
}

type Scalar = boolean | number | string

/** 'set' holds the edited value (raw input string for number/currency); 'revert' marks a store override for deletion. */
type DraftEntry = { kind: 'set'; value: Scalar } | { kind: 'revert' }

const input: React.CSSProperties = { padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)', outline: 'none' }
const label: React.CSSProperties = { fontSize: 12, color: 'var(--text2)', marginBottom: 3, display: 'block' }
const badge: React.CSSProperties = { fontSize: 10, borderRadius: 10, padding: '1px 8px', fontWeight: 700, whiteSpace: 'nowrap' }

interface Props {
  stores: PosStore[]
  rows: PosSettingRow[]
  loading: boolean
  loadError: string
  /** Re-fetches the settings rows (owned by the page so the Sales Tax rule stays coherent). */
  reload: () => Promise<void>
}

export default function PosConfigSection({ stores, rows, loading, loadError, reload }: Props) {
  const [scope, setScope] = useState('') // '' = org defaults, otherwise a store_code
  const [draft, setDraft] = useState<Record<string, DraftEntry>>({})
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [savedAt, setSavedAt] = useState('')

  const resolved = resolvePosConfig(rows, scope || null)
  const orgResolved = resolvePosConfig(rows, null)

  const draftCount = Object.keys(draft).length

  function changeScope(next: string) {
    if (draftCount > 0 && !confirm('Discard unsaved POS configuration changes?')) return
    setDraft({})
    setFieldErrors({})
    setSaveError('')
    setScope(next)
  }

  /** Value the control should show right now (draft-aware; a revert shows the inherited value). */
  function controlValue(def: PosSettingDef): Scalar {
    const d = draft[def.key]
    if (d) return d.kind === 'set' ? d.value : orgResolved.values[def.key]
    return resolved.values[def.key]
  }

  function setDraftValue(def: PosSettingDef, raw: Scalar) {
    const base = resolved.values[def.key]
    let same: boolean
    if (def.type === 'number' || def.type === 'currency') {
      same = String(raw).trim() !== '' && Number(raw) === base
    } else {
      same = raw === base
    }
    setDraft(prev => {
      const next = { ...prev }
      if (same) delete next[def.key]
      else next[def.key] = { kind: 'set', value: raw }
      return next
    })
    setFieldErrors(prev => {
      if (!prev[def.key]) return prev
      const next = { ...prev }
      delete next[def.key]
      return next
    })
  }

  function revertToInherited(def: PosSettingDef) {
    const savedRow = rows.find(r => r.key === def.key && r.store_code === scope)
    setDraft(prev => {
      const next = { ...prev }
      if (savedRow) next[def.key] = { kind: 'revert' }
      else delete next[def.key]
      return next
    })
    setFieldErrors(prev => {
      if (!prev[def.key]) return prev
      const next = { ...prev }
      delete next[def.key]
      return next
    })
  }

  function cancelDraft() {
    setDraft({})
    setFieldErrors({})
    setSaveError('')
  }

  async function applyDraft() {
    const entries = Object.entries(draft)
    if (entries.length === 0) return
    // Validate numeric drafts before touching the API.
    const problems: Record<string, string> = {}
    for (const [key, entry] of entries) {
      const def = POS_SETTING_DEFS[key]
      if (!def || entry.kind !== 'set') continue
      if (def.type === 'number' || def.type === 'currency') {
        const n = Number(entry.value)
        if (String(entry.value).trim() === '' || !Number.isFinite(n) || n < 0) {
          problems[key] = 'Enter a number of 0 or more.'
        }
      }
    }
    if (Object.keys(problems).length > 0) {
      setFieldErrors(problems)
      setSaveError('Fix the highlighted values before applying.')
      return
    }
    setSaving(true)
    setSaveError('')
    setSavedAt('')
    try {
      for (const [key, entry] of entries) {
        const def = POS_SETTING_DEFS[key]
        if (!def) continue
        if (entry.kind === 'revert') {
          if (!scope) continue
          const existing = rows.find(r => r.key === key && r.store_code === scope)
          if (!existing) continue
          await api(`/api/v1/pos/settings?key=${encodeURIComponent(key)}&store_code=${encodeURIComponent(scope)}`, { method: 'DELETE' })
        } else {
          let value: Scalar
          if (def.type === 'boolean') value = entry.value === true
          else if (def.type === 'select' || def.type === 'text' || def.type === 'textarea') value = String(entry.value)
          else if (def.type === 'currency') value = Math.round(Number(entry.value) * 100) / 100
          else value = Math.round(Number(entry.value))
          await api('/api/v1/pos/settings', {
            method: 'PUT',
            body: JSON.stringify({ key, value, store_code: scope || null }),
          })
        }
      }
      await reload()
      setDraft({})
      setFieldErrors({})
      setSavedAt(new Date().toLocaleTimeString())
    } catch (err) {
      setSaveError(friendlyError(err, 'Could not save POS configuration'))
    } finally {
      setSaving(false)
    }
  }

  function badgeFor(def: PosSettingDef): { text: string; background: string; color: string } {
    const d = draft[def.key]
    if (!scope) {
      return resolved.sources[def.key] === 'org'
        ? { text: 'ORG DEFAULT', background: '#2980b9', color: 'white' }
        : { text: 'SYSTEM DEFAULT', background: 'var(--surface2)', color: 'var(--text2)' }
    }
    const overridden = d ? d.kind === 'set' : resolved.sources[def.key] === 'override'
    if (overridden) return { text: 'OVERRIDDEN HERE', background: '#f39c12', color: 'white' }
    const src = d?.kind === 'revert' ? orgResolved.sources[def.key] : resolved.sources[def.key]
    return src === 'org'
      ? { text: 'INHERITED', background: '#2980b9', color: 'white' }
      : { text: 'SYSTEM DEFAULT', background: 'var(--surface2)', color: 'var(--text2)' }
  }

  function renderControl(def: PosSettingDef) {
    const v = controlValue(def)
    const reverting = draft[def.key]?.kind === 'revert'
    const dim = reverting ? 0.55 : 1
    if (def.type === 'boolean') {
      return (
        <input type="checkbox" checked={v === true} disabled={saving}
          onChange={e => setDraftValue(def, e.target.checked)}
          style={{ width: 16, height: 16, cursor: 'pointer', opacity: dim }} />
      )
    }
    if (def.type === 'select') {
      return (
        <select value={String(v)} disabled={saving}
          onChange={e => setDraftValue(def, e.target.value)}
          style={{ ...input, width: 210, opacity: dim }}>
          {(def.options || []).map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      )
    }
    if (def.type === 'text') {
      return (
        <input type="text" value={String(v)} disabled={saving}
          onChange={e => setDraftValue(def, e.target.value)}
          style={{ ...input, width: 240, opacity: dim }} />
      )
    }
    if (def.type === 'textarea') {
      return (
        <textarea value={String(v)} disabled={saving} rows={3}
          onChange={e => setDraftValue(def, e.target.value)}
          style={{ ...input, width: 240, opacity: dim, resize: 'vertical', fontFamily: 'inherit', boxSizing: 'border-box' }} />
      )
    }
    if (def.type === 'currency') {
      return (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, opacity: dim }}>
          <span style={{ fontSize: 13, color: 'var(--text2)' }}>$</span>
          <input type="number" min={0} step={0.01} value={String(v)} disabled={saving}
            onChange={e => setDraftValue(def, e.target.value)}
            style={{ ...input, width: 90, borderColor: fieldErrors[def.key] ? '#dc2626' : 'var(--border)' }} />
        </span>
      )
    }
    return (
      <input type="number" min={0} step={1} value={String(v)} disabled={saving}
        onChange={e => setDraftValue(def, e.target.value)}
        style={{ ...input, width: 90, opacity: dim, borderColor: fieldErrors[def.key] ? '#dc2626' : 'var(--border)' }} />
    )
  }

  // No overflow:hidden here — the Apply/Cancel bar needs position:sticky to reach the viewport.
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, marginBottom: 16 }}>
      <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700 }}>🖥️ POS Configuration</div>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>Register rules, cash controls, and payment settings — org-wide defaults with per-store overrides</div>
        </div>
        <div style={{ fontSize: 12 }}>
          {saving && <span style={{ color: '#f39c12' }}>saving…</span>}
          {!saving && savedAt && <span style={{ color: '#16a34a' }}>saved {savedAt}</span>}
        </div>
      </div>

      {/* Scope selector */}
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'flex-end', gap: 14, flexWrap: 'wrap' }}>
        <div>
          <label style={label}>Configuring</label>
          <select value={scope} onChange={e => changeScope(e.target.value)} style={{ ...input, width: 300 }}>
            <option value="">🏢 Organization defaults (all stores)</option>
            {stores.map(s => <option key={s.store_code} value={s.store_code}>{storeLabel(s)}</option>)}
          </select>
        </div>
        <div style={{ fontSize: 12, color: 'var(--text2)', paddingBottom: 8 }}>
          {scope
            ? 'Store values override the org defaults; anything not overridden here is inherited.'
            : 'These defaults apply to every store unless a store overrides them.'}
        </div>
      </div>

      {loadError && (
        <div style={{ margin: '12px 16px', border: '1px solid #dc2626', color: '#dc2626', borderRadius: 8, padding: '10px 14px', fontSize: 12 }}>{loadError}</div>
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><div className="spinner" /></div>
      ) : (
        POS_SETTING_SECTIONS.map(section => (
          <div key={section.id}>
            <div style={{ background: 'var(--surface2)', padding: '10px 16px', borderBottom: '1px solid var(--border)' }}>
              <div style={{ fontSize: 13, fontWeight: 700 }}>{section.icon} {section.title}</div>
              <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>{section.subtitle}</div>
            </div>
            {section.settings.map(def => {
              const modified = def.key in draft
              const b = badgeFor(def)
              const canRevert = !!scope && !saving
                && (draft[def.key] ? draft[def.key].kind === 'set' : resolved.sources[def.key] === 'override')
              return (
                <div key={def.key} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16, padding: '10px 16px', borderBottom: '1px solid var(--border)', background: modified ? 'var(--surface2)' : 'transparent' }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <span style={{ fontSize: 13, fontWeight: 600 }}>{def.label}</span>
                      <span style={{ ...badge, background: b.background, color: b.color }}>{b.text}</span>
                      {modified && <span style={{ fontSize: 11, color: '#f39c12' }}>● modified</span>}
                      {canRevert && (
                        <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }} onClick={() => revertToInherited(def)}>↩ Revert to inherited</button>
                      )}
                    </div>
                    {def.hint && <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>{def.hint}</div>}
                    {def.notYetEnforced && (
                      <div style={{ fontSize: 11, color: '#f39c12', marginTop: 2 }}>⏳ {def.notYetEnforced}</div>
                    )}
                    {fieldErrors[def.key] && (
                      <div style={{ fontSize: 11, color: '#dc2626', marginTop: 2 }}>{fieldErrors[def.key]}</div>
                    )}
                  </div>
                  <div style={{ flexShrink: 0 }}>{renderControl(def)}</div>
                </div>
              )
            })}
          </div>
        ))
      )}

      {saveError && (
        <div style={{ margin: '12px 16px', border: '1px solid #dc2626', color: '#dc2626', borderRadius: 8, padding: '10px 14px', fontSize: 12 }}>{saveError}</div>
      )}

      {/* Apply / Cancel bar */}
      {draftCount > 0 && (
        <div style={{ position: 'sticky', bottom: 0, background: 'var(--surface)', borderTop: '1px solid var(--border)', borderRadius: '0 0 12px 12px', padding: '10px 16px', display: 'flex', alignItems: 'center', gap: 10, zIndex: 5 }}>
          <span style={{ fontSize: 12, color: '#f39c12' }}>● {draftCount} unsaved change{draftCount === 1 ? '' : 's'}</span>
          <div style={{ flex: 1 }} />
          <button className="btn btn-secondary" onClick={cancelDraft} disabled={saving}>Cancel</button>
          <button className="btn btn-primary" onClick={applyDraft} disabled={saving}>{saving ? 'Applying…' : 'Apply'}</button>
        </div>
      )}
    </div>
  )
}
