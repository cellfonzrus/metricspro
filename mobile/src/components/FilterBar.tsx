import React, { useState } from 'react'
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native'

import { colors, font, radius, spacing } from '@/theme'
import type { FilterOptions } from '@/api/reports'
import { addDays, presetRange, rangeLabel, type DateRange, type RangePreset } from '@/lib/daterange'

// ── Shared dashboard filter bar ────────────────────────────────────────────────────────────────────
// A date range (quick presets Today / Week / Month, plus a custom from–to) and Store / Market / Rep
// multi-selects, drawn from the org's /core/filter-options. Each dashboard shows only the controls its
// endpoint actually honours (see `show`) so a filter never silently does nothing.

export type FilterState = {
  preset: RangePreset
  range: DateRange
  stores: string[]
  markets: string[]
  reps: string[]
}

export function defaultFilter(preset: RangePreset): FilterState {
  return { preset, range: presetRange(preset === 'custom' ? 'today' : preset), stores: [], markets: [], reps: [] }
}

export function FilterBar({ value, onChange, options, show }: {
  value: FilterState
  onChange: (next: FilterState) => void
  options: FilterOptions | null
  show: { date?: boolean; stores?: boolean; markets?: boolean; reps?: boolean }
}) {
  const setPreset = (p: RangePreset) => {
    if (p === 'custom') onChange({ ...value, preset: 'custom' })
    else onChange({ ...value, preset: p, range: presetRange(p) })
  }
  const stepFrom = (n: number) => onChange({ ...value, preset: 'custom', range: { ...value.range, from: addDays(value.range.from, n) } })
  const stepTo = (n: number) => onChange({ ...value, preset: 'custom', range: { ...value.range, to: addDays(value.range.to, n) } })

  const storeOpts = (options?.stores || []).map((s) => ({ value: s.store, label: s.store, sublabel: s.market || undefined }))
  const marketOpts = (options?.markets || []).map((m) => ({ value: m, label: m }))
  const repOpts = (options?.reps || []).map((r) => ({ value: r.id, label: r.label, sublabel: r.sublabel || undefined }))

  return (
    <View style={styles.wrap}>
      {show.date !== false && (
        <>
          <View style={styles.presetRow}>
            {(['today', 'week', 'month', 'custom'] as RangePreset[]).map((p) => (
              <Pressable key={p} onPress={() => setPreset(p)}
                style={[styles.chip, value.preset === p && styles.chipOn]}>
                <Text style={[styles.chipText, value.preset === p && styles.chipTextOn]}>
                  {p === 'today' ? 'Today' : p === 'week' ? 'Week' : p === 'month' ? 'Month' : 'Custom'}
                </Text>
              </Pressable>
            ))}
          </View>
          {value.preset === 'custom' ? (
            <View style={styles.customRow}>
              <Stepper label="From" value={value.range.from} onStep={stepFrom} />
              <Stepper label="To" value={value.range.to} onStep={stepTo} />
            </View>
          ) : (
            <Text style={styles.rangeHint}>{rangeLabel(value.range)}</Text>
          )}
        </>
      )}

      {(show.stores || show.markets || show.reps) && (
        <View style={styles.pickRow}>
          {show.markets && (
            <MultiPick title="Market" options={marketOpts} selected={value.markets}
              onChange={(markets) => onChange({ ...value, markets })} />
          )}
          {show.stores && (
            <MultiPick title="Store" options={storeOpts} selected={value.stores}
              onChange={(stores) => onChange({ ...value, stores })} />
          )}
          {show.reps && (
            <MultiPick title="Rep" options={repOpts} selected={value.reps}
              onChange={(reps) => onChange({ ...value, reps })} />
          )}
        </View>
      )}
    </View>
  )
}

function Stepper({ label, value, onStep }: { label: string; value: string; onStep: (n: number) => void }) {
  return (
    <View style={styles.stepper}>
      <Text style={styles.stepLabel}>{label}</Text>
      <Pressable onPress={() => onStep(-1)} hitSlop={8} style={styles.stepBtn}><Text style={styles.stepArrow}>◀</Text></Pressable>
      <Text style={styles.stepVal}>{value.slice(5)}</Text>
      <Pressable onPress={() => onStep(1)} hitSlop={8} style={styles.stepBtn}><Text style={styles.stepArrow}>▶</Text></Pressable>
    </View>
  )
}

type Opt = { value: string; label: string; sublabel?: string }

