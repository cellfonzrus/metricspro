import { describe, expect, it } from 'vitest'

import {
  BUNDLE_UNKNOWN,
  describeRunningBundle,
  type RunningBundleFacts,
} from './running-bundle'

// A build that is getting updates normally and is running one.
const ON_AN_UPDATE: RunningBundleFacts = {
  updatesEnabled: true,
  isEmbeddedLaunch: false,
  updateId: '32e7773b-ea85-4dc9-ba56-221f0b3278a4',
  runtimeVersion: '0.1.0',
  channel: 'production',
  createdAt: new Date('2026-10-31T18:00:00Z'),
  nativeAppVersion: '0.1.0',
  nativeBuildVersion: '9',
  bundleSdkVersion: '52.0.0',
}

// The same build, running the bundle it shipped with. This is the shape of the defect that cost six
// weeks: Expo was serving an update and the app was quietly not running it.
const SILENTLY_EMBEDDED: RunningBundleFacts = {
  ...ON_AN_UPDATE,
  isEmbeddedLaunch: true,
  updateId: null,
  createdAt: null,
}

function valueOf(facts: RunningBundleFacts, label: string): string {
  const line = describeRunningBundle(facts).lines.find((l) => l.label === label)
  if (!line) throw new Error(`no line labelled ${label}`)
  return line.value
}

describe('describeRunningBundle', () => {
  it('names the update it is running', () => {
    expect(valueOf(ON_AN_UPDATE, 'Running')).toBe('Update 32e7773b')
  })

  it('says plainly when it is running the bundle that shipped with the app', () => {
    expect(valueOf(SILENTLY_EMBEDDED, 'Running')).toBe('The bundle built into the app')
  })

  it('flags the app as behind when it CAN take updates but is on the built-in bundle', () => {
    expect(describeRunningBundle(SILENTLY_EMBEDDED).behind).toBe(true)
    expect(describeRunningBundle(ON_AN_UPDATE).behind).toBe(false)
  })

  it('does not flag a build that was never wired for updates', () => {
    const noChannel = { ...SILENTLY_EMBEDDED, channel: null, updatesEnabled: false }
    expect(describeRunningBundle(noChannel).behind).toBe(false)
  })

  it('says why, in words, when it is behind', () => {
    const d = describeRunningBundle(SILENTLY_EMBEDDED)
    expect(d.note).toContain('has not been applied')
  })

  it('says nothing alarming when it is up to date', () => {
    expect(describeRunningBundle(ON_AN_UPDATE).note).toBe('')
  })

  it('never renders a blank value, whatever is missing', () => {
    const nothing: RunningBundleFacts = {
      updatesEnabled: false,
      isEmbeddedLaunch: true,
      updateId: null,
      runtimeVersion: null,
      channel: null,
      createdAt: null,
      nativeAppVersion: null,
      nativeBuildVersion: null,
      bundleSdkVersion: null,
    }
    const d = describeRunningBundle(nothing)
    expect(d.lines.length).toBeGreaterThan(3)
    for (const line of d.lines) {
      expect(line.label.trim()).not.toBe('')
      expect(line.value.trim()).not.toBe('')
    }
  })

  it('reports an absent fact as unknown rather than inventing one', () => {
    const d = describeRunningBundle({ ...ON_AN_UPDATE, runtimeVersion: null, channel: null })
    expect(valueOf({ ...ON_AN_UPDATE, runtimeVersion: null, channel: null }, 'Runtime')).toBe(
      BUNDLE_UNKNOWN,
    )
    expect(d.lines.find((l) => l.label === 'Channel')?.value).toBe('none')
  })

  it('shows the binary and the bundle separately, so a mismatch between them is visible', () => {
    // The whole point: on 2026-10-31 Expo recorded the installed build as SDK 57 while the
    // repository declared SDK 52, and nothing on the phone could have shown that.
    expect(valueOf(ON_AN_UPDATE, 'App build')).toBe('0.1.0 (9)')
    expect(valueOf(ON_AN_UPDATE, 'Bundle SDK')).toBe('52.0.0')
  })

  it('dates the update it is running, and does not pretend to date one it is not', () => {
    expect(valueOf(ON_AN_UPDATE, 'Published')).toContain('2026')
    expect(valueOf(SILENTLY_EMBEDDED, 'Published')).toBe('with the app')
  })

  it('turns updates being off into a statement, not a blank', () => {
    const off = { ...SILENTLY_EMBEDDED, updatesEnabled: false, channel: null }
    expect(valueOf(off, 'Running')).toBe('The bundle built into the app')
    expect(describeRunningBundle(off).note).toContain('not set up')
  })
})
