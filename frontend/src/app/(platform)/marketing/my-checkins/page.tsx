'use client'
// "My check-ins" — what the platform has recorded about YOU.
//
// This page exists because employee location is sensitive personal data and the person it is about
// should not have to ask anyone to see it. It is filtered server-side to the caller's own employee
// id: it cannot be pointed at anybody else, by anybody, including a super-admin.
//
// It also states plainly what is NOT collected, because "we only take one reading when you press the
// button" is only reassuring if someone actually says it.
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import {
  panel, btn, th, cell, DECISION_COLOR, DECISION_LABEL, fmtDateTime,
  type EventCheckin,
} from '@/lib/marketing'

export default function MyCheckins() {
  const [rows, setRows] = useState<EventCheckin[]>([])
  const [explanation, setExplanation] = useState('')
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true); setMsg('')
    try {
      const r = await api('/api/v1/marketing/my-checkins')
      setRows(r.checkins || []); setExplanation(r.explanation || '')
    } catch (e: any) { setMsg(e?.message || String(e)) }
    setLoading(false)
  }, [])
  useEffect(() => { load() }, [load])

  return (
    <div style={{ padding: 20, maxWidth: 1000 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
        <Link href="/marketing" style={{ fontSize: 13, color: 'var(--text2)' }}>← Marketing</Link>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>My event check-ins</h1>
        <div style={{ flex: 1 }} />
        <button style={btn} onClick={load} disabled={loading}>{loading ? 'Loading…' : 'Refresh'}</button>
      </div>

      {msg && <div style={{ ...panel, marginBottom: 14, borderColor: '#dc2626', color: '#dc2626' }}>{msg}</div>}

      <div style={{ ...panel, marginBottom: 14 }}>
        <strong style={{ fontSize: 13 }}>What is recorded, and what is not</strong>
        <p style={{ fontSize: 13, color: 'var(--text2)', margin: '6px 0 0', lineHeight: 1.6 }}>
          {explanation || `Each row below is one location reading, taken at the moment you pressed
          check-in at an event.`}
        </p>
        <ul style={{ fontSize: 13, color: 'var(--text2)', margin: '8px 0 0', paddingLeft: 20, lineHeight: 1.7 }}>
          <li>Your location is read <strong>once</strong>, when you press check in — never in the background,
              never while the app is closed, and never between events.</li>
          <li>Checking out records <strong>only the time</strong>. No second location is taken.</li>
          <li>If your phone could not get a location, or the venue has no map pin, the check-in is still
              recorded and simply marked as not verified. It is not counted against you.</li>
          <li>Each row shows the date its location data is scheduled to be removed.</li>
        </ul>
      </div>

      <div style={{ ...panel, padding: 0, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr>
            <th style={th}>Event</th><th style={th}>Checked in</th><th style={th}>Result</th>
            <th style={th}>Distance from venue</th><th style={th}>Accuracy</th>
            <th style={th}>Checked out</th><th style={th}>Kept until</th>
          </tr></thead>
          <tbody>
            {rows.map(c => (
              <tr key={c.id}>
                <td style={cell}>
                  {c.event_id
                    ? <Link href={`/marketing/events/${c.event_id}`}>{c.event_title || 'Event'}</Link>
                    : (c.event_title || '—')}
                </td>
                <td style={cell}>{fmtDateTime(c.checked_in_at)}</td>
                <td style={{ ...cell, color: DECISION_COLOR[c.decision || ''] || 'var(--text2)' }}>
                  {DECISION_LABEL[c.decision || ''] || c.decision || '—'}
                  {c.decision_note && <div style={{ fontSize: 11, color: 'var(--text2)' }}>{c.decision_note}</div>}
                </td>
                <td style={cell}>{c.distance_m != null ? `${c.distance_m} m` : '—'}</td>
                <td style={cell}>{c.check_in_accuracy != null ? `±${Math.round(c.check_in_accuracy)} m` : '—'}</td>
                <td style={cell}>{c.checked_out_at ? fmtDateTime(c.checked_out_at) : '—'}</td>
                <td style={cell}>{c.purge_after_date || '—'}</td>
              </tr>
            ))}
            {!loading && !rows.length && (
              <tr><td style={{ ...cell, color: 'var(--text2)' }} colSpan={7}>
                Nothing recorded — you have not checked in to an event.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
