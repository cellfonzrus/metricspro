import { describe, it, expect } from 'vitest'
import {
  sortItems, unreadCount, badgeLabel, standingFor, seenLabel, scopeLabel, groupByScope,
  type InboxItem,
} from './notification-inbox'

const item = (o: Partial<InboxItem> = {}): InboxItem => ({
  id: 'i1', scope: 'connector', inbox_key: 'k1', title: 't', ...o,
})

describe('the notification inbox view model', () => {
  it('shows nothing at all until a read has actually succeeded', () => {
    expect(unreadCount(null)).toBeNull()
    expect(unreadCount({ unread: 3 })).toBeNull()        // no read_ok: the read did not happen
    expect(unreadCount({ unread: 0, read_ok: true })).toBe(0)
    expect(badgeLabel(null)).toBe('Notifications')
    expect(badgeLabel(0)).toBe('Notifications — nothing new')
    expect(badgeLabel(1)).toBe('1 new notification')
    expect(badgeLabel(4)).toBe('4 new notifications')
  })

  it('puts the worst first and, within a severity, the longest-standing finding first', () => {
    const rows = [
      item({ id: 'warn-new', severity: 'warning', first_seen_at: '2026-11-23T00:00:00Z' }),
      item({ id: 'err', severity: 'error', first_seen_at: '2026-11-24T00:00:00Z' }),
      item({ id: 'warn-old', severity: 'warning', first_seen_at: '2026-11-01T00:00:00Z' }),
      item({ id: 'info', severity: 'info', first_seen_at: '2026-10-01T00:00:00Z' }),
    ]
    expect(sortItems(rows).map(r => r.id)).toEqual(['err', 'warn-old', 'warn-new', 'info'])
  })

  it('says how long a finding has been standing, which a daily email never could', () => {
    const now = new Date('2026-11-24T12:00:00Z')
    expect(standingFor(item({ first_seen_at: '2026-11-24T09:00:00Z' }), now)).toBe('today')
    expect(standingFor(item({ first_seen_at: '2026-11-23T09:00:00Z' }), now)).toBe('since yesterday')
    expect(standingFor(item({ first_seen_at: '2026-11-17T09:00:00Z' }), now)).toBe('for 7 days')
    expect(standingFor(item({ first_seen_at: null }), now)).toBe('just now')
  })

  it('replaces nine identical emails with one honest count', () => {
    expect(seenLabel(item({ occurrences: 1 }))).toBe('reported once')
    expect(seenLabel(item({ occurrences: 9 }))).toBe('reported 9 times')
  })

  it('shows a scope it has no words for rather than mislabelling it', () => {
    expect(scopeLabel('connector')).toBe('Data not arriving')
    expect(scopeLabel('upload_due')).toBe('Files waiting to be uploaded')
    expect(scopeLabel('some_future_scope')).toBe('some_future_scope')
  })

  it('groups by scope and keeps the ordering inside each group', () => {
    const g = groupByScope([
      item({ id: 'a', scope: 'upload_due', severity: 'warning', first_seen_at: '2026-11-02T00:00:00Z' }),
      item({ id: 'b', scope: 'connector', severity: 'error', first_seen_at: '2026-11-03T00:00:00Z' }),
      item({ id: 'c', scope: 'connector', severity: 'error', first_seen_at: '2026-11-01T00:00:00Z' }),
    ])
    expect(g.map(x => x.scope)).toEqual(['connector', 'upload_due'])
    expect(g[0].items.map(i => i.id)).toEqual(['c', 'b'])
  })
})
