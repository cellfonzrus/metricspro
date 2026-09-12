// ── THE FLOWCHART LIBRARY — operating runbooks that live inside the app ─────────────────────────
// Owner directive 2026-09-10: "All of these will be in the training module under a tile called
// flowcharts and named appropriately."
//
// Each entry is one procedure: who does what, in what order, and what "done" looks like at their
// level. They are TYPED CONTENT (see components/RunbookDoc) rather than stored HTML, so nothing is
// ever injected into a reader's authenticated page, and adding a runbook is a data object here
// rather than a new route.
//
// THE DIAGRAMS ARE HAND-DRAWN INLINE SVG. No chart library, no image host, no CDN: a training page
// that breaks when a third-party script does is not training. Every stroke and label takes its
// colour from `currentColor` or a `--rb-*` token declared for BOTH themes by RunbookDoc, so a
// flowchart is legible on the dark theme without a second drawing.
//
// WRITTEN AGAINST THE BUILD, NOT AROUND IT. Every step below names a screen that exists and a rule
// the code actually enforces. Where a rule is a deliberate refusal — the close gate never telling a
// rep the variance, an uncounted envelope never counting as short — the runbook says so and says
// why, because a procedure whose reasons are hidden gets worked around within a month.
import type { Runbook } from '@/components/RunbookDoc'
import { F, Pill } from '@/components/RunbookDoc'

// ── shared SVG furniture ────────────────────────────────────────────────────────────────────────
const Arrow = ({ id, color = 'currentColor' }: { id: string; color?: string }) => (
  <marker id={id} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
    orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill={color} /></marker>
)

// ════════════════════════════════════════════════════════════════════════════════════════════════
// 1 · STORE CASH
// ════════════════════════════════════════════════════════════════════════════════════════════════
const cashFigure = (
  <svg viewBox="0 0 1260 610" role="img" style={{ minWidth: 1000, maxWidth: 1260 }}
    aria-label="Swimlane flowchart. A rep closes the day and seals an envelope. The district manager verifies the figures, then picks the envelope up, recording a count only when the envelope was opened. The envelope is either banked with a slip or handed to management for confirmation. Accounting reconciles against the bank, and short counts and undeposited balances surface to the owner.">
    <defs><Arrow id="cf-a" /><Arrow id="cf-r" color="var(--rb-own)" /></defs>
    <g>
      <rect x="0" y="46" width="1260" height="104" fill="currentColor" opacity="0.028" />
      <rect x="0" y="150" width="1260" height="150" fill="currentColor" opacity="0.055" />
      <rect x="0" y="300" width="1260" height="104" fill="currentColor" opacity="0.028" />
      <rect x="0" y="404" width="1260" height="94" fill="currentColor" opacity="0.055" />
      <rect x="0" y="498" width="1260" height="100" fill="currentColor" opacity="0.028" />
      <line x1="150" y1="46" x2="150" y2="598" stroke="currentColor" opacity="0.22" />
    </g>
    <g fontSize="11" fontWeight="600" fill="currentColor" opacity="0.55" letterSpacing="1.2">
      <text x="235" y="34" textAnchor="middle">CLOSE</text>
      <text x="425" y="34" textAnchor="middle">VERIFY</text>
      <text x="625" y="34" textAnchor="middle">COLLECT</text>
      <text x="835" y="34" textAnchor="middle">DISPOSE</text>
      <text x="1055" y="34" textAnchor="middle">ACCOUNT</text>
    </g>
    <g fontSize="12" fontWeight="700">
      <text x="18" y="94" fill="var(--rb-rep)">Rep</text>
      <text x="18" y="110" fontSize="9.5" fontWeight="500" fill="currentColor" opacity="0.5">in store</text>
      <text x="18" y="212" fill="var(--rb-dm)">District Mgr</text>
      <text x="18" y="228" fontSize="9.5" fontWeight="500" fill="currentColor" opacity="0.5">the collector</text>
      <text x="18" y="348" fill="var(--rb-mm)">Market Mgr</text>
      <text x="18" y="364" fontSize="9.5" fontWeight="500" fill="currentColor" opacity="0.5">and above</text>
      <text x="18" y="450" fill="var(--rb-acct)">Accounting</text>
      <text x="18" y="548" fill="var(--rb-own)">Owner</text>
      <text x="18" y="564" fontSize="9.5" fontWeight="500" fill="currentColor" opacity="0.5">exceptions</text>
    </g>
    <g fontSize="12.5" fontWeight="600" fill="currentColor">
      <rect x="172" y="72" width="126" height="52" rx="3" fill="var(--rb-card)" stroke="var(--rb-rep)" strokeWidth="1.5" />
      <text x="235" y="94" textAnchor="middle">Close the day</text>
      <text x="235" y="110" textAnchor="middle" fontSize="10" fontWeight="500" opacity="0.7">declare cash</text>

      <rect x="316" y="72" width="126" height="52" rx="3" fill="var(--rb-card)" stroke="var(--rb-rep)" strokeWidth="1.5" />
      <text x="379" y="94" textAnchor="middle">Seal + photo</text>
      <text x="379" y="110" textAnchor="middle" fontSize="10" fontWeight="500" opacity="0.7">envelope</text>

      <rect x="362" y="176" width="126" height="52" rx="3" fill="var(--rb-card)" stroke="var(--rb-dm)" strokeWidth="1.5" />
      <text x="425" y="198" textAnchor="middle">DM Verify</text>
      <text x="425" y="214" textAnchor="middle" fontSize="10" fontWeight="500" opacity="0.7">correct, don&rsquo;t erase</text>

      <rect x="562" y="176" width="126" height="52" rx="3" fill="var(--rb-card)" stroke="var(--rb-dm)" strokeWidth="1.5" />
      <text x="625" y="198" textAnchor="middle">Pick up</text>
      <text x="625" y="214" textAnchor="middle" fontSize="10" fontWeight="500" opacity="0.7">tick the envelope</text>

      <rect x="530" y="248" width="90" height="38" rx="3" fill="var(--rb-card)" stroke="currentColor" strokeWidth="1" opacity="0.85" />
      <text x="575" y="264" textAnchor="middle" fontSize="10.5">Sealed</text>
      <text x="575" y="277" textAnchor="middle" fontSize="9.5" fontWeight="500" opacity="0.7">declared stands</text>

      <rect x="632" y="248" width="98" height="38" rx="3" fill="var(--rb-card)" stroke="var(--rb-own)" strokeWidth="1.2" />
      <text x="681" y="264" textAnchor="middle" fontSize="10.5" fill="var(--rb-own)">Opened</text>
      <text x="681" y="277" textAnchor="middle" fontSize="9.5" fontWeight="500" fill="var(--rb-own)" opacity="0.9">count required</text>

      <rect x="772" y="176" width="126" height="52" rx="3" fill="var(--rb-card)" stroke="var(--rb-dm)" strokeWidth="1.5" />
      <text x="835" y="198" textAnchor="middle">Bank it</text>
      <text x="835" y="214" textAnchor="middle" fontSize="10" fontWeight="500" opacity="0.7">deposit + slip</text>

      <rect x="772" y="326" width="126" height="52" rx="3" fill="var(--rb-card)" stroke="var(--rb-mm)" strokeWidth="1.5" />
      <text x="835" y="348" textAnchor="middle">Hand over</text>
      <text x="835" y="364" textAnchor="middle" fontSize="10" fontWeight="500" opacity="0.7">mgmt confirms</text>

      <rect x="992" y="326" width="126" height="52" rx="3" fill="var(--rb-seal-dim)" stroke="var(--rb-seal)" strokeWidth="1.8" />
      <text x="1055" y="348" textAnchor="middle" fill="var(--rb-seal)">Green day</text>
      <text x="1055" y="364" textAnchor="middle" fontSize="10" fontWeight="500" fill="var(--rb-seal)" opacity="0.9">all accounted</text>

      <rect x="992" y="422" width="126" height="52" rx="3" fill="var(--rb-card)" stroke="var(--rb-acct)" strokeWidth="1.5" />
      <text x="1055" y="444" textAnchor="middle">Deposit recon</text>
      <text x="1055" y="460" textAnchor="middle" fontSize="10" fontWeight="500" opacity="0.7">vs bank</text>

      <rect x="562" y="520" width="146" height="52" rx="3" fill="var(--rb-card)" stroke="var(--rb-own)" strokeWidth="1.5" />
      <text x="635" y="542" textAnchor="middle" fill="var(--rb-own)">Cash Short by DM</text>
      <text x="635" y="558" textAnchor="middle" fontSize="10" fontWeight="500" fill="var(--rb-own)" opacity="0.85">who is losing it</text>

      <rect x="992" y="520" width="126" height="52" rx="3" fill="var(--rb-card)" stroke="var(--rb-own)" strokeWidth="1.5" />
      <text x="1055" y="542" textAnchor="middle" fill="var(--rb-own)">Cash on hand</text>
      <text x="1055" y="558" textAnchor="middle" fontSize="10" fontWeight="500" fill="var(--rb-own)" opacity="0.85">balance sheet</text>
    </g>
    <g stroke="currentColor" strokeWidth="1.4" fill="none" opacity="0.75">
      <line x1="298" y1="98" x2="310" y2="98" markerEnd="url(#cf-a)" />
      <path d="M 379 124 L 379 160 Q 379 176 395 176 L 356 176" markerEnd="url(#cf-a)" />
      <line x1="488" y1="202" x2="556" y2="202" markerEnd="url(#cf-a)" />
      <line x1="688" y1="202" x2="766" y2="202" markerEnd="url(#cf-a)" />
      <path d="M 835 228 L 835 320" markerEnd="url(#cf-a)" />
      <path d="M 898 352 L 986 352" markerEnd="url(#cf-a)" />
      <path d="M 898 202 Q 1055 202 1055 320" markerEnd="url(#cf-a)" />
      <path d="M 1055 378 L 1055 416" markerEnd="url(#cf-a)" />
      <path d="M 1055 474 L 1055 514" markerEnd="url(#cf-a)" />
    </g>
    <g stroke="currentColor" strokeWidth="1.1" fill="none" opacity="0.45">
      <path d="M 600 228 L 590 248" /><path d="M 650 228 L 662 248" />
    </g>
    <g stroke="var(--rb-own)" strokeWidth="1.4" fill="none" strokeDasharray="5 4">
      <path d="M 681 286 L 681 460 Q 681 520 662 520" markerEnd="url(#cf-r)" />
    </g>
    <g fontSize="9.5" fill="currentColor" opacity="0.62" fontFamily="ui-monospace, monospace">
      <text x="248" y="150">declared cash + photo</text>
      <text x="512" y="196">verified figures</text>
      <text x="700" y="196">actual taken</text>
      <text x="843" y="278">handed to mgmt</text>
      <text x="908" y="346">confirmed</text>
      <text x="912" y="196">deposited + slip</text>
      <text x="1062" y="400">green</text>
      <text x="1062" y="498">undeposited</text>
    </g>
    <g fontSize="9.5" fill="var(--rb-own)" opacity="0.9" fontFamily="ui-monospace, monospace">
      <text x="690" y="404">short / over</text>
    </g>
  </svg>
)

