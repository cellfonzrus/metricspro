// ── Design tokens ────────────────────────────────────────────────────────────────────────────────
// A small, dark-first palette matching the platform's splash (#0B1220). Kept as plain constants so
// every screen reads from one source; a future ThemeProvider can swap these for light mode.
export const colors = {
  bg: '#0B1220',
  surface: '#131C2E',
  surfaceAlt: '#1B2740',
  border: '#26324B',
  text: '#E7ECF5',
  textDim: '#93A0B7',
  primary: '#3B82F6',
  primaryText: '#FFFFFF',
  success: '#22C55E',
  warning: '#F59E0B',
  danger: '#EF4444',
  hot: '#EF4444',
  warm: '#F59E0B',
  cold: '#60A5FA',
}

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
}

export const radius = {
  sm: 8,
  md: 12,
  lg: 16,
  pill: 999,
}

export const font = {
  h1: 28,
  h2: 22,
  h3: 18,
  body: 15,
  small: 13,
  tiny: 11,
}
