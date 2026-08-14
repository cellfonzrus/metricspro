# App assets

These are **placeholder** brand assets (solid `#0B1220`) so the project builds out of the box.
Replace them before submitting to the stores:

| File | Purpose | Recommended size |
|------|---------|------------------|
| `icon.png` | iOS + fallback app icon | 1024×1024, no transparency, no rounded corners |
| `adaptive-icon.png` | Android adaptive icon foreground | 1024×1024, safe zone centered |
| `splash.png` | Launch splash image | ~1284×2778 (portrait), centered logo on `#0B1220` |

The background color and splash are configured in `app.config.ts`. Keep the icon free of the App
Store's disallowed elements (no transparency for iOS, no "beta"/price text).