const storeCash: Runbook = {
  stages: [
    { href: '/closing/submit', label: 'Submit Closing', who: 'Rep',
      does: 'Count the drawer, enter every tender, attach the envelope photo.',
      handoff: 'the declared figures and a sealed envelope' },
    { href: '/closing/verify', label: 'DM Verify', who: 'District Manager',
      does: 'Check each store-day against the POS and correct what is wrong.',
      handoff: 'verified figures the money can be collected against' },
    { href: '/closing/pickup', label: 'Cash Pickup', who: 'District Manager',
      does: 'Tick the envelopes you physically take, and count any you opened.',
      handoff: 'cash out of the store, and a count if the seal was broken' },
    { href: '/closing/billpay-pickup', label: 'Bill Payment Pickup', who: 'District Manager',
      does: 'The same collection, for bill-payment cash.',
      handoff: 'bill-payment cash out of the store' },
    { href: '/closing/deposit-recon', label: 'Cash Deposit Recon', who: 'Market Mgr / Accounting',
      does: 'Confirm hand-overs, chase missing slips, drive the day green.',
      handoff: 'every envelope accounted for' },
    { href: '/closing/envelope-report', label: 'Envelope Report', who: 'Market Manager',
      does: 'Count disputed envelopes and raise a chargeback where one is genuinely short.' },
    { href: '/closing/store-cash-on-hand', label: 'Store Cash on Hand', who: 'Owner / Accounting',
      does: 'What is still sitting in stores undeposited.' },
  ],

  slug: 'store-cash',
  title: 'Store Cash Runbook',
  summary: 'How cash moves from the drawer to the bank, and who is answerable at each hand-off.',
  module: 'closing', audience: 'managers', icon: '💵',
  chain: 'Rep → DM → Market Mgr → Accounting → Owner',
  lede: 'How cash moves from the drawer to the bank, and who is answerable at each hand-off. Every step is a screen that already exists — this is the order to use them in, and the state each one must be left in.',
  kin: <>Follows on from the <strong>Daily Closing Runbook</strong>, which ends at the sealed envelope.</>,
  figure: cashFigure,
  figcaption: <><strong>The lane says who; the column says when.</strong> Cash only leaves a lane by a labelled hand-off, and each hand-off is a record, not a conversation. The dashed red path is the only one that skips levels: a short count reaches the owner directly, because the person who lost the money is the person the report is about.</>,
  levels: [
    {
      title: 'The rep closes the day', who: 'Level 1 · in store', tone: 'rep',
      intro: 'Everything downstream is measured against what the rep declares here. A wrong figure entered at 9pm becomes a variance three people have to chase.',
      steps: [
        { title: 'Count the drawer and submit the close', body: <p>On <F>Submit Closing</F>, enter total cash, store cash and bill-payment cash. Store cash is total cash <em>minus</em> bill-payment cash by definition — enter what you actually counted.</p> },
        { title: 'Seal the envelope and photograph it', body: <p>The photo attaches to your closing row and is what management sees if the count is later disputed. An envelope with no photo is not a closed day.</p> },
        { title: 'Leave the envelope where the DM collects it', body: <p>Do not open it again. Once sealed, the declared figure is what the whole chain uses until someone counts it in front of a witness.</p> },
      ],
      done: <> your close is submitted with a photo attached, and the envelope is sealed and set aside.</>,
    },
    {
      title: 'The DM verifies, then collects', who: 'Level 2 · the collector', tone: 'dm',
      intro: 'Two distinct jobs, in this order. Verifying is about the numbers; picking up is about the money.',
      steps: [
        { title: 'Verify the close', body: <p>On <F>DM Verify</F>, check each rep-day against the POS. Correcting a figure does not overwrite what the rep entered — the original stays, your value lands beside it, and every save after the first is kept as a revision.</p> },
        { title: 'Collect the envelopes', body: <p>On <F>Cash Pickup</F>, tick each envelope you physically take. The tick is your statement that the envelope is in your hand — nothing else ticks it for you.</p> },
        {
          title: 'If you opened it, count it', body: <>
            <p>Tick <F>Opened?</F> only when you broke the seal. <strong>Actual picked</strong> then becomes mandatory — an opened envelope cannot be confirmed with no count. A count of <F>0.00</F> is a legitimate answer; a blank is not.</p>
            <p>Left sealed, leave the count blank. The declared figure stands and nothing is treated as short.</p>
          </>,
        },
        { title: 'Dispose of it the same day', body: <p>Either bank it — <em>with the deposit slip</em> — or hand it to your market manager. An envelope that is neither is <Pill tone="red">undisposed</Pill> and sits on the board with your name on it.</p> },
      ],
      done: <> every envelope you touched is either deposited with a slip, or handed over and awaiting confirmation. Not when the cash is in your car.</>,
    },
    {
      title: 'The market manager accounts for it', who: 'Level 3 · and above', tone: 'mm',
      intro: 'This level closes the loop the DM cannot close alone: nobody confirms their own hand-off.',
      steps: [
        { title: 'Confirm what was handed to you', body: <p>Confirm receipt per store-day or per envelope. Until you do, the hand-over reads <Pill tone="amber">unconfirmed</Pill> — money that has left the store and arrived nowhere.</p> },
        { title: 'Drive the day green', body: <p>A day turns <Pill tone="green">green</Pill> only when at least one envelope was picked up <em>and</em> every picked-up envelope is accounted for. One missing slip keeps the whole day amber.</p> },
        { title: 'Count disputed envelopes, charge back real shortages', body: <p>Your count on <F>Envelope Report</F> is a separate record from the DM&rsquo;s pickup count — different person, different moment, and that is the point. A genuine shortage raises a pending chargeback; it does not deduct anything on its own.</p> },
      ],
      done: <> every store-day in your market is green, or the ones that are not have a named reason you could defend out loud.</>,
    },
    {
      title: 'Accounting reconciles against the bank', who: 'Level 4', tone: 'acct',
      intro: 'Up to here everything is what people said happened. This is where the bank agrees or does not.',
      steps: [
        { title: 'Match deposits to closes', body: <p><F>Reconciliation</F> compares cash collected at close against what landed in the bank, per store per day. The pickup-flow deposit capture is shown as its own line — evidence, never a second source of truth.</p> },
        { title: 'Work the variances, oldest first', body: <p>Anything outside tolerance is a question for a named DM on a named day — and the envelope photo and both figures are already attached.</p> },
        { title: 'Check the undeposited balance', body: <p>Verified-but-unbanked cash sits on the balance sheet as <em>Cash on hand — stores</em>. It should fall to near zero when same-day pickups work. A balance that only grows means envelopes are being collected and not banked.</p> },
      ],
      done: <> every store-day is reconciled or carries a written reason, and the undeposited line matches envelopes you can name.</>,
    },
    {
      title: 'The owner reads the exceptions', who: 'Level 5', tone: 'own',
      intro: 'Three numbers, none of which require reading the detail underneath them.',
      steps: [
        { title: 'Cash Short by DM', body: <p>Shortages folded by whoever collected. Uncounted envelopes are <em>not</em> in this number and an overage never nets a shortage away — so a DM who is consistently light cannot hide inside their own good days.</p> },
        { title: 'Store Cash on Hand', body: <p>What is sitting in stores undeposited right now. Rising steadily means collection has stopped, well before the bank recon shows it.</p> },
        { title: 'Days not green', body: <p>Store-days where cash moved and nobody closed the loop. The one to ask about on a Monday call.</p> },
      ],
    },
  ],
  rulesHeading: 'The rules that keep the numbers honest',
  rules: [
    { kind: 'ok', claim: 'The rep’s original figure is never overwritten.', why: 'A DM correction is stored beside it, with a revision kept for every save. Both appear in the export, so “it was changed later” is answerable from the record.' },
    { kind: 'bad', claim: 'Uncounted is not short.', why: 'A sealed envelope is in neither the short nor the over bucket. Treating a missing count as zero would manufacture a shortfall against a DM who did nothing wrong.' },
    { kind: 'bad', claim: 'An overage never cancels a shortage.', why: '$40 short at one store and $40 over at another is two problems, not zero. They are reported separately and the net is shown on its own.' },
    { kind: 'hold', claim: 'A missing figure is never displayed as $0.00.', why: '“Not recorded” and “counted, and it was empty” are different facts. The screens show them differently, including in exports.' },
    { kind: 'hold', claim: 'A missing deposit slip flags, it does not block.', why: 'Blocking would strand cash that has genuinely been banked. The day goes loudly amber instead, and cannot turn green until the slip appears.' },
    { kind: 'ok', claim: 'Cash reconciliation is market-manager-and-above.', why: 'DMs record pickups and dispositions as normal, but confirming a hand-off is gated — the point of the confirmation is that a second person did it.' },
  ],
  table: {
    heading: 'Envelope states',
    intro: 'Any envelope, at any moment, is in exactly one of these. The board colours from this.',
    columns: ['State', 'Means', 'Whose move'],
    rows: [
      { key: 'undisposed', means: 'Picked up, and neither banked nor handed over', move: 'DM' },
      { key: 'missing_slip', means: 'Deposited, but no slip attached', move: 'DM' },
      { key: 'handed_unconfirmed', means: 'Given to management, not yet confirmed', move: 'Market manager' },
      { key: 'handed_confirmed', means: <>Received and confirmed <Pill tone="green">green</Pill></>, move: '—' },
      { key: 'deposited', means: <>Banked with a slip <Pill tone="green">green</Pill></>, move: '—' },
    ],
  },
}

