"""Employee behavior analytics from voice transcripts — redaction, rubric matching, day scoring.

WHAT IS BEING MEASURED, HONESTLY
────────────────────────────────
This scores **what a salesperson said and when**, against a rubric the tenant writes. It does not
measure whether they are good at their job, and nothing in here should ever be presented as if it
did. A rep who greets every customer inside ten seconds, asks what they came in for, and covers
accessories and protection on every ticket will score high; so will a rep who has memorised the
phrases. The number is a COACHING PROMPT — "you closed 40 conversations and asked about protection
on 6 of them" — and the UI states that. Tying it to pay would be both wrong and, given the recording
consent involved, legally reckless; the migration deliberately gives it no path into any payout table.

WHOSE SPEECH
────────────
Employees only, and only consenting ones. `ingest.py` drops any segment whose speaker is not the
employee before it reaches storage, so the customer's half of the conversation is never written down.
That is not squeamishness — it is what makes the whole feature defensible: the store is recording its
own staff, with their signed consent, not its customers.

REDACTION
─────────
Whatever the ASR heard is scrubbed before it is stored: phone numbers, emails, long digit runs that
look like a card or an account, and SSN-shaped runs. A rep reading a customer's card number aloud
into a transcript store would be a PCI incident created by an analytics feature, so the redactor runs
at INGEST — the unredacted string is never persisted, not even briefly.

RULES ARE DATA (RULE TWO)
─────────────────────────
`DEFAULT_RULES` seeds `core.vision_behavior_rule`; after that the tenant owns the rubric. A store
selling home internet wants a different pitch checklist than one selling tablets, and neither should
need a deploy. The matcher is deliberately plain phrase containment over normalized text — not a
model — so an operator can predict exactly why a segment did or did not hit, which is the only way a
coaching number survives being disputed by the person it is about.
"""
import re
import unicodedata

# ── the seed rubric ──────────────────────────────────────────────────────────────────────────────
# weight: contribution toward the 0..100 day score. window_s: only counts inside N seconds of the
# customer's arrival (greeting is the only rule that is time-sensitive by default).
DEFAULT_RULES = [
    {"rule_key": "greeting", "label": "Greeted the customer", "category": "sales", "weight": 20,
     "polarity": "positive", "window_s": 30, "sort_order": 10,
     "phrases": ["welcome to", "welcome in", "hi there", "hello", "good morning", "good afternoon",
                 "good evening", "how are you", "how can i help", "how may i help", "what can i do for you"]},
    {"rule_key": "discovery", "label": "Asked what they came in for", "category": "sales", "weight": 15,
     "polarity": "positive", "window_s": None, "sort_order": 20,
     "phrases": ["what brings you in", "what are you looking for", "are you looking for",
                 "who are you with right now", "who's your carrier", "how many lines",
                 "what are you paying", "tell me about"]},
    {"rule_key": "needs_probe", "label": "Probed the real need", "category": "sales", "weight": 10,
     "polarity": "positive", "window_s": None, "sort_order": 30,
     "phrases": ["how do you use your phone", "do you stream", "how much data", "who else is on the plan",
                 "is that working for you", "what do you not like about"]},
    {"rule_key": "pitch_plan", "label": "Presented a plan", "category": "sales", "weight": 10,
     "polarity": "positive", "window_s": None, "sort_order": 40,
     "phrases": ["unlimited", "per line", "per month", "the plan includes", "switch and save",
                 "we can get you on", "autopay"]},
    {"rule_key": "pitch_accessory", "label": "Offered accessories", "category": "sales", "weight": 10,
     "polarity": "positive", "window_s": None, "sort_order": 50,
     "phrases": ["case", "screen protector", "charger", "earbuds", "bundle", "protect your screen",
                 "keep it safe"]},
    {"rule_key": "pitch_protection", "label": "Offered protection / insurance", "category": "sales",
     "weight": 10, "polarity": "positive", "window_s": None, "sort_order": 60,
     "phrases": ["protection plan", "insurance", "if it breaks", "cracked screen", "deductible",
                 "replacement"]},
    {"rule_key": "close", "label": "Asked for the sale", "category": "sales", "weight": 15,
     "polarity": "positive", "window_s": None, "sort_order": 70,
     "phrases": ["let's get you set up", "want me to get that started", "should we go ahead",
                 "i can activate that today", "would you like to", "let's do it", "ready to"]},
    {"rule_key": "thanks", "label": "Closed the interaction well", "category": "service", "weight": 10,
     "polarity": "positive", "window_s": None, "sort_order": 80,
     "phrases": ["thank you for coming", "thanks for stopping", "have a great", "come back anytime",
                 "anything else i can help"]},
    # Negative rules subtract. These are the things a manager genuinely wants flagged, not profanity
    # policing — the first two are the ones that lose a sale and the third is a compliance exposure.
    {"rule_key": "dismissive", "label": "Dismissed the customer", "category": "service", "weight": 15,
     "polarity": "negative", "window_s": None, "sort_order": 90,
     "phrases": ["i can't help you", "that's not my problem", "you'll have to call", "nothing i can do",
                 "we don't do that here"]},
    {"rule_key": "price_only", "label": "Led with price, no discovery", "category": "sales", "weight": 5,
     "polarity": "negative", "window_s": None, "sort_order": 100,
     "phrases": ["that one's cheaper", "cheapest one is", "just get the cheap"]},
    {"rule_key": "unapproved_claim", "label": "Made an unapproved promise", "category": "compliance",
     "weight": 20, "polarity": "negative", "window_s": None, "sort_order": 110,
     "phrases": ["i guarantee", "it's completely free", "there's no contract at all", "no fees ever",
                 "i promise you won't"]},
]

