/**
 * THE one place this app asks "where is this punch being made?".
 *
 * OWNER ASK 2026-11-01 (verbatim): *"the sales reps when they log in should capture the gos location
 * so they are not clocking out at their homes and the geolocation when they clock out from somewhere
 * else than their usual location which should match the store address should be reported as flag to
 * the management"*.
 *
 * WHY IT IS ITS OWN FILE. This logic used to live inside the time-clock screen, which meant the
 * clock-OUT — the punch the owner is actually worried about — had no location at all, and nothing
 * could test the one promise that matters. Both punches now call this, and so can any screen that
 * grows a punch later. A second copy of "ask for permission, time-box the fix, give up quietly"
 * would drift from this one, and the drift would show up as a rep who cannot end their shift.
 *
 * THE PROMISE: asking can never cost a rep their punch. A denied permission, a phone with no fix, a
 * GPS call that hangs, a thrown exception and a coordinate that is not a real coordinate all end the
 * same way — `{}`, promptly — and the caller sends the punch without coordinates. The server records
 * that as "unverified", which is explicitly NOT the same as "they were not there"
 * (`core/store_geofence`). Pinned by `punch-location.test.ts`.
 *
 * WHAT IT WILL NEVER DO: watch a position, keep one, or report one at any moment the employee did not
 * themselves initiate. It is called at a tap, it returns one reading, it keeps nothing. Continuous
 * location is the primitive of tracking and no requirement in this platform needs it.
 */

/** Every step is time-boxed: tapping "Clock out" must never look like nothing happened. */
export const PUNCH_LOCATION_TIMEOUT_MS = 5000

export type PunchLocation = {
  gps_lat?: number
  gps_lng?: number
  gps_accuracy_m?: number
}

type PermissionResult = { status: string }
type PositionResult = { coords: { latitude: unknown; longitude: unknown; accuracy?: unknown } } | null

/**
 * The two platform calls, injected — so the rules above are provable in a plain Node test with no
 * device, no emulator and no permission dialog.
 */
export type PunchLocationDeps = {
  requestPermission: () => Promise<PermissionResult>
  getPosition: () => Promise<PositionResult>
}

/** Cap a promise so a slow permission prompt or GPS read can never hang a punch. */
function withTimeout<T>(p: Promise<T>, ms: number, fallback: T): Promise<T> {
  return Promise.race([p, new Promise<T>((resolve) => setTimeout(() => resolve(fallback), ms))])
}

/** A coordinate as a finite number inside its real range, or undefined. Mirrors the server's own
 * `core.geo.parse_lat` / `parse_lng`, so a reading this app sends is one the server will accept —
 * and a NaN or an out-of-range value is dropped here rather than stored as a fact about somebody. */
function coord(value: unknown, limit: number): number | undefined {
  const n = typeof value === 'number' ? value : Number(value)
  if (value === null || value === undefined || value === '' || !Number.isFinite(n)) return undefined
  if (Math.abs(n) > limit) return undefined
  return n
}

export async function punchLocation(deps: PunchLocationDeps): Promise<PunchLocation> {
  try {
    const permission = await withTimeout(deps.requestPermission(), PUNCH_LOCATION_TIMEOUT_MS, {
      status: 'undetermined',
    })
    // Declining is a legitimate answer, not an error, and it ends here: nothing asks the device for
    // a position after the rep has said no.
    if (permission?.status !== 'granted') return {}

    const position = await withTimeout(deps.getPosition(), PUNCH_LOCATION_TIMEOUT_MS, null)
    if (!position) return {}

    const lat = coord(position.coords?.latitude, 90)
    const lng = coord(position.coords?.longitude, 180)
    if (lat === undefined || lng === undefined) return {}

    const body: PunchLocation = { gps_lat: lat, gps_lng: lng }
    // An accuracy the device did not report is OMITTED, never sent as 0: a zero would tell the
    // server the fix is exact, which is how a bad reading becomes authoritative evidence against
    // somebody. Absent accuracy makes the server's borderline branch cautious instead.
    const accuracy = coord(position.coords?.accuracy, Number.MAX_SAFE_INTEGER)
    if (accuracy !== undefined && accuracy >= 0) body.gps_accuracy_m = accuracy
    return body
  } catch {
    return {}
  }
}
