'use client'
// Shared pieces for the /storeops/admin SPLIT (Phase W2, owner directive 2026-09-01): Store Setup
// (/storeops/setup/stores) + Employee Setup (/storeops/setup/employees) are mechanical extractions
// of the two tab branches of storeops/admin/page.tsx. Everything both (or the old combined page's
// future maintenance) need lives here ONCE — styles, dirty-tracking, phone normalization, the
// RULE-THREE MarketField picker and the store time-zone options — so the three surfaces cannot drift.
import { useState } from 'react'

export const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
export const cell: React.CSSProperties = { padding: '6px 8px', borderBottom: '1px solid var(--border)' }

// 2026-07-25 owner directive (inherited from the combined admin page): the Active toggle auto-saves
// on change AND a Save button still exists per row / bulk. `orig*` snapshots drive the "N unsaved
// changes" bulk-save count via isDirty().
export const EMP_EDIT_FIELDS = ['name', 'employee_id', 'home_store', 'email', 'phone', 'is_active']
export const STORE_EDIT_FIELDS = ['store_code', 'address', 'market', 'monthly_target', 'is_active', 'phone', 'timezone']

// Per-store time zone (migration 851). Empty = inherit the company default set in Pay-period settings.
export const STORE_TZ_OPTS: { v: string; label: string }[] = [
  { v: '', label: 'Company default' },
  { v: 'America/New_York', label: 'Eastern (ET)' },
  { v: 'America/Chicago', label: 'Central (CT)' },
  { v: 'America/Denver', label: 'Mountain (MT)' },
  { v: 'America/Phoenix', label: 'Arizona (MST)' },
  { v: 'America/Los_Angeles', label: 'Pacific (PT)' },
  { v: 'America/Anchorage', label: 'Alaska (AKT)' },
  { v: 'Pacific/Honolulu', label: 'Hawaii (HST)' },
]

export function isDirty(row: any, orig: any, fields: string[]) {
  if (!orig) return false
  return fields.some(f => String(row[f] ?? '') !== String(orig[f] ?? ''))
}

export const PHONE_EG = 'Enter a 10-digit number or include country code — e.g. 2125550123 or +1 212 555 0123'
export function cleanPhone(raw: any): string | null {
  const s = String(raw ?? '').trim()
  if (!s) return ''                          // empty allowed
  const hasPlus = s.startsWith('+')
  const d = s.replace(/\D/g, '')
  if (hasPlus && d.length >= 11 && d.length <= 15) return '+' + d
  if (d.length === 10) return d
  if (d.length === 11 && d.startsWith('1')) return '+' + d
  return null                                // invalid → caller prompts with PHONE_EG
}

// RULE THREE (pick-don't-type, 2026-07-28 owner directive): market is a dropdown over the org's
// existing markets (GET /storeops/markets — sourced from BOTH storeops.stores.market and
// commcalc.store_mapping.market so the two vocabularies can't diverge silently), with an explicit
// "+ New market" affordance for a genuinely new one. The server normalizes on save.
const NEW_MARKET_SENTINEL = '__new_market__'
export function MarketField({ value, options, onChange, width = 110 }:
  { value: string; options: string[]; onChange: (v: string) => void; width?: number }) {
  const v = String(value || '').trim()
  const matched = options.find(o => o.toLowerCase() === v.toLowerCase())
  const [adding, setAdding] = useState(!!v && !matched)
  if (adding) {
    return (
      <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
        <input style={{ ...sel, width }} placeholder="New market name" value={value || ''}
          onChange={e => onChange(e.target.value)} autoFocus />
        {options.length > 0 &&
          <button type="button" title="Choose an existing market instead" onClick={() => setAdding(false)}
            style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 13, color: 'var(--text3)', padding: 0 }}>▾</button>}
      </div>
    )
  }
  return (
    <select style={{ ...sel, width }} value={matched || ''}
      onChange={e => {
        if (e.target.value === NEW_MARKET_SENTINEL) { setAdding(true); onChange('') }
        else onChange(e.target.value)
      }}>
      <option value="">— Unassigned —</option>
      {options.map(o => <option key={o} value={o}>{o}</option>)}
      <option value={NEW_MARKET_SENTINEL}>➕ New market…</option>
    </select>
  )
}
