'use client'
import ClosingPickupStatus from '../_lib/ClosingPickupStatus'

// CLOSING & CASH PICKUP STATUS (owner ask 2026-10-14) — "report which store did not submit closing
// and which store got store cash picked and another report which cash did not get [picked up] - these
// can be in one report with 2 filters added picked and not picked up - with all standard filters".
//
// The whole report is `_lib/ClosingPickupStatus`, SHARED with the Daily Closing dashboard's own tab
// (the owner asked for it in both places, and two copies of one report is how two screens start
// disagreeing). This page is the standalone entry — the Daily Closing dashboard tile — and owns its
// own filter bar; the dashboard passes its shared one in instead.
export default function ClosingPickupStatusPage() {
  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🔔 Closing &amp; Cash Pickup Status</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 820 }}>
          Which stores have <b>not submitted a daily closing</b>, and whose <b>store cash has not been picked up</b> —
          one line per store and day, with the money still sitting in each store.
        </p>
      </div>
      <ClosingPickupStatus />
    </div>
  )
}
