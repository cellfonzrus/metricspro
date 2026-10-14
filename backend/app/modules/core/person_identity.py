"""WHO IS THIS PERSON — one fact, one home (owner report 2026-10-14).

OWNER REPORT, verbatim: *"why does it show 2 reps who worked at the store, it should only show the
actual person who worked for all reporting not the one with the home store"*.

THE INSTANCE. The Closing & Cash Pickup Status report (§48.8) showed B-1750 on 2026-10-10 as
"Simarjyot Singh, Singh, Simarjyot" — one man, listed twice. The punch (`storeops.timelog`) spells
him `Simarjyot Singh`; the sales feed (`salesperson`) spells him `Singh, Simarjyot`; the who-worked
signal unioned the two NAME STRINGS, and a union of strings has no notion of a person.

THE CLASS. A name string is not an identity. Every surface that joins two systems' people — the
schedule against the POS, a punch against a sale, a closing against a roster — needs the SAME answer
to "is this the same human", and this repo had SIX implementations of it (`commission_engine.
_canon_person`, a byte copy in `management_incentive`, `targets_engine.name_key`,
`closing/router._name_match`, a byte copy in `closing/ops_chargebacks`, and
`commission_drilldown._tok`). Six copies is six future divergences; each is now a dereference of
this module.

THREE DIFFERENT QUESTIONS live here, named so the next reader cannot mistake one for another:

  · `person_key(name)`          — IS this the same human? Canonical, name-ORDER-insensitive,
                                  deliberately NOT fuzzy. The key you GROUP and JOIN on.
  · `token_key(name)`           — the same question with the word ORDER thrown away as well, for the
                                  schedule-vs-POS join that predates `person_key`.
  · `folded_token_key(name)`    — THE STRONGEST of the three, and the one to reach for in new code:
                                  order-free AND accent-folded, so 'Martínez, LESLIEE' and 'LESLIEE
                                  Martinez' are one person. It is what the clock feed's employee
                                  matcher and the who-worked signal join two feeds on.
  · `loosely_same_person(a, b)` — COULD this be the same human, generously (first token, substring)?
                                  A screening rule for narration and "did this person submit", never
                                  a grouping key: it is not transitive.

And one presentation fact, because collapsing two spellings immediately asks which one to show:

  · `best_spelling(variants, roster)` / `collapse(names, roster)` — the spelling a human recognises.

PURE at import time: `re` only. `fold_person` defers its one app import (the accent fold's own
home) to call time, so no module can cycle on this one. The rules below are byte-for-byte the rules the six call
sites already ran — this module moved them, it did not change them, and `harness_person_identity.py`
proves that against the live spellings and fails the build if a seventh copy appears.
"""
import re

__all__ = ["person_key", "tokens", "token_key", "fold_person", "name_tokens",
           "folded_token_key", "loosely_same_person", "best_spelling", "collapse"]


def person_key(name) -> str:
    """Canonicalize a person-name for name-ORDER-insensitive equality matching. PURE.

    Steps, in order: casefold · trim · collapse internal whitespace runs to one space · then
    IF the result contains EXACTLY ONE comma, treat it as "Last, First" and reorder to "First Last"
    (the pre-comma part is the surname — possibly multi-token — and is appended AFTER the post-comma
    first-part, preserving each part's internal token order): "Islam Khan, Ariful" -> "ariful islam khan".
    More than one comma, or an empty side, -> the non-reordered folded string, unchanged.

    Deliberately NOT fuzzy: no token dropping, no middle-name stripping, no spelling tolerance.
    "natasha cabrera" != "natasha nicole cabrera" stays UNMATCHED by design (data hygiene, not
    matching), and so does "Abdul K" against "Abdul Kakar" — two spellings that may well be one man,
    reported as two, because guessing there would silently merge two real people elsewhere.
    For comma-free ASCII single-spaced input this equals a bare `.strip().lower()` byte-for-byte."""
    folded = re.sub(r"\s+", " ", ("" if name is None else str(name)).strip().casefold())
    if folded.count(",") == 1:
        last, first = (x.strip() for x in folded.split(","))
        if last and first:
            return f"{first} {last}"
    return folded


def tokens(name) -> set:
    """The casefolded alphanumeric word-set of a name. PURE. Punctuation (commas, periods, hyphens)
    is a spelling habit, not part of a person, so it is dropped rather than tokenised."""
    return set(re.sub(r"[^a-z0-9]+", " ", str(name or "").casefold()).split())


