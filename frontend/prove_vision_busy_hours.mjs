// Proves the two decisions behind the Busy Hours view, offline — no network, no React, no DB.
//
// The page reports Google's own person events. Everything it says is a claim about a store's day,
// and two of those claims are decisions rather than arithmetic:
//
//   peakHour     — WHICH hour gets called "busiest" when two tie, and what is said when a store has
//                  no events at all. Get the tie rule wrong and the headline flips as rows reorder;
//                  get the empty case wrong and a store with zero sightings is told midnight is its
//                  busiest hour.
//   perDayLabel  — an hour that genuinely saw somebody but averages under 0.1/day. The backend
//                  rounds to one decimal, so the row reads "1 sighting · 0 per day" — a table
//                  contradicting itself on one line, on exactly the marginal hours an operator is
//                  deciding whether to open for.
//
// Run:  node frontend/prove_vision_busy_hours.mjs
import { peakHour, perDayLabel, hourLabel } from './src/lib/vision.ts'

let pass = 0, fail = 0
const eq = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want)
  if (ok) pass++
  else { fail++; console.log(`FAIL ${name}\n  got  ${JSON.stringify(got)}\n  want ${JSON.stringify(want)}`) }
}
// A full 24-row payload, the shape the endpoint always returns (every hour present, zeros included).
const day = (counts, daysSeen = 10) =>
  counts.map((e, hour) => ({ hour, events: e, per_day: Math.round(e / daysSeen * 10) / 10 }))
const flat = n => new Array(24).fill(n)

// ── peakHour ────────────────────────────────────────────────────────────────────────────────────
const trading = flat(0)
trading[10] = 24; trading[13] = 40; trading[17] = 61; trading[19] = 12
eq('a normal trading day picks the single busiest hour', peakHour(day(trading)).hour, 17)

// THE TIE. Strict `>` means the EARLIEST wins. A manager told "3p and 7p tie" still has to pick one,
// and the earlier is where the day's staffing decision gets made.
const tie = flat(0); tie[15] = 50; tie[19] = 50
eq('a tie goes to the EARLIER hour', peakHour(day(tie)).hour, 15)
eq('...and reordering the rows does not change that',
  peakHour(day(tie).slice().reverse()).hour, 15)

// THE EMPTY STORE. hour -1 is the signal to print a dash. Returning hour 0 would have the page
// announce that midnight is the busiest hour of a store that saw nobody.
eq('a store with no events at all reports hour -1', peakHour(day(flat(0))).hour, -1)
eq('...and -1 is not a real hour, so the page can tell', hourLabel(-1) === undefined, false)
eq('an empty array is the same as an empty day', peakHour([]).hour, -1)
eq('undefined rows do not throw', peakHour(undefined).hour, -1)

// One busy hour and nothing else — the single-camera, just-switched-on case.
const lone = flat(0); lone[14] = 1
eq('one lone sighting is still the peak', peakHour(day(lone)).hour, 14)
eq('...and it carries its count', peakHour(day(lone)).events, 1)

// Midnight can legitimately BE the peak, and must not be confused with the empty signal.
const late = flat(0); late[0] = 9; late[1] = 2
eq('midnight can genuinely be the busiest hour', peakHour(day(late)).hour, 0)

// ── perDayLabel ─────────────────────────────────────────────────────────────────────────────────
// THE REGRESSION. 1 sighting across 27 days = 0.037 -> the backend rounds it to 0.0, and the row
// would read "1 sighting · 0 per day".
eq('1 sighting over 27 days says "<0.1", never "0"',
  perDayLabel({ hour: 7, events: 1, per_day: 0 }), '<0.1')
eq('2 over 27 days is still under a tenth', perDayLabel({ hour: 7, events: 2, per_day: 0.1 }), '0.1')
eq('a genuinely empty hour is a flat 0', perDayLabel({ hour: 3, events: 0, per_day: 0 }), '0')
eq('a real rate is printed as the backend rounded it',
  perDayLabel({ hour: 17, events: 61, per_day: 2.3 }), '2.3')
eq('a whole number keeps no fake decimal', perDayLabel({ hour: 12, events: 40, per_day: 4 }), '4')
eq('exactly 0.1 is above the floor, so it prints itself',
  perDayLabel({ hour: 9, events: 3, per_day: 0.1 }), '0.1')
eq('a large rate is untouched', perDayLabel({ hour: 17, events: 4000, per_day: 148.1 }), '148.1')
eq('a missing row does not throw', perDayLabel(undefined), '0')

// The invariant that ties the two together: no row may ever show a non-zero count beside "0" a day.
const mixed = day([0, 0, 0, 0, 0, 0, 0, 1, 2, 9, 24, 38, 41, 35, 44, 52, 61, 74, 66, 48, 29, 12, 4, 1], 27)
const contradictions = mixed.filter(r => r.events > 0 && perDayLabel(r) === '0')
eq('NO row ever reads "n sightings · 0 per day"', contradictions, [])
eq('...and every empty hour still reads a plain 0',
  mixed.filter(r => r.events === 0).every(r => perDayLabel(r) === '0'), true)
eq('the peak of that day is 5p', peakHour(mixed).hour, 17)

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
