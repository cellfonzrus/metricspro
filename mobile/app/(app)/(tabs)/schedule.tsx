import React, { useMemo, useState } from 'react'
import { Modal, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { useAuth } from '@/auth/AuthContext'
import { getTenantSettings } from '@/api/core'
import {
  getShifts, getShiftSwaps, getTimeOff, requestShiftSwap, requestTimeOff,
  type Shift, type ShiftSwap, type TimeOffRequest,
} from '@/api/schedule'
import {
  dayLabel, daysOf, isCurrentWeek, shiftWeek, startDowOf, timeRange, weekLabel, weekOf,
  type Week,
} from '@/lib/schedule-week'
import {
  Badge, Body, Button, Card, EmptyState, ErrorView, H2, Input, Loading, Screen,
} from '@/components/ui'
import { colors, font, radius, spacing } from '@/theme'

// ── My Schedule (phone) ──────────────────────────────────────────────────────────────────────────
// The rota a rep actually needs on a shift: which days they work this week, when, and where — plus
// the two things they ask a manager for by text today (time off, and somebody to cover a shift).
//
// WHOSE SHIFTS THIS SHOWS IS THE SERVER'S ANSWER, NOT THIS SCREEN'S. `GET /storeops/shifts` runs
// `gate_person_rows` (§14w): a rep sees their own week wherever they worked it, a manager theirs plus
// the people under them. So this screen renders what it is given. The "Just me / Everyone" toggle is
// a VIEW over an already-permitted response — it is never the thing keeping anyone's shifts private,
// and it only appears when the response actually contains other people.
//
// THE WEEK IS THE TENANT'S WEEK. `work_week_start_dow` is read from `GET /core/tenant-settings` —
// the same declaration, from the same endpoint, that the web schedule page reads. One tenant here
// starts its week on Thursday, so a hardcoded Monday would render real shifts under the wrong dates
// and look entirely plausible doing it (owner directive 2026-10-30: a tenant's own declaration is
// never a default with house words behind it). `schedule-week.weekOf` therefore REQUIRES the dow.
//
// WHAT IT DOES NOT DO: it does not edit the rota. Publishing, assigning and approving stay on the
// web, where the manager tools and their guards already live; a second write path for a shift would
// be a second set of rules about who may change one.

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      <View style={styles.rowValue}>{children}</View>
    </View>
  )
}

const STATUS_COLOR: Record<string, string> = {
  approved: colors.success,
  pending: colors.warning,
  denied: colors.danger,
  cancelled: colors.textDim,
  voided: colors.textDim,
}
const statusColor = (s: unknown) => STATUS_COLOR[String(s || '').toLowerCase()] ?? colors.textDim

function ShiftCard({ shift, showWho }: { shift: Shift; showWho: boolean }) {
  const times = timeRange(shift.start_time, shift.end_time)
  const hours = Number(shift.scheduled_hours ?? 0)
  return (
    <View style={styles.shift}>
      <View style={{ flex: 1 }}>
        <Text style={styles.shiftTime}>{times ?? 'Time not set'}</Text>
        <Text style={styles.shiftMeta}>
          {shift.store_code || 'No store'}
          {shift.role ? ` · ${shift.role}` : ''}
          {showWho && shift.employee_name ? ` · ${shift.employee_name}` : ''}
        </Text>
        {shift.notes ? <Text style={styles.shiftNote}>{String(shift.notes)}</Text> : null}
      </View>
      {hours > 0 ? <Text style={styles.shiftHours}>{hours.toFixed(hours % 1 ? 1 : 0)}h</Text> : null}
    </View>
  )
}

