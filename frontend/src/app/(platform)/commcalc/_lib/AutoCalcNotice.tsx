'use client'
// WHAT THE LANDING HOOK DID FOR THIS MONTH (owner 2026-09-28: "when sept is uploaded the system should calculate
// automatically without manual intervention"; index §6l).
//
// Every upload / sweep / intake that lands sales for a month queues ONE standard Run Calculation of that month
// (backend `auto_calc.landed`), which a background poller runs once the month's uploads have been quiet for the
// company's window. This panel shows the ONE sentence the backend builds for it — `GET /calc-status/{period}` →
// `auto_calc` — so the page never re-derives what happened:
//   queued     "The upload of … landed at …. Auto-calculation is queued — it runs once uploads … (about …)."
//   calculated "Auto-calculated at … from the upload of … — N rep(s)."
//   refused    "Auto-calculation refused at … after …: <the calculation's own words> The last good snapshot … was kept."
//   failed / busy / off / running — each in plain words.
// Display only: it reads the status row and never starts a calculation (the Run Calculation button does that).
// While a calculation is queued or running it re-reads once a minute and tells the page when the month changed,
// so the numbers below refresh on their own.
import { useEffect, useRef, useState } from 'react'
import { api, ORG_ID } from '@/lib/client'
import { isRunning } from '@/lib/job-run'
import { paths } from './runCommission'

export type AutoCalc = {
  state?: string | null            // queued | running | calculated | calculated_with_errors | refused | failed | busy | off
  tone?: string | null             // ok | warn | error | info
  sentence?: string | null
  due_at?: string | null
}

const TONE: Record<string, { border: string; label: string }> = {
  ok: { border: 'var(--green)', label: 'Auto-calculated' },
  warn: { border: 'var(--amber)', label: 'Auto-calculation' },
  error: { border: 'var(--red)', label: 'Auto-calculation did not run' },
  info: { border: 'var(--accent)', label: 'Auto-calculation' },
}

// WHICH WORDS MEAN "a run is in flight" is NOT this file's fact — it is job-run.RUNNING_STATES
// (owner report 2026-10-06, index §6q). This file held a third private copy of that set; a fourth
// would be the next divergence. The POLL CADENCE below stays local on purpose: it follows the
// landing hook's own queue rather than a press on this page, so it is a different question.
const PENDING = { has: (s: string) => isRunning(s) }

export default function AutoCalcNotice({ period, onSettled }: { period: string; onSettled?: () => void }) {
  const [ac, setAc] = useState<AutoCalc | null>(null)
  const prev = useRef<string | null | undefined>(null)

  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout> | null = null
    prev.current = null
    const load = async () => {
      try {
        const s = await api(paths.calcStatus(period, ORG_ID)) as { auto_calc?: AutoCalc } | null
        if (!alive) return
        const next = s?.auto_calc && s.auto_calc.state ? s.auto_calc : null
        const was = prev.current
        prev.current = next?.state
        setAc(next)
        if (was && PENDING.has(was) && next?.state && !PENDING.has(next.state)) onSettled?.()
        if (next?.state && PENDING.has(next.state)) timer = setTimeout(load, 60000)
      } catch {
        if (alive) setAc(null)
      }
    }
    load()
    return () => { alive = false; if (timer) clearTimeout(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period])

  if (!ac?.sentence) return null
  const t = TONE[ac.tone || 'info'] || TONE.info
  return (
    <div className="card" role="status"
      style={{ borderLeft: `4px solid ${t.border}`, background: 'var(--surface2)', marginBottom: 16, padding: '10px 14px' }}>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 4 }}>{t.label} — {period}</div>
      <div style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.6 }}>{ac.sentence}</div>
    </div>
  )
}
