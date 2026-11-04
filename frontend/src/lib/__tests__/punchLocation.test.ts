/**
 * The kiosk's punch fix: fresh at the tap, and never at the cost of the punch.
 *
 * THE DEFECT UNDER TEST is not the missing clock-out location — it is the CACHED one. The kiosk read
 * a position once at sign-in and re-sent it with every punch, so the evidence said "at the store"
 * however far away the rep actually was. `maximumAge: 0` is therefore an assertion here, not a
 * detail: a cached fix presented as a record of where a punch happened is worse than no fix at all.
 */
import { describe, expect, it, vi } from 'vitest'

import {
  PUNCH_LOCATION_TIMEOUT_MS,
  punchLocation,
  punchLocationFrom,
} from '../punchLocation'

const at = (coords: unknown) =>
  ((onOk: (p: { coords: never }) => void) => onOk({ coords: coords as never })) as never

describe('punchLocationFrom', () => {
  it('keeps a real fix, rounding the accuracy the way the column stores it', () => {
    expect(punchLocationFrom({ latitude: 40.65, longitude: -73.95, accuracy: 12.4 })).toEqual({
      gps_lat: 40.65,
      gps_lng: -73.95,
      gps_accuracy_m: 12,
    })
  })

  it('omits an accuracy the browser did not report rather than claiming a perfect fix', () => {
    const body = punchLocationFrom({ latitude: 1, longitude: 2 })
    expect(body).toEqual({ gps_lat: 1, gps_lng: 2 })
    expect('gps_accuracy_m' in body).toBe(false)
  })

  it('drops anything that is not a real coordinate', () => {
    for (const coords of [
      undefined,
      {},
      { latitude: NaN, longitude: -73.95 },
      { latitude: 40.65, longitude: NaN },
      { latitude: 91, longitude: 0 },
      { latitude: 0, longitude: 181 },
      { latitude: 'abc', longitude: 'def' },
      { latitude: 40.65 },
    ]) {
      expect(punchLocationFrom(coords as never)).toEqual({})
    }
  })

  it('keeps a legitimate zero coordinate', () => {
    expect(punchLocationFrom({ latitude: 0, longitude: 0, accuracy: 5 })).toEqual({
      gps_lat: 0,
      gps_lng: 0,
      gps_accuracy_m: 5,
    })
  })
})

describe('punchLocation', () => {
  it('returns the fix the browser reports', async () => {
    expect(await punchLocation(at({ latitude: 40.65, longitude: -73.95, accuracy: 9 }))).toEqual({
      gps_lat: 40.65,
      gps_lng: -73.95,
      gps_accuracy_m: 9,
    })
  })

  it('NEVER accepts a cached position — the defect that made the evidence lie', async () => {
    const get = vi.fn(
      (onOk: (p: { coords: unknown }) => void, _onErr: () => void, options?: unknown) => {
        void _onErr
        void options
        onOk({ coords: { latitude: 1, longitude: 2, accuracy: 3 } })
      },
    )
    await punchLocation(get as never)
    expect(get.mock.calls[0][2]).toMatchObject({ maximumAge: 0 })
  })

  it('returns nothing when the rep denies permission', async () => {
    const get = ((_ok: unknown, onErr: () => void) => onErr()) as never
    expect(await punchLocation(get)).toEqual({})
  })

  it('returns nothing when the browser has no geolocation at all', async () => {
    expect(await punchLocation(null)).toEqual({})
  })

  it('returns nothing rather than throwing when the browser call throws', async () => {
    const get = (() => {
      throw new Error('blocked by permissions policy')
    }) as never
    await expect(punchLocation(get)).resolves.toEqual({})
  })

  it('gives up when the browser never calls back, instead of hanging the punch', async () => {
    vi.useFakeTimers()
    try {
      const pending = punchLocation((() => {}) as never)
      await vi.advanceTimersByTimeAsync(PUNCH_LOCATION_TIMEOUT_MS + 1)
      expect(await pending).toEqual({})
    } finally {
      vi.useRealTimers()
    }
  })

  it('settles once even if the browser answers after the timeout', async () => {
    vi.useFakeTimers()
    try {
      let late: ((p: { coords: unknown }) => void) | undefined
      const pending = punchLocation(((onOk: (p: { coords: unknown }) => void) => {
        late = onOk
      }) as never)
      await vi.advanceTimersByTimeAsync(PUNCH_LOCATION_TIMEOUT_MS + 1)
      late?.({ coords: { latitude: 5, longitude: 6, accuracy: 1 } })
      expect(await pending).toEqual({})
    } finally {
      vi.useRealTimers()
    }
  })
})
