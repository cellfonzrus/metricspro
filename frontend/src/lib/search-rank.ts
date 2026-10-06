// THE ONE HOME for "what did the person mean by what they typed" in the platform search console
// (owner directive 2026-10-05).
//
// WHY THIS FILE EXISTS — the defect it replaces, stated so nobody re-derives it. The ⌘/ bar scored a
// result by counting how many TYPED WORDS appeared anywhere in a report's label, category or
// description, one point each. Asked *"who is working in 509 nostrand"* it returned:
//
//     3.0  Management Watchdog     matched: who, is, in
//     3.0  Reports Index           matched: who, is, in
//     2.0  Activations             matched: is, in
//
// Nothing matched "nostrand". Nothing matched "working". The ranking was carried entirely by **who**,
// **is** and **in**, ties broke alphabetically, and the owner was taken to a carrier report. Two
// separate faults, and both are fixed here rather than tuned:
//
//   1. EVERY WORD SCORED THE SAME. A word that appears in half the catalogue carries no information,
//      so it may not decide an ordering. `STOPWORDS` below earn zero on their own, and a result that
//      matched nothing else is not a result at all.
//   2. A NUMBER WAS NOT A WORD. The old tokenizer dropped tokens of length 1 but kept stopwords —
//      exactly backwards. "509" is the most specific thing in that sentence; here a digit run is
//      always a content token, which is what lets a store code or a street number find its store.
//
// WHAT THIS FILE IS NOT. It holds no catalogue: the things that can be found are the registries that
// already exist (the nav, the report categories, the settings screens) plus the entities the caller
// resolved from the platform's own endpoints. A second copy of "which pages exist" here would be the
// duplicate defect the house rules forbid, so the caller passes items IN and this file only ranks.
//
// It also decides nothing about permissions. The caller filters by entitlement BEFORE ranking, so an
// item that reaches this file is one the viewer may already open. Ranking an item the viewer cannot
// see would leak its existence, which is why the filter is upstream and not a flag here.
//
// PURE and import-free, so `frontend/prove_search_rank.mjs` can drive the real functions under Node
// with no React, no network and no build step.

/** What kind of thing was found. The console groups by this, and `looksLikeQuestion` never changes it. */
export type SearchKind = 'page' | 'report' | 'setting' | 'store' | 'person' | 'customer'

export type Searchable = {
  /** Stable id, unique across kinds — the caller's own (`report:/commcalc/sales-report`, `store:B-1115`). */
  key: string
  kind: SearchKind
  /** What the person would call it. Weighted highest, because it is the thing they typed. */
  label: string
  /** Where selecting it goes. */
  href: string
  /** A second line that is part of THIS thing's own identity — a store's street address, a
   *  customer's phone. Weighted near a label, because "509 Nostrand" IS how its owner refers to
   *  that store; see the defect note above. */
  subtitle?: string
  /** A second line BORROWED from something else — the store a person works at, the market a store
   *  sits in. Weighted well below `subtitle`, and the distinction is load-bearing rather than
   *  cosmetic: asked about "509 Nostrand", the STORE at that address must outrank the person who
   *  happens to work there, and before this field existed the two tied and lost to the alphabet. */
  context?: string
  /** The group it sits in (nav section, report category). Weak: it matches many things. */
  category?: string
  /** Other spellings — the ScreenLink aliases, a store's POS synonyms, a nickname. */
  aliases?: string[]
  /** Prose. Weakest, and the only field the old ranking really used. */
  desc?: string
}

export type Hit = {
  item: Searchable
  score: number
  /** Which CONTENT tokens this hit actually accounted for — shown to the person so a surprising
   *  result explains itself instead of looking arbitrary. */
  matched: string[]
}

// Words that carry no information about WHICH thing is meant. They are not noise to be stripped from
// the person's intent — `looksLikeQuestion` reads several of them — they simply may not rank a result.
// Kept deliberately short: a word is here because it is common across this catalogue, not because it
// is a "stop word" in general English.
export const STOPWORDS = new Set([
  'a', 'an', 'and', 'any', 'are', 'as', 'at', 'be', 'been', 'but', 'by', 'can', 'did', 'do', 'does',
  'for', 'from', 'get', 'give', 'had', 'has', 'have', 'he', 'her', 'his', 'how', 'i', 'if', 'in',
  'into', 'is', 'it', 'its', 'just', 'many', 'me', 'much', 'my', 'need', 'of', 'on', 'or', 'our',
  'out', 'over', 'please', 'see', 'she', 'should', 'show', 'so', 'tell', 'that', 'the', 'their',
  'them', 'then', 'there', 'these', 'they', 'this', 'to', 'up', 'us', 'was', 'we', 'were', 'what',
  'when', 'where', 'which', 'who', 'whom', 'whose', 'why', 'will', 'with', 'would', 'you', 'your',
])