// ════════════════════════════════════════════════════════════════════════════════════════════════
// 2 · DAILY CLOSING
// ════════════════════════════════════════════════════════════════════════════════════════════════
const closingFigure = (
  <svg viewBox="0 0 960 640" role="img" style={{ minWidth: 720, maxWidth: 960 }}
    aria-label="Flow of a closing submission through the three-try gate. The declared cash and credit are compared against the POS day. A match, or missing data, accepts. Cash over or credit under accepts with a flag. Cash short or credit over blocks, and the rep is asked to recount and told nothing else, up to three tries, after which the close is auto-accepted and raised for management review.">
    <defs><Arrow id="dc-a" /><Arrow id="dc-r" color="var(--rb-own)" /></defs>
    <g fontWeight="600" fill="currentColor">
      <rect x="330" y="24" width="300" height="52" rx="3" fill="var(--rb-card)" stroke="var(--rb-rep)" strokeWidth="1.6" />
      <text x="480" y="46" textAnchor="middle" fontSize="13.5">Rep submits the close</text>
      <text x="480" y="63" textAnchor="middle" fontSize="10.5" fontWeight="500" opacity=".7">7 tender boxes · counts · envelope photo</text>

      <rect x="330" y="112" width="300" height="46" rx="3" fill="var(--rb-card)" stroke="currentColor" strokeWidth="1.2" opacity=".9" />
      <text x="480" y="132" textAnchor="middle" fontSize="12">Cash declared but no photo?</text>
      <text x="480" y="148" textAnchor="middle" fontSize="10.5" fontWeight="500" fill="var(--rb-own)">refused before anything is compared</text>

      <rect x="290" y="196" width="380" height="60" rx="3" fill="var(--rb-card)" stroke="var(--rb-dm)" strokeWidth="1.8" />
      <text x="480" y="220" textAnchor="middle" fontSize="13.5">The close gate</text>
      <text x="480" y="238" textAnchor="middle" fontSize="10.5" fontWeight="500" opacity=".75">declared cash + credit vs the POS day for this rep</text>

      <rect x="34" y="326" width="238" height="66" rx="3" fill="var(--rb-seal-dim)" stroke="var(--rb-seal)" strokeWidth="1.5" />
      <text x="153" y="348" textAnchor="middle" fontSize="12.5" fill="var(--rb-seal)">Accepted</text>
      <text x="153" y="365" textAnchor="middle" fontSize="10.3" fontWeight="500" fill="var(--rb-seal)" opacity=".9">it ties — or there is no</text>
      <text x="153" y="378" textAnchor="middle" fontSize="10.3" fontWeight="500" fill="var(--rb-seal)" opacity=".9">POS day to tie it to</text>

      <rect x="300" y="326" width="238" height="66" rx="3" fill="var(--rb-hold-dim)" stroke="var(--rb-hold)" strokeWidth="1.5" />
      <text x="419" y="348" textAnchor="middle" fontSize="12.5" fill="var(--rb-hold)">Accepted, flagged</text>
      <text x="419" y="365" textAnchor="middle" fontSize="10.3" fontWeight="500" fill="var(--rb-hold)" opacity=".9">cash OVER or credit UNDER</text>
      <text x="419" y="378" textAnchor="middle" fontSize="10.3" fontWeight="500" fill="var(--rb-hold)" opacity=".9">— never blocks</text>

      <rect x="566" y="326" width="238" height="66" rx="3" fill="var(--rb-short-dim)" stroke="var(--rb-own)" strokeWidth="1.6" />
      <text x="685" y="348" textAnchor="middle" fontSize="12.5" fill="var(--rb-own)">Blocked</text>
      <text x="685" y="365" textAnchor="middle" fontSize="10.3" fontWeight="500" fill="var(--rb-own)" opacity=".9">cash SHORT or credit OVER</text>
      <text x="685" y="378" textAnchor="middle" fontSize="10.3" fontWeight="500" fill="var(--rb-own)" opacity=".9">&ldquo;recount and resubmit&rdquo;</text>

      <rect x="566" y="452" width="238" height="52" rx="3" fill="var(--rb-card)" stroke="var(--rb-own)" strokeWidth="1.3" strokeDasharray="5 4" />
      <text x="685" y="473" textAnchor="middle" fontSize="12" fill="var(--rb-own)">Try again — twice</text>
      <text x="685" y="490" textAnchor="middle" fontSize="10.3" fontWeight="500" fill="var(--rb-own)" opacity=".9">every attempt is logged</text>

      <rect x="566" y="552" width="238" height="60" rx="3" fill="var(--rb-card)" stroke="var(--rb-hold)" strokeWidth="1.6" />
      <text x="685" y="574" textAnchor="middle" fontSize="12.5" fill="var(--rb-hold)">Third try: auto-accepted</text>
      <text x="685" y="591" textAnchor="middle" fontSize="10.3" fontWeight="500" fill="var(--rb-hold)" opacity=".9">and raised for management review</text>

      <rect x="34" y="452" width="440" height="82" rx="3" fill="none" stroke="currentColor" strokeWidth="1" opacity=".35" strokeDasharray="3 3" />
      <text x="52" y="476" fontSize="11.5" fontWeight="600" opacity=".85">What the rep is told: nothing specific.</text>
      <text x="52" y="495" fontSize="10.6" fontWeight="500" opacity=".7">Not the amount. Not whether they are over or short.</text>
      <text x="52" y="511" fontSize="10.6" fontWeight="500" opacity=".7">Not which attempt this is. Only that it does not tie</text>
      <text x="52" y="527" fontSize="10.6" fontWeight="500" opacity=".7">and to count again.</text>
    </g>
    <g stroke="currentColor" strokeWidth="1.4" fill="none" opacity=".75">
      <line x1="480" y1="76" x2="480" y2="106" markerEnd="url(#dc-a)" />
      <line x1="480" y1="158" x2="480" y2="190" markerEnd="url(#dc-a)" />
      <path d="M 400 256 L 400 292 Q 400 300 386 300 L 153 300 L 153 320" markerEnd="url(#dc-a)" />
      <path d="M 480 256 L 480 300 L 419 300 L 419 320" markerEnd="url(#dc-a)" />
      <path d="M 560 256 L 560 292 Q 560 300 574 300 L 685 300 L 685 320" markerEnd="url(#dc-a)" />
    </g>
    <g stroke="var(--rb-own)" strokeWidth="1.4" fill="none">
      <line x1="685" y1="392" x2="685" y2="446" markerEnd="url(#dc-r)" />
      <path d="M 804 478 Q 856 478 856 226 Q 856 196 700 196 L 676 210" markerEnd="url(#dc-r)" />
      <line x1="685" y1="504" x2="685" y2="546" markerEnd="url(#dc-r)" />
    </g>
    <g fontSize="9.6" fill="currentColor" opacity=".62" fontFamily="ui-monospace, monospace">
      <text x="490" y="98">then, and only then</text>
      <text x="316" y="290">ties</text><text x="434" y="290">over</text><text x="596" y="290">short</text>
    </g>
    <g fontSize="9.6" fill="var(--rb-own)" opacity=".9" fontFamily="ui-monospace, monospace">
      <text x="700" y="424">try 1 · try 2</text>
      <text x="700" y="534">try 3</text>
    </g>
  </svg>
)

