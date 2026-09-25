// THE hook over THE predicate (see ./multimonthOffer.ts): is multi-month pay configured for this org, and is
// any multi-month money on the rep rows for the periods on screen. Reads GET /commcalc/multimonth/status
// (backend multimonth_config.load); the org is added by client.ts.
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { MULTIMONTH_UNKNOWN, type MultimonthStatus } from './multimonthOffer'

export { multimonthOffered, multimonthRows, MULTIMONTH_UNKNOWN, type MultimonthStatus } from './multimonthOffer'

export function useMultimonthStatus(periods: string[]): MultimonthStatus {
  const [s, setS] = useState<MultimonthStatus>(MULTIMONTH_UNKNOWN)
  const key = (periods || []).filter(Boolean).join(',')
  useEffect(() => {
    let alive = true
    api(`/api/v1/commcalc/multimonth/status?periods=${encodeURIComponent(key)}`)
      .then((d: any) => { if (alive && d && typeof d.offered === 'boolean') setS(d as MultimonthStatus) })
      .catch(() => { if (alive) setS(MULTIMONTH_UNKNOWN) })
    return () => { alive = false }
  }, [key])
  return s
}
