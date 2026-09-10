'use client'
// ── ONE FLOWCHART ────────────────────────────────────────────────────────────────────────────────
// Owner directive 2026-09-10: "All of these will be in the training module under a tile called
// flowcharts and named appropriately."
//
// The runbook itself is typed content in lib/flowcharts, rendered by components/RunbookDoc. This
// route only resolves a slug and handles the miss — a stale bookmark to a renamed runbook lands on a
// named "not found" with a way back, never a blank screen or a crash.
//
// Client component, useParams: the same idiom as /hub/[group], so both dynamic routes in this app
// read their segment the same way.
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { RunbookDoc } from '@/components/RunbookDoc'
import { FLOWCHARTS, flowchartBySlug } from '@/lib/flowcharts'

export default function FlowchartPage() {
  const { slug } = useParams<{ slug: string }>()
  const doc = flowchartBySlug(String(slug || ''))

  if (!doc) {
    return (
      <div className="card" style={{ maxWidth: 520, margin: '60px auto', padding: 32, textAlign: 'center' }}>
        <div style={{ fontSize: 28, marginBottom: 8 }}>🗺️</div>
        <div style={{ fontSize: 17, fontWeight: 700, marginBottom: 6 }}>No flowchart here</div>
        <p style={{ fontSize: 13, color: 'var(--text3)', marginBottom: 18 }}>
          There is no runbook called “{String(slug || '')}”. It may have been renamed.
        </p>
        <div style={{ display: 'grid', gap: 8, textAlign: 'left' }}>
          {FLOWCHARTS.map(f => (
            <Link key={f.slug} href={`/training/flowcharts/${f.slug}`}
              style={{ fontSize: 13, color: 'var(--accent)', textDecoration: 'none' }}>
              {f.icon} {f.title}
            </Link>
          ))}
        </div>
      </div>
    )
  }

  return <RunbookDoc doc={doc} />
}