const dailyClosing: Runbook = {
  stages: [
    { href: '/closing/submit', label: 'Submit Closing', who: 'Rep',
      does: 'Count the drawer, enter every tender, attach the envelope photo.',
      handoff: 'the declared figures and a sealed envelope' },
    { href: '/closing/verify', label: 'DM Verify', who: 'District Manager',
      does: 'Check each store-day against the POS and correct what is wrong.',
      handoff: 'verified figures the money can be collected against' },
    { href: '/closing/pickup', label: 'Cash Pickup', who: 'District Manager',
      does: 'Tick the envelopes you physically take, and count any you opened.',
      handoff: 'cash out of the store, and a count if the seal was broken' },
    { href: '/closing/billpay-pickup', label: 'Bill Payment Pickup', who: 'District Manager',
      does: 'The same collection, for bill-payment cash.',
      handoff: 'bill-payment cash out of the store' },
    { href: '/closing/deposit-recon', label: 'Cash Deposit Recon', who: 'Market Mgr / Accounting',
      does: 'Confirm hand-overs, chase missing slips, drive the day green.',
      handoff: 'every envelope accounted for' },
    { href: '/closing/envelope-report', label: 'Envelope Report', who: 'Market Manager',
      does: 'Count disputed envelopes and raise a chargeback where one is genuinely short.' },
    { href: '/closing/store-cash-on-hand', label: 'Store Cash on Hand', who: 'Owner / Accounting',
      does: 'What is still sitting in stores undeposited.' },
  ],

  slug: 'daily-closing',
  title: 'Daily Closing Runbook',
  summary: 'What a rep enters at the end of a shift, and how the three-try close gate checks it.',
  module: 'closing', audience: 'reps', icon: '🧾',
  chain: 'Rep → DM → Management',
  lede: 'What a rep enters at the end of a shift, what the system checks it against before accepting it, and what a manager does when it will not go through. The close is the first measurement of the day’s money — everything downstream inherits whatever is entered here.',
  kin: <>Hands over to the <strong>Store Cash Runbook</strong> at the sealed envelope.</>,
  figure: closingFigure,
  figcaption: <><strong>The gate is one-directional on purpose.</strong> Cash <em>short</em> and credit <em>over</em> are the two directions a mis-entry moves money in someone&rsquo;s favour, so those block; the opposite directions only flag. Because the rep is never told the amount or the direction, a close cannot be tuned until it passes — and the third attempt goes through anyway, so nobody is locked out of finishing their shift.</>,
  levels: [
    {
      title: 'The rep closes their own register', who: 'Level 1 · in store', tone: 'rep',
      intro: 'By default everyone closes what they worked. Two people on a shift means two closings — not one person closing for both.',
      steps: [
        { title: 'Count before you open the screen', body: <p>Count the drawer, run the register X-report, and have both in front of you. Entering figures and then reconciling them is how the third-attempt flag gets raised.</p> },
        { title: 'Enter every tender, not just the ones with money in them', body: <p>Total cash <em>including bill payments</em>, credit, external credit card, gift card, store account, wallet transfers and lease-to-own. A tender left blank because it was zero and one left blank because you forgot look identical to the reviewer.</p> },
        { title: 'Attach the envelope photo', body: <p>If you declared any cash the photo is required — the submission is refused before anything else is checked. The photo is also read automatically, and a disagreement with what you typed is raised for management.</p> },
        {
          title: 'If it comes back “recount”, recount', body: <>
            <p>You are told it does not tie and nothing more — not the amount, not whether you are over or short, not which attempt this is. Knowing the gap would let you type toward it instead of counting again.</p>
            <p>The third attempt goes through regardless and management picks it up. Do not keep a store open over this.</p>
          </>,
        },
      ],
      done: <> the close is accepted, the photo is attached, and your envelope is sealed. Corrected two days running? Take the walkthrough the app offers before the third.</>,
    },
    {
      title: 'The DM works the exceptions', who: 'Level 2 · district manager', tone: 'dm',
      intro: 'Most closes need nothing from you. These three do.',
      steps: [
        { title: 'Stores that worked and did not close', body: <p>The dashboard surfaces a store with activity and no submission. That is a missing close, not a quiet day — chase it the same evening, while the drawer can still be counted.</p> },
        {
          title: 'Two worked, one closed', body: <>
            <p>Two people on a shift and one closing is <em>fine</em> when the cash ties to the X-report. You get a note, not a flag, and there is nothing to chase.</p>
            <p>It is flagged <Pill tone="red">verify</Pill> only when the money is <em>also</em> off and the second person never closed — and the flag names who did not close.</p>
          </>,
        },
        { title: 'Releases for a genuine re-entry', body: <p>A rep cannot resubmit a day that is already closed. A release lets corrected figures replace the original in place, keeping one row for the day rather than two competing ones.</p> },
      ],
      done: <> every store in your span has a closing for every day it traded, and every flagged pair has been looked at rather than aged out.</>,
    },
    {
      title: 'Management reviews what the gate could not settle', who: 'Level 3 · management review', tone: 'mm',
      intro: 'This level sees the figures the rep and DM deliberately cannot: the POS day, the actual variance, and the full attempt history.',
      steps: [
        { title: 'Work the auto-accepted closes first', body: <p>A close that went through on the third attempt is a rep who could not make their drawer agree three times running. It is the strongest single signal the close process produces.</p> },
        { title: 'Read the attempt log, not just the final row', body: <p>Every try is kept — what was entered, what it was compared against, which direction it was out. Three attempts converging on one number reads very differently from three unrelated guesses.</p> },
        { title: 'Decide: release, accept, or escalate', body: <p>Release for a genuine miscount. Accept where the variance is explained. Escalate where the same rep or store keeps appearing — closing is where cash problems announce themselves first.</p> },
      ],
    },
  ],
  rulesHeading: 'Why the screen behaves as it does',
  rules: [
    { kind: 'bad', claim: 'The rep is never shown the variance.', why: 'Not the amount, not the direction, not the attempt count. A gate that tells you how far off you are is a gate you can type your way through.' },
    { kind: 'ok', claim: 'Only two directions block.', why: 'Cash short and credit over — the two ways a mis-entry moves money in someone’s favour. Cash over and credit under flag and go through.' },
    { kind: 'ok', claim: 'Nobody is ever locked out.', why: 'The third attempt is accepted whatever it says, and raised for review. A rep can always finish their shift; the problem moves to a level that can act on it.' },
    { kind: 'hold', claim: 'No POS data means unknown, not pass.', why: 'With no POS day to compare against, the close is accepted and marked recon-pending — never quietly reported as tying. It gets checked when the data lands.' },
    { kind: 'ok', claim: 'A correction replaces, it does not duplicate.', why: 'A released day is re-entered into the same row, so no report has to guess which of two closings counts.' },
    { kind: 'hold', claim: 'A predefined closer is a preference, not a fact.', why: 'Where a store’s assigned closer no longer works there or was not in that day, the assignment is set aside and reality used — but the stale assignment is still shown, so somebody fixes it.' },
  ],
  table: {
    heading: 'Close states',
    columns: ['State', 'Means', 'Whose move'],
    rows: [
      { key: 'ok', means: <>Declared money ties to the POS day <Pill tone="green">clear</Pill></>, move: '—' },
      { key: 'flagged', means: 'Out, but in a direction that does not block', move: 'DM at review' },
      { key: 'blocked', means: 'Cash short or credit over; rep asked to recount', move: 'Rep' },
      { key: 'recon_pending', means: <>No POS day to compare against yet <Pill tone="amber">unknown</Pill></>, move: 'Nobody, until data lands' },
      { key: 'auto_accepted', means: <>Went through on the third attempt <Pill tone="red">review</Pill></>, move: 'Management' },
    ],
  },
}

