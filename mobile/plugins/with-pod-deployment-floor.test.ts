import { describe, expect, it } from 'vitest'

// eslint-disable-next-line @typescript-eslint/no-var-requires
const plugin = require('./with-pod-deployment-floor.js')
const { raiseEveryPodFloor, MARKER } = plugin as {
  raiseEveryPodFloor: (podfile: string, floor: string) => string
  MARKER: string
}

const PODFILE = [
  "require File.join(File.dirname(`node --print \"require.resolve('expo/package.json')\"`), 'scripts/autolinking')",
  "platform :ios, '15.1'",
  '',
  "target 'MetricsPro' do",
  '  use_expo_modules!',
  'end',
].join('\n')

describe('raiseEveryPodFloor', () => {
  it('adds a post_install hook that rewrites every pod target', () => {
    const out = raiseEveryPodFloor(PODFILE, '15.1')
    expect(out).toContain('post_install do |installer|')
    expect(out).toContain('installer.pods_project.targets.each do |target|')
    expect(out).toContain("config.build_settings['IPHONEOS_DEPLOYMENT_TARGET'] = '15.1'")
  })

  it('keeps everything that was already in the Podfile', () => {
    expect(raiseEveryPodFloor(PODFILE, '15.1')).toContain(PODFILE)
  })

  it('only raises a floor, never lowers one', () => {
    // A pod declaring iOS 16 must keep 16: lowering it could break that pod's own code.
    const out = raiseEveryPodFloor(PODFILE, '15.1')
    expect(out).toContain('current.nil? || current.to_f < 15.1')
  })

  it('is idempotent — a second prebuild does not stack a second hook', () => {
    const once = raiseEveryPodFloor(PODFILE, '15.1')
    const twice = raiseEveryPodFloor(once, '15.1')
    expect(twice).toBe(once)
    expect(twice.split('post_install do |installer|').length - 1).toBe(1)
  })

  it('leaves the marker so the hook can be found again', () => {
    expect(raiseEveryPodFloor(PODFILE, '15.1')).toContain(MARKER)
  })

  it('uses the floor it is given, not a hard-coded one', () => {
    expect(raiseEveryPodFloor(PODFILE, '16.0')).toContain(
      "config.build_settings['IPHONEOS_DEPLOYMENT_TARGET'] = '16.0'",
    )
  })
})
