import React, { useMemo, useState } from 'react'
import { Alert, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native'
import * as Location from 'expo-location'
import { useQuery } from '@tanstack/react-query'

import {
  clockInDurable,
  clockOutDurable,
  getAllowedStores,
  getStatus,
  type ClockInBody,
} from '@/api/timeclock'
import { useOnline } from '@/offline/net'
import { Badge, Body, Button, Card, H2, Loading, Screen } from '@/components/ui'
import { OfflineBanner } from '@/components/OfflineBanner'
import { colors, font, radius, spacing } from '@/theme'

// Cap any promise so a slow GPS/permission call can never hang the clock-in. Resolves to `fallback`
// if `p` doesn't settle within `ms`.
function withTimeout<T>(p: Promise<T>, ms: number, fallback: T): Promise<T> {
  return Promise.race([p, new Promise<T>((resolve) => setTimeout(() => resolve(fallback), ms))])
}

// Best-effort GPS for a clock-in punch (attendance verification). NEVER blocks the punch: a denied
// permission or a slow/indoor fix just means the punch is recorded without coordinates. Previously a
// slow `getCurrentPositionAsync` could hang indefinitely, so tapping "Clock in" looked like nothing
// happened — every step is now time-boxed.
async function tryGetLocation(): Promise<Pick<ClockInBody, 'gps_lat' | 'gps_lng' | 'gps_accuracy_m'>> {
  try {
    const perm = await withTimeout(
      Location.requestForegroundPermissionsAsync(),
      4000,
      { status: 'undetermined' } as Awaited<ReturnType<typeof Location.requestForegroundPermissionsAsync>>,
    )
    if (perm.status !== 'granted') return {}
    const pos = await withTimeout(
      Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced }),
      5000,
      null,
    )
    if (!pos) return {}
    return { gps_lat: pos.coords.latitude, gps_lng: pos.coords.longitude, gps_accuracy_m: pos.coords.accuracy ?? undefined }
  } catch {
    return {}
  }
}