// ════════════════════════════════════════════════════════════════════════════════════════════════
// 3 · DM VERIFY
// ════════════════════════════════════════════════════════════════════════════════════════════════
const verifyFigure = (
  <svg viewBox="0 0 1020 470" role="img" style={{ minWidth: 760, maxWidth: 1020 }}
    aria-label="How a DM correction is stored. The rep's submitted figure stays untouched in its own column, the DM's corrected value is written to a separate column beside it, and each save appends a revision recording old value, new value, who changed it and whether the day had already been verified. Reports read the corrected value; exports show both columns.">
    <defs><Arrow id="dv-a" /></defs>
    <rect x="40" y="60" width="216" height="150" rx="3" fill="var(--rb-card)" stroke="var(--rb-rep)" strokeWidth="1.6" />
    <g fill="currentColor">
      <text x="148" y="40" textAnchor="middle" fontSize="11" fontWeight="600" fill="var(--rb-rep)" letterSpacing="1">WHAT THE REP SUBMITTED</text>
      <text x="148" y="88" textAnchor="middle" fontSize="12.5" fontWeight="600">Original figures</text>
    </g>
    <g fontSize="11" fill="currentColor" opacity=".8" fontFamily="ui-monospace, monospace">
      <text x="62" y="116">total cash</text><text x="234" y="116" textAnchor="end">1,240.00</text>
      <text x="62" y="138">credit</text><text x="234" y="138" textAnchor="end">3,110.42</text>
      <text x="62" y="160">bill-pay cash</text><text x="234" y="160" textAnchor="end">180.00</text>
    </g>
    <text x="148" y="192" textAnchor="middle" fontSize="10.5" fontWeight="600" fill="var(--rb-rep)">never overwritten</text>

    <rect x="330" y="60" width="216" height="150" rx="3" fill="var(--rb-card)" stroke="var(--rb-dm)" strokeWidth="1.6" />
    <g fill="currentColor">
      <text x="438" y="40" textAnchor="middle" fontSize="11" fontWeight="600" fill="var(--rb-dm)" letterSpacing="1">WHAT THE DM FOUND</text>
      <text x="438" y="88" textAnchor="middle" fontSize="12.5" fontWeight="600">Corrections only</text>
    </g>
    <g fontSize="11" fill="currentColor" opacity=".8" fontFamily="ui-monospace, monospace">
      <text x="352" y="116">total cash</text><text x="524" y="116" textAnchor="end" fill="var(--rb-dm)" fontWeight="500">1,215.00</text>
      <text x="352" y="138">credit</text><text x="524" y="138" textAnchor="end" opacity=".45">—</text>
      <text x="352" y="160">bill-pay cash</text><text x="524" y="160" textAnchor="end" opacity=".45">—</text>
    </g>
    <text x="438" y="192" textAnchor="middle" fontSize="10.5" fontWeight="600" fill="var(--rb-dm)">a dash means &ldquo;agreed&rdquo;</text>

    <rect x="620" y="60" width="216" height="150" rx="3" fill="var(--rb-seal-dim)" stroke="var(--rb-seal)" strokeWidth="1.8" />
    <g fill="var(--rb-seal)">
      <text x="728" y="40" textAnchor="middle" fontSize="11" fontWeight="600" letterSpacing="1">WHAT REPORTS USE</text>
      <text x="728" y="88" textAnchor="middle" fontSize="12.5" fontWeight="600">Corrected value</text>
    </g>
    <g fontSize="11" fill="var(--rb-seal)" fontFamily="ui-monospace, monospace">
      <text x="642" y="116">total cash</text><text x="814" y="116" textAnchor="end" fontWeight="500">1,215.00</text>
      <text x="642" y="138">credit</text><text x="814" y="138" textAnchor="end" fontWeight="500">3,110.42</text>
      <text x="642" y="160">bill-pay cash</text><text x="814" y="160" textAnchor="end" fontWeight="500">180.00</text>
    </g>
    <text x="728" y="192" textAnchor="middle" fontSize="10.5" fontWeight="600" fill="var(--rb-seal)">DM value where one exists</text>

    <rect x="330" y="288" width="506" height="132" rx="3" fill="var(--rb-card)" stroke="var(--rb-hold)" strokeWidth="1.5" />
    <text x="583" y="268" textAnchor="middle" fontSize="11" fontWeight="600" fill="var(--rb-hold)" letterSpacing="1">AND EVERY SAVE IS KEPT</text>
    <g fontSize="10.5" fill="currentColor" opacity=".78" fontFamily="ui-monospace, monospace">
      <text x="352" y="314">rev 1   total cash   1,240.00 &rarr; 1,215.00   R. Patel   19:42</text>
      <text x="352" y="336">rev 2   total cash   1,215.00 &rarr; 1,232.00   R. Patel   21:08</text>
    </g>
    <rect x="344" y="348" width="478" height="26" rx="2" fill="var(--rb-short-dim)" />
    <text x="352" y="366" fontSize="10.5" fill="var(--rb-own)" fontWeight="500" fontFamily="ui-monospace, monospace">
      rev 2 changed a day that was ALREADY VERIFIED — surfaced, not buried
    </text>
    <text x="352" y="398" fontSize="10.5" fontWeight="600" fill="var(--rb-hold)">a second save never replaces the first correction</text>

    <g stroke="currentColor" strokeWidth="1.4" fill="none" opacity=".7">
      <line x1="256" y1="135" x2="324" y2="135" markerEnd="url(#dv-a)" />
      <line x1="546" y1="135" x2="614" y2="135" markerEnd="url(#dv-a)" />
      <path d="M 438 210 L 438 282" markerEnd="url(#dv-a)" />
    </g>
    <g fontSize="9.6" fill="currentColor" opacity=".6" fontFamily="ui-monospace, monospace">
      <text x="262" y="128">you check</text><text x="552" y="128">overlay</text><text x="446" y="250">each save</text>
      <text x="40" y="240" opacity=".9">both columns appear side by side in the export</text>
    </g>
    <line x1="40" y1="222" x2="836" y2="222" stroke="currentColor" opacity=".18" strokeDasharray="4 4" />
  </svg>
)

