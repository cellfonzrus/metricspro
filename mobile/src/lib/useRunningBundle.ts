// The expo-updates / expo-constants reads for "what code is this app running". The RULE lives in
// ./running-bundle (index §CORE-29) and is proved in CI; this file only gathers the facts, so it is
// the one place that touches the native modules and the only part that cannot be unit-tested.
import Constants from 'expo-constants'
import * as Updates from 'expo-updates'

import { describeRunningBundle, type RunningBundleDescription } from './running-bundle'

function readFacts() {
  // Every one of these can throw or be undefined in Expo Go, in a dev client, or on a build made
  // before expo-updates was added — hence the defensive reads. An absent fact must reach
  // describeRunningBundle as null so it renders as words, never as "undefined".
  const asDate = (value: unknown): Date | null =>
    value instanceof Date && !Number.isNaN(value.getTime()) ? value : null
  const asText = (value: unknown): string | null => {
    const text = String(value ?? '').trim()
    return text === '' ? null : text
  }
  return {
    updatesEnabled: Boolean(Updates.isEnabled),
    isEmbeddedLaunch: Boolean(Updates.isEmbeddedLaunch),
    updateId: asText(Updates.updateId),
    runtimeVersion: asText(Updates.runtimeVersion),
    channel: asText(Updates.channel),
    createdAt: asDate(Updates.createdAt),
    nativeAppVersion: asText(Constants.nativeAppVersion),
    nativeBuildVersion: asText(Constants.nativeBuildVersion),
    bundleSdkVersion: asText(Constants.expoConfig?.sdkVersion),
  }
}

/** The running-code description for display. Dereferences the one home; never re-derives it. */
export function runningBundle(): RunningBundleDescription {
  try {
    return describeRunningBundle(readFacts())
  } catch {
    // A read that throws is itself a fact worth showing, and must not take a screen down with it.
    return describeRunningBundle({
      updatesEnabled: false,
      isEmbeddedLaunch: true,
      updateId: null,
      runtimeVersion: null,
      channel: null,
      createdAt: null,
      nativeAppVersion: null,
      nativeBuildVersion: null,
      bundleSdkVersion: null,
    })
  }
}
