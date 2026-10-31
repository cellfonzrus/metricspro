"""WHAT CONDITION IS THIS DEVICE — the one home for the device condition vocabulary (§POS-18).

OWNER ASK (2026-10-31, verbatim): *"cpo is certified pre owned is adifferent device than new  ,
imnei is unique"* — said of the accessory order grid, which had been asked whether a refurbished
handset and a new one of the same model should share an order row.

**THE CLASS, not the instance (CLAUDE.md, "a fix is a DESIGN fix").** The owner's ruling is about
one grid, but the fact it needs — *which device conditions exist, and what does a feed call them* —
was written down TWICE before this and nowhere as a home:

  * `CHECK (condition IN ('new','refurbished','used','damaged'))` on `pos.inventory_serial` (mig 725)
  * a literal `const CONDITIONS = [...]` on the POS inventory screen

and a THIRD, worse copy had just appeared by accident: the accessory order grid kept a CPO handset
off the new handset's row only because the word `CPO` happened to survive its model-spelling parse.
Nothing asserted that. A curated `device_model_alias` row, or a promo token that swallowed the
suffix, would have merged two different devices into one order line with nothing to notice it.

So the vocabulary is declared here, ONCE, and split along the line CLAUDE.md draws:

  * WHICH CONDITIONS EXIST — `HOUSE_CONDITIONS`. A POLICY of the platform, so a house default is
    right: these are the four the schema has always allowed, and every tenant is offered the same
    four. No tenant, carrier or product is named (RULE TWO).
  * WHAT A TENANT'S FEED CALLS ONE — `resolve_feed_tokens`, over that org's OWN declared words.
    `CPO` in one feed, `CERTIFIED_PREOWNED` in another, `Refurb` in a third: that is the tenant's
    vocabulary, so it is a REQUIRED argument with NO house fallback, and an org that has declared
    nothing identifies NO condition and says so (`basis='no_vocabulary'`). A house list of
    condition words would be exactly the "default that is really one tenant's vocabulary" the
    2026-10-30 every-tenant directive bans — and it would read a CPO handset as new, silently.

**WHY `new` IS THE ANSWER FOR AN UNMARKED LINE, AND WHY IT SAYS SO.** A feed marks the exception,
not the rule: Luxelink's 191 September-October CPO lines carry the word and its new handsets carry
nothing. So an unmarked line resolves to the house default condition — but with
`basis='unmarked'`, never `'declared'`, so a surface can tell a reading from an inference. The
schema made the same choice first (`condition TEXT DEFAULT 'new'`), and this module does not get to
disagree with it: the lock pins the house keys against that `CHECK` constraint, key by key.

**WHAT IS NOT HERE.** The unit STATUS list (`in_stock`, `sold`, …) already has a home —
`pos/inventory_integrity.UNIT_STATUSES` — and is dereferenced from there by the endpoint that serves
both lists, never restated. Two lists, two homes, one reader.

PURE: no IO, no database, no clock. Every input is handed in.
"""
from __future__ import annotations

#: THE device conditions. `key` is what the column stores (and what mig 725's CHECK allows), `label`
#: is what a human is shown, and exactly one carries `unmarked` — the condition a feed line with no
#: condition word is taken to be. Order is the order a screen offers them in.
HOUSE_CONDITIONS: tuple[dict, ...] = (
    {"key": "new", "label": "New", "unmarked": True},
    {"key": "refurbished", "label": "Refurbished", "unmarked": False},
    {"key": "used", "label": "Used", "unmarked": False},
    {"key": "damaged", "label": "Damaged", "unmarked": False},
)

#: Why a condition reads the way it does. An allowlist, so an unruled basis is not evidence —
#: the same shape as `carrier_dollar_class.component_is_evidence` (§66).
CONDITION_BASES = ("declared", "unmarked", "no_vocabulary")


def condition_keys() -> tuple[str, ...]:
    """The storable condition keys, in offer order. THE list — never spelled out by a caller."""
    return tuple(c["key"] for c in HOUSE_CONDITIONS)


def unmarked_condition() -> str:
    """The condition a line carrying no condition word is taken to be."""
    for c in HOUSE_CONDITIONS:
        if c["unmarked"]:
            return c["key"]
    raise AssertionError("no unmarked condition declared")   # pragma: no cover — the lock pins it


def label_of(key) -> str:
    """A condition key → the words a human is shown. An unknown key answers itself, never a guess."""
    k = str(key or "").strip().lower()
    for c in HOUSE_CONDITIONS:
        if c["key"] == k:
            return c["label"]
    return k


def offered() -> list:
    """The conditions a screen offers, as `[{'key','label'}]`. THE list for every surface.

    A caller never spells the keys out: the POS inventory screen, the accessory grid and mig 725's
    CHECK are all reading this one declaration, which is what stops the next change from editing
    one of the three and leaving the others behind."""
    return [{"key": c["key"], "label": c["label"], "unmarked": bool(c["unmarked"])}
            for c in HOUSE_CONDITIONS]