def token_key(name) -> str:
    """Identity key for a PERSON's name across systems that spell it in a different word ORDER.

    WHY THIS IS NOT `person_key`, measured 2026-09-02: LuxeLink's POS emits 'Antunez, Diana' while
    its schedule says 'Diana Antunez', and a plain upper-case compare matched ZERO of that org's reps
    — every per-employee daily target silently multiplied by rep_share = 0. `person_key` handles that
    particular shape (one comma) but not 'KELLIE MARK' against 'Kellie, Mark'. This key throws word
    order away entirely: uppercase, punctuation stripped, tokens SORTED. Two different people sharing
    the exact same name-token set within one store would merge — that vanishing edge is accepted over
    a join that is broken by default. Explicit `commcalc.name_map` / `rep_aliases` rows canonicalize
    FIRST; this is the format-normalization fallback beneath them. Names only; store codes keep their
    plain upper-case compare."""
    return " ".join(sorted(tokens(name))).upper()


def fold_person(v) -> str:
    """The comparison form for a person spelling: accent-folded through THE one accent fold
    (`account/store_identity.fold_accents`), lower-cased, non-alphanumerics dropped.

    THE ACCENT FOLD IS WHY THIS EXISTS. `str.isalnum()` is True for `'í'`, so every plain fold in
    this codebase kept it and `Martínez` compared unequal to `Martinez` — a live employee whose clock
    rows matched nothing at all. The fold itself lives in its own one home and this function simply
    uses it; the import is deferred so this module stays import-time pure."""
    from app.modules.account.store_identity import fold_accents
    return "".join(ch for ch in fold_accents(v).lower() if ch.isalnum())


def name_tokens(v) -> set:
    """The set of folded name TOKENS in a person spelling, in any of its shapes.

    `"Martínez, LESLIEE"` → `{'martinez', 'lesliee'}`; `"Caba, David, A"` → `{'caba','david','a'}`
    (a middle initial is a THIRD comma segment, so nothing here splits on the comma and assumes two
    parts); `"Leslie Martinez"` → `{'leslie','martinez'}`. Order-free on purpose: the feed writes
    `Last, First` and the roster writes `First Last`, and which way round a name is written is not a
    fact about who the person is."""
    return {t for t in (fold_person(x) for x in re.split(r"[,\s]+", str(v or ""))) if t}


def folded_token_key(v) -> str:
    """The order-free, accent-folded identity key for a person spelling — the sorted folded tokens
    joined. THE KEY NEW CODE SHOULD JOIN ON.

    `"Martínez, LESLIEE"` and `"LESLIEE Martinez"` share a key; `"Caba, David, A"` does NOT share a
    key with `"Caba, David"`, because dropping a middle initial is a judgement a human makes, not one
    a key may make silently (`loosely_same_person` is the looser, REPORTED comparison for that case).

    It is a superset of what `person_key` forgives — a comma, a shout, a double space, an accent and
    the word order — which is why the who-worked signal joins a punch to a sale on it. `person_key`
    stays as it is because ~30 commission call sites already key money on it, and widening that join
    moves money; this key is the one to reach for everywhere else."""
    return "|".join(sorted(name_tokens(v)))


def loosely_same_person(a, b) -> bool:
    """COULD these two strings be the same person, generously — equal, either contained in the other,
    or sharing a first token. PURE.

    This is a SCREENING rule, not an identity: it is not transitive ('Amir Khan' ~ 'Amir' ~ 'Amir
    Hassan'), so it must never be used as a dict key or a grouping key — `person_key` is. It earns its
    looseness where the question is "has this person already been accounted for" (did they submit a
    closing, are they also on the schedule), where a false merge costs a narration line and a missed
    match costs an employee being dunned for a closing they filed."""
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return a.split()[0] == b.split()[0]


def best_spelling(variants, roster=None, key=None) -> str:
    """The spelling of one person to SHOW, given every spelling the systems hold for them. PURE.

    `roster` is an optional {<key>: name} map of the authoritative roster spelling
    (`storeops.employees.name` — what an administrator typed, and what the owner recognises on a
    report). It wins outright when it holds this person.

    Otherwise, in order: a comma-free spelling beats 'Last, First' (people read 'Simarjyot Singh',
    not 'Singh, Simarjyot'); more words beats fewer, so a full name beats an abbreviated one; and the
    alphabetical first breaks any remaining tie so the chosen spelling never depends on which feed
    loaded first."""
    keyf = key or person_key
    names = [str(v).strip() for v in (variants or []) if str(v or "").strip()]
    if not names:
        return ""
    if roster:
        for n in names:
            hit = roster.get(keyf(n))
            if hit:
                return hit
    return sorted(names, key=lambda n: (("," in n), -len(n.split()), n))[0]


def collapse(names, roster=None, key=None) -> dict:
    """{<key>: {"name": <best spelling>, "variants": [every spelling, sorted]}} for a bag of raw
    name strings. PURE. This is the whole answer to "two feeds spell one person two ways": collapse
    once, then group, join and display on the key — never on the string."""
    keyf = key or person_key
    buckets = {}
    for raw in (names or []):
        s = str(raw or "").strip()
        if not s:
            continue
        buckets.setdefault(keyf(s), set()).add(s)
    return {k: {"name": best_spelling(v, roster, keyf), "variants": sorted(v)}
            for k, v in buckets.items()}
