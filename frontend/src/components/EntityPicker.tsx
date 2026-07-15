'use client'
import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { api } from '@/lib/client'
import {
  EntityOption, MenuRow, buildMenu, buildMenuMulti, computeDisplays, selectableRows, resolveRow,
  addSelection, removeSelection, selectedChips,
} from '@/lib/entity-picker-core'

// Re-export the primitives so a page needs a single import:  import EntityPicker, { US_STATES } from '@/components/EntityPicker'
export type { EntityOption } from '@/lib/entity-picker-core'
export { US_STATES, normalizeText, buildMenu, buildMenuMulti } from '@/lib/entity-picker-core'

/**
 * EntityPicker — the shared "pick, don't type" primitive (AGENT_CONTRACT §3b / RULE THREE).
 *
 * A combo-box over EXISTING values: typing filters (case/whitespace-insensitive, contains-match);
 * selection stores/emits the entity's ID (canonical key), never the display string. When two options
 * share a label (two people named "John Smith"), the sublabel (email) is appended to both automatically.
 *
 *   // pick-only (states — no create):
 *   <EntityPicker options={US_STATES} value={state} onChange={setState} placeholder="State…" />
 *
 *   // pick-or-create (a store list that may legitimately gain a new store):
 *   <EntityPicker options={stores} value={storeId} onChange={setStoreId}
 *                 allowCreate onCreate={createStore} placeholder="Store…" />
 *
 *   // convenience fetch (GET -> [{id,label,sublabel}]):
 *   <EntityPicker fetchUrl="/api/v1/commcalc/reps" value={repId} onChange={setRepId} />
 *
 *   // MULTI-select — selected values render as removable chips, dropdown excludes the chosen,
 *   // emits string[] (same id/label/sublabel + allowCreate contract):
 *   <EntityPicker multi options={reps} value={repIds} onChange={setRepIds} placeholder="Reps…" />
 *
 * The pure logic (filter / disambiguation / create-affordance / emit / multi) lives in
 * `@/lib/entity-picker-core` and is unit-proven by `scratchpad/prove_entity_picker.mjs`.
 */
interface EntityPickerBaseProps {
  /** Existing values. Each page fetches its own list — no new backend needed. */
  options?: EntityOption[]
  /** Optional convenience: GET this URL for `[{id,label,sublabel}]` (uses lib/client `api`). */
  fetchUrl?: string
  /** Called when the "➕ Create new" affordance is chosen (only reachable when `allowCreate`). */
  onCreate?: (value: string) => void
  /** Show the "➕ Create new: '<value>'" affordance for an unmatched value. Default false. */
  allowCreate?: boolean
  placeholder?: string
  disabled?: boolean
  /** Allow clearing back to nothing (shows an ✕ / "clear all" in multi). Default true. */
  clearable?: boolean
  /** Customize the create-row text. Default: `Create new: "<value>"`. */
  createLabel?: (value: string) => string
  width?: number | string
  /** Accessible name for the input (falls back to placeholder). */
  ariaLabel?: string
  autoFocus?: boolean
  /** Optional id for the underlying input (label association). */
  id?: string
}
/** Single-select (default). Emits the selected entity's ID (canonical key) — or null when cleared. */
export interface EntityPickerSingleProps extends EntityPickerBaseProps {
  multi?: false
  /** Controlled selected ID (or null when nothing is chosen). */
  value?: string | null
  /** Emits the selected entity's ID (canonical key) — or null when cleared. NEVER the label. */
  onChange: (id: string | null) => void
}
/** Multi-select. Chips for each selection; the dropdown excludes already-chosen; emits string[]. */
export interface EntityPickerMultiProps extends EntityPickerBaseProps {
  multi: true
  /** Controlled selected IDs (canonical keys). */
  value?: string[] | null
  /** Emits the full selected-ID array (canonical keys). NEVER the labels. */
  onChange: (ids: string[]) => void
}
export type EntityPickerProps = EntityPickerSingleProps | EntityPickerMultiProps

