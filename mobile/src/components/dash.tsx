import React from 'react'
import { Pressable, StyleSheet, Text, View, type ViewStyle } from 'react-native'

import { colors, font, radius, spacing } from '@/theme'
import { periodLabel, nextPeriod, prevPeriod, isCurrentPeriod } from '@/lib/period'

// ── Dashboard UI kit ─────────────────────────────────────────────────────────────────────────────
// Small, chart-library-free primitives shared by every native dashboard: a KPI tile, a KPI grid, a
// labelled stat row, and a proportional bar row (our "chart" — a filled View, no dependency). Keeps
// the six dashboard screens short and visually consistent.

export type Tone = 'default' | 'good' | 'warn' | 'bad' | 'info'

function toneColor(tone: Tone): string {
  return tone === 'good'
    ? colors.success
    : tone === 'warn'
      ? colors.warning
      : tone === 'bad'
        ? colors.danger
        : tone === 'info'
          ? colors.primary
          : colors.text
}

export function Kpi({ label, value, sub, tone = 'default', style }: {
  label: string
  value: string
  sub?: string
  tone?: Tone
  style?: ViewStyle
}) {
  return (
    <View style={[styles.kpi, style]}>
      <Text style={styles.kpiLabel} numberOfLines={2}>{label}</Text>
      <Text style={[styles.kpiValue, { color: toneColor(tone) }]} numberOfLines={1} adjustsFontSizeToFit>
        {value}
      </Text>
      {sub ? <Text style={styles.kpiSub} numberOfLines={1}>{sub}</Text> : null}
    </View>
  )
}

export function KpiGrid({ children }: { children: React.ReactNode }) {
  return <View style={styles.grid}>{children}</View>
}

/** A labelled numeric row (e.g. "Gross profit    $12,340"), optionally emphasised. */
export function StatRow({ label, value, tone = 'default', strong }: {
  label: string
  value: string
  tone?: Tone
  strong?: boolean
}) {
  return (
    <View style={styles.statRow}>
      <Text style={[styles.statLabel, strong && styles.strong]} numberOfLines={1}>{label}</Text>
      <Text style={[styles.statValue, strong && styles.strong, { color: toneColor(tone) }]}>{value}</Text>
    </View>
  )
}

/** A ranked row with a proportional bar underneath — the app's dependency-free "bar chart". */
export function BarRow({ label, value, valueText, max, tone = 'info', right }: {
  label: string
  value: number
  valueText: string
  max: number
  tone?: Tone
  right?: string
}) {
  const frac = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0
  return (
    <View style={styles.barRow}>
      <View style={styles.barTop}>
        <Text style={styles.barLabel} numberOfLines={1}>{label}</Text>
        <Text style={styles.barValue}>{valueText}{right ? `  ·  ${right}` : ''}</Text>
      </View>
      <View style={styles.barTrack}>
        <View style={[styles.barFill, { width: `${frac * 100}%`, backgroundColor: toneColor(tone) }]} />
      </View>
    </View>
  )
}

export function SectionTitle({ children }: { children: React.ReactNode }) {
  return <Text style={styles.section}>{children}</Text>
}

/** ◀  September 2026  ▶ — steps the reporting period; the ▶ is disabled at the current month. */
export function PeriodSwitcher({ period, onChange }: {
  period: string
  onChange: (p: string) => void
}) {
  const atNow = isCurrentPeriod(period)
  return (
    <View style={styles.period}>
      <Pressable onPress={() => onChange(prevPeriod(period))} style={styles.periodBtn} hitSlop={8}>
        <Text style={styles.periodArrow}>◀</Text>
      </Pressable>
      <Text style={styles.periodLabel}>{periodLabel(period)}</Text>
      <Pressable
        onPress={() => !atNow && onChange(nextPeriod(period))}
        style={styles.periodBtn}
        hitSlop={8}
        disabled={atNow}
      >
        <Text style={[styles.periodArrow, atNow && { opacity: 0.25 }]}>▶</Text>
      </Pressable>
    </View>
  )
}

const styles = StyleSheet.create({
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.md },
  kpi: {
    flexGrow: 1,
    flexBasis: '46%',
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    padding: spacing.lg,
    gap: 2,
  },
  kpiLabel: { color: colors.textDim, fontSize: font.small },
  kpiValue: { fontSize: font.h2, fontWeight: '800' },
  kpiSub: { color: colors.textDim, fontSize: font.tiny },
  statRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: spacing.sm,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
    gap: spacing.md,
  },
  statLabel: { color: colors.textDim, fontSize: font.body, flex: 1 },
  statValue: { color: colors.text, fontSize: font.body, fontWeight: '700' },
  strong: { color: colors.text, fontWeight: '800' },
  barRow: { gap: 6, marginBottom: spacing.md },
  barTop: { flexDirection: 'row', justifyContent: 'space-between', gap: spacing.sm },
  barLabel: { color: colors.text, fontSize: font.small, flex: 1 },
  barValue: { color: colors.textDim, fontSize: font.small, fontWeight: '600' },
  barTrack: { height: 8, borderRadius: radius.pill, backgroundColor: colors.surfaceAlt, overflow: 'hidden' },
  barFill: { height: '100%', borderRadius: radius.pill },
  section: { color: colors.text, fontSize: font.h3, fontWeight: '700', marginTop: spacing.sm },
  period: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.lg,
    paddingVertical: spacing.xs,
  },
  periodBtn: { padding: spacing.sm },
  periodArrow: { color: colors.primary, fontSize: font.h3, fontWeight: '800' },
  periodLabel: { color: colors.text, fontSize: font.body, fontWeight: '700', minWidth: 140, textAlign: 'center' },
})