/** A word that opens a question. Read by `looksLikeQuestion`, NOT by the ranker. */
const INTERROGATIVES = new Set([
  'who', 'what', 'which', 'why', 'how', 'when', 'where', 'whose', 'whom', 'is', 'are', 'was', 'were',
  'do', 'does', 'did', 'can', 'should', 'would', 'will', 'am',
])

/** Lowercase word/number runs. A DIGIT RUN IS ALWAYS KEPT, whatever its length — "509" is the whole
 *  point of "509 Nostrand". A one-letter word is dropped; a one-digit one is not. */
export function tokens(q: string): string[] {
  const raw = String(q || '').toLowerCase().match(/[a-z0-9]+/g) || []
  return raw.filter(t => t.length > 1 || /^\d$/.test(t))
}

/** The tokens that can rank something: everything that is not a stopword. A pure-stopword query
 *  ("who is it") has none, and then nothing should be ranked at all. */
export function contentTokens(q: string): string[] {
  return tokens(q).filter(t => !STOPWORDS.has(t))
}

/** Is what they typed a QUESTION rather than the name of a place to go?
 *
 *  This decides which half of the console answers, so it is deliberately conservative in one
 *  direction: a false "question" sends somebody to the assistant when a page would have done, which
 *  costs them a click. A false "destination" is what put the owner on a carrier report.
 *
 *  Three signals, any of which is enough: it ends in a question mark; it opens with an interrogative
 *  or an auxiliary ("who is…", "can I…"); or it is a sentence-shaped run of words (4+) carrying a
 *  stopword, which a page name never is ("Carrier Earned vs Employee Paid" has none). */
export function looksLikeQuestion(q: string): boolean {
  const s = String(q || '').trim()
  if (!s) return false
  if (s.endsWith('?')) return true
  const t = tokens(s)
  if (!t.length) return false
  if (INTERROGATIVES.has(t[0])) return true
  return t.length >= 4 && t.some(w => STOPWORDS.has(w))
}

// ── field weights ────────────────────────────────────────────────────────────────────────────────
// An exact label hit beats a prefix, which beats a word inside the label, which beats an alias, and
// prose comes last. The numbers only have to preserve that order; they are not tuned against a
// corpus, and the proof asserts the ORDER rather than the values so a later re-weighting stays honest.
const W_LABEL_EXACT = 120
const W_LABEL_START = 70
const W_LABEL_WORD = 45
const W_SUBTITLE = 40      // a store's address: how its owner names it
const W_CONTEXT = 18       // the store a PERSON works at: real, but not what the thing IS
const W_ALIAS = 38
const W_CATEGORY = 10
const W_DESC = 6
/** Every content token accounted for is worth more than any single field, so a hit that explains the
 *  whole query always outranks one that explains a word of it loudly. */
const W_COVERAGE = 200

const norm = (s?: string) => String(s || '').toLowerCase().trim()
const words = (s?: string) => new Set(tokens(s || ''))

/** A conservative shared-stem match, so "working" finds a page whose text says "work" and
 *  "scheduled". One word must be a prefix of the other and the shorter must be at least four
 *  characters — long enough that "work"/"working" matches while "pay"/"payroll" and "in"/"inventory"
 *  do not, which is the whole reason for the floor. Never applied to a digit run: "50" is not "509".
 *  Returns false for an exact match, which the callers have already scored higher. */
function sharedStem(a: string, b: string): boolean {
  if (a === b) return false
  if (/\d/.test(a) || /\d/.test(b)) return false
  const [short, long] = a.length <= b.length ? [a, b] : [b, a]
  return short.length >= 4 && long.startsWith(short)
}

