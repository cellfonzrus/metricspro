'use client'
import { useState, useEffect } from 'react'
import { useAuth } from '@/lib/auth-context'
import { usePeriod } from '@/lib/period-context'
import { api } from '@/lib/client'
import TeamSnapshot from '@/components/TeamSnapshot'

// Manager team view on the platform: defaults to the SIGNED-IN manager's span (via their token); an
// admin can pick any org unit to roll up. Shares <TeamSnapshot> with the /portal "My Team" tab.
export default function TeamPage() {
  const { token } = useAuth()
  const { period } = usePeriod()
  const [units, setUnits] = useState<any[]>([])
  const [levels, setLevels] = useState<any[]>([])
  const [unitId, setUnitId] = useState('')   // '' = my span (use token)

  useEffect(() => {
    api('/api/v1/storeops/org/tree')
      .then((t: any) => { setUnits(t?.units || []); setLevels(t?.levels || []) })
      .catch(() => {})
  }, [])

  const levelName = (lid: number | null) => {
    const l = levels.find(x => x.id === lid)
    return l ? l.name : ''
  }
  const sorted = [...units].sort((a, b) => a.name.localeCompare(b.name))

  return (
    <div style={{ padding: 24, maxWidth: 1000 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🫂 My Team</h1>
        <span style={{ flex: 1 }} />
        <label style={{ fontSize: 13, color: 'var(--text3)' }}>Scope:
          <select value={unitId} onChange={e => setUnitId(e.target.value)} style={{ marginLeft: 6 }}>
            <option value="">My team (assigned units)</option>
            {sorted.map(u => <option key={u.id} value={u.id}>{levelName(u.level_id)}: {u.name}</option>)}
          </select>
        </label>
      </div>
      <p style={{ color: 'var(--text3)', fontSize: 13, marginTop: 0 }}>
        Performance for every store and rep under you for <b>{period}</b>. Pick a unit to roll up a specific
        part of the org (admins). Tap a rep to see their full dashboard.
      </p>
      <TeamSnapshot period={period} token={unitId ? undefined : (token || undefined)} unitId={unitId || undefined} />
    </div>
  )
}
