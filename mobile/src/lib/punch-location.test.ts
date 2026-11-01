/**
 * The fix a punch carries — and the promise that asking for it can never cost a rep their punch.
 *
 * OWNER ASK 2026-11-01: capture the location at clock-in AND clock-out *"so they are not clocking
 * out at their homes"*.
 *
 * WHY THIS IS A UNIT AND NOT A SCREEN. Before this file, `tryGetLocation` lived inside the time-clock
 * screen, which no test ran, and the clock-out did not call it at all. The thing worth pinning is not
 * the button: it is that a denied permission, a phone with no fix, a hanging GPS call and a thrown
 * exception all end the SAME way — `{}`, promptly, and the punch goes out without coordinates. A rep
 * who declines location, or stands in a basement, still gets to end their shift.
 */
import { describe, expect, it, vi } from 'vitest'

import { PUNCH_LOCATION_TIMEOUT_MS, punchLocation } from './punch-location'

const GRANTED = { status: 'granted' as const }
const DENIED = { status: 'denied' as const }
const FIX = { coords: { latitude: 40.65, longitude: -73.95, accuracy: 12.5 } }

const ok = (coords = FIX) => ({
  requestPermission: vi.fn().mockResolvedValue(GRANTED),
  getPosition: vi.fn().mockResolvedValue(coords),
})

describe('punchLocation', () => {
  it('returns the fix when permission is granted and the device has one', async () => {
    expect(await punchLocation(ok())).toEqual({
      gps_lat: 40.65,
      gps_lng: -73.95,
      gps_accuracy_m: 12.5,
    })
  })

  it('returns nothing when the rep declines location, and never asks for a position', async () => {
    const deps = { requestPermission: vi.fn().mockResolvedValue(DENIED), getPosition: vi.fn() }
    expect(await punchLocation(deps)).toEqual({})
    expect(deps.getPosition).not.toHaveBeenCalled()
  })

  it('returns nothing when the device reports no position', async () => {
    expect(await punchLocation({ ...ok(), getPosition: vi.fn().mockResolvedValue(null) })).toEqual({})
  })

  it('returns nothing rather than throwing when the permission call fails', async () => {
    const deps = {
      requestPermission: vi.fn().mockRejectedValue(new Error('no location services')),
      getPosition: vi.fn(),
    }
    await expect(punchLocation(deps)).resolves.toEqual({})
  })

  it('returns nothing rather than throwing when the position call fails', async () => {
    const deps = { ...ok(), getPosition: vi.fn().mockRejectedValue(new Error('timeout')) }
    await expect(punchLocation(deps)).resolves.toEqual({})
  })

  it('gives up on a hanging GPS call instead of hanging the punch', async () => {
    vi.useFakeTimers()
    try {
      const deps = { ...ok(), getPosition: vi.fn(() => new Promise<never>(() => {})) }
      const pending = punchLocation(deps)
      await vi.advanceTimersByTimeAsync(PUNCH_LOCATION_TIMEOUT_MS + 1)
      expect(await pending).toEqual({})
    } finally {
      vi.useRealTimers()
    }
  })

  it('gives up on a hanging PERMISSION prompt too', async () => {
    vi.useFakeTimers()
    try {
      const deps = { requestPermission: vi.fn(() => new Promise<never>(() => {})), getPosition: vi.fn() }
      const pending = punchLocation(deps)
      await vi.advanceTimersByTimeAsync(PUNCH_LOCATION_TIMEOUT_MS + 1)
      expect(await pending).toEqual({})
      expect(deps.getPosition).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('omits an accuracy the device did not report rather than claiming a perfect fix', async () => {
    /** An accuracy of 0 would tell the server the fix is exact, which is how a bad reading becomes
     * authoritative evidence against somebody. */
    const body = await punchLocation(
      ok({ coords: { latitude: 1, longitude: 2, accuracy: null } } as never),
    )
    expect(body).toEqual({ gps_lat: 1, gps_lng: 2 })
    expect('gps_accuracy_m' in body).toBe(false)
  })

  it('drops a position that is not a real coordinate instead of sending it', async () => {
    /** `0, 0` off the coast of Africa is the classic shape of a failed read being sent as a fact. */
    for (const coords of [
      { latitude: NaN, longitude: -73.95, accuracy: 10 },
      { latitude: 40.65, longitude: NaN, accuracy: 10 },
      { latitude: 91, longitude: -73.95, accuracy: 10 },
      { latitude: 40.65, longitude: 181, accuracy: 10 },
      { latitude: null, longitude: null, accuracy: 10 },
    ]) {
      expect(await punchLocation(ok({ coords } as never))).toEqual({})
    }
  })

  it('keeps a legitimate zero coordinate', async () => {
    /** The Gulf of Guinea is not in this platform's estate, but a rule that treated 0 as missing
     * would be wrong for a reason nobody could find later. The server's own parser keeps it too. */
    expect(await punchLocation(ok({ coords: { latitude: 0, longitude: 0, accuracy: 5 } } as never)))
      .toEqual({ gps_lat: 0, gps_lng: 0, gps_accuracy_m: 5 })
  })
})