def resolve_feed_tokens(raw=None):
    """An org's declared condition words → `({phrase: condition_key}, basis, {condition_key: word})`.
    PURE.

    Accepts either a flat list of words (every one of them meaning the NON-default condition this
    platform calls `refurbished`, which is what a feed that only marks CPO needs) or a mapping of
    `{condition_key: [words]}` for a tenant whose feed marks more than one. A word naming no known
    condition is DROPPED rather than invented, and phrases are returned longest-first so a
    two-word marker is never half-matched.

    **No house fallback** (see the header): nothing declared returns an empty mapping and
    `basis='not_declared'`, which leaves every caller unable to identify a condition rather than
    able to identify one by another tenant's words."""
    keys = set(condition_keys())
    #: the condition a flat list of words is understood to mark (see the docstring)
    default_non_new = "refurbished"
    out: dict = {}
    shown: dict = {}

    def _take(word, key):
        # NORMALISED TO ALNUM WORDS ONCE, HERE. A feed writes a marker as words
        # (`Certified Pre-Owned`), hyphenated, or glued into a SKU (`CERTIFIED_PREOWNED_IPHONE_13`),
        # and all three are the same declaration. Flattening at the DECLARATION means every reader
        # matches the same way and cannot disagree about whether a line is marked — the first cut of
        # this module normalised in one reader and not the other, and `condition_of` then called a
        # line unmarked while `strip_markers` removed its marker.
        p = " ".join("".join(ch if ch.isalnum() else " " for ch in str(word or "").lower()).split())
        if not p:
            return
        out.setdefault(p, key)
        # THE ORG'S OWN SPELLING IS WHAT A HUMAN IS SHOWN. The stored key is the platform's policy
        # word (`refurbished`, what mig 725's CHECK allows), but the owner calls it CPO, so the
        # first word this org declared for a condition is kept verbatim as its display marker.
        # A house label would be the platform telling a tenant what its own devices are called.
        shown.setdefault(key, " ".join(str(word or "").split()))

    if isinstance(raw, dict):
        for k, words in raw.items():
            key = str(k or "").strip().lower()
            if key not in keys:
                continue
            for w in (words or ()):
                _take(w, key)
    elif raw is not None and not isinstance(raw, (str, bytes)) and hasattr(raw, "__iter__"):
        for w in raw:
            _take(w, default_non_new)
    ordered = dict(sorted(out.items(), key=lambda kv: (-len(kv[0].split()), -len(kv[0]), kv[0])))
    return ordered, ("declared" if ordered else "not_declared"), shown


def _runs(text):
    """The text as lowercase word tokens, plus the same text with separators flattened.

    A feed writes a marker as words (`iPhone 13 CPO TO`), hyphenated, or glued into an upper-case
    SKU (`CERTIFIED_PREOWNED_IPHONE_13`); flattening the separators makes all three one sequence,
    which is the same thing `resolve_feed_tokens` does to the declaration."""
    low = str(text or "").lower()
    flat = "".join(ch if ch.isalnum() else " " for ch in low)
    return [t for t in flat.split() if t], " ".join(flat.split())


def condition_of(text, *, feed_tokens, markers=None):
    """One product description → `{'key', 'label', 'basis', 'matched', 'marker'}`. PURE.

    `feed_tokens` is REQUIRED — a caller that has not asked the org for its condition words must not
    be able to answer this question (CLAUDE.md, "every feature is for EVERY tenant").

    Nothing declared → `key=None` and `basis='no_vocabulary'`: the surface then says the condition
    is not identified rather than showing every device as new. A declared phrase found → that
    condition, `basis='declared'`, with `matched` naming the phrase that was read. Vocabulary
    declared and no phrase found → the unmarked condition, `basis='unmarked'`.

    `markers` is the org's own spellings off `resolve_feed_tokens`; when one is given for the
    condition read, it is returned as `marker` and is what a screen shows ("CPO", not
    "Refurbished"). The unmarked condition carries no marker: a device nobody marked is the
    baseline, and labelling it would put a word on every row for nothing."""
    if not feed_tokens:
        return {"key": None, "label": None, "basis": "no_vocabulary", "matched": None,
                "marker": None}
    toks, _flat = _runs(text)
    for phrase, key in feed_tokens.items():
        parts = phrase.split()
        n = len(parts)
        if not n:
            continue
        # Both sides are alnum word runs (`_runs` here, `resolve_feed_tokens` for the phrase), so
        # one comparison serves every spelling a feed uses.
        if any(toks[i:i + n] == parts for i in range(len(toks) - n + 1)):
            return {"key": key, "label": label_of(key), "basis": "declared", "matched": phrase,
                    "marker": (markers or {}).get(key) or phrase}
    k = unmarked_condition()
    return {"key": k, "label": label_of(k), "basis": "unmarked", "matched": None, "marker": None}


def strip_markers(text, *, feed_tokens):
    """The text with every declared condition phrase removed. PURE.

    This is what lets two spellings of ONE condition fold together: a feed that writes
    `iPhone 13 CPO` and `CERTIFIED_PREOWNED_IPHONE_13` is describing one device, and the condition
    is carried as a FIELD afterwards rather than as whichever words that line happened to use. Only
    whole token runs are removed, so a declared `used` cannot bite the `used` inside another word."""
    original = " ".join(str(text or "").split())
    if not feed_tokens or not original:
        return original
    # Matched over the ALNUM WORD sequence, so a marker glued into a SKU (`CERTIFIED_PREOWNED_
    # IPHONE_13`) is removed the same as one written as words (`iPhone 13 CPO`). That is the whole
    # point: two spellings of one condition must fold to one row.
    words = [w for w in "".join(ch if ch.isalnum() else " " for ch in original).split() if w]
    low = [w.lower() for w in words]
    keep = [True] * len(words)
    for phrase in feed_tokens:
        parts = phrase.split()
        n = len(parts)
        if not n:
            continue
        for i in range(len(low) - n + 1):
            if all(keep[i + j] for j in range(n)) and low[i:i + n] == parts:
                for j in range(n):
                    keep[i + j] = False
    if all(keep):
        # NOTHING WAS A MARKER, SO NOTHING IS REBUILT. An org with no condition in a spelling keeps
        # that spelling byte for byte — flattening its separators would restate every family key on
        # the platform for a question that did not apply to it.
        return original
    return " ".join(w for w, k in zip(words, keep) if k).strip() or original