const dmVerify: Runbook = {
  stages: [
    { href: '/closing/submit', label: 'Submit Closing', who: 'Rep',
      does: 'Count the drawer, enter every tender, attach the envelope photo.',
      handoff: 'the declared figures and a sealed envelope' },
    { href: '/closing/verify', label: 'DM Verify', who: 'District Manager',
      does: 'Check each store-day against the POS and correct what is wrong.',
      handoff: 'verified figures the money can be collected against' },
    { href: '/closing/pickup', label: 'Cash Pickup', who: 'District Manager',
      does: 'Tick the envelopes you physically take, and count any you opened.',
      handoff: 'cash out of the store, and a count if the seal was broken' },
    { href: '/closing/billpay-pickup', label: 'Bill Payment Pickup', who: 'District Manager',
      does: 'The same collection, for bill-payment cash.',
      handoff: 'bill-payment cash out of the store' },
    { href: '/closing/deposit-recon', label: 'Cash Deposit Recon', who: 'Market Mgr / Accounting',
      does: 'Confirm hand-overs, chase missing slips, drive the day green.',
      handoff: 'every envelope accounted for' },
    { href: '/closing/envelope-report', label: 'Envelope Report', who: 'Market Manager',
      does: 'Count disputed envelopes and raise a chargeback where one is genuinely short.' },
    { href: '/closing/store-cash-on-hand', label: 'Store Cash on Hand', who: 'Owner / Accounting',
      does: 'What is still sitting in stores undeposited.' },
  ],

  slug: 'dm-verify',
  title: 'DM Verify Runbook',
  summary: 'Checking what the stores submitted, and correcting it without erasing it.',
  module: 'closing', audience: 'managers', icon: '✅',
  chain: 'Rep → DM → Market Mgr',
  lede: 'Checking what the stores submitted, correcting it without erasing it, and knowing which partial closings are a problem and which are not. Verifying is a judgement you sign your name to — the record keeps both the figure you found and the figure you replaced.',
  kin: <>Sits between the <strong>Daily Closing Runbook</strong> and the <strong>Store Cash Runbook</strong>: verify the numbers, then collect the money.</>,
  figure: verifyFigure,
  figcaption: <><strong>Correcting is additive.</strong> Your value goes in its own column and the rep&rsquo;s stays where it was, so &ldquo;the numbers were changed after the fact&rdquo; is always answerable from the record rather than from memory. A change made to a day you had already verified is marked as exactly that.</>,
  levels: [
    {
      title: 'The DM verifies', who: 'Level 2 · your span only', tone: 'dm',
      intro: 'You see the stores in your span. Work by store-day, not by rep — a store’s money either ties for the day or it does not.',
      steps: [
        { title: 'Compare against the POS, not against last week', body: <p>Each store card shows what was declared against the day&rsquo;s POS figures. A card that reads clean needs nothing from you; do not re-key figures that already agree.</p> },
        { title: 'Correct only what is actually wrong', body: <p>Leave a field alone if you agree with it. A correction is a statement that the rep&rsquo;s number was wrong, and it is recorded as one — with your name on it.</p> },
        { title: 'Open the envelope photo before correcting cash', body: <p>The photo is one click from the card and travels into the export as a link. Correcting a cash figure without looking at the envelope is guessing with a signature attached.</p> },
        {
          title: 'Handle “two worked, one closed” by the money', body: <>
            <p>Two worked, one closed, cash <em>ties</em>: nothing to chase — you get a note saying so, and it is not a flag.</p>
            <p>Two worked, one closed, money <em>off</em>: flagged, with the person who never closed named. Verify before signing off — that combination is how a shortage gets attributed to the wrong person.</p>
          </>,
        },
        { title: 'Note why, when it is not obvious', body: <p>A correction with a note survives the conversation three weeks later. One without becomes an argument between two people&rsquo;s recollections.</p> },
      ],
      done: <> every store-day in your span is verified or has a named reason it is not — and you have not corrected a single figure you actually agreed with.</>,
    },
    {
      title: 'Market manager and above', who: 'Level 3 · oversight', tone: 'mm',
      intro: 'Your question is not “does this day tie” — the DM answered that. It is “is the verifying itself trustworthy”.',
      steps: [
        { title: 'Look for figures changed after verification', body: <p>A day that was verified and then edited again is marked as such. Occasionally a genuine second look; repeatedly, from the same person, a conversation.</p> },
        { title: 'Read original against corrected in the export', body: <p>Both columns are there. A DM whose corrections run consistently one way, or who corrects one rep far more than others, shows up in that comparison and nowhere else.</p> },
        { title: 'Watch what never gets verified at all', body: <p>Unverified days are the quiet failure: they do not flag and they chase nobody — and where the cash-on-hand basis is <em>verified</em>, they silently keep their cash off the balance sheet.</p> },
      ],
    },
  ],
  rulesHeading: 'What the screen guarantees',
  rules: [
    { kind: 'ok', claim: 'The rep’s figure survives every correction.', why: 'Corrections are stored beside the originals, never on top. Reports read the corrected value; the export shows both.' },
    { kind: 'ok', claim: 'A second save does not erase the first.', why: 'Each changed save appends a revision — old value, new value, who, when. Verification history is a list, not a single current state.' },
    { kind: 'bad', claim: 'Editing an already-verified day is marked.', why: 'Changing money on a day you previously signed off is recorded as exactly that, so it can be asked about rather than discovered.' },
    { kind: 'hold', claim: 'Two worked and one closed is not automatically wrong.', why: 'It flags only when the money is also off. Flagging every partial closing would train everyone to ignore the flag.' },
    { kind: 'hold', claim: 'No POS data is unknown, not green.', why: 'A store-day with nothing to tie against is reported as untied. Showing it clean would be a pass nobody earned.' },
    { kind: 'ok', claim: 'You verify your span, and only your span.', why: 'The store list is your keyset. Verification is meant to be done by someone who knows the store, not whoever opens the screen first.' },
  ],
  table: {
    heading: 'What a card is telling you',
    columns: ['Signal', 'Means', 'Your move'],
    rows: [
      { key: 'clean', means: <>Declared ties to POS <Pill tone="green">ok</Pill></>, move: 'Verify and move on' },
      { key: 'note', means: 'Partial closing, money ties', move: 'Nothing — it explains why it looks odd' },
      { key: 'flag', means: <>Partial closing <em>and</em> money off <Pill tone="red">verify</Pill></>, move: 'Check who did not close' },
      { key: 'dm_corrected', means: 'A figure was changed at verification', move: 'Nothing, unless it is you doing it often' },
      { key: 'edited_after_verify', means: <>Money changed on an already-verified day <Pill tone="red">review</Pill></>, move: 'Market manager’s question' },
      { key: 'unverified', means: <>Nobody has looked <Pill tone="amber">open</Pill></>, move: 'The one that ages badly — clear it' },
    ],
  },
}