# ── redaction ────────────────────────────────────────────────────────────────────────────────────
# Order matters: the long-digit-run pattern would otherwise eat a phone number and mislabel it.
_REDACTORS = (
    ("[email]", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", re.I)),
    ("[card]", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("[ssn]", re.compile(r"\b\d{3}[ -]\d{2}[ -]\d{4}\b")),
    ("[phone]", re.compile(r"\b(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}\b")),
    ("[acct]", re.compile(r"\b\d{8,}\b")),
)


def redact(text: str):
    """(redacted_text, replacement_count). Runs at INGEST — the unredacted string is never stored.

    An ASR transcript spells numbers inconsistently ("four one five..."), so this catches the digit
    forms and not the spelled-out ones. That is a real limit and it is stated in the docs rather than
    papered over: the mitigation that actually holds is the short retention window on transcripts,
    not a regex claiming to be complete."""
    s = text or ""
    n = 0
    for token, rx in _REDACTORS:
        s, k = rx.subn(token, s)
        n += k
    return s, n


# ── matching ─────────────────────────────────────────────────────────────────────────────────────
_APOSTROPHES = re.compile(r"[\u2018\u2019\u02bc']")


def normalize(text: str) -> str:
    """Lowercase, strip accents, DELETE apostrophes, then collapse remaining punctuation to spaces.

    ASR output is inconsistently punctuated, so matching raw text would miss "how can i help?" vs
    "how can I help". The apostrophe is deleted rather than collapsed to a space for a specific
    reason found while proving this: engines transcribe the same phrase as both "i can't help you"
    and "i cant help you", and a rule author writes it whichever way occurs to them. Collapsing to a
    space turns one into "i can t help you" and the other into "i cant help you", which do not match
    — so a compliance rule would silently never fire depending on how someone typed it. Deleting the
    apostrophe maps every spelling to "cant" and the rule fires either way."""
    s = unicodedata.normalize("NFKD", text or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = _APOSTROPHES.sub("", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return f" {s.strip()} "


def match_segment(text: str, rules, elapsed_s=None) -> dict:
    """{rule_key: hits} for one transcript segment.

    `elapsed_s` is how long the customer had been in the store when this was said; a rule with a
    `window_s` only counts inside that window. A "greeting" said four minutes after someone walked in
    is not a greeting, and counting it as one is exactly how a behavior metric stops meaning anything.
    """
    norm = normalize(text)
    hits = {}
    for r in rules or []:
        if not r.get("is_active", True):
            continue
        w = r.get("window_s")
        if w is not None and elapsed_s is not None and elapsed_s > float(w):
            continue
        n = 0
        for phrase in r.get("phrases") or []:
            p = normalize(phrase).strip()
            if p and p in norm:
                n += 1
        if n:
            hits[r.get("rule_key")] = hits.get(r.get("rule_key"), 0) + n
    return hits


def score_interactions(segments, rules, min_greeting_s: int = 30) -> dict:
    """Score one employee's day.

    `segments` is a list of dicts:
        {text, duration_s, visit_id, elapsed_s}   elapsed_s = seconds since that visit started
    `rules` is the tenant's rubric rows (DEFAULT_RULES shape).

    The score is COVERAGE, not volume: for each rule, what share of this employee's INTERACTIONS
    (distinct visits they spoke during) contained it. Scoring raw hit counts would reward a rep who
    says "protection plan" nine times to one customer over one who says it once to nine customers —
    the opposite of the behaviour a manager wants. Negative rules subtract their weight times their
    coverage, and the total is clamped to 0..100.

    Returns the row shape `core.vision_behavior_score` stores, plus a ranked `coaching` list.
    """
    active = [r for r in (rules or []) if r.get("is_active", True)]
    by_key = {r.get("rule_key"): r for r in active}

    # per-visit union of rule hits — coverage is a per-interaction question
    visits = {}
    total_hits, talk_seconds, segs = {}, 0.0, 0
    for s in segments or []:
        segs += 1
        talk_seconds += float(s.get("duration_s") or 0)
        vid = s.get("visit_id") or f"_seg{segs}"     # a segment with no visit is its own interaction
        hits = match_segment(s.get("text") or "", active, s.get("elapsed_s"))
        bucket = visits.setdefault(vid, set())
        for k, n in hits.items():
            bucket.add(k)
            total_hits[k] = total_hits.get(k, 0) + n

    interactions = len(visits)
    coverage = {}
    for key in by_key:
        covered = sum(1 for keys in visits.values() if key in keys)
        coverage[key] = (covered / interactions) if interactions else 0.0

    pos_weight = sum(float(r.get("weight") or 0) for r in active
                     if (r.get("polarity") or "positive") == "positive")
    earned = sum(float(by_key[k].get("weight") or 0) * coverage[k] for k in by_key
                 if (by_key[k].get("polarity") or "positive") == "positive")
    penalty = sum(float(by_key[k].get("weight") or 0) * coverage[k] for k in by_key
                  if (by_key[k].get("polarity") or "positive") == "negative")

    raw = (earned / pos_weight * 100.0) if pos_weight else 0.0
    score = max(0.0, min(100.0, raw - penalty))

    greeted = sum(1 for keys in visits.values() if "greeting" in keys)

    # Coaching: the positive rules with the LOWEST coverage first (biggest weighted gap), then any
    # negative rule that fired at all. This is the list the UI shows, in this order.
    gaps = [{"rule_key": k, "label": by_key[k].get("label") or k,
             "coverage": round(coverage[k], 3),
             "gap": round(float(by_key[k].get("weight") or 0) * (1 - coverage[k]), 2)}
            for k in by_key if (by_key[k].get("polarity") or "positive") == "positive"]
    gaps.sort(key=lambda g: (-g["gap"], g["rule_key"]))
    flags = [{"rule_key": k, "label": by_key[k].get("label") or k,
              "coverage": round(coverage[k], 3), "severity": "flag"}
             for k in by_key if (by_key[k].get("polarity") or "positive") == "negative"
             and coverage[k] > 0]
    flags.sort(key=lambda f: -f["coverage"])

    return {
        "segments": segs,
        "talk_seconds": round(talk_seconds, 1),
        "interactions": interactions,
        "greeted": greeted,
        "missed_greetings": max(0, interactions - greeted),
        "score": round(score, 1),
        "rule_hits": total_hits,
        "coverage": {k: round(v, 3) for k, v in coverage.items()},
        "coaching": flags + [g for g in gaps if g["gap"] > 0][:3],
        "source": "rules",
    }


def rules_or_defaults(rows) -> list:
    """The tenant's rubric, or the seed defaults when migration 900 has not been seeded / read.
    Same degrade shape as every other config read in the platform: the module still answers."""
    rows = [r for r in (rows or []) if r.get("rule_key")]
    return rows or [dict(r, is_active=True) for r in DEFAULT_RULES]
