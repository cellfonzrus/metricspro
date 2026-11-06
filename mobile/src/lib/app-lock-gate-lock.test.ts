/**
 * THE DESIGN-FIX LOCK for the app lock (house rule: "a fix is a DESIGN fix or it is not a fix" —
 * *"lock it so it cannot un-wire"*).
 *
 * `app-lock.ts` is the one home for every decision the lock makes, and `app-lock.test.ts` proves
 * those decisions. Neither stops the NEXT change from putting a copy of one back into the screen,
 * which is precisely the shape the reported defect had: the screen decided whether to prompt, and
 * decided it from a React callback identity.
 *
 * So this file reads the callers' SOURCE and fails the build when a caller stops dereferencing the
 * shared fact, or grows a second copy of it. It asserts nothing about behaviour; `app-lock.test.ts`
 * does that.
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const read = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8')

const GATE = read('../auth/AppLockGate.tsx')
const BIOMETRIC = read('../auth/biometric.ts')
const SETTINGS = read('../../app/(app)/(tabs)/settings.tsx')

/** Every `useCallback`-declared local in a file. */
const callbackNames = (src: string): string[] =>
  [...src.matchAll(/const\s+(\w+)\s*=\s*useCallback\(/g)].map((m) => m[1])

/** Every React hook dependency array in a file, as its list of identifiers. */
const dependencyArrays = (src: string): string[][] =>
  [...src.matchAll(/\},\s*\[([^\]]*)\]\s*\)/g)].map((m) =>
    m[1]
      .split(',')
      .map((d) => d.trim())
      .filter(Boolean),
  )

describe('the lock screen reads the one home and decides nothing', () => {
  it('dereferences every decision from src/lib/app-lock', () => {
    expect(GATE).toMatch(/from '@\/lib\/app-lock'/)
    for (const selector of [
      'appLockReducer',
      'initialAppLockState',
      'shouldShowAppLockUi',
      'shouldAutoPromptAppLock',
      'canAttemptAppLock',
      'shouldOfferAppLockSignOut',
    ]) {
      expect(GATE, `AppLockGate must read ${selector} rather than deciding it`).toContain(selector)
    }
  })

  it('holds no lock state of its own — the reducer is the only state', () => {
    // `useState` in this file is how `locked` / `checking` / `enabled` came to be three
    // independently-settable booleans that could disagree with each other.
    expect(GATE).not.toMatch(/useState\s*[(<]/)
    expect(GATE).toMatch(/useReducer\(appLockReducer/)
  })

  it('never puts a callback identity in a dependency array — THE reported defect', () => {
    // `useEffect(..., [locked, enabled, status, tryUnlock])` with `tryUnlock = useCallback(…,
    // [checking])` re-fired the prompt every time an attempt finished, so a cancelled Face ID came
    // straight back and the overlay could not be dismissed.
    const callbacks = callbackNames(GATE)
    for (const deps of dependencyArrays(GATE)) {
      for (const name of callbacks) {
        expect(
          deps,
          `AppLockGate must not depend on the callback "${name}" — its identity changes per attempt`,
        ).not.toContain(name)
      }
    }
  })

  it('keeps no second copy of the grace period or the failure ladder', () => {
    expect(GATE).not.toMatch(/GRACE_MS\s*=/)
    expect(GATE).not.toMatch(/\b60_?000\b/)
    expect(GATE).not.toMatch(/failures\s*[><=]/)
  })
})

describe('"can this handset unlock at all" has one answer', () => {
  it('biometric.ts dereferences it instead of deriving a label itself', () => {
    expect(BIOMETRIC).toMatch(/describeDeviceFactor/)
    // The factor labels are the one home's vocabulary. A literal here is the second copy.
    for (const label of ['Face ID', 'Touch ID', 'Passcode']) {
      expect(BIOMETRIC, `"${label}" belongs to describeDeviceFactor, not to biometric.ts`).not.toContain(
        `'${label}'`,
      )
    }
  })

  it('the lock screen asks for hasFactor, never for hasHardware', () => {
    // `hasHardwareAsync()` is wrong in both directions (sensor with nothing enrolled; passcode-only
    // phone with no sensor), which is why the decision moved to the enrolled level.
    expect(GATE).toMatch(/hasFactor/)
    expect(GATE).not.toMatch(/hasHardware/)
  })

  it('the Settings switch gates on hasFactor, so the lock cannot be turned on unopenably', () => {
    expect(SETTINGS).toMatch(/disabled=\{!biometric\?\.hasFactor\}/)
  })
})