// ════════════════════════════════════════════════════════════════════════════════════════════════
// 4 · TENANT IMPLEMENTATION
// ════════════════════════════════════════════════════════════════════════════════════════════════
// Owner 2026-09-12: "we need to organize the set up of a new tenant in an organized way, right now
// we have too many modules which do not have a flow and one thing leads to the other by links on
// their respective pages to a different module altogether".
//
// NOTE FOR THE NEXT READER: this runbook is defined AFTER the three above on purpose.
// `harness_workflow_stacking.py` §A parses the FIRST `stages: [` block out of this file and pins it
// against the closing tile in migration 1003. The three closing runbooks share one stage list, so
// that parse is correct today — but a differently-shaped `stages` block placed earlier in the file
// would silently become what that harness compares, and it would fail for a reason that has nothing
// to do with the closing workflow.
const implFigure = (
  <svg viewBox="0 0 1020 430" role="img" style={{ minWidth: 760, maxWidth: 1020 }}
    aria-label="How a carrier scopes the implementation. One carrier row fans out to the reports registered against it; each report carries both the place it is uploaded by hand and the connector that fetches it automatically, because the report row already names that connector. A carrier the tenant does not run matches no rows, so its reports never appear.">
    <defs><Arrow id="im-a" /></defs>

    <rect x="36" y="150" width="190" height="104" rx="3" fill="var(--rb-card)" stroke="var(--rb-own)" strokeWidth="1.8" />
    <text x="131" y="130" textAnchor="middle" fontSize="11" fontWeight="600" fill="var(--rb-own)" letterSpacing="1">STEP 1 · THE CARRIER</text>
    <text x="131" y="182" textAnchor="middle" fontSize="12.5" fontWeight="600" fill="currentColor">commcalc.carrier</text>
    <text x="131" y="204" textAnchor="middle" fontSize="10.5" fill="currentColor" opacity=".72" fontFamily="ui-monospace, monospace">one row per carrier</text>
    <text x="131" y="224" textAnchor="middle" fontSize="10.5" fill="currentColor" opacity=".72" fontFamily="ui-monospace, monospace">they actually sell</text>
    <text x="131" y="274" textAnchor="middle" fontSize="10.5" fontWeight="600" fill="var(--rb-own)">adding one is a row, never code</text>

    <rect x="330" y="60" width="250" height="92" rx="3" fill="var(--rb-card)" stroke="var(--rb-dm)" strokeWidth="1.6" />
    <text x="455" y="42" textAnchor="middle" fontSize="11" fontWeight="600" fill="var(--rb-dm)" letterSpacing="1">STEP 2–3 · ITS REPORTS</text>
    <g fontSize="10.5" fill="currentColor" opacity=".82" fontFamily="ui-monospace, monospace">
      <text x="348" y="86">report_definitions</text>
      <text x="348" y="108">carrier_id  → whose report it is</text>
      <text x="348" y="130">connector_id → who fetches it</text>
    </g>

    <rect x="330" y="252" width="250" height="92" rx="3" fill="var(--rb-card)" stroke="currentColor" strokeWidth="1.2" opacity=".55" />
    <text x="455" y="234" textAnchor="middle" fontSize="11" fontWeight="600" fill="currentColor" opacity=".6" letterSpacing="1">A CARRIER THEY DO NOT RUN</text>
    <g fontSize="10.5" fill="currentColor" opacity=".5" fontFamily="ui-monospace, monospace">
      <text x="348" y="278">its rows match nothing</text>
      <text x="348" y="300">so its reports never render</text>
      <text x="348" y="322">nothing is branched around</text>
    </g>

    <rect x="672" y="46" width="220" height="106" rx="3" fill="var(--rb-seal-dim)" stroke="var(--rb-seal)" strokeWidth="1.8" />
    <text x="782" y="28" textAnchor="middle" fontSize="11" fontWeight="600" fill="var(--rb-seal)" letterSpacing="1">UPLOAD IT BY HAND</text>
    <g fontSize="10.5" fill="var(--rb-seal)" fontFamily="ui-monospace, monospace">
      <text x="690" y="74">upload_endpoint</text>
      <text x="690" y="96">map the columns,</text>
      <text x="690" y="118">load one file,</text>
      <text x="690" y="140">prove it lands</text>
    </g>

    <rect x="672" y="196" width="220" height="106" rx="3" fill="var(--rb-card)" stroke="var(--rb-mm)" strokeWidth="1.8" />
    <text x="782" y="178" textAnchor="middle" fontSize="11" fontWeight="600" fill="var(--rb-mm)" letterSpacing="1">OR LET IT ARRIVE</text>
    <g fontSize="10.5" fill="currentColor" opacity=".82" fontFamily="ui-monospace, monospace">
      <text x="690" y="224">connector_instances</text>
      <text x="690" y="246">the portal / mailbox</text>
      <text x="690" y="268">that fetches this</text>
      <text x="690" y="290">same report</text>
    </g>

    <g stroke="currentColor" strokeWidth="1.4" fill="none" opacity=".7">
      <path d="M 226 190 L 300 190 L 300 106 L 324 106" markerEnd="url(#im-a)" />
      <path d="M 226 214 L 300 214 L 300 298 L 324 298" markerEnd="url(#im-a)" strokeDasharray="4 4" opacity=".4" />
      <line x1="580" y1="92" x2="666" y2="92" markerEnd="url(#im-a)" />
      <line x1="580" y1="122" x2="666" y2="232" markerEnd="url(#im-a)" />
    </g>
    <path d="M 782 152 L 782 190" stroke="var(--rb-mm)" strokeWidth="1.6" fill="none" markerEnd="url(#im-a)" />
    <text x="798" y="176" fontSize="10" fill="var(--rb-mm)" fontWeight="600">then automate</text>
    <g fontSize="9.6" fill="currentColor" opacity=".6" fontFamily="ui-monospace, monospace">
      <text x="236" y="182">scopes</text><text x="586" y="84">manual</text><text x="586" y="150">automatic</text>
    </g>
    <text x="672" y="330" fontSize="10.5" fontWeight="600" fill="var(--rb-mm)">ONE row names both — that is why they sit together</text>
  </svg>
)