export default function SchedulePage() {
  const { me } = useAuth()
  const myId = String(me?.user?.employee_id || '')
  const qc = useQueryClient()

  // The tenant's own week start. Until it answers we do not guess a week: a rota rendered under the
  // wrong seven dates is worse than one that is still loading, because it looks correct.
  const settings = useQuery({ queryKey: ['tenant-settings'], queryFn: getTenantSettings })
  const startDow = startDowOf(settings.data?.settings?.work_week_start_dow)

  const [offset, setOffset] = useState(0)
  const week: Week | null = useMemo(
    () => (settings.isSuccess || settings.isError ? shiftWeek(weekOf(startDow), offset) : null),
    [settings.isSuccess, settings.isError, startDow, offset],
  )

  const shifts = useQuery({
    queryKey: ['schedule', 'shifts', week?.start, week?.end],
    queryFn: () => getShifts(week!.start, week!.end),
    enabled: !!week,
  })
  const timeOff = useQuery({ queryKey: ['schedule', 'time-off'], queryFn: getTimeOff })
  const swaps = useQuery({ queryKey: ['schedule', 'swaps'], queryFn: getShiftSwaps })

  const [mineOnly, setMineOnly] = useState(true)
  const all = shifts.data ?? []
  // Only offer the toggle when the permitted response really does contain somebody else — otherwise
  // it is a control that does nothing, which reads as though shifts are being hidden.
  const hasOthers = useMemo(
    () => !!myId && all.some((s) => s.employee_id && String(s.employee_id) !== myId),
    [all, myId],
  )
  const shown = mineOnly && myId && hasOthers
    ? all.filter((s) => String(s.employee_id || '') === myId)
    : all

  const byDay = useMemo(() => {
    const map: Record<string, Shift[]> = {}
    for (const s of shown) {
      const d = String(s.shift_date || '').slice(0, 10)
      if (!d) continue
      ;(map[d] ||= []).push(s)
    }
    for (const list of Object.values(map)) {
      list.sort((a, b) => String(a.start_time || '').localeCompare(String(b.start_time || '')))
    }
    return map
  }, [shown])

  const weekHours = shown.reduce((t, s) => t + Number(s.scheduled_hours ?? 0), 0)

  // ── asks ───────────────────────────────────────────────────────────────────────────────────────
  const [askOff, setAskOff] = useState(false)
  const [offFrom, setOffFrom] = useState('')
  const [offTo, setOffTo] = useState('')
  const [offReason, setOffReason] = useState('')
  const [askSwap, setAskSwap] = useState<Shift | null>(null)
  const [swapNote, setSwapNote] = useState('')
  const [problem, setProblem] = useState('')

  const offMutation = useMutation({
    mutationFn: () =>
      requestTimeOff({ employee_id: myId, start_date: offFrom, end_date: offTo, reason: offReason }),
    onSuccess: () => {
      setAskOff(false)
      setOffFrom(''); setOffTo(''); setOffReason(''); setProblem('')
      qc.invalidateQueries({ queryKey: ['schedule', 'time-off'] })
    },
    onError: (e: unknown) => setProblem(e instanceof Error ? e.message : 'Could not send the request.'),
  })

  const swapMutation = useMutation({
    mutationFn: () =>
      requestShiftSwap({ requester_id: myId, shift_id: String(askSwap?.id || ''), notes: swapNote }),
    onSuccess: () => {
      setAskSwap(null); setSwapNote(''); setProblem('')
      qc.invalidateQueries({ queryKey: ['schedule', 'swaps'] })
    },
    onError: (e: unknown) => setProblem(e instanceof Error ? e.message : 'Could not send the request.'),
  })

  const DATE = /^\d{4}-\d{2}-\d{2}$/
  const offValid = DATE.test(offFrom) && DATE.test(offTo) && offFrom <= offTo && !!myId

  if (settings.isLoading) return <Screen><Loading label="Loading your week…" /></Screen>
  if (shifts.isError && !shifts.data)
    return (
      <Screen>
        <ErrorView
          message={shifts.error instanceof Error ? shifts.error.message : 'Could not load your schedule.'}
          onRetry={() => shifts.refetch()}
        />
      </Screen>
    )

  const refreshing = shifts.isFetching || timeOff.isFetching || swaps.isFetching

  return (
    <Screen>
      <ScrollView
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => { shifts.refetch(); timeOff.refetch(); swaps.refetch() }}
            tintColor={colors.primary}
          />
        }
      >
        {/* week pager */}
        <Card>
          <View style={styles.pager}>
            <Pressable onPress={() => setOffset((n) => n - 1)} style={styles.pagerBtn} hitSlop={8}>
              <Text style={styles.pagerGlyph}>‹</Text>
            </Pressable>
            <View style={{ alignItems: 'center', flex: 1 }}>
              <Text style={styles.weekLabel}>{week ? weekLabel(week) : '—'}</Text>
              <Text style={styles.weekSub}>
                {week && isCurrentWeek(startDow, week) ? 'This week' : 'Week of ' + (week?.start ?? '')}
                {weekHours > 0 ? ` · ${weekHours.toFixed(weekHours % 1 ? 1 : 0)}h scheduled` : ''}
              </Text>
            </View>
            <Pressable onPress={() => setOffset((n) => n + 1)} style={styles.pagerBtn} hitSlop={8}>
              <Text style={styles.pagerGlyph}>›</Text>
            </Pressable>
          </View>
          {offset !== 0 ? (
            <Pressable onPress={() => setOffset(0)}>
              <Text style={styles.link}>Back to this week</Text>
            </Pressable>
          ) : null}
          {hasOthers ? (
            <View style={styles.toggleRow}>
              {([true, false] as const).map((v) => (
                <Pressable
                  key={String(v)}
                  onPress={() => setMineOnly(v)}
                  style={[styles.toggle, mineOnly === v && styles.toggleOn]}
                >
                  <Text style={[styles.toggleText, mineOnly === v && styles.toggleTextOn]}>
                    {v ? 'Just me' : 'My team'}
                  </Text>
                </Pressable>
              ))}
            </View>
          ) : null}
        </Card>

        {/* the week, day by day */}
        {shifts.isLoading && !shifts.data ? (
          <Loading label="Loading shifts…" />
        ) : (
          <Card>
            <H2>{mineOnly && hasOthers ? 'Your week' : 'The week'}</H2>
            {week && daysOf(week).map((day) => {
              const list = byDay[day] ?? []
              return (
                <View key={day} style={styles.day}>
                  <View style={styles.dayHead}>
                    <Text style={styles.dayName}>{dayLabel(day)}</Text>
                    <Text style={styles.dayDate}>{day.slice(5).replace('-', '/')}</Text>
                  </View>
                  {list.length === 0 ? (
                    <Text style={styles.off}>Not scheduled</Text>
                  ) : (
                    list.map((s, i) => (
                      <View key={String(s.id ?? i)}>
                        <ShiftCard shift={s} showWho={!mineOnly || !hasOthers} />
                        {String(s.employee_id || '') === myId && s.id ? (
                          <Pressable onPress={() => { setAskSwap(s); setProblem('') }}>
                            <Text style={styles.linkSmall}>Ask for cover</Text>
                          </Pressable>
                        ) : null}
                      </View>
                    ))
                  )}
                </View>
              )
            })}
            {shown.length === 0 ? (
              <EmptyState
                title="No shifts this week"
                subtitle="If that looks wrong, your manager publishes the rota on the web."
              />
            ) : null}
          </Card>
        )}

        {/* time off */}
        <Card>
          <View style={styles.cardHead}>
            <H2>Time off</H2>
            <Pressable onPress={() => { setAskOff(true); setProblem('') }} hitSlop={8}>
              <Text style={styles.link}>Request</Text>
            </Pressable>
          </View>
          {(timeOff.data ?? []).length === 0 ? (
            <Body dim>No time-off requests.</Body>
          ) : (
            (timeOff.data ?? []).slice(0, 8).map((r: TimeOffRequest, i) => (
              <Row key={String(r.id ?? i)} label={`${r.start_date ?? '?'} → ${r.end_date ?? '?'}`}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.xs }}>
                  <Badge label={String(r.status ?? 'pending')} color={statusColor(r.status)} />
                  {r.employee_name && String(r.employee_id || '') !== myId ? (
                    <Text style={styles.rowWho}>{r.employee_name}</Text>
                  ) : null}
                </View>
              </Row>
            ))
          )}
        </Card>

        {/* swaps */}
        <Card>
          <H2>Cover requests</H2>
          {(swaps.data ?? []).length === 0 ? (
            <Body dim>No cover requests.</Body>
          ) : (
            (swaps.data ?? []).slice(0, 8).map((s: ShiftSwap, i) => (
              <Row key={String(s.id ?? i)} label={String(s.requester_name || s.requester_id || '—')}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.xs }}>
                  <Badge label={String(s.status ?? 'pending')} color={statusColor(s.status)} />
                  {s.target_name ? <Text style={styles.rowWho}>→ {s.target_name}</Text> : null}
                </View>
              </Row>
            ))
          )}
          <Body dim>A manager approves a swap on the web — this asks, it does not move the shift.</Body>
        </Card>
      </ScrollView>

      {/* ── request time off ── */}
      <Modal visible={askOff} animationType="slide" transparent onRequestClose={() => setAskOff(false)}>
        <View style={styles.sheetWrap}>
          <View style={styles.sheet}>
            <H2>Request time off</H2>
            <Body dim>Dates as YYYY-MM-DD. Your manager approves it on the web.</Body>
            <Input placeholder="From (2026-11-10)" value={offFrom} onChangeText={setOffFrom}
                   autoCapitalize="none" placeholderTextColor={colors.textDim} />
            <Input placeholder="To (2026-11-12)" value={offTo} onChangeText={setOffTo}
                   autoCapitalize="none" placeholderTextColor={colors.textDim} />
            <Input placeholder="Reason (optional)" value={offReason} onChangeText={setOffReason}
                   placeholderTextColor={colors.textDim} />
            {!myId ? (
              <Text style={styles.problem}>
                Your login is not linked to an employee record yet, so a request cannot be filed
                under your name. Ask your manager to link it.
              </Text>
            ) : null}
            {offFrom && offTo && offFrom > offTo ? (
              <Text style={styles.problem}>The first date must not be after the second.</Text>
            ) : null}
            {problem ? <Text style={styles.problem}>{problem}</Text> : null}
            <View style={styles.sheetBtns}>
              <Button title="Cancel" variant="secondary" onPress={() => setAskOff(false)} />
              <Button
                title={offMutation.isPending ? 'Sending…' : 'Send request'}
                onPress={() => offMutation.mutate()}
                disabled={!offValid || offMutation.isPending}
              />
            </View>
          </View>
        </View>
      </Modal>

      {/* ── ask for cover ── */}
      <Modal visible={!!askSwap} animationType="slide" transparent onRequestClose={() => setAskSwap(null)}>
        <View style={styles.sheetWrap}>
          <View style={styles.sheet}>
            <H2>Ask for cover</H2>
            <Body dim>
              {askSwap
                ? `${askSwap.shift_date ?? ''} ${timeRange(askSwap.start_time, askSwap.end_time) ?? ''} · ${askSwap.store_code ?? ''}`
                : ''}
            </Body>
            <Input placeholder="Why, or who you have in mind (optional)" value={swapNote}
                   onChangeText={setSwapNote} placeholderTextColor={colors.textDim} />
            {problem ? <Text style={styles.problem}>{problem}</Text> : null}
            <View style={styles.sheetBtns}>
              <Button title="Cancel" variant="secondary" onPress={() => setAskSwap(null)} />
              <Button
                title={swapMutation.isPending ? 'Sending…' : 'Send request'}
                onPress={() => swapMutation.mutate()}
                disabled={!myId || swapMutation.isPending}
              />
            </View>
          </View>
        </View>
      </Modal>
    </Screen>
  )
}

