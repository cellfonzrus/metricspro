import { describe, expect, it } from 'vitest'

// eslint-disable-next-line @typescript-eslint/no-var-requires
const plugin = require('./with-pod-deployment-floor.js')
const { raiseEveryPodFloor, countHooks, MARKER, HOOK_OPENER } = plugin as {
  raiseEveryPodFloor: (podfile: string, floor: string) => string
  countHooks: (podfile: string) => number
  MARKER: string
  HOOK_OPENER: string
}

// The shape Expo SDK 52 actually generates, confirmed by running `npx expo prebuild --platform ios`
// in this repository on 2026-10-31. The post_install is INSIDE the target block and already does two
// jobs; a Podfile with a second post_install does not install, which is how EAS 3037ddb8 failed.
const PODFILE = [
  "require File.join(File.dirname(`node --print \"require.resolve('expo/package.json')\"`), 'scripts/autolinking')",
  "platform :ios, '15.1'",
  '',
  "target 'MetricsPro' do",
  '  use_expo_modules!',
  '  post_install do |installer|',
  '    react_native_post_install(',
  '      installer,',
  '      config[:reactNativePath],',
  '    )',
  '  end',
  'end',
].join('\n')

describe('raiseEveryPodFloor', () => {
  it('raises the floor on every pod target', () => {
    const out = raiseEveryPodFloor(PODFILE, '15.1')
    expect(out).toContain('installer.pods_project.targets.each do |target|')
    expect(out).toContain("config.build_settings['IPHONEOS_DEPLOYMENT_TARGET'] = '15.1'")
  })

  it('leaves exactly ONE post_install hook — a second one does not install', () => {
    // The regression. The first version of this plugin appended its own hook and broke the build
    // one phase earlier than the error it fixed.
    expect(countHooks(PODFILE)).toBe(1)
    expect(countHooks(raiseEveryPodFloor(PODFILE, '15.1'))).toBe(1)
  })

  it('injects INSIDE the existing hook, before what was already there', () => {
    const out = raiseEveryPodFloor(PODFILE, '15.1')
    const hookAt = out.indexOf(HOOK_OPENER)
    const loopAt = out.indexOf('installer.pods_project.targets.each')
    const existingAt = out.indexOf('react_native_post_install(')
    const closeAt = out.indexOf('\nend', existingAt)
    expect(hookAt).toBeLessThan(loopAt)
    expect(loopAt).toBeLessThan(closeAt)
    // And what Expo's own hook did must still be there, after ours.
    expect(loopAt).toBeLessThan(existingAt)
  })

  it('keeps every line the Podfile already had', () => {
    const out = raiseEveryPodFloor(PODFILE, '15.1')
    for (const line of PODFILE.split('\n')) expect(out).toContain(line)
  })

  it('only raises a floor, never lowers one', () => {
    // A pod declaring iOS 16 must keep 16: lowering it could break that pod's own code.
    expect(raiseEveryPodFloor(PODFILE, '15.1')).toContain('current.nil? || current.to_f < 15.1')
  })

  it('is idempotent — a second prebuild does not inject twice', () => {
    const once = raiseEveryPodFloor(PODFILE, '15.1')
    const twice = raiseEveryPodFloor(once, '15.1')
    expect(twice).toBe(once)
    expect(twice.split('installer.pods_project.targets.each').length - 1).toBe(1)
  })

  it('leaves the marker so the injection can be found again', () => {
    expect(raiseEveryPodFloor(PODFILE, '15.1')).toContain(MARKER)
  })

  it('uses the floor it is given, not a hard-coded one', () => {
    expect(raiseEveryPodFloor(PODFILE, '16.0')).toContain(
      "config.build_settings['IPHONEOS_DEPLOYMENT_TARGET'] = '16.0'",
    )
  })

  it('THROWS rather than silently shipping a Podfile without the floor', () => {
    // If a future Expo template drops the hook, the build must fail loudly at prebuild instead of
    // producing a Podfile that compiles nothing — the whole point of this plugin existing.
    expect(() => raiseEveryPodFloor("target 'X' do\nend\n", '15.1')).toThrow(/no .*post_install/)
  })
})
