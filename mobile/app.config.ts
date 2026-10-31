import type { ExpoConfig, ConfigContext } from 'expo/config'

// ── App configuration ────────────────────────────────────────────────────────────────────────────
// Secrets never live here. The Supabase URL + ANON (publishable) key and the API base URL are read
// from environment variables at build time and exposed under `extra` (see src/config/env.ts). The
// anon key is a *public* client key by design (Row Level Security + the backend's token verification
// are the real trust boundary), so shipping it in the bundle is expected — but the service-role key
// must NEVER appear in this app.
//
// Provide these via EAS build env vars (eas.json `env`) or a local `.env` consumed by your shell:
//   EXPO_PUBLIC_SUPABASE_URL, EXPO_PUBLIC_SUPABASE_ANON_KEY, EXPO_PUBLIC_API_URL
// EAS project id — the anchor for both build attribution and the OTA (EAS Update) URL below.
const projectId = process.env.EAS_PROJECT_ID || 'a3ab84a6-1706-473b-9046-f66dbf2c8b9a'

export default ({ config }: ConfigContext): ExpoConfig => ({
  ...config,
  name: 'MetricsPro',
  slug: 'metricspro',
  owner: 'cellfonzrus',
  scheme: 'metricspro',
  version: '0.1.0',
  orientation: 'portrait',
  icon: './assets/icon.png',
  userInterfaceStyle: 'automatic',
  // ── OTA updates (EAS Update) ────────────────────────────────────────────────────────────────
  // Lets JS/asset-only changes (like a new dashboard screen) reach installed apps without a store
  // build: `eas update --branch <channel>` publishes, and a build stamped with the matching channel
  // (see eas.json) pulls it on next launch. `runtimeVersion` is the compatibility key — an OTA only
  // lands on a build whose runtime matches, so a NATIVE change (new module, permission, SDK bump)
  // must ship as a fresh build, never OTA. Policy 'appVersion' ties the runtime to `version` above,
  // so bumping the app version correctly invalidates OTA against the old native binary.
  runtimeVersion: { policy: 'appVersion' },
  updates: { url: `https://u.expo.dev/${projectId}` },
  // The New Architecture is the default (and only) architecture from React Native 0.81+ / Expo SDK
  // 54+, so there is no `newArchEnabled` flag to set — the old-arch opt-out no longer exists.
  // The splash screen is configured through the expo-splash-screen config plugin below (the
  // top-level `splash` field was removed in SDK 54); keeping only the plugin avoids a duplicate.
  assetBundlePatterns: ['**/*'],
  ios: {
    supportsTablet: true,
    bundleIdentifier: 'com.metricspro.app',
    buildNumber: '1',
    // The app talks only to your own HTTPS backend + Supabase — keep ATS on (no arbitrary loads).
    infoPlist: {
      NSFaceIDUsageDescription:
        'MetricsPro uses Face ID to unlock the app and protect store, sales and payroll data.',
      NSLocationWhenInUseUsageDescription:
        'MetricsPro records the store location of a clock-in punch for attendance verification.',
      NSCameraUsageDescription:
        'MetricsPro uses the camera to capture a verification selfie when you clock in and to photograph a receipt for import.',
      NSPhotoLibraryUsageDescription:
        'MetricsPro lets you choose a receipt photo from your library to import it as a sale.',
      ITSAppUsesNonExemptEncryption: false,
    },
  },
  android: {
    package: 'com.metricspro.app',
    versionCode: 1,
    adaptiveIcon: {
      foregroundImage: './assets/adaptive-icon.png',
      backgroundColor: '#0B1220',
    },
    permissions: [
      'ACCESS_FINE_LOCATION',
      'ACCESS_COARSE_LOCATION',
      'CAMERA',
      'USE_BIOMETRIC',
      'USE_FINGERPRINT',
    ],
  },
  plugins: [
    // ── The minimum iOS this app declares ─────────────────────────────────────────────────────
    // REQUIRED, and its absence is what the first real build from GitHub actually found
    // (2026-10-31, EAS build d4d6b18d): EAS now builds on a current Xcode, which refuses any pod
    // declaring an iOS deployment target below 15.0 — and this app's dependency set still carries
    // `RNCAsyncStorage` at 9.0, `ReachabilitySwift` at 12.0 and `SDWebImage` at 9.0. The build died
    // in fastlane with "the range of supported deployment target versions is 15.0 to 27.1.x".
    //
    // Setting it here raises the floor for the app AND, through Expo's Podfile post-install, for
    // every pod, which is the one lever that reaches dependencies we do not own. It is PINNED by
    // harness_running_bundle_lock.py (R6.4) so it cannot silently drop back.
    //
    // THIS IS NOT THE WHOLE FIX. The underlying fact is that this app is pinned to Expo SDK 52
    // while the binary in TestFlight is SDK 57 and EAS's image has moved on — see index §CORE-29.3.
    // Raising the floor lets SDK 52 compile again on today's Xcode; it does not stop the next Xcode
    // from moving past it. The design fix is the SDK upgrade, and this buys the time to do it
    // properly rather than under an outage.
    ['expo-build-properties', { ios: { deploymentTarget: '15.1' } }],
    // And the half `expo-build-properties` does NOT reach. Setting `ios.deploymentTarget` raises the
    // Podfile's platform and the APP target; it does not touch the targets CocoaPods GENERATES for
    // each pod and each pod's resource bundle, which inherit the pod's own declared minimum. Three
    // of this app's dependencies still declare iOS 9 or 12, and the second build failed on exactly
    // those three WITH the line above already in place (EAS d34458a2). A current Expo Podfile
    // rewrites them itself; SDK 52's does not. See plugins/with-pod-deployment-floor.js, and delete
    // it along with this line when the app is upgraded off SDK 52 (index §CORE-29.3b).
    ['./plugins/with-pod-deployment-floor', { floor: '15.1' }],
    'expo-router',
    'expo-secure-store',
    'expo-local-authentication',
    [
      'expo-location',
      {
        locationWhenInUsePermission:
          'MetricsPro records the store location of a clock-in punch for attendance verification.',
      },
    ],
    [
      'expo-splash-screen',
      { backgroundColor: '#0B1220', image: './assets/splash.png', imageWidth: 200 },
    ],
    [
      'expo-image-picker',
      {
        photosPermission:
          'MetricsPro lets you choose a receipt photo from your library to import it as a sale.',
        cameraPermission:
          'MetricsPro uses the camera to photograph a receipt for import.',
      },
    ],
  ],
  experiments: { typedRoutes: true },
  extra: {
    supabaseUrl: process.env.EXPO_PUBLIC_SUPABASE_URL ?? '',
    supabaseAnonKey: process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY ?? '',
    apiUrl: process.env.EXPO_PUBLIC_API_URL ?? '',
    eas: { projectId },
  },
})
