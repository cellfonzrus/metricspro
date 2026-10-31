"""WHICH SOLD LINES ARE THE SAME PHONE — the one home for the device MODEL FAMILY (§COMMCALC-49.11).

OWNER ASK (2026-10-30, verbatim): *"accessory order plan should combine the accessories for all line
items for iphone 13 oin different colors with a drill down option to see what was sold, , iph 13 is
different from 13 pro"*.

THE QUESTION, in one line: **a case fits a phone, not a colour of it — so which of these product
descriptions are the same phone for the purpose of ordering accessories?**

Measured on the live feed before a line was written (September 2026, both tenants' own device
departments). The report was ranking these as four different phones:

    Apple iPhone 13 128GB Pink CPO TO      20 units
    Apple iPhone 13 128GB Midnight TO      12 units
    Apple iPhone 13 128GB Midnight TO       7 units   (a second promo spelling)
    Apple iPhone 14 128GB Red CPO TO        8 units
    Apple iPhone 14 128GB Purple CPO TO     6 units

One case SKU fits every iPhone 13 in that list; none of them fits an iPhone 13 Pro. So the grain the
accessory order needs is neither the raw description nor the carrier's SKU — it is the FAMILY.

════════════════════════════════════════════════════════════════════════════════════════════════════
DUPLICATE CHECK (build gate, CLAUDE.md) — WHAT WAS SEARCHED AND WHAT IS REUSED
════════════════════════════════════════════════════════════════════════════════════════════════════
Searched the index and the tree for an existing home for "which models are the same phone" before
writing: §62 `product_mix.model_of`, §15 / module 095 `device_model_alias`, §36
`asset/purchase_orders` (its own alias read), §COMMCALC-49 the order plan, `payables/engine` (its
`_alias` map), `lib/forecast-matrix.ts` (the store x model grid).

| the fact this module needs | the ONE home it DEREFERENCES |
|---|---|
| the MODEL out of a promo-decorated description | `commcalc/product_mix.model_of` (§62) — not re-parsed here; this module takes the model it returns |
| a model spelling a human has already curated | `commcalc.device_model_alias.canonical_model` (mig `096`) — handed in as `alias`, and it WINS over anything derived here |

**NEW here and nowhere else:** collapsing the colour and the capacity out of a model spelling. No
other module answered that question — the four alias readers all answer "what is this model called",
which is a different question and stays where it is.

**THE DECLARATION OUTRANKS THE DERIVATION.** `alias` is a human's curation of `device_model_alias`,
so a hit there is returned unchanged with `basis='declared_alias'`. The derivation below exists for
the 148-row tail nobody has curated, and it can be overridden one row at a time by curating it.

════════════════════════════════════════════════════════════════════════════════════════════════════
WHAT IS DROPPED, AND THE ONE THING THAT MAY NOT BE
════════════════════════════════════════════════════════════════════════════════════════════════════
    CAPACITY   `128GB`, `128 GB`, `1TB`   — dropped by PATTERN (a number followed by a storage unit)
    COLOUR     `Pink`, `Midnight`, `BLK`  — dropped only if the ORG HAS DECLARED IT A COLOUR WORD

**THE CAPACITY IS A FORMAT, THE COLOUR IS A VOCABULARY, AND THAT IS WHY THEY ARE TREATED
DIFFERENTLY.** "a number followed by GB" is the shape of a storage size in every feed on the
platform, the way `category='CellPhone'` is a file-format marker — so it is a house rule and a
regex. A colour WORD is the tenant's own spelling (`BLK` in one feed, `Midnight` in another), and
CLAUDE.md's "every feature is for EVERY tenant" rule bans a house default that is really one
tenant's vocabulary. So `colour_tokens` is a REQUIRED argument, it has no house fallback, and an
org that has declared nothing gets **no collapsing at all** with `basis='no_vocabulary'` — the
report then says so rather than silently grouping by another tenant's colour words. Seeded per org
by the migration from each feed's OWN measured spellings.

**`PRO` IS NOT A COLOUR, AND THE OWNER SAID SO FIRST.** Nothing is dropped except a declared colour
phrase and a capacity, so `iPhone 13 Pro` can only ever collapse with another `iPhone 13 Pro` — the
model words, the generation, `PLUS`, `MINI`, `5G`, `CPO` and the carrier's own suffix all survive. A
test asserts the Pro case directly, because it is the half of the ask that a careless "strip the
noise" implementation gets wrong.

RULE TWO. No colour, carrier, tenant or product name appears in this file; the only literals are the
storage units of the capacity pattern, which are units and not anybody's vocabulary. A test asserts
it over the AST.

PURE. Every input is handed in. No import of a router, no table read, no clock.
"""
from __future__ import annotations

import re

from app.modules.pos import device_condition as _cond

