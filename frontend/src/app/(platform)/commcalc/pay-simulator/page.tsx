'use client'
// EMPLOYEE PAY SIMULATOR — full page (owner 2026-08-03: "the employee should be able to play the
// numbers to get an idea of what they will make").
//
// SELF-SERVICE + SELF-ONLY: there is no employee picker. The backend resolves WHO from the bearer
// token (`pay_simulator.require_self`) and 403s any request aimed at another rep, so this page shows
// the signed-in employee their OWN plan and nobody else's — no page-level nav gate is needed for the
// data, because the data IS the caller's own.
//
// READ-ONLY: nothing on this page writes, and no pay number moves. The dollars are produced by the
// REAL engine server-side (`commission_engine.preview` with a read-only sales override), never by a
// formula in the browser.
import { usePeriod } from '@/lib/period-context'
import PaySimulator from './_components/PaySimulator'

export default function PaySimulatorPage() {
  const { period } = usePeriod()
  return (
    <div>
      <div style={{ marginBottom: 18 }}>
        <a href="/commcalc" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← Commissions</a>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 0' }}>🎚️ What would I make?</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Play with your numbers and see what your incentive would be. This runs your real pay plan —
          the same engine that pays you — but it is a projection only: nothing is saved and no pay is changed.
        </p>
      </div>
      <div className="card" style={{ padding: 18 }}>
        <PaySimulator period={period} />
      </div>
    </div>
  )
}
