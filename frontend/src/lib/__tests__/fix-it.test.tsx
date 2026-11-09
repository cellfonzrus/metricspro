// THE SCREEN HALF of "it isn't set up — here is how to fix it" (owner directive 2026-11-09,
// backend one home app/modules/core/setup_fix.py, index §CORE-39).
//
// Owner: *"to fix any such problem going forward shpow it on the screen hpow the user can fix teh
// problem so it is taken care of right away and if it is not under tehoer role permissionsm, have
// them send a link ot thier manager from the chat menu so it can be fixed"*.
//
// These are the contract for what a person SEES and what pressing it DOES. Written before the
// component (docs/TESTING.md §1): the first run failed on the import, which is the only evidence
// that the test is connected to the thing it claims to prove.
//
// The words are never re-worded here — they are the server's. A second spelling of a remedy in the
// browser is the divergence the house rules forbid, and §D is the test that would catch it.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

const apiMock = vi.hoisted(() => vi.fn())
vi.mock('@/lib/client', () => ({ api: apiMock }))

import { FixItNotice, askTheFixer, fixActionLabel } from '../fix-it'
import type { FixGuide } from '../fix-it'

const CAN_FIX: FixGuide = {
  key: 'store_targets',
  title: 'No target is set for this store and month.',
  consequence: 'Attainment is a comparison, so the figure is “no target set”, never 0%.',
  steps: ['Open Target Settings.', 'Pick the month, then enter the target for this store.'],
  page: '/commcalc/targets/settings',
  can_fix: true,
  gate: 'targets',
  ask: null,
  support: false,
}
const MUST_ASK: FixGuide = {
  ...CAN_FIX,
  can_fix: false,
  ask: {
    recipients: [{ employee_id: 'E100', name: 'Dana Admin', role: 'admin', is_manager: true },
                 { employee_id: 'E101', name: 'Al Admin', role: 'admin', is_manager: false }],
    message: 'Rab Rep needs something set up on Targets report.\n\nGo straight to it: /commcalc/targets/settings',
  },
  support: false,
}
const SUPPORT_ONLY: FixGuide = { ...CAN_FIX, can_fix: false, page: null, ask: null, support: true }

beforeEach(() => { apiMock.mockReset() })

// ── §A a permitted viewer is handed the way in ──────────────────────────────────────────────────
describe('a viewer whose role permits the fix', () => {
  it('shows the steps and a link straight to the page that closes it', () => {
    render(<FixItNotice fix={CAN_FIX} />)
    expect(screen.getByText(CAN_FIX.title)).toBeTruthy()
    for (const step of CAN_FIX.steps) expect(screen.getByText(step)).toBeTruthy()
    const link = screen.getByRole('link', { name: /fix this now/i })
    expect(link.getAttribute('href')).toBe('/commcalc/targets/settings')
  })

  it('offers nobody to pester', () => {
    render(<FixItNotice fix={CAN_FIX} />)
    expect(screen.queryByRole('button', { name: /ask/i })).toBeNull()
  })
})

// ── §B a refused viewer is handed a person, not a dead end ─────────────────────────────────────
describe('a viewer whose role does not permit the fix', () => {
  it('still shows every step, so they can say what is needed', () => {
    render(<FixItNotice fix={MUST_ASK} />)
    for (const step of MUST_ASK.steps) expect(screen.getByText(step)).toBeTruthy()
  })

  it('offers the manager BY NAME rather than the word "manager"', () => {
    render(<FixItNotice fix={MUST_ASK} />)
    expect(screen.getByRole('button', { name: /Dana Admin/ })).toBeTruthy()
    expect(screen.queryByRole('link', { name: /fix this now/i })).toBeNull()
  })

  it('sends the request through the existing chat conversation, not a new send path', async () => {
    apiMock.mockImplementation(async (path: string) => {
      if (path.startsWith('/api/v1/chat/dm')) return { channel: { id: 'c-7' }, created: true }
      return { message: { id: 'm-1' } }
    })
    render(<FixItNotice fix={MUST_ASK} />)
    fireEvent.click(screen.getByRole('button', { name: /Dana Admin/ }))
    await waitFor(() => expect(apiMock).toHaveBeenCalledTimes(2))
    const [dmPath, dmOpts] = apiMock.mock.calls[0]
    expect(dmPath).toContain('/api/v1/chat/dm')
    expect(JSON.parse(dmOpts.body).employee_ids).toEqual(['E100', 'E101'])
    const [msgPath, msgOpts] = apiMock.mock.calls[1]
    expect(msgPath).toContain('/api/v1/chat/channels/c-7/messages')
    expect(JSON.parse(msgOpts.body).body).toBe(MUST_ASK.ask!.message)
  })

  it('confirms it was sent and links to the conversation it was sent in', async () => {
    apiMock.mockImplementation(async (path: string) =>
      path.startsWith('/api/v1/chat/dm') ? { channel: { id: 'c-7' } } : { message: { id: 'm-1' } })
    render(<FixItNotice fix={MUST_ASK} />)
    fireEvent.click(screen.getByRole('button', { name: /Dana Admin/ }))
    const link = await screen.findByRole('link', { name: /open the conversation/i })
    expect(link.getAttribute('href')).toBe('/chat?channel=c-7')
  })

  it('says plainly when the send failed instead of claiming it was sent', async () => {
    apiMock.mockRejectedValue(new Error('nope'))
    render(<FixItNotice fix={MUST_ASK} />)
    fireEvent.click(screen.getByRole('button', { name: /Dana Admin/ }))
    expect(await screen.findByText(/could not be sent/i)).toBeTruthy()
    expect(screen.queryByText(/sent to/i)).toBeNull()
  })
})

// ── §C nobody in the company can close it ──────────────────────────────────────────────────────
describe('a gap no role in this company can close', () => {
  it('says to contact support and offers no button that would be refused', () => {
    render(<FixItNotice fix={SUPPORT_ONLY} />)
    expect(screen.getByText(/contact support/i)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /ask/i })).toBeNull()
    expect(screen.queryByRole('link', { name: /fix this now/i })).toBeNull()
  })
})

// ── §D the words are the server's ──────────────────────────────────────────────────────────────
describe('the copy', () => {
  it('renders the server sentences verbatim', () => {
    render(<FixItNotice fix={MUST_ASK} />)
    expect(screen.getByText(MUST_ASK.consequence)).toBeTruthy()
  })

  it('renders nothing at all when there is no guide, so a page can mount it unconditionally', () => {
    const { container } = render(<FixItNotice fix={null} />)
    expect(container.textContent).toBe('')
  })

  it('names the recipient in the button label and never invents a title for them', () => {
    expect(fixActionLabel(MUST_ASK)).toBe('Ask Dana Admin to fix this')
    expect(fixActionLabel(CAN_FIX)).toBe('Fix this now')
    expect(fixActionLabel(SUPPORT_ONLY)).toBe('')
  })
})

// ── §E the send is a function, provable on its own ─────────────────────────────────────────────
describe('askTheFixer', () => {
  it('refuses to send a guide with nobody to send it to', async () => {
    await expect(askTheFixer(SUPPORT_ONLY)).rejects.toThrow()
    expect(apiMock).not.toHaveBeenCalled()
  })

  it('returns the conversation it used, so the caller can link to it', async () => {
    apiMock.mockImplementation(async (path: string) =>
      path.startsWith('/api/v1/chat/dm') ? { channel: { id: 'c-9' } } : { message: { id: 'm' } })
    await expect(askTheFixer(MUST_ASK)).resolves.toEqual({ channelId: 'c-9' })
  })
})