#: THE CAPACITY PATTERN — a number, optional space, a storage unit, as its own token or glued to the
#: number. The units are units of measurement, not a vocabulary (see the header).
_CAPACITY = re.compile(r"^\d+(?:\.\d+)?\s*(?:gb|tb|mb)$", re.I)
#: A bare number, kept ONLY when a storage unit follows it as the next token ("128 GB").
_BARE_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")
_UNIT_ONLY = re.compile(r"^(?:gb|tb|mb)$", re.I)

#: Why a family reads the way it does.
FAMILY_BASES = ("declared_alias", "colour_collapsed", "unchanged", "no_vocabulary")

#: The separator between a family's model words and its condition marker in a LABEL. The KEY uses
#: `KEY_CONDITION_SEP` instead, which cannot occur in a product description.
LABEL_CONDITION_SEP = " · "
#: The separator inside a family KEY. A vertical bar is not a character any feed writes in a model
#: spelling, so a key can never be mistaken for a model and two conditions can never collide.
KEY_CONDITION_SEP = "|"


def resolve_colour_tokens(raw=None):
    """An org's declared colour words → `(tokens, basis)`, longest phrase first. PURE.

    **There is no house default and that is deliberate** (see the header): nothing declared returns
    an empty tuple and `basis='not_declared'`, which makes every caller unable to collapse rather
    than able to collapse by one tenant's words. A multi-word phrase is allowed, so a feed that
    spells a colour `CLOUD SILVER` declares it as one entry; the longest phrase is tried first so a
    two-word colour is never half-matched."""
    out = []
    if raw is not None and not isinstance(raw, (str, bytes)) and hasattr(raw, "__iter__"):
        for t in raw:
            s = " ".join(str(t or "").strip().lower().split())
            if s and s not in out:
                out.append(s)
    out.sort(key=lambda s: (-len(s.split()), -len(s), s))
    return tuple(out), ("declared" if out else "not_declared")


def _tokens(model):
    return [t for t in str(model or "").split() if t]


def _drop_capacity(toks):
    """Tokens with the storage size removed. Handles `128GB` and the split `128 GB`."""
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        if _CAPACITY.match(t):
            i += 1
            continue
        if _BARE_NUMBER.match(t) and i + 1 < len(toks) and _UNIT_ONLY.match(toks[i + 1]):
            i += 2
            continue
        out.append(t)
        i += 1
    return out


def _drop_colours(toks, colour_tokens):
    """Tokens with every declared colour PHRASE removed, longest first.

    A phrase is matched over consecutive tokens, case-insensitively, and only as a WHOLE token run —
    so a declared `red` cannot bite the `RED` inside another word, and `cloud silver` is removed as
    one thing rather than leaving a stray `cloud`."""
    low = [t.lower().strip(",;") for t in toks]
    keep = [True] * len(toks)
    for phrase in colour_tokens:
        parts = phrase.split()
        n = len(parts)
        if not n:
            continue
        for i in range(len(low) - n + 1):
            if all(keep[i + j] for j in range(n)) and low[i:i + n] == parts:
                for j in range(n):
                    keep[i + j] = False
    return [t for t, k in zip(toks, keep) if k]


def family_of(model, *, colour_tokens, condition_tokens, alias=None, condition_markers=None):
    """One model spelling → the FAMILY it belongs to, for accessory ordering. PURE.

    `colour_tokens` and `condition_tokens` are both REQUIRED — a caller that has not asked the org
    for its own words must not be able to answer this question (CLAUDE.md, "every feature is for
    EVERY tenant").

    `alias` is the optional `{model_lower: canonical}` curation off `device_model_alias`; a hit is
    returned unchanged, because a human saying what a model is called outranks any derivation.

    Returns `{'key', 'label', 'basis', 'condition', 'condition_label', 'condition_marker',
    'condition_basis'}`. `key` is the lowercased, whitespace-collapsed grouping key; `label` is the
    family spelled the way this member spelled it, so the caller can show the best seller's own
    words rather than a normalised shout."""
    s = " ".join(str(model or "").split())
    if not s:
        return _with_condition({"key": "", "label": "", "basis": "unchanged"},
                               {"key": None, "label": None, "basis": "no_vocabulary",
                                "matched": None, "marker": None})
    # ── A CONDITION IS NOT A SPELLING (owner 2026-10-31) ─────────────────────────────────────────
    # Owner, verbatim: *"cpo is certified pre owned is adifferent device than new , imnei is
    # unique"*. So the condition is read FIRST, off the raw spelling, and applied LAST — after the
    # alias — because a human curating `device_model_alias` is answering "what is this model
    # called", not "is this one refurbished". Before this, a refurbished handset stayed off the new
    # one's row only because the word `CPO` happened to survive the parse; one curated alias row
    # would have merged two different devices into one order line with nothing to notice it.
    cond = _cond.condition_of(s, feed_tokens=condition_tokens, markers=condition_markers)
    declared = (alias or {}).get(s.lower())
    if declared:
        d = " ".join(str(declared).split())
        return _with_condition({"key": d.lower(), "label": d, "basis": "declared_alias"}, cond)
    # THE BASIS REPORTS THE COLOUR QUESTION, NOT THE CAPACITY ONE. The capacity is a format rule
    # that is always on, so letting it set the basis would hide the thing the basis exists to say:
    # whether this org has told the platform its colour words. Caught by the lock (A3) after the
    # first write conflated them.
    # The marker words come out of the model spelling, so two spellings of ONE condition fold
    # together (`iPhone 13 CPO` and `CERTIFIED_PREOWNED_IPHONE_13` are one refurbished iPhone 13)
    # and the condition travels as a FIELD rather than as whichever words that line used.
    toks = _tokens(_cond.strip_markers(s, feed_tokens=condition_tokens))
    kept = _drop_capacity(toks)
    if not colour_tokens:
        _basis = "no_vocabulary"
    else:
        before = list(kept)
        kept = _drop_colours(kept, tuple(colour_tokens))
        _basis = "colour_collapsed" if kept != before else "unchanged"
    label = " ".join(kept).strip(" -,;") or s
    label = " ".join(label.split())
    return _with_condition({"key": label.lower(), "label": label, "basis": _basis}, cond)


