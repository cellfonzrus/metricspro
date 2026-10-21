'use client'
// Marketing form controls — ONE home, and they live at MODULE SCOPE on purpose.
//
// OWNER BUG 2026-10-21: "While create an event in the marketing menu, the user cannot type freely
// only letter at a time is entered."
//
// THE CLASS, not the field. `Field` and `Picker` used to be declared INSIDE the page component's
// body. A function declared there is a NEW function identity on every render, and React compares
// element types by REFERENCE: a different type means it UNMOUNTS the old subtree and MOUNTS a fresh
// one instead of updating it. So every keystroke destroyed the very <input> being typed into, the
// browser dropped focus with it, and the next character went nowhere — exactly "only a letter at a
// time". The value was never lost (state was fine); the DOM node was.
//
// Declared out here the element type is stable for the life of the module, so React updates the
// existing input and focus survives. Both marketing event pages dereference these rather than
// keeping a copy each — the page that holds the form is not the place that decides what a form
// control is. Lock: backend/harness_inner_component_inputs.py fails the build if a component that
// renders a TYPED control is ever declared inside another component again.
import type { CSSProperties, ReactNode } from 'react'
import Link from 'next/link'
import { input, label, btn, type MarketingOption } from '@/lib/marketing'

/** A labelled text/number/datetime input. `value` + `onChange` are the caller's state, as usual. */
export function Field({ name, value, onChange, type = 'text', ph, style }: {
  name: string
  value: string
  onChange: (v: string) => void
  type?: string
  ph?: string
  style?: CSSProperties
}) {
  return (
    <div>
      <span style={label}>{name}</span>
      <input style={{ ...input, ...style }} type={type} value={value} placeholder={ph}
             onChange={e => onChange(e.target.value)} />
    </div>
  )
}

/** A labelled textarea. Same contract as Field. */
export function Area({ name, value, onChange, minHeight = 60 }: {
  name: string; value: string; onChange: (v: string) => void; minHeight?: number
}) {
  return (
    <div>
      <span style={label}>{name}</span>
      <textarea style={{ ...input, minHeight }} value={value} onChange={e => onChange(e.target.value)} />
    </div>
  )
}

/** An option picker. `addHref` renders the owner's "+" beside it (2026-09 marketing directive:
 *  "options pre added with plus sign to add more as per user discretion"); omit it for a bare
 *  select. `name` omitted = no label, which is how the event detail page uses it inline. */
export function Picker({ options, value, onChange, name, addHref, width, children }: {
  options: MarketingOption[]
  value: string
  onChange: (v: string) => void
  name?: string
  addHref?: string
  width?: number | string
  children?: ReactNode
}) {
  const select = (
    <select style={{ ...input, ...(width ? { width } : {}) }} value={value || ''}
            onChange={e => onChange(e.target.value)}>
      <option value="">—</option>
      {(options || []).map(o => <option key={o.key} value={o.key}>{o.label}</option>)}
      {children}
    </select>
  )
  if (!name && !addHref) return select
  return (
    <div>
      {name && <span style={label}>{name}</span>}
      <div style={{ display: 'flex', gap: 6 }}>
        {select}
        {addHref && (
          <Link href={addHref} title={name ? `Add a ${name.toLowerCase()}` : 'Add an option'}
                style={{ ...btn, padding: '7px 11px', textDecoration: 'none', lineHeight: 1.2 }}>＋</Link>
        )}
      </div>
    </div>
  )
}