function since(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  const mins = Math.max(0, Math.round((Date.now() - d.getTime()) / 60000))
  const h = Math.floor(mins / 60)
  const m = mins % 60
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

export default function TimeClock() {
  const online = useOnline()
  const [store, setStore] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const status = useQuery({ queryKey: ['timeclock', 'status'], queryFn: getStatus })
  const allowed = useQuery({ queryKey: ['timeclock', 'allowed-stores'], queryFn: getAllowedStores })

  const stores = allowed.data?.stores ?? []
  const selectedStore = store ?? allowed.data?.home_store ?? stores[0] ?? null
  const clockedIn = status.data?.clockedIn
  const entry = status.data?.entry

  const refreshing = status.isFetching || allowed.isFetching
  const refetchAll = () => {
    status.refetch()
    allowed.refetch()
  }

  const doClockIn = async (extra: Partial<ClockInBody> = {}) => {
    if (!selectedStore) {
      Alert.alert('No store', 'No store is available to clock in at. Contact your manager.')
      return
    }
    setBusy(true)
    try {
      const loc = await tryGetLocation()
      const body: ClockInBody = { store_code: selectedStore, device: 'mobile-app', ...loc, ...extra }
      const res = await clockInDurable(body)
      if ('queued' in res) {
        Alert.alert('Saved offline', 'Your clock-in will sync automatically when you are back online.')
      } else if (res.success) {
        Alert.alert('Clocked in', `At ${res.data.time} · ${res.data.store_code}`)
      } else if ('needs_override' in res) {
        Alert.alert(
          'Not scheduled here',
          `${res.message}\n\nAllowed today: ${res.allowed_stores.join(', ') || '—'}`,
        )
      } else if ('needs_priority_ack' in res) {
        Alert.alert('Priority phones', res.message, [
          { text: 'Cancel', style: 'cancel' },
          { text: 'Acknowledge & clock in', onPress: () => doClockIn({ priority_ack: true }) },
        ])
      }
      refetchAll()
    } catch (e) {
      Alert.alert('Clock-in failed', e instanceof Error ? e.message : 'Please try again.')
    } finally {
      setBusy(false)
    }
  }

  const doClockOut = async (override = false) => {
    setBusy(true)
    try {
      const res = await clockOutDurable({ override })
      if ('queued' in res) {
        Alert.alert('Saved offline', 'Your clock-out will sync automatically when you are back online.')
      } else if (res.success) {
        Alert.alert('Clocked out', `At ${res.data.time} · ${res.data.hours ?? '?'}h`)
      } else if ('needs_closing' in res) {
        Alert.alert('Closing required', res.message, [
          { text: 'OK', style: 'cancel' },
          { text: 'Clock out anyway', style: 'destructive', onPress: () => doClockOut(true) },
        ])
      }
      refetchAll()
    } catch (e) {
      Alert.alert('Clock-out failed', e instanceof Error ? e.message : 'Please try again.')
    } finally {
      setBusy(false)
    }
  }

  const headerNote = useMemo(() => {
    if (!online) return 'You are offline — punches are saved and synced automatically.'
    return null
  }, [online])

  if (status.isLoading || allowed.isLoading)
    return (
      <Screen>
        <Loading label="Loading your clock…" />
      </Screen>
    )

  return (
    <Screen>
      <OfflineBanner />
      <ScrollView
        contentContainerStyle={styles.container}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refetchAll} tintColor={colors.primary} />}
      >
        <Card style={styles.statusCard}>
          <Badge
            label={clockedIn ? 'ON THE CLOCK' : 'CLOCKED OUT'}
            color={clockedIn ? colors.success : colors.textDim}
          />
          {clockedIn && entry ? (
            <>
              <Text style={styles.big}>{since(entry.clock_in)}</Text>
              <Body dim>
                Since {new Date(entry.clock_in).toLocaleTimeString()} · {entry.store_code ?? 'store'}
              </Body>
            </>
          ) : (
            <Text style={styles.bigDim}>Not clocked in</Text>
          )}
          {headerNote ? <Body dim>{headerNote}</Body> : null}
        </Card>

        {!clockedIn && (
          <>
            <H2>Store</H2>
            <View style={styles.storeRow}>
              {stores.length === 0 ? (
                <Body dim>No stores available today.</Body>
              ) : (
                stores.map((s) => {
                  const on = s === selectedStore
                  return (
                    <Pressable
                      key={s}
                      onPress={() => setStore(s)}
                      style={[styles.chip, on && styles.chipOn]}
                    >
                      <Text style={[styles.chipText, on && styles.chipTextOn]}>{s}</Text>
                    </Pressable>
                  )
                })
              )}
            </View>
          </>
        )}

        <View style={{ marginTop: spacing.lg }}>
          {clockedIn ? (
            <Button title="Clock out" variant="danger" loading={busy} onPress={() => doClockOut(false)} />
          ) : (
            <Button title="Clock in" variant="success" loading={busy} onPress={() => doClockIn()} />
          )}
        </View>

        <Body dim>
          A clock-in records your store and (with permission) GPS location for attendance verification.
        </Body>
      </ScrollView>
    </Screen>
  )
}

const styles = StyleSheet.create({
  container: { padding: spacing.lg, gap: spacing.md, paddingBottom: spacing.xxl },
  statusCard: { alignItems: 'center', gap: spacing.sm, paddingVertical: spacing.xl },
  big: { color: colors.text, fontSize: 44, fontWeight: '800' },
  bigDim: { color: colors.textDim, fontSize: font.h2, fontWeight: '700' },
  storeRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  chip: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    borderRadius: radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  chipOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { color: colors.text, fontSize: font.body, fontWeight: '600' },
  chipTextOn: { color: colors.primaryText },
})
