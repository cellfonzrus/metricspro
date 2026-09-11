# Mobile OTA updates (EAS Update)

The app is wired for over-the-air updates so JS/asset-only changes (a new dashboard, a copy fix)
reach installed apps without a store submission. Native changes still need a full build.

## How it's configured

- **`mobile/app.config.ts`** — `updates.url = https://u.expo.dev/<projectId>` and
  `runtimeVersion: { policy: 'appVersion' }`. The runtime version is the compatibility key: an OTA
  only lands on a build whose runtime matches, so bumping `version` (a native release) correctly
  stops old binaries from pulling incompatible JS.
- **`mobile/eas.json`** — each build profile declares a `channel`: `development`, `preview`,
  `production`. A build stamped with a channel pulls updates published to the matching branch.
- **`mobile/package.json`** — `expo-updates` dependency + `update:preview` / `update:production`
  scripts.

## One-time enablement (per profile)

OTA only reaches a binary that was **built with `expo-updates` embedded**. The builds currently on
devices predate this, so they cannot receive OTA — do one fresh build first:

```bash
cd mobile
npx expo install expo-updates      # locks the SDK-matched version into the lockfile
npm run build:ios:prod             # (and/or) build:android:prod  — this binary can receive OTA
```

Install that build. From then on it self-updates from the `production` channel.

## Pushing an OTA update (every time after)

```bash
cd mobile
npm run update:production          # eas update --branch production --message "…"
# or, with a message:
npx eas update --branch production --message "native dashboards + chat"
```

Devices on that channel apply the update on next launch (Expo's default is to fetch on start and
swap in on the following launch).

## When OTA is NOT enough — ship a build instead

- Any native change: a new native module (e.g. adding `expo-updates` itself the first time), a new
  permission, an SDK/`react-native` bump, an icon/splash/native-config change.
- A `version` bump — it changes `runtimeVersion`, so the OTA won't match the old binary by design.

In those cases run `build:*:prod` (or `preview`) and distribute the binary; resume `eas update`
afterward.