export default function EntityPicker(props: EntityPickerProps) {
  const {
    options: optionsProp, fetchUrl, onCreate, allowCreate = false,
    placeholder = 'Select…', disabled = false, clearable = true, createLabel,
    width = 240, ariaLabel, autoFocus = false, id,
  } = props
  const multi = props.multi === true
  // Normalized controlled state — the union's value/onChange are narrowed by `multi` and cast at the
  // (guarded) call sites so single-select stays byte-identical while multi gets the array contract.
  const selectedIds: string[] = multi && Array.isArray(props.value) ? props.value : []
  const value: string | null = !multi ? ((props.value as string | null | undefined) ?? null) : null
  const emitSingle = (v: string | null) => { (props.onChange as (id: string | null) => void)(v) }
  const emitMulti = (ids: string[]) => { (props.onChange as (ids: string[]) => void)(ids) }

  const [fetched, setFetched] = useState<EntityOption[] | null>(null)
  const options = optionsProp ?? fetched ?? []

  // fetchUrl convenience — page can still pass options directly (options win).
  useEffect(() => {
    if (optionsProp || !fetchUrl) return
    let alive = true
    api(fetchUrl)
      .then((d: any) => { if (alive) setFetched(Array.isArray(d) ? d.map(normalizeOption) : []) })
      .catch(() => { if (alive) setFetched([]) })
    return () => { alive = false }
  }, [fetchUrl, optionsProp])

  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [hi, setHi] = useState(0)
  const rootRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const listId = useMemo(() => `ep-list-${Math.random().toString(36).slice(2, 8)}`, [])

  const displays = useMemo(() => computeDisplays(options), [options])
  const selected = useMemo(() => options.find(o => o.id === value) || null, [options, value])
  const selectedDisplay = selected ? displays[selected.id] : ''
  // multi: the removable chips (disambiguated display; off-roster ids kept as raw ids so they stay visible)
  const chips = useMemo(() => (multi ? selectedChips(options, selectedIds) : []), [multi, options, selectedIds])

  const rows: MenuRow[] = useMemo(
    () => (multi ? buildMenuMulti(options, query, allowCreate, selectedIds) : buildMenu(options, query, allowCreate)),
    [options, query, allowCreate, multi, selectedIds],
  )
  const picks = useMemo(() => selectableRows(rows), [rows])

  // click-outside closes and restores the input to the selected display
  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) close()
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  useEffect(() => { if (autoFocus) inputRef.current?.focus() }, [autoFocus])
  // keep highlight in range as the filtered list shrinks/grows
  useEffect(() => { setHi(h => (picks.length === 0 ? 0 : Math.min(h, picks.length - 1))) }, [picks.length])

  const openMenu = useCallback(() => { if (!disabled) { setQuery(''); setHi(0); setOpen(true) } }, [disabled])
  function close() { setOpen(false); setQuery('') }

  function commit(row: MenuRow | undefined) {
    if (!row) return
    const r = resolveRow(row)
    if (!r) return
    if (r.create) { onCreate?.(r.value) }
    else if (multi) { emitMulti(addSelection(selectedIds, r.id)) }
    else { emitSingle(r.id) }
    if (multi) {
      // stay open so several can be added in a row; reset the filter + keep focus
      setQuery(''); setHi(0); inputRef.current?.focus()
    } else {
      close(); inputRef.current?.blur()
    }
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (disabled) return
    // multi: Backspace on an empty box removes the last chip (standard tag-input affordance)
    if (multi && e.key === 'Backspace' && query === '' && selectedIds.length) {
      e.preventDefault(); emitMulti(selectedIds.slice(0, -1)); return
    }
    if (!open && (e.key === 'ArrowDown' || e.key === 'Enter')) { openMenu(); return }
    if (e.key === 'ArrowDown') { e.preventDefault(); setHi(h => Math.min(h + 1, picks.length - 1)); return }
    if (e.key === 'ArrowUp') { e.preventDefault(); setHi(h => Math.max(h - 1, 0)); return }
    if (e.key === 'Enter') { e.preventDefault(); commit(picks[hi]); return }
    if (e.key === 'Escape') { e.preventDefault(); close(); inputRef.current?.blur(); return }
  }

  const inputValue = open ? query : (multi ? '' : selectedDisplay)

  const box: React.CSSProperties = {
    padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13,
    background: disabled ? 'var(--surface2)' : 'var(--surface)', color: 'var(--text1)',
    width: '100%', outline: 'none',
  }

  // shared input props (identical role/keyboard/aria in both modes)
  const inputCommon = {
    id, ref: inputRef, role: 'combobox' as const, 'aria-expanded': open, 'aria-controls': listId,
    'aria-autocomplete': 'list' as const, 'aria-label': ariaLabel || placeholder, autoComplete: 'off',
    disabled, value: inputValue,
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => { setQuery(e.target.value); setHi(0); if (!open) setOpen(true) },
    onFocus: openMenu, onKeyDown,
  }

  return (
    <div ref={rootRef} style={{ position: 'relative', display: 'inline-block', width }}>
      {!multi ? (
        <div style={{ position: 'relative' }}>
          <input {...inputCommon}
            placeholder={selected && !open ? selectedDisplay : placeholder}
            style={{ ...box, paddingRight: 46 }}
          />
          {clearable && selected && !disabled && (
            <button type="button" aria-label="Clear" tabIndex={-1}
              onMouseDown={e => { e.preventDefault(); emitSingle(null); close() }}
              style={clearBtn}>✕</button>
          )}
          <span aria-hidden style={caret}>▾</span>
        </div>
      ) : (
        <div style={{ position: 'relative' }}>
          <div style={{ ...box, paddingRight: 46, display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center', minHeight: 38, cursor: disabled ? 'default' : 'text' }}
            onMouseDown={e => { if (e.target === e.currentTarget && !disabled) inputRef.current?.focus() }}>
            {chips.map(c => (
              <span key={c.id} style={chip}>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 160 }}>{c.display}</span>
                {!disabled && (
                  <button type="button" aria-label={`Remove ${c.display}`} tabIndex={-1}
                    onMouseDown={e => { e.preventDefault(); emitMulti(removeSelection(selectedIds, c.id)) }}
                    style={chipX}>✕</button>
                )}
              </span>
            ))}
            <input {...inputCommon}
              placeholder={chips.length === 0 ? placeholder : ''}
              style={{ border: 'none', outline: 'none', background: 'transparent', color: 'var(--text1)', fontSize: 13, flex: 1, minWidth: 80, padding: '2px 0' }}
            />
          </div>
          {clearable && chips.length > 0 && !disabled && (
            <button type="button" aria-label="Clear all" tabIndex={-1}
              onMouseDown={e => { e.preventDefault(); emitMulti([]); close() }}
              style={clearBtn}>✕</button>
          )}
          <span aria-hidden style={caret}>▾</span>
        </div>
      )}

      {open && (
        <ul id={listId} role="listbox" aria-multiselectable={multi || undefined} style={menu}>
          {rows.length === 0 && (
            <li role="presentation" style={emptyRow}>No options</li>
          )}
          {rows.map((row, i) => {
            if (row.kind === 'empty') {
              return <li key={`empty-${i}`} role="presentation" style={emptyRow}>{row.message}</li>
            }
            const pickIdx = picks.indexOf(row)
            const active = pickIdx === hi
            if (row.kind === 'create') {
              return (
                <li key="create" role="option" aria-selected={active}
                  onMouseEnter={() => setHi(pickIdx)}
                  onMouseDown={e => { e.preventDefault(); commit(row) }}
                  style={{ ...optRow(active), borderTop: '1px solid var(--border)', color: 'var(--accent)', fontWeight: 600 }}>
                  ➕ {createLabel ? createLabel(row.value) : <>Create new: “{row.value}”</>}
                </li>
              )
            }
            // option | suggest
            const isSel = row.id === value
            return (
              <li key={row.id} role="option" aria-selected={active || isSel}
                onMouseEnter={() => setHi(pickIdx)}
                onMouseDown={e => { e.preventDefault(); commit(row) }}
                style={optRow(active)}>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {row.kind === 'suggest' && <span style={{ color: 'var(--text3)', marginRight: 6 }}>↳</span>}
                  {row.display}
                </span>
                {isSel && <span style={{ marginLeft: 'auto', color: 'var(--accent)' }}>✓</span>}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

function normalizeOption(o: any): EntityOption {
  return { id: String(o.id ?? o.value ?? ''), label: String(o.label ?? o.name ?? o.id ?? ''), sublabel: o.sublabel ?? o.email ?? undefined }
}

const menu: React.CSSProperties = {
  listStyle: 'none', margin: 0, padding: 6, position: 'absolute', zIndex: 2000,
  top: 'calc(100% + 4px)', left: 0, minWidth: '100%', maxHeight: 320, overflowY: 'auto',
  background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8,
  boxShadow: '0 6px 24px rgba(0,0,0,0.18)',
}
const optRow = (active: boolean): React.CSSProperties => ({
  display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', fontSize: 13,
  cursor: 'pointer', borderRadius: 6, background: active ? 'var(--surface2)' : 'transparent',
})
const emptyRow: React.CSSProperties = { padding: '7px 8px', fontSize: 12, color: 'var(--text3)' }
const caret: React.CSSProperties = { position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', fontSize: 10, color: 'var(--text3)', pointerEvents: 'none' }
const clearBtn: React.CSSProperties = {
  position: 'absolute', right: 26, top: '50%', transform: 'translateY(-50%)', border: 'none',
  background: 'transparent', color: 'var(--text3)', cursor: 'pointer', fontSize: 12, padding: 0, lineHeight: 1,
}
// multi-select chips
const chip: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 4px 2px 8px', borderRadius: 6,
  background: 'var(--surface2)', border: '1px solid var(--border)', fontSize: 12, maxWidth: '100%',
}
const chipX: React.CSSProperties = {
  border: 'none', background: 'transparent', color: 'var(--text3)', cursor: 'pointer', fontSize: 11,
  padding: '0 2px', lineHeight: 1,
}
