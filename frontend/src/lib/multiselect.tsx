'use client'
import { useState, useRef, useEffect } from 'react'

export type MSOption = { value: string; label?: string }

/**
 * Reusable multi-select dropdown (checkbox list + All/None + optional search).
 * Styled to match the app's <select> filters. `value` is the selected values array.
 *
 *   <MultiSelect allLabel="All markets" value={selMarkets} options={markets} onChange={setSelMarkets} />
 */
export function MultiSelect({
  options, value, onChange, allLabel = 'All', width = 170, searchable,
}: {
  options: (MSOption | string)[]
  value: string[]
  onChange: (v: string[]) => void
  allLabel?: string
  width?: number
  searchable?: boolean
}) {
  const opts: MSOption[] = options.map(o => (typeof o === 'string' ? { value: o } : o))
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const sel = new Set(value)
  const labelFor = (v: string) => opts.find(o => o.value === v)?.label || v
  const filtered = q ? opts.filter(o => (o.label || o.value).toLowerCase().includes(q.toLowerCase())) : opts
  const btnText = value.length === 0 ? allLabel : value.length === 1 ? labelFor(value[0]) : `${value.length} selected`
  const showSearch = searchable ?? opts.length > 10

  function toggle(v: string) {
    const next = new Set(value)
    next.has(v) ? next.delete(v) : next.add(v)
    onChange([...next])
  }

  return (
    <div ref={ref} style={{ position: 'relative', display: 'inline-block' }}>
      <button type="button" onClick={() => setOpen(o => !o)}
        style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13,
                 background: 'var(--surface)', minWidth: width, textAlign: 'left', cursor: 'pointer',
                 display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                       color: value.length ? 'var(--text1)' : 'var(--text3)' }}>{btnText}</span>
        <span style={{ fontSize: 10, color: 'var(--text3)' }}>▾</span>
      </button>
      {open && (
        <div style={{ position: 'absolute', zIndex: 60, top: 'calc(100% + 4px)', left: 0,
                      minWidth: Math.max(width, 210), maxHeight: 340, overflowY: 'auto',
                      background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8,
                      boxShadow: '0 6px 24px rgba(0,0,0,0.18)', padding: 6 }}>
          <div style={{ display: 'flex', gap: 6, padding: '2px 4px 6px', borderBottom: '1px solid var(--border)', marginBottom: 4 }}>
            <button type="button" className="btn" style={{ fontSize: 11, padding: '3px 8px' }}
              onClick={() => onChange(opts.map(o => o.value))}>All</button>
            <button type="button" className="btn" style={{ fontSize: 11, padding: '3px 8px' }}
              onClick={() => onChange([])}>None</button>
            {value.length > 0 && <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text3)', alignSelf: 'center' }}>{value.length} selected</span>}
          </div>
          {showSearch && (
            <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search…" autoFocus
              style={{ width: '100%', padding: '5px 8px', marginBottom: 4, fontSize: 12,
                       border: '1px solid var(--border)', borderRadius: 6, background: 'var(--surface2)' }} />
          )}
          {filtered.length === 0 ? (
            <div style={{ padding: 8, fontSize: 12, color: 'var(--text3)' }}>No matches</div>
          ) : filtered.map(o => (
            <label key={o.value} onMouseDown={e => e.preventDefault()}
              style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 8px', fontSize: 13, cursor: 'pointer', borderRadius: 6 }}>
              <input type="checkbox" checked={sel.has(o.value)} onChange={() => toggle(o.value)} />
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{o.label || o.value}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}
