/**
 * THE one place the kiosk asks "where is this punch being made?" — a FRESH reading, at the tap.
 *
 * OWNER ASK 2026-11-01 (verbatim): *"the sales reps when they log in should capture the gos location
 * so they are not clocking out at their homes and the geolocation when they clock out from somewhere
 * else than their usual location which should match the store address should be reported as flag to
 * the management"*.
 *
 * THE DEFECT THIS REPLACES, which is worse than the missing clock-out fix. The kiosk read the
 * browser's position ONCE, at sign-in, and stashed it in a ref that every clock-in then re-sent. A
 * rep who signs in at the store and clocks out eight hours later from anywhere at all would have
 * re-sent the morning's coordinates — so the evidence would have said "at the store" no matter where
 * they were. A cached fix is not a record of where a punch happened; it is a record of where the
 * SESSION started, and storing it as the former is worse than storing nothing. So every punch takes
 * its own reading here, and a reading that cannot be taken sends NOTHING: the server records that as
 * "unverified", which is explicitly not "they were not there".
 *
 * THE PROMISE: asking can never cost a rep their punch. A denied permission, a browser with no
 * geolocation, a timeout and a throw all end the same way — `{}`, promptly. Mirrors the phone app's
 * `mobile/src/lib/punch-location.ts` rule for rule, and both mirror the server's own coordinate
 * parser (`core.geo.parse_lat` / `parse_lng`), so a reading either client sends is one the server
 * accepts. Pinned by `punchLocation.test.ts`.
 *
 * WHAT IT WILL NEVER DO: watch a position, keep one, or report one at a moment the employee did not
 * themselves initiate. Called at a tap, returns one reading, keeps nothing.
 */

/** A punch must not wait on a slow fix. Past this, it goes out without coordinates. */
export const PUNCH_LOCATION_TIMEOUT_MS = 6000

export type PunchLocationBody = {
  gps_lat?: number
  gps_lng?: number
  gps_accuracy_m?: number
}

type Coords = { latitude?: unknown; longitude?: unknown; accuracy?: unknown }

/** The browser call, injected so the rules above are provable with no browser. */
export type GetPosition = (
  onSuccess: (position: { coords: Coords }) => void,
  onError: () => void,
  options?: { enableHighAccuracy?: boolean; timeout?: number; maximumAge?: number },
) => void

/** A coordinate as a finite number inside its real range, or undefined — so a NaN or an
 * out-of-range reading is dropped here rather than stored as a fact about somebody. */
function coord(value: unknown, limit: number): number | undefined {
  const n = typeof value === 'number' ? value : Number(value)
  if (value === null || value === undefined || value === '' || !Number.isFinite(n)) return undefined
  if (Math.abs(n) > limit) return undefined
  return n
}

export function punchLocationFrom(coords: Coords | undefined): PunchLocationBody {
  const lat = coord(coords?.latitude, 90)
  const lng = coord(coords?.longitude, 180)
  if (lat === undefined || lng === undefined) return {}
  const body: PunchLocationBody = { gps_lat: lat, gps_lng: lng }
  // An accuracy the browser did not report is OMITTED, never sent as 0: a zero would tell the server
  // the fix is exact, which is how a bad reading becomes authoritative evidence against somebody.
  const accuracy = coord(coords?.accuracy, Number.MAX_SAFE_INTEGER)
  if (accuracy !== undefined && accuracy >= 0) body.gps_accuracy_m = Math.round(accuracy)
  return body
}

export async function punchLocation(getPosition?: GetPosition | null): Promise<PunchLocationBody> {
  const get =
    getPosition ??
    (typeof navigator !== 'undefined' && navigator.geolocation
      ? navigator.geolocation.getCurrentPosition.bind(navigator.geolocation)
      : null)
  if (!get) return {}
  return new Promise<PunchLocationBody>((resolve) => {
    let settled = false
    const done = (body: PunchLocationBody) => {
      if (settled) return
      settled = true
      resolve(body)
    }
    // Our own timer as well as the browser's: a `getCurrentPosition` that never calls back at all
    // (it happens) would otherwise leave the punch waiting forever on a promise.
    const timer = setTimeout(() => done({}), PUNCH_LOCATION_TIMEOUT_MS)
    const finish = (body: PunchLocationBody) => {
      clearTimeout(timer)
      done(body)
    }
    try {
      get(
        (position) => finish(punchLocationFrom(position?.coords)),
        () => finish({}),
        // `maximumAge: 0` is the whole point: never hand back a cached fix from earlier in the
        // session, which is the defect this module exists to end.
        { enableHighAccuracy: true, timeout: PUNCH_LOCATION_TIMEOUT_MS, maximumAge: 0 },
      )
    } catch {
      finish({})
    }
  })
}
