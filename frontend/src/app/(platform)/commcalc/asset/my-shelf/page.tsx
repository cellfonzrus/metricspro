'use client'
import Link from 'next/link'
import { useAuth } from '@/lib/auth-context'
import { repConfined, REP_LANDING } from '@/lib/rbac'
import ShelfAging from '@/components/ShelfAging'

// PHONES ON MY SHELF — the per-rep aging screen, its own page and its own tile (§CORE-33.7).
//
// OWNER 2026-11-27: *"every sales rep should be show which phones ar sititng in thier store
// inventory hwere they are currenly clocked in so they an address those on a top priortiy."*
//
// WHY A PAGE AND NOT A CARD ON ANOTHER ONE. It was a card on My targets, so when NY LOGISTICS
// switched My Targets off for its reps the shelf disappeared with it. The card itself is unchanged
// and shared (`components/ShelfAging`): this page adds no second copy of the question, and the
// narrowing to "the store I am clocked into" is the SERVER's (`GET /commcalc/my-shelf`), which the
// card already renders along with the reason it gives.
//
// A confined employee has no menu, so the one link out goes back to their landing — `repConfined`
// off `/me`, used for that single purpose exactly as on the closing form and the barcode rescue.
export default function MyShelfPage() {
  const { permissions } = useAuth()
  const confined = repConfined(permissions)
  return (
    <div style={{ maxWidth: 1100 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📦 Phones on my shelf</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Oldest first, with how long each one has left in the selling window. While you are clocked
            in, this is the store you are clocked into.
          </p>
        </div>
        {confined
          ? <Link href={REP_LANDING} className="btn btn-secondary" style={{ fontSize: 13 }}>← My day</Link>
          : <Link href="/commcalc/asset/aging" className="btn btn-secondary" style={{ fontSize: 13 }}>← Inventory Aging</Link>}
      </div>
      <ShelfAging />
    </div>
  )
}
