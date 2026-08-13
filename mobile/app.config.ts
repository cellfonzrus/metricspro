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
export default ({ config }: ConfigContext): ExpoConfig => ({
  ...config,
  name: 'MetricsPro',
  slug: 'metricspro',
  scheme: 'metricspro',
  version: '0.1.0',
  orientation: 'portrait',
  icon: './assets/icon.png',
  userInterfaceStyle: 'automatic',
  newArchEnabled: true,
  splash: {
    image: './assets/splash.png',
    resizeMode: 'contain',
    backgroundColor: '#0B1220',
  },
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
        'MetricsPro uses the camera to capture a verification selfie when you clock in.',
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
  ],
  experiments: { typedRoutes: true },
  extra: {
    supabaseUrl: process.env.EXPO_PUBLIC_SUPABASE_URL ?? '',
    supabaseAnonKey: process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY ?? '',
    apiUrl: process.env.EXPO_PUBLIC_API_URL ?? '',
    eas: { projectId: process.env.EAS_PROJECT_ID ?? '' },
  },
})