/** Does any word of `text` START with `t`? This is the loose fallback, and it is deliberately a
 *  WORD-PREFIX test rather than a substring one. A substring test reads well and is wrong: "509"
 *  would match the store at 1509, and a short token would match the middle of an unrelated word.
 *  A prefix is what a person typing half a name actually means — "pay" finds Payroll, "nostr" finds
 *  Nostrand Ave — while "509" stays away from 1509. */
function wordPrefix(text: string | undefined, t: string): boolean {
  for (const w of words(text)) if (w !== t && w.startsWith(t)) return true
  return false
}

/** Does any word of `text` share a stem with `t`? */
function stemHit(text: string | undefined, t: string): boolean {
  for (const w of words(text)) if (sharedStem(w, t)) return true
  return false
}

function fieldScore(item: Searchable, t: string): number {
  const label = norm(item.label)
  if (label === t) return W_LABEL_EXACT
  let best = 0
  if (label.startsWith(t)) best = W_LABEL_START
  else if (words(label).has(t)) best = W_LABEL_WORD
  else if (wordPrefix(label, t)) best = W_LABEL_WORD - 10
  if (best) return best
  if (stemHit(label, t)) return W_LABEL_WORD - 15
  if (words(item.subtitle).has(t) || wordPrefix(item.subtitle, t)) return W_SUBTITLE
  for (const a of item.aliases || []) {
    if (norm(a) === t) return W_ALIAS + 6
    if (words(a).has(t) || wordPrefix(a, t)) return W_ALIAS
    if (stemHit(a, t)) return W_ALIAS - 8
  }
  if (words(item.context).has(t) || wordPrefix(item.context, t)) return W_CONTEXT
  if (words(item.category).has(t)) return W_CATEGORY
  if (words(item.desc).has(t) || stemHit(item.desc, t)) return W_DESC
  return 0
}

/**
 * Rank `items` for the query. Returns at most `limit` hits, best first.
 *
 * THE RULE THAT FIXES THE REPORTED DEFECT: a hit must account for at least one CONTENT token. A
 * result that only matched "who", "is" or "in" scores nothing and is not returned, so an empty
 * result is an honest "I did not find that" — which the console can answer by offering the
 * assistant, instead of handing over a confident wrong page.
 *
 * Ties break on coverage, then score, then label, so the order never depends on the input order.
 */
export function rank(items: Searchable[], q: string, limit = 8): Hit[] {
  const ct = contentTokens(q)
  if (!ct.length) return []
  const out: Hit[] = []
  for (const item of items || []) {
    let score = 0
    const matched: string[] = []
    for (const t of ct) {
      const s = fieldScore(item, t)
      if (s > 0) { score += s; matched.push(t) }
    }
    if (!matched.length) continue
    score += W_COVERAGE * (matched.length / ct.length)
    out.push({ item, score: Math.round(score * 10) / 10, matched })
  }
  out.sort((a, b) =>
    b.matched.length - a.matched.length ||
    b.score - a.score ||
    a.item.label.localeCompare(b.item.label) ||
    a.item.key.localeCompare(b.item.key))
  return out.slice(0, Math.max(0, limit))
}

/** Does any hit explain the WHOLE query? The console uses this to decide whether to lead with a
 *  destination or with the assistant: a question nothing covers is a question, not a bad search. */
export function fullyCovered(hits: Hit[], q: string): boolean {
  const n = contentTokens(q).length
  return n > 0 && hits.some(h => h.matched.length === n)
}

/** WHICH assistant door an unexplained question belongs to (owner 2026-10-05: *"the assistant
 *  should be able to give the most appropriate solution"*). The panel has had two doors since §52 —
 *  `/core/data-qa` for this tenant's numbers and `/helpdesk/ai-assist` for how the product works —
 *  and until now the person had to pick. Picking for them is the difference between "someone wanted a
 *  password reset" getting an answer and getting a refusal from the door that has no database.
 *
 *  'howto' when the question is about DOING something — a verb of change ("reset", "set up", "turn
 *  on"), or a "how/where/can I" opener that is not asking for a quantity. 'data' otherwise, which is
 *  the safe default: the data door reads the real reports, so a misrouted question there comes back
 *  with a number rather than with nothing.
 *
 *  Note the deliberate exception: "how much" and "how many" are quantities, so they stay 'data' even
 *  though they open with "how". */
export type AskDoor = 'data' | 'howto'