function MultiPick({ title, options, selected, onChange }: {
  title: string; options: Opt[]; selected: string[]; onChange: (next: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  const count = selected.length
  const toggle = (v: string) =>
    onChange(selected.includes(v) ? selected.filter((x) => x !== v) : [...selected, v])

  return (
    <>
      <Pressable style={[styles.pick, count > 0 && styles.pickOn]} onPress={() => setOpen(true)}>
        <Text style={[styles.pickText, count > 0 && styles.pickTextOn]} numberOfLines={1}>
          {title}{count > 0 ? ` · ${count}` : ' · All'}
        </Text>
        <Text style={[styles.pickText, count > 0 && styles.pickTextOn]}>▾</Text>
      </Pressable>

      <Modal visible={open} animationType="slide" transparent onRequestClose={() => setOpen(false)}>
        <Pressable style={styles.backdrop} onPress={() => setOpen(false)}>
          <Pressable style={styles.sheet} onPress={() => {}}>
            <View style={styles.sheetHead}>
              <Text style={styles.sheetTitle}>{title}</Text>
              <Pressable onPress={() => onChange([])}><Text style={styles.clear}>Clear</Text></Pressable>
            </View>
            <ScrollView style={{ maxHeight: 360 }}>
              {options.length === 0 ? (
                <Text style={styles.empty}>No options for your scope.</Text>
              ) : options.map((o) => {
                const on = selected.includes(o.value)
                return (
                  <Pressable key={o.value} style={styles.optRow} onPress={() => toggle(o.value)}>
                    <View style={[styles.box, on && styles.boxOn]}>{on ? <Text style={styles.check}>✓</Text> : null}</View>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.optLabel} numberOfLines={1}>{o.label}</Text>
                      {o.sublabel ? <Text style={styles.optSub} numberOfLines={1}>{o.sublabel}</Text> : null}
                    </View>
                  </Pressable>
                )
              })}
            </ScrollView>
            <Pressable style={styles.doneBtn} onPress={() => setOpen(false)}>
              <Text style={styles.doneText}>Done</Text>
            </Pressable>
          </Pressable>
        </Pressable>
      </Modal>
    </>
  )
}

const styles = StyleSheet.create({
  wrap: { gap: spacing.sm },
  presetRow: { flexDirection: 'row', gap: spacing.sm },
  chip: {
    flex: 1, alignItems: 'center', paddingVertical: spacing.sm, borderRadius: radius.pill,
    backgroundColor: colors.surfaceAlt, borderWidth: StyleSheet.hairlineWidth, borderColor: colors.border,
  },
  chipOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { color: colors.textDim, fontSize: font.small, fontWeight: '700' },
  chipTextOn: { color: colors.primaryText },
  rangeHint: { color: colors.textDim, fontSize: font.small, textAlign: 'center' },
  customRow: { flexDirection: 'row', gap: spacing.sm },
  stepper: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.xs,
    backgroundColor: colors.surfaceAlt, borderRadius: radius.md, borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border, paddingHorizontal: spacing.sm, paddingVertical: 6,
  },
  stepLabel: { color: colors.textDim, fontSize: font.tiny },
  stepBtn: { padding: 2 },
  stepArrow: { color: colors.primary, fontSize: font.small, fontWeight: '800' },
  stepVal: { color: colors.text, fontSize: font.small, fontWeight: '700' },
  pickRow: { flexDirection: 'row', gap: spacing.sm, flexWrap: 'wrap' },
  pick: {
    flexGrow: 1, flexBasis: '30%', flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    gap: 4, backgroundColor: colors.surfaceAlt, borderRadius: radius.md, borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border, paddingHorizontal: spacing.sm, paddingVertical: 8,
  },
  pickOn: { borderColor: colors.primary },
  pickText: { color: colors.textDim, fontSize: font.small, fontWeight: '600' },
  pickTextOn: { color: colors.primary, fontWeight: '800' },
  backdrop: { flex: 1, backgroundColor: '#0008', justifyContent: 'flex-end' },
  sheet: {
    backgroundColor: colors.surface, borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg,
    padding: spacing.lg, gap: spacing.sm,
  },
  sheetHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  sheetTitle: { color: colors.text, fontSize: font.h3, fontWeight: '800' },
  clear: { color: colors.primary, fontSize: font.small, fontWeight: '700' },
  empty: { color: colors.textDim, fontSize: font.small, padding: spacing.lg, textAlign: 'center' },
  optRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.md, paddingVertical: spacing.sm },
  box: {
    width: 22, height: 22, borderRadius: 6, borderWidth: 1.5, borderColor: colors.border,
    alignItems: 'center', justifyContent: 'center',
  },
  boxOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  check: { color: colors.primaryText, fontSize: 13, fontWeight: '900' },
  optLabel: { color: colors.text, fontSize: font.body },
  optSub: { color: colors.textDim, fontSize: font.tiny },
  doneBtn: {
    backgroundColor: colors.primary, borderRadius: radius.md, paddingVertical: spacing.md,
    alignItems: 'center', marginTop: spacing.xs,
  },
  doneText: { color: colors.primaryText, fontSize: font.body, fontWeight: '800' },
})
