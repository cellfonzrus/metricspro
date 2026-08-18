# Getting MetricsPro onto phones — step by step

This guide takes you from nothing to the app running on a real phone. There are two routes:

- **Route A — Quick test (Expo Go):** see the app on your own phone in ~15 minutes, no developer
  accounts, no build. Use this first to confirm everything works.
- **Route B — Real install (EAS builds):** produce actual installable apps for your staff and the
  stores (Android APK / iOS TestFlight). Use this once Route A looks good.

> The app is a **front-end only**. It talks to the existing FastAPI backend + Supabase. If those
> aren't deployed and reachable over HTTPS, the app opens but sign-in fails.

---

## 0. What you need first

**On your computer (install once):**

| Tool | Where | Notes |
|------|-------|-------|
| Node.js 20+ | <https://nodejs.org> | pick the "LTS" version |
| Git | <https://git-scm.com> | |
| EAS CLI | run `npm install -g eas-cli` | Expo's build tool (needed for Route B) |
| Expo account | <https://expo.dev> | free; needed for Route B |

**Three config values** (the same ones the website uses — get them from your Supabase and Railway
dashboards, or from whoever set up the web app):

- **Supabase URL** — e.g. `https://xxxx.supabase.co`
- **Supabase anon key** — the *public* key (NOT the `service_role` key)
- **API URL** — the Railway backend, e.g. `https://your-api.railway.app`

**For store distribution later:**

- Apple Developer Program — $99/year — <https://developer.apple.com> (iPhone/TestFlight)
- Google Play Developer — $25 one-time — <https://play.google.com/console> (Android)

---

## 1. Get the code and install

```bash
git clone https://github.com/cellfonzrus/metricspro.git
cd metricspro/mobile
npm install
```

## 2. Add your config values

```bash
cp .env.example .env
```

Open `.env` in any text editor and fill in the three values:

```
EXPO_PUBLIC_SUPABASE_URL=https://xxxx.supabase.co
EXPO_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
EXPO_PUBLIC_API_URL=https://your-api.railway.app
```

Save it. (`.env` is git-ignored — never commit it.)

---

## Route A — Quick test on your own phone (Expo Go)

1. On your **phone**, install **Expo Go** (App Store for iPhone, Play Store for Android).
2. On your **computer**, in the `mobile` folder:
   ```bash
   npx expo start
   ```
   A QR code appears.
3. Connect (phone and computer must be on the **same Wi-Fi**):
   - **iPhone:** open the built-in **Camera**, point at the QR code, tap the banner.
   - **Android:** open **Expo Go** → "Scan QR code".
4. The app loads. Sign in with a MetricsPro account and try Time Clock / POS / CRM / Earnings.

> If the QR won't connect (strict Wi-Fi/firewall), run `npx expo start --tunnel` instead.

Expo Go is for **development only** — it's not what you hand to staff. For that, use Route B.

---

## Route B — Real installable app (EAS cloud build)

Expo builds in the cloud, so you can build **iPhone apps without a Mac**.

### One-time project setup

In the `mobile` folder:

```bash
eas login          # sign in to your Expo account
eas init           # links this project to your account, prints a "Project ID"
```

Put the printed Project ID into `.env`:

```
EAS_PROJECT_ID=the-id-it-printed
```

### Where the build gets its config

The build bakes in `EXPO_PUBLIC_*` values. Two options:

- **Simplest:** edit `eas.json` and set the real `EXPO_PUBLIC_API_URL` under the `preview` /
  `production` profiles (replace `https://your-api.railway.app`).
- **Cleaner (recommended):** store them as EAS environment variables so they aren't committed:
  ```bash
  eas env:create --name EXPO_PUBLIC_API_URL --value https://your-api.railway.app --environment production
  eas env:create --name EXPO_PUBLIC_SUPABASE_URL --value https://xxxx.supabase.co --environment production
  eas env:create --name EXPO_PUBLIC_SUPABASE_ANON_KEY --value your-anon-key --environment production
  ```
  (Repeat with `--environment preview` for preview builds.)

### Android — the easy one (direct install)

```bash
eas build --platform android --profile preview
```

- Wait ~10–20 min. EAS gives you a link/QR when done.
- On the Android phone, open the link, download the **APK**, install it (allow "install from unknown
  sources" — normal for internal builds). Done.

### iPhone — via TestFlight

iPhones don't sideload freely, so use Apple's **TestFlight**:

1. Enroll in the **Apple Developer Program** ($99/yr).
2. In **App Store Connect**, create the app (name **MetricsPro**, bundle id **`com.metricspro.app`**).
3. Fill your Apple details into `eas.json` → `submit.production.ios` (Apple ID, App Store Connect app
   id, Team id).
4. Build and upload:
   ```bash
   eas build --platform ios --profile production
   eas submit --platform ios --profile production
   ```
   Expo will prompt for your Apple ID and create the signing certificates automatically.
5. In **App Store Connect → TestFlight**, add testers by email. They install the free **TestFlight**
   app and get MetricsPro there.

---

## 3. Publishing to the stores (public release)

Do these two first (they don't affect testing):

1. **Replace the placeholder app icons** in `mobile/assets/` (see `mobile/assets/README.md` for sizes).
2. Prepare listings: screenshots, a privacy-policy URL (the platform serves `/privacy`), and the
   data-safety / App Privacy answers (auth login + location for attendance; no ad tracking).

Then:

```bash
eas build --platform ios --profile production      && eas submit --platform ios
eas build --platform android --profile production  && eas submit --platform android
```

- **iOS:** submit for review in App Store Connect (usually 1–3 days).
- **Android:** in Play Console, promote from the internal track to production.

---

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| Sign-in fails / "Network unavailable" | `EXPO_PUBLIC_API_URL` wrong or backend unreachable. Open the API URL in a browser to check. |
| "No employee record linked" on Earnings | That login isn't tied to an employee id — an admin sets it in **Roles & Access** on the web app. |
| Expo Go QR won't connect | Phone/computer not on same Wi-Fi, or firewall. Try `npx expo start --tunnel`. |
| App lock / biometrics do nothing | Only work on a real phone with Face ID / fingerprint enrolled — not the simulator. |
| Build fails on `eas init` with a dynamic config | `app.config.ts` reads `EAS_PROJECT_ID` from env; paste the printed id into `.env` (this repo does this on purpose). |
| Running backend on your laptop instead of Railway | Use your computer's LAN IP in `EXPO_PUBLIC_API_URL` (e.g. `http://192.168.1.20:8000`), not `localhost` — `localhost` on the phone means the phone itself. |