// Verbs of CHANGE. A question containing one is about doing something to the product, not about what
// the numbers say — "reset a password", "add a store", "turn on the digest".
const DOING = new Set([
  'reset', 'change', 'update', 'edit', 'enable', 'disable', 'add', 'remove', 'delete', 'create',
  'configure', 'setup', 'install', 'invite', 'assign', 'grant', 'revoke', 'upload', 'import',
  'export', 'fix', 'rename', 'move', 'switch', 'turn', 'set', 'approve', 'submit', 'print',
])
// Words that mean the person wants a FIGURE, whatever else the sentence contains.
const QUANTITY = new Set([
  'much', 'many', 'total', 'revenue', 'profit', 'sales', 'commission', 'payout', 'best', 'worst',
  'top', 'average', 'count', 'margin', 'activations', 'gross', 'net',
])

export function askDoor(q: string): AskDoor {
  const t = tokens(q)
  if (!t.length) return 'data'
  // "how much" / "how many" ask for a FIGURE, not for instructions — checked before the opener rule
  // below, because they open with the same word as "how do I".
  if (t[0] === 'how' && (t[1] === 'much' || t[1] === 'many')) return 'data'
  // An explicit how-to opener decides it: "how do I …", "where is …", "can I …". This is checked
  // BEFORE the quantity words, and the reason is a case that caught the first version of this
  // function: "how do i upload the commission ledger" contains "commission" and is still a how-to
  // question. The opener is what the person is asking FOR; a noun further along is only the subject.
  if (t[0] === 'how' || t[0] === 'where' || (t[0] === 'can' && t[1] === 'i')) return 'howto'
  // No opener: a verb of change means doing, unless a quantity word says the person wants a figure
  // ("set the revenue target" is doing; "what did we add in revenue" is not).
  if (t.some(w => DOING.has(w)) && !t.some(w => QUANTITY.has(w))) return 'howto'
  return 'data'
}

export type Intent = 'navigate' | 'ask' | 'empty'

/**
 * What the console should DO with what was typed — the one decision, so the keyboard, the mouse and
 * the Enter key cannot disagree about it.
 *
 *   'empty'    — nothing typed, or only stopwords: show the recent/suggested list, rank nothing.
 *   'ask'      — it reads as a question and no result explains all of it: lead with the assistant.
 *   'navigate' — a result explains what they typed, or it does not read as a question at all.
 *
 * Note the deliberate asymmetry: a question that IS fully covered still navigates ("who is my best
 * rep" is a question, but if a report is literally called that, open it). Only an unexplained
 * question goes to the assistant.
 */
export function intent(q: string, hits: Hit[]): Intent {
  if (!contentTokens(q).length) return 'empty'
  if (looksLikeQuestion(q) && !fullyCovered(hits, q)) return 'ask'
  if (!hits.length) return 'ask'
  return 'navigate'
}

/** What a KEYSTROKE does, which is not the same question as what was typed. `intent` reads the
 *  words; this reads the words together with what the console can actually reach right now. */
export type SubmitAction = 'figure' | 'ask' | 'navigate' | 'unanswered'

/**
 * The one home for "Enter was pressed — now what", and the reason it exists is a reported defect.
 *
 * Asked *"which sales rep worked in 509 today"* the console correctly judged the question
 * unexplained (`intent` → 'ask') and then, because the assistant was not switched on for the tenant,
 * fell through and navigated to the Sales Report anyway. *"who worked in 509 today"* landed on Pay
 * period & work-week the same way. Both are the class the console was built to end: **a best guess
 * presented as an answer.** An unexplained question whose assistant is unreachable is not a
 * navigation, it is a "no" — and saying so is the only honest move left.
 *
 * So the fall-through is gone. `unanswered` renders the reason and leaves every ranked row clickable,
 * which keeps the destination one deliberate click away instead of arriving unasked.
 */
export function submitAction(
  mode: Intent,
  opts: { hasFigure?: boolean; canAsk?: boolean; hasDest?: boolean },
): SubmitAction {
  // A figure read from the report's own endpoint is deterministic and always wins.
  if (opts.hasFigure) return 'figure'
  if (mode === 'empty') return 'unanswered'
  if (mode === 'ask') return opts.canAsk ? 'ask' : 'unanswered'
  return opts.hasDest ? 'navigate' : 'unanswered'
}
