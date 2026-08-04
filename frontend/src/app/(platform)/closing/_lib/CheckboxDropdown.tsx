'use client'
import { useEffect, useMemo, useRef, useState } from 'react'

/**
 * CheckboxDropdown — a combo-box multi-select with REAL checkboxes in the open list (OWNER DIRECTIVE
 * 2026-08-04, in-chat: "the filter if has a check box is better than typing a store" / "the store picker
 * needs to have check box under the drop down to pick multiple stores"). Pick-don't-type (§3b): typing
 * in the closed-state box FILTERS the existing option list (contains-match, case/whitespace-insensitive),
 * it never free-types a value into the selection. Closed state shows a short summary ("3 selected") plus
 * the first couple of labels, not a giant chip wall, so several of these can sit in one filter row.
 *
 * Deliberately a fresh, self-contained control (not a fork of the shared `EntityPicker`/`StandardFilterBar`
 * — those live outside this module's ownership, `frontend/src/components/**`, used by every other module;
 * per AGENT_CONTRACT the retail-ops dispatch explicitly says build this new shape under `closing/_lib`
 * rather than touch the cross-module primitive). Same option shape as `EntityOption` so it drops into any
 * existing `{id,label,sublabel?}` list with no adapter.
 */
export type CheckboxOption = { id: string; label: string; sublabel?: string }

export function CheckboxDropdown({
  options, value, onChange, placeholder = 'Select…', width = 190, ariaLabel, disabled = false,
}: {
  options: CheckboxOption[]
  value: string[]
  onChange: (ids: string[]) => void
  placeholder?: string
  width?: number | string
  ariaLabel?: string
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const rootRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const listId = useMemo(() => `cbdd-${Math.random().toString(36).slice(2, 8)}`, [])

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) { setOpen(false); setQuery('') }
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])
  useEffect(() => { if (open) searchRef.current?.focus() }, [open])

  const byId = useMemo(() => { const m: Record<string, CheckboxOption> = {}; options.forEach(o => { m[o.id] = o }); return m }, [options])
  const q = query.trim().toLowerCase()
  const filtered = useMemo(() => (
    !q ? options : options.filter(o => o.label.toLowerCase().includes(q) || (o.sublabel || '').toLowerCase().includes(q))
  ), [options, q])
  const selectedSet = useMemo(() => new Set(value), [value])

  function toggle(id: string) {
    onChange(selectedSet.has(id) ? value.filter(v => v !== id) : [...value, id])
  }

  const summary = value.length === 0 ? placeholder
    : value.length <= 2 ? value.map(id => byId[id]?.label || id).join(', ')
    : `${value.length} selected`

  const box: React.CSSProperties = {
    padding: '7px 30px 7px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13,
    background: disabled ? 'var(--surface2)' : 'var(--surface)', color: value.length ? 'var(--text1)' : 'var(--text3)',
    width: '100%', cursor: disabled ? 'default' : 'pointer', userSelect: 'none',
    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', position: 'relative',
  }

  return (
    <div ref={rootRef} style={{ position: 'relative', display: 'inline-block', width }}>
      <div role="button" tabIndex={disabled ? -1 : 0} aria-haspopup="listbox" aria-expanded={open}
        aria-label={ariaLabel || placeholder}
        style={box}
        onClick={() => !disabled && setOpen(o => !o)}
        onKeyDown={e => { if (!disabled && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); setOpen(o => !o) } if (e.key === 'Escape') setOpen(false) }}>
        {summary}
        {!disabled && value.length > 0 && (
          <button type="button" aria-label="Clear" tabIndex={-1}
            onClick={e => { e.stopPropagation(); onChange([]) }}
            style={{ position: 'absolute', right: 24, top: '50%', transform: 'translateY(-50%)', border: 'none', background: 'transparent', color: 'var(--text3)', cursor: 'pointer', fontSize: 12, padding: 0, lineHeight: 1 }}>✕</button>
        )}
        <span aria-hidden style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', fontSize: 10, color: 'var(--text3)', pointerEvents: 'none' }}>▾</span>
      </div>

      {open && !disabled && (
        <div style={{
          position: 'absolute', zIndex: 2000, top: 'calc(100% + 4px)', left: 0, minWidth: '100%', width: 'max-content', maxWidth: 320,
          background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, boxShadow: '0 6px 24px rgba(0,0,0,0.18)', padding: 6,
        }}>
          <input ref={searchRef} value={query} onChange={e => setQuery(e.target.value)}
            placeholder="Filter…" aria-label={`Filter ${ariaLabel || placeholder}`}
            style={{ width: '100%', padding: '6px 8px', marginBottom: 4, borderRadius: 6, border: '1px solid var(--border)', fontSize: 12, background: 'var(--surface)', color: 'var(--text1)' }}
            onKeyDown={e => { if (e.key === 'Escape') { setOpen(false); setQuery('') } }} />
          <ul id={listId} role="listbox" aria-multiselectable style={{ listStyle: 'none', margin: 0, padding: 0, maxHeight: 260, overflowY: 'auto' }}>
            {filtered.length === 0 && <li style={{ padding: '7px 8px', fontSize: 12, color: 'var(--text3)' }}>No options</li>}
            {filtered.map(o => {
              const checked = selectedSet.has(o.id)
              return (
                <li key={o.id} role="option" aria-selected={checked}
                  onClick={() => toggle(o.id)}
                  style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', fontSize: 13, cursor: 'pointer', borderRadius: 6 }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--surface2)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                  <input type="checkbox" checked={checked} readOnly tabIndex={-1} style={{ pointerEvents: 'none' }} />
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {o.label}{o.sublabel ? <span style={{ color: 'var(--text3)' }}> · {o.sublabel}</span> : null}
                  </span>
                </li>
              )
            })}
          </ul>
          {value.length > 0 && (
            <div style={{ borderTop: '1px solid var(--border)', marginTop: 4, paddingTop: 4, textAlign: 'right' }}>
              <button type="button" className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }}
                onClick={() => onChange([])}>Clear ({value.length})</button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default CheckboxDropdown