def _with_condition(fam, cond):
    """A derived family plus the condition read off its raw spelling. PURE.

    The condition joins the KEY only when it is one the org DECLARED a word for. The unmarked
    condition adds nothing — it is the baseline, and suffixing every row of every tenant with
    "New" would be a word on every line for no information. An org that has declared no condition
    vocabulary at all reads `condition=None` and its keys are byte-identical to what they were
    before this question existed, which is what makes the change safe for a tenant it does not
    apply to."""
    out = dict(fam)
    out["condition"] = cond.get("key")
    out["condition_label"] = cond.get("label")
    out["condition_marker"] = cond.get("marker")
    out["condition_basis"] = cond.get("basis")
    if cond.get("basis") == "declared" and out["key"]:
        out["key"] = f"{out['key']}{KEY_CONDITION_SEP}{cond['key']}"
        out["label"] = f"{out['label']}{LABEL_CONDITION_SEP}{cond.get('marker') or cond['label']}"
    return out


def group_units(units_by_model, *, colour_tokens, condition_tokens, alias=None,
                condition_markers=None):
    """`{model: {period: units}}` → `({family_label: {period: units}},
    {family_label: [variant, ...]}, {family_label: {condition facts}})`. PURE.

    The THIRD return is the condition each family was read as, so a caller shows it from ONE read
    rather than asking `family_of` again — a second derivation of the same fact is the duplicate
    defect CLAUDE.md forbids, and it is how the two would come to disagree.

    The family's LABEL is the spelling of its best-selling member, which is the name a human
    recognises. Each variant carries its own units so the screen can open the family and show what
    was actually sold — the drill-down half of the owner's ask, served off rows the report already
    folded rather than a second query."""
    buckets: dict = {}
    for model, by_period in (units_by_model or {}).items():
        fam = family_of(model, colour_tokens=colour_tokens, condition_tokens=condition_tokens,
                        alias=alias, condition_markers=condition_markers)
        if not fam["key"]:
            continue
        b = buckets.setdefault(fam["key"], {"labels": {}, "periods": {}, "variants": {},
                                            "bases": set(), "cond": fam})
        total = sum(int(v or 0) for v in (by_period or {}).values())
        b["labels"][fam["label"]] = b["labels"].get(fam["label"], 0) + total
        b["bases"].add(fam["basis"])
        v = b["variants"].setdefault(str(model), {})
        for p, n in (by_period or {}).items():
            b["periods"][p] = b["periods"].get(p, 0) + int(n or 0)
            v[p] = v.get(p, 0) + int(n or 0)

    units, variants, families = {}, {}, {}
    for b in buckets.values():
        # The best-selling spelling names the family; ties broken by the spelling itself so the
        # label cannot flip between two reads of the same data.
        label = sorted(b["labels"].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        units[label] = dict(b["periods"])
        variants[label] = sorted(
            ({"model": m, "units_by_month": dict(p), "units_total": sum(p.values())}
             for m, p in b["variants"].items()),
            key=lambda r: (-r["units_total"], r["model"]))
        fam = b["cond"]
        families[label] = {
            "condition": fam.get("condition"),
            "condition_label": fam.get("condition_label"),
            "condition_marker": fam.get("condition_marker"),
            "condition_basis": fam.get("condition_basis"),
            "basis": sorted(b["bases"])[0] if b["bases"] else "unchanged",
        }
    return units, variants, families