const tenantImplementation: Runbook = {
  // ONE DEFINITION OF THE ORDER, and it is pinned in BOTH directions:
  //   · `WorkflowNext` at the foot of each screen below reads this array (via `stagesAfter`), and
  //   · backend/app/modules/commcalc/implementation_spine.py `SPINE` carries the same sequence for
  //     the wizard that emits the steps.
  // Those two live in different LANGUAGES, so neither tsc nor a Python linter can see them drift.
  // `backend/harness_tenant_implementation.py` §A parses both and requires them equal, in order —
  // the same job `harness_workflow_stacking.py` §A does for the closing tile vs this file.
  stages: [
    { href: '/commcalc/onboarding', label: 'Setup Wizard', who: 'Implementation lead',
      does: 'Say who this tenant is and which carriers they sell. Add a carrier here.',
      handoff: 'the carrier list every step below is scoped to' },
    { href: '/commcalc/connectors', label: 'Connectors', who: 'Implementation lead',
      does: "Register each carrier's source portal and the reports it provides.",
      handoff: 'a named source for each report, and which of them can be automated' },
    { href: '/commcalc/implementation', label: 'Implementation Wizard', who: 'Implementation lead',
      does: "Per carrier: map each report's columns, then load the first file by hand.",
      handoff: 'mapped columns and one proven ingest per report' },
    { href: '/commcalc/store-match', label: 'Store Matching', who: 'Implementation lead',
      does: 'Attach the store names the feed uses to the tenant’s own stores.',
      handoff: 'rows that land under a named store instead of nowhere' },
    { href: '/commcalc/email-imports', label: 'Email & Portal Logins', who: 'Implementation lead',
      does: 'Turn on the automation for each report you just proved, so the file arrives by itself.',
      handoff: 'feeds that keep arriving without anyone uploading' },
    { href: '/commcalc/commission-plans', label: 'Incentive Plans', who: 'Owner / Implementation lead',
      does: 'Define how people are paid from the data that is now flowing.' },
  ],

  slug: 'tenant-implementation',
  title: 'Tenant Implementation Runbook',
  summary: 'Setting up a new tenant as one ordered flow, scoped to the carriers they actually sell.',
  module: 'commissions', audience: 'admins', icon: '🧭',
  chain: 'Implementation lead → Owner',
  lede: 'Standing up a new tenant, in the order it can actually be done. Every step is scoped to the carriers that tenant sells, and each report shows both places it can come from — the upload you do by hand and the automation that does it for you afterwards.',
  kin: <>The same shape as the <strong>Daily Closing Runbook</strong>: one sequence, each screen naming the next. That one is run every evening; this one is run once per tenant.</>,
  figure: implFigure,
  figcaption: <><strong>The carrier is the scope, and the report row is the binding.</strong> A report already records both whose carrier it belongs to and which connector fetches it, so &ldquo;where do I upload this&rdquo; and &ldquo;how do I stop uploading this&rdquo; are two fields on one row rather than two pages you have to know are related. A carrier the tenant does not sell simply matches nothing — there is no rule anywhere naming a carrier.</>,
  levels: [
    {
      title: 'Start with who they sell', who: 'Step 1 · the scope', tone: 'own',
      intro: 'Nothing below can be narrowed until the tenant says which carriers they run. This is the one answer the whole flow hangs on.',
      steps: [
        { title: 'Add each carrier the tenant actually sells', body: <p>Add a carrier from the Setup Wizard itself — it is a row, not a deployment. Until at least one exists there is nothing to scope by, and the flow will tell you so rather than showing you an empty page that looks finished.</p> },
        { title: 'Do not add a carrier they might sell later', body: <p>Every carrier you add puts its reports in front of somebody. A speculative carrier is clutter on every screen below, which is the exact problem this flow exists to remove.</p> },
        { title: 'Mark the one they mostly sell as default', body: <p>The default carrier is what single-carrier screens resolve to. A tenant with one carrier should have it marked, or downstream screens have to guess.</p> },
      ],
      done: <> every carrier the tenant sells exists as a row, and none that they do not.</>,
    },
    {
      title: 'Register the sources, then map one file by hand', who: 'Steps 2–3 · the data', tone: 'dm',
      intro: 'A report has to exist in the registry before it can be mapped, and has to be mapped before anything it feeds can be trusted.',
      steps: [
        { title: 'Register the source before the report', body: <p>A connector is the portal, mailbox or server the file comes from. Registering it first is what lets each report point at it — and that pointer is the whole reason the automation appears beside the upload later.</p> },
        { title: 'Upload a sample and READ the proposal', body: <p>The mapper proposes a column match and shows what it matched on. It is a proposal, not an answer: on a real carrier export we measured a column called <code>Invoiced At</code> holding a store name, <code>Related Tracking Number</code> holding an IMEI, and <code>Region</code> holding a person&rsquo;s name. A name-similarity guess is confidently wrong on all three. Confirm each field against the sample values shown, not against the column&rsquo;s title.</p> },
        { title: 'Load one real file before automating anything', body: <p>Mapping saved is not mapping proven. Import one file and look at what landed. Turning on a sweep over unmapped columns schedules a failure that then repeats nightly.</p> },
      ],
      done: <> every required field on every registered report is mapped, and at least one real file per report has landed and been looked at.</>,
    },
    {
      title: 'Then stop doing it by hand', who: 'Steps 4–6 · the handover', tone: 'mm',
      intro: 'Automation comes last on purpose. It is the step that makes every earlier step repeat without you.',
      steps: [
        { title: 'Match the store names the data uses', body: <p>Feeds name stores their own way. Until each name is attached to a store, rows land under nothing and every store-scoped report quietly under-reports.</p> },
        { title: 'Turn on the automation for reports you proved', body: <p>Each report names its own connector, so the automation is offered next to the upload it replaces. A source that cannot be automated says so — that is usually a vendor instruction or a blocked second factor, not something to keep retrying.</p> },
        { title: 'Only then define how people are paid', body: <p>An incentive plan built on data that is not arriving yet pays out on gaps. Get the feeds landing first.</p> },
      ],
    },
  ],
  rulesHeading: 'What this flow guarantees',
  rules: [
    { kind: 'ok', claim: 'No carrier name decides anything in this flow.', why: 'Which uploads and automations belong to a carrier is rows — commcalc.carrier, report_definitions.carrier_id, connector_instances.carrier_id — so a carrier nobody has built for works the same way as one we have. Where a carrier name still appears elsewhere it is a shipped DEFAULT that a row overrides, never a branch.' },
    { kind: 'ok', claim: 'The automation sits with the upload it replaces.', why: 'A report row already names the connector that fetches it, so the two are one record. Nobody has to know that a page in one module relates to a page in another.' },
    { kind: 'hold', claim: 'A carrier with nothing registered is shown, not hidden.', why: 'It is named, with the action that fixes it. Dropping it would make an unfinished implementation look complete.' },
    { kind: 'bad', claim: 'A column match is never applied on your behalf.', why: 'The mapper proposes and shows its basis; a human confirms. Measured on a real export, three columns hold something other than what their names say.' },
    { kind: 'hold', claim: 'An unclassified upload stays visible.', why: 'A tile whose report is not registered to any carrier is shown to everyone. Failing to classify something must never hide a report a tenant needs.' },
    { kind: 'ok', claim: 'This is not a new wizard.', why: 'It is the Setup Wizard that already existed, put in order. The complaint was too many setup screens; another one would have been the same defect with a new name.' },
  ],
  table: {
    heading: 'What a report row is telling you',
    columns: ['Signal', 'Means', 'Your move'],
    rows: [
      { key: 'not registered', means: <>The carrier has no reports at all <Pill tone="amber">empty</Pill></>, move: 'Register them on Connectors' },
      { key: 'unmapped', means: 'Registered, but required fields have no column', move: 'Upload a sample and confirm the mapping' },
      { key: 'mapped', means: <>Every required field has a column <Pill tone="green">ready</Pill></>, move: 'Import one real file and check it' },
      { key: 'manual only', means: 'The source cannot be automated', move: 'Nothing — keep uploading, or route it by email' },
      { key: 'automation off', means: 'A connector exists but is not pulling this report', move: 'Turn it on once the file has landed once' },
      { key: 'automation on', means: <>The file arrives by itself <Pill tone="green">done</Pill></>, move: '—' },
    ],
  },
}

// ── the registry ────────────────────────────────────────────────────────────────────────────────
// Sort order is the order the work actually happens in, not alphabetical: close, verify, collect.
// Tenant implementation sits last: it is run once, before any of the others can be run at all.
// Scheduling joins this list when its runbook lands.
export const FLOWCHARTS: Runbook[] = [dailyClosing, dmVerify, storeCash, tenantImplementation]

// ── THE WORKFLOW, ASKED BY BOTH READERS ─────────────────────────────────────────────────────────
// Owner directive 2026-09-10: "the modules for these should be stacked properly based on thr work
// flow in one tile so the user does not have to loo for the next module … and also the current
// module should ask the chart what do they want to do next".
//
// `CLOSING_WORKFLOW` is that chart, as data. The stacked hub tile (migration 1003) and the
// WorkflowNext prompt at the foot of each screen both read THIS — so the order somebody is walked
// through cannot drift from the order the runbook teaches.
export const CLOSING_WORKFLOW = dailyClosing.stages

/** The runbook that teaches this screen — what "ask the chart" resolves to. */
export function runbookForScreen(href: string) {
  return FLOWCHARTS.find(f => f.stages.some(s => s.href === href))
}

// ── ONE SEQUENCER, N WORKFLOWS (2026-09-12) ─────────────────────────────────────────────────────
// These two used to read `CLOSING_WORKFLOW` directly, which hard-bound every "what next" prompt in
// the app to the closing workflow. That was invisible while closing was the only sequence — but it
// meant a second workflow could never use the mechanism: `runbookForScreen` would find its runbook
// while `stagesAfter` returned [], so `WorkflowNext` would render a heading with nothing under it.
//
// EXTENDED, NOT FORKED. Writing a second sequencer for the implementation flow would have been two
// implementations of "what comes after this screen" — the drift the house rules forbid, and the
// exact thing WorkflowNext's own header comment promises it does not do. So they now ask
// `runbookForScreen` and walk THAT book's stages.
//
// BYTE-IDENTICAL FOR THE CLOSING SCREENS, provably: `runbookForScreen` returns the FIRST runbook in
// FLOWCHARTS containing the href, FLOWCHARTS starts with `dailyClosing`, and `CLOSING_WORKFLOW` IS
// `dailyClosing.stages`. Every closing href therefore resolves to the same array it did before.
// Pinned by harness_tenant_implementation.py §B so a reorder of FLOWCHARTS cannot quietly change
// which chain a closing screen is walked through.

/** The stage AFTER `href` in the workflow that contains it, or undefined at the end of the chain. */
export function nextStage(href: string) {
  return stagesAfter(href)[0]
}

/** Every stage after `href`, so a caller who may not open the next one can be offered the one after. */
export function stagesAfter(href: string) {
  const book = runbookForScreen(href)
  if (!book) return []
  const i = book.stages.findIndex(s => s.href === href)
  return i >= 0 ? book.stages.slice(i + 1) : []
}

export function flowchartBySlug(slug: string): Runbook | undefined {
  return FLOWCHARTS.find(f => f.slug === slug)
}
