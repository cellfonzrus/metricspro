'use client'
import { createContext, useContext, useState, useEffect, ReactNode } from 'react'

function genPeriods(): string[] {
  const months = ['January','February','March','April','May','June','July','August','September','October','November','December']
  const out: string[] = []
  const now = new Date()
  let y = now.getFullYear(), m = now.getMonth()
  for (let i = 0; i < 24; i++) {
    out.push(`${months[m]} ${y}`)
    m--; if (m < 0) { m = 11; y-- }
  }
  return out
}

const PERIODS = genPeriods()

interface PeriodCtx { period: string; setPeriod: (p: string) => void; periods: string[] }
const Ctx = createContext<PeriodCtx>({ period: PERIODS[0], setPeriod: () => {}, periods: PERIODS })

export function PeriodProvider({ children }: { children: ReactNode }) {
  const [period, setPeriodState] = useState(PERIODS[0])

  useEffect(() => {
    const saved = typeof window !== 'undefined' ? localStorage.getItem('mp_period') : null
    if (saved) setPeriodState(saved)
  }, [])

  const setPeriod = (p: string) => {
    setPeriodState(p)
    if (typeof window !== 'undefined') localStorage.setItem('mp_period', p)
  }

  return <Ctx.Provider value={{ period, setPeriod, periods: PERIODS }}>{children}</Ctx.Provider>
}

export const usePeriod = () => useContext(Ctx)