const styles = StyleSheet.create({
  pager: { flexDirection: 'row', alignItems: 'center' },
  pagerBtn: { paddingHorizontal: spacing.md, paddingVertical: spacing.xs },
  pagerGlyph: { color: colors.primary, fontSize: 28, lineHeight: 30 },
  weekLabel: { color: colors.text, fontSize: font.h3, fontWeight: '700' },
  weekSub: { color: colors.textDim, fontSize: font.tiny, marginTop: 2 },
  link: { color: colors.primary, fontSize: font.small, fontWeight: '600', marginTop: spacing.xs },
  linkSmall: { color: colors.primary, fontSize: font.tiny, fontWeight: '600', marginTop: 2 },
  toggleRow: { flexDirection: 'row', gap: spacing.xs, marginTop: spacing.sm },
  toggle: {
    paddingHorizontal: spacing.md, paddingVertical: spacing.xs,
    borderRadius: radius.sm, borderWidth: 1, borderColor: colors.border,
  },
  toggleOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  toggleText: { color: colors.textDim, fontSize: font.tiny, fontWeight: '600' },
  toggleTextOn: { color: colors.surface },
  day: { borderTopWidth: 1, borderTopColor: colors.border, paddingTop: spacing.sm, marginTop: spacing.sm },
  dayHead: { flexDirection: 'row', justifyContent: 'space-between' },
  dayName: { color: colors.text, fontSize: font.small, fontWeight: '700' },
  dayDate: { color: colors.textDim, fontSize: font.tiny },
  off: { color: colors.textDim, fontSize: font.tiny, marginTop: 2 },
  shift: { flexDirection: 'row', alignItems: 'center', marginTop: spacing.xs },
  shiftTime: { color: colors.text, fontSize: font.body, fontWeight: '600' },
  shiftMeta: { color: colors.textDim, fontSize: font.tiny, marginTop: 1 },
  shiftNote: { color: colors.textDim, fontSize: font.tiny, fontStyle: 'italic', marginTop: 1 },
  shiftHours: { color: colors.text, fontSize: font.small, fontWeight: '700' },
  cardHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  row: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingVertical: spacing.xs,
  },
  rowLabel: { color: colors.text, fontSize: font.small, flex: 1 },
  rowValue: { alignItems: 'flex-end' },
  rowWho: { color: colors.textDim, fontSize: font.tiny },
  sheetWrap: { flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(0,0,0,0.5)' },
  sheet: {
    backgroundColor: colors.surface, padding: spacing.lg,
    borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg, gap: spacing.sm,
  },
  sheetBtns: { flexDirection: 'row', justifyContent: 'flex-end', gap: spacing.sm, marginTop: spacing.sm },
  problem: { color: colors.danger, fontSize: font.tiny },
})
