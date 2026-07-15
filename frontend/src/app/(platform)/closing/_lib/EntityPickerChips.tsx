'use client'
import EntityPicker, { EntityOption } from '@/components/EntityPicker'

/**
 * Multi-select composed from the single-select EntityPicker: pick one at a time, removable chips.
 * Per AGENT_CONTRACT §3b (RULE THREE, "pick, don't type") — bridges until platform-core's native
 * `multi` EntityPicker prop ships (BACKLOG platform-core-12); swap the internals for that prop later,
 * this component's own props (`options`/`value: string[]`/`onChange`) don't need to change.
 *
 *   <EntityPickerChips options={storeOptions} value={fStores} onChange={setFStores} placeholder="Add a store…" />
 */
export function EntityPickerChips({
  options, value, onChange, placeholder = 'Add…', width = 220,
}: {
  options: EntityOption[]
  value: string[]
  onChange: (ids: string[]) => void
  placeholder?: string
  width?: number | string
}) {
  const byId: Record<string, EntityOption> = {}
  options.forEach(o => { byId[o.id] = o })
  const remaining = options.filter(o => !value.includes(o.id))

  function add(id: string | null) {
    if (!id || value.includes(id)) return
    onChange([...value, id])
  }
  function remove(id: string) { onChange(value.filter(v => v !== id)) }

  return (
    <div style={{ display: 'inline-flex', flexDirection: 'column', gap: 6, verticalAlign: 'top' }}>
      <EntityPicker options={remaining} value={null} onChange={add} placeholder={placeholder} width={width} />
      {value.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, maxWidth: typeof width === 'number' ? width + 80 : undefined }}>
          {value.map(id => (
            <span key={id} style={{
              display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11,
              padding: '2px 6px 2px 8px', borderRadius: 12, background: 'var(--surface2)', border: '1px solid var(--border)',
            }}>
              {byId[id]?.label || id}
              <button type="button" onClick={() => remove(id)}
                style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'var(--text3)', fontSize: 12, lineHeight: 1, padding: 0 }}
                aria-label={`Remove ${byId[id]?.label || id}`}>✕</button>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

export default EntityPickerChips
