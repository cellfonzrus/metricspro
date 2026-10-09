"""PRODUCT MIX & PORT DISCIPLINE — is a rep selling, or just handing out the cheap phone?

OWNER DIRECTIVE (2026-10-09, verbatim): *"sales by each store as per the product sold, if the store
is selling more of a particular phone at a cheaper price or free it could be that the sales person is
just pushing cheaper phones or free phones and not trying to sell higher end devices which bring more
accessory sales , the report should highlight the sales reps whose accessory per box is low and also
who are porting in less numbers - the logic is built but not displayed that the ports are low. the
system should co-relate the two and provide an action plan for the store whoa re lagging."*

THE CLAIM BEING TESTED, stated plainly so the report cannot be mistaken for an accusation. A phone
handed over at $0 closes the sale with nothing asked of the customer: no case, no screen protector,
no port. A $900 handset is a conversation, and the conversation is where the accessory attaches. So
the owner's hypothesis is that a rep whose device mix is mostly free and budget handsets will show a
LOW accessory-per-box and a LOW port-in share — and that the two travel together.

**IT IS A HYPOTHESIS, AND THIS MODULE MEASURES IT RATHER THAN ASSUMING IT.** `correlations()` reports
the actual Pearson coefficient between the mix and each outcome, with its own n, every time the report
runs. Measured on live September 2026 (house org, 29 reps with enough volume to rank), RE-MEASURED
2026-10-09 after the owner took `BYOD` out of `box_departments` — that is the box DENOMINATOR of both
rates, so every coefficient moved and the first read (-0.33 / -0.28 / +0.28 / +0.30) is superseded:

    cheap-device share   vs accessory $ per box    r = -0.18     the hypothesis, in the stated direction
    free-device share    vs accessory $ per box    r = -0.10     same direction, weaker still
    port-in share        vs accessory $ per box    r = +0.12     ~NO relationship: they are INDEPENDENT
    cheap-device share   vs port-in share          r = +0.50     the two LAGGING behaviours travel together

These are one run against a feed appended daily, which is the whole reason this is computed on every
load and not asserted here. Two things follow, and the second is the load-bearing one:

  1. The hypothesis holds in the stated direction but WEAKLY. Cheap-device share explains only a few
     percent of the variance in accessory dollars, so a low mix share is a reason to LOOK, never a
     finding by itself — which is why `rep_prompt` words it as something to check.
  2. Porting and attaching are INDEPENDENT (+0.12). A rep who ports well tells you nothing about
     whether they attach, so a single composite score would average two unrelated things and hide the
     rep who ports well and attaches nothing. That is why the verdict takes BOTH signals and never
     blends them. The +0.50 row says the two LAGGING behaviours share a cause (the rep who hands out
     the free phone also does not ask for the number), which is what makes `both` a coachable story.

════════════════════════════════════════════════════════════════════════════════════════════════════
DUPLICATE CHECK (build gate, CLAUDE.md) — WHAT WAS SEARCHED AND WHAT IS REUSED
════════════════════════════════════════════════════════════════════════════════════════════════════
Searched the index for each number this report needs before writing a line of it:

  boxes                 §3 `router._sales_cell_agg['box_count']` — the config-driven `box_departments`
                        tally (mig 218) plus the `box_count_buckets` opt-in (mig 231) with the
                        double-count guard (§59.8). NOT recounted here.
  accessory $           §3 `cell['accessory_rev']` — THE shared `_is_accessory` classifier.
  accessory $ per box   §59 `peer_comparison._ratio(acc, boxes)` — the ratio the Peer Sales Comparison
                        already publishes, DEREFERENCED so this report and that screen cannot print
                        two different accessory-per-box figures for one store.
  port-ins (ours)       §3 `cell['_port']` — `line_class.activation_class(...) == 'port'`, the ONE
                        activation predicate, at distinct-transaction grain.
  port % (carrier's)    §10 `raw_dlar_store.port_pct` through `kpi_failing.port_in_rate()` — THE one
                        home for what that column means, added in this change (see below).
  which line is a box   §3 the same `box_departments` membership test the box count uses, so the price
                        mix and the box total are over the SAME lines by construction.
  which sale is a port  §6 `line_class.activation_class` — never a `contract_type` substring here.
  band / median / gap /
  "this store is behind"/
  the coaching sentence §59 `peer_comparison.with_extra_metric` → `lagging()` → `peer_action_item()`.
                        The store action plan this report asks for is §59's verdict about a number §59
                        does not compute, exactly the case `with_extra_metric` was built for in §60.
                        There is no second median, no second gap rule and no second idea of "behind".

NEW HERE, and nowhere else on the platform:
  · the CUSTOMER PRICE BAND of a device line (`band_of_price`) and the device MODEL read out of a
    promo-decorated product description (`model_of`);
  · the per-REP roll-up of boxes / ports / accessory $ (every surface that exists today rolls the same
    cells to a STORE or a day, never to a rep across the month);
  · the TWO-SIGNAL verdict (`rank`) and the measured correlation between mix and outcome.

════════════════════════════════════════════════════════════════════════════════════════════════════
WHICH LINES THE PRICE MIX IS OVER — and the three it is deliberately NOT over
════════════════════════════════════════════════════════════════════════════════════════════════════
A line enters the mix when it is in a box department AND the activation predicate CLASSIFIES it
(`activation_class` returns a class). Both halves are load-bearing, and measured live:

  · A box department carries lines that are not a handset at all. September 2026, house org: 388 of
    1,508 box-department lines classify as NOTHING — 381 of them in the department 'BYOD' (median
    $30.00, so they are a kit charge rather than a free phone), the rest services such as 'Data
    Transfer Services' and a support bundle. Counting those as free or budget phones would have put
    the biggest single block of "cheap devices" in the report on lines where no device was sold.
    They are counted and REPORTED as `unclassified_lines`, never silently dropped and never banded.
  · A NEGATIVE ext_price is a credit against a device, not a cheap device. Counted as
    `credit_lines` and excluded from the bands (1 line live).
  · THE MIX IS NOT A PARTITION OF THE BOX COUNT, and the payload says so. Boxes additionally count
    the activation buckets a tenant configured (a BYOD transaction with no device line is a box), so
    `device_lines` is smaller than `boxes` by design. Presenting one as a breakdown of the other
    would be a lie in both directions.

THE BANDS ARE MONEY CUTS, NOT PRODUCT NAMES (RULE TWO). No handset, carrier or tenant name appears in
this file. The house cuts fall where the live distribution actually sits — measured over the 1,120
classified device lines of September 2026: free 186, budget 640, mid 263, premium 31, median customer
price $29.99. A caller may override them per call; they are stated on every payload so a reader never
has to guess what "cheap" meant.

PURE. Every input is handed in, so `backend/harness_product_mix.py` drives the whole module DB-free.
"""
from __future__ import annotations

from app.modules.commcalc import peer_comparison as _pc

# ── THE PRICE BANDS (money cuts — config, never product names) ───────────────────────────────────
# Each cut is the UPPER bound of the band below it. `free` is <= the first cut, which is a cent rather
# than exactly 0.00 so a feed that rounds a giveaway to 0.004 is still a giveaway.
HOUSE_PRICE_CUTS = (0.01, 50.0, 200.0)
PRICE_BANDS = (
    ("free", "Free"),
    ("budget", "Budget"),
    ("mid", "Mid"),
    ("premium", "Premium"),
)
PRICE_BAND_KEYS = tuple(k for k, _l in PRICE_BANDS)
# The bands that answer the owner's "cheaper price or free". Stated once, read everywhere, so the
# share and the verdict can never be computed over different bands.
CHEAP_BANDS = ("free", "budget")

# A product description decorated with a promotion — the model is everything before the first
# ' - '-separated segment that names a promotion. House tokens; a tenant whose POS decorates
# differently passes its own. CONTAINS, lower-cased, over the segment only.
HOUSE_PROMO_TOKENS = ("promo", "offer")
_SEP = " - "


def resolve_price_cuts(raw=None):
    """A caller's band cuts → an ascending tuple of positive floats. Missing / junk → the house cuts.

    An explicitly EMPTY list is honoured and means ONE band holding every device, which is a real
    answer for a tenant that does not want a price judgement made at all. PURE."""
    if raw is None or not isinstance(raw, (list, tuple)):
        return tuple(HOUSE_PRICE_CUTS)
    cuts = []
    for v in raw:
        try:
            n = round(float(str(v).replace(",", "").replace("$", "").strip()), 2)
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in cuts:
            cuts.append(n)
    return tuple(sorted(cuts))


def band_of_price(ext_price, cuts=HOUSE_PRICE_CUTS):
    """What the CUSTOMER paid for one device line → its band key, or None when there is no band to
    put it in (a negative line is a credit, not a cheap phone). PURE.

    With n cuts there are n+1 bands and the keys come from `PRICE_BANDS`, so a caller passing fewer
    cuts gets the first bands only — the key set is never invented from the cut count."""
    try:
        p = float(ext_price or 0)
    except (TypeError, ValueError):
        return None
    if p < 0:
        return None
    for i, cut in enumerate(cuts):
        if p <= cut:
            return PRICE_BAND_KEYS[min(i, len(PRICE_BAND_KEYS) - 1)]
    return PRICE_BAND_KEYS[min(len(cuts), len(PRICE_BAND_KEYS) - 1)]


def band_labels(cuts=HOUSE_PRICE_CUTS):
    """The bands in play for these cuts, each with the money range it covers said out loud — so the
    screen never shows a bare word like "budget" that a reader has to interpret. PURE."""
    out, lo = [], None
    n = min(len(cuts) + 1, len(PRICE_BANDS))
    for i in range(n):
        key, label = PRICE_BANDS[i]
        hi = cuts[i] if i < len(cuts) else None
        if i == 0:
            rng = f"${hi:,.2f} or less" if hi is not None else "any price"
        elif hi is None:
            rng = f"over ${lo:,.2f}"
        else:
            rng = f"over ${lo:,.2f} up to ${hi:,.2f}"
        out.append({"key": key, "label": label, "range": rng,
                    "cheap": key in CHEAP_BANDS})
        lo = hi
    return out


def model_of(product_desc, promo_tokens=None):
    """A promo-decorated product description → the device MODEL. PURE.

    'IPHONE 16E BLK 128GB PPD-USA - 2026 Q3 Promo PIC Offer - $550.00' → 'IPHONE 16E BLK 128GB PPD-USA'
    'moto g play - 2026 - 2026 Q3 Promo Upgrade - $240.00'            → 'moto g play - 2026'

    WHY A SEGMENT TEST AND NOT A SPLIT ON THE FIRST ' - '. The second example carries the model year
    in its own segment, so cutting at the first separator would report two different models for one
    phone. The cut is made at the first segment that NAMES a promotion, which is the fact that marks
    where the model stops. No promotion named → the description is the model, unchanged.

    `promo_tokens` is the vocabulary (RULE TWO: config, never product names in code). An explicitly
    EMPTY tuple means "this tenant's POS does not decorate", and is honoured — the whole description
    is then the model."""
    s = str(product_desc or "").strip()
    if not s:
        return ""
    toks = HOUSE_PROMO_TOKENS if promo_tokens is None else tuple(promo_tokens)
    if not toks:
        return s
    parts = s.split(_SEP)
    keep = []
    for seg in parts:
        low = seg.lower()
        if any(t in low for t in toks):
            break
        keep.append(seg)
    return _SEP.join(keep).strip() or s


def cell_price_cfg(cuts=None, promo_tokens=None):
    """What `router._sales_cell_agg(..., price_cfg=…)` is handed: the two predicates, resolved ONCE
    here, plus the cuts for the payload. The cell pass only ACCUMULATES — it never decides what a
    band or a model is, so there is exactly one answer to each on the platform. PURE."""
    cuts = cuts if isinstance(cuts, tuple) else resolve_price_cuts(cuts)
    return {
        "cuts": cuts,
        "band": (lambda p: band_of_price(p, cuts)),
        "model": (lambda d: model_of(d, promo_tokens)),
    }


# ── THE RANKING FLOOR ─────────────────────────────────────────────────────────────────────────────
# A rep with a handful of boxes has a ratio that is noise: one accessory sale moves accessory-per-box
# by tens of dollars. Such a rep is LISTED with their numbers and the reason, and is excluded from the
# medians and from every verdict. Measured live September 2026: 29 of 99 store-rep pairs clear it.
MIN_BOXES_TO_RANK = 10
# …and a SECOND floor, on the device lines, because the first one does not cover the mix. FOUND BY
# DRIVING THE REPORT ON PRODUCTION, 2026-10-09: a rep clears the box floor on boxes that came from
# the configured activation BUCKETS (a customer-phone activation is a box with no device line at
# all), so the live September run produced the sentence "50% of the 2 devices they sold were in the
# cheap bands" — a mix verdict on two lines. Worse, those reps were in the correlation: including
# them moved cheap-share vs accessory-per-box from r = -0.33 to r = -0.13 (both measured on the
# then-current box basis, before BYOD left `box_departments`), which would have made the
# owner's own claim look weak off an artefact of the floor. So the MIX columns have their own floor,
# and below it they are None with the reason rather than a share of a handful.
MIN_DEVICE_LINES_FOR_MIX = 10
# How far below the median counts as a real gap rather than noise — the SAME cut §59 chose for the
# store-level peer gap, dereferenced rather than re-picked, so a rep and their store are judged
# behind by one rule.
CRITICAL_SHORTFALL = _pc.CRITICAL_SHORTFALL

LOW_VOLUME_REASON = (
    "Fewer than {floor} boxes in this window, so accessory $ per box and port share would move by "
    "tens of dollars on a single sale. The numbers are shown; the rep is not ranked and not flagged.")
LOW_MIX_REASON = (
    "Fewer than {floor} classified device lines in this window, so the share of cheap devices would "
    "be a verdict on a handful of phones. The band counts are shown; the share is withheld and the "
    "rep is left out of the measured correlation.")
NO_PORT_BASIS_REASON = (
    "Port-ins come from the Executive MTD activation predicate, which the caller did not supply, so "
    "every port share here would read 0.00. The port column is withheld rather than shown as a zero.")


def rep_rows(cells, *, store_of=None, cuts=HOUSE_PRICE_CUTS, ports_available=True):
    """`router._sales_cell_agg` cells → ONE row per (store, rep) for the whole window. PURE.

    `store_of` is the caller's store-code resolver (the same one the Peer Sales Comparison uses), so a
    store spelled two ways in the feed is one rep row rather than two halves of one.

    Every number is a roll-up of a cell field — the sets are UNIONED, never summed, because a rep who
    rang one transaction across two days must count once (the §59 negative control, same trap)."""
    agg = {}
    for cell in (cells or {}).values():
        rep = str(cell.get("salesperson") or "").strip()
        if not rep:
            continue
        raw = str(cell.get("store") or "").strip()
        store = (store_of(cell) if store_of else raw) or raw
        key = (store, rep)
        a = agg.get(key)
        if not a:
            a = agg[key] = {"store": store, "rep": rep, "boxes": 0, "accessory_revenue": 0.0,
                            "_port": set(), "_prem": set(), "_txn": set(),
                            "device_lines": 0, "unclassified_lines": 0, "credit_lines": 0,
                            "unnamed_lines": 0,
                            "device_price_sum": 0.0,
                            "bands": {k: 0 for k in PRICE_BAND_KEYS}, "_models": {}}
        a["boxes"] += int(cell.get("box_count") or 0)
        a["accessory_revenue"] += float(cell.get("accessory_rev") or 0.0)
        a["_port"] |= (cell.get("_port") or set())
        a["_prem"] |= (cell.get("_prem") or set())
        a["_txn"] |= (cell.get("_txn") or set())
        a["device_lines"] += int(cell.get("_dev_lines") or 0)
        a["unclassified_lines"] += int(cell.get("_dev_unclassified") or 0)
        a["credit_lines"] += int(cell.get("_dev_credit") or 0)
        a["unnamed_lines"] += int(cell.get("_dev_unnamed") or 0)
        a["device_price_sum"] += float(cell.get("_dev_price_sum") or 0.0)
        for k, n in (cell.get("_dev_bands") or {}).items():
            if k in a["bands"]:
                a["bands"][k] += int(n or 0)
        for model, m in (cell.get("_dev_models") or {}).items():
            t = a["_models"].setdefault(model, {"model": model, "lines": 0, "price_sum": 0.0,
                                                "free_lines": 0, "cheap_lines": 0})
            t["lines"] += int(m.get("lines") or 0)
            t["price_sum"] += float(m.get("price_sum") or 0.0)
            t["free_lines"] += int(m.get("free_lines") or 0)
            t["cheap_lines"] += int(m.get("cheap_lines") or 0)

    out = []
    for (store, rep) in sorted(agg):
        a = agg[(store, rep)]
        boxes, dev = int(a["boxes"]), int(a["device_lines"])
        cheap = sum(a["bands"].get(k, 0) for k in CHEAP_BANDS)
        models = sorted(a["_models"].values(), key=lambda m: (-m["lines"], m["model"]))
        for m in models:
            m["avg_price"] = (round(m["price_sum"] / m["lines"], 2) if m["lines"] else None)
            m.pop("price_sum", None)
        out.append({
            "store": store, "rep": rep,
            "transactions": len(a["_txn"]),
            "boxes": boxes,
            "ports": (len(a["_port"]) if ports_available else None),
            "port_share": (_pc._ratio(len(a["_port"]), boxes) if ports_available else None),
            "accessory_revenue": round(a["accessory_revenue"], 2),
            # THE SAME ratio the Peer Sales Comparison publishes, dereferenced from §59 — not a
            # second division, so the two reports cannot print different accessory-per-box figures.
            "accessory_per_box": _pc._ratio(a["accessory_revenue"], boxes),
            "device_lines": dev,
            "unclassified_lines": int(a["unclassified_lines"]),
            "credit_lines": int(a["credit_lines"]),
            "unnamed_lines": int(a["unnamed_lines"]),
            "avg_device_price": (round(a["device_price_sum"] / dev, 2) if dev else None),
            "bands": dict(a["bands"]),
            "cheap_devices": cheap,
            # THE MIX SHARES ARE GATED ON THEIR OWN FLOOR — see MIN_DEVICE_LINES_FOR_MIX. None is the
            # honest answer for a rep with three device lines; a 67% cheap share off two of them is
            # not a finding, and it skewed the correlation when it was allowed in.
            "mix_ranked": dev >= MIN_DEVICE_LINES_FOR_MIX,
            "cheap_share": (_pc._ratio(cheap, dev) if dev >= MIN_DEVICE_LINES_FOR_MIX else None),
            "free_share": (_pc._ratio(a["bands"].get("free", 0), dev)
                           if dev >= MIN_DEVICE_LINES_FOR_MIX else None),
            "mix_reason": (None if dev >= MIN_DEVICE_LINES_FOR_MIX
                           else LOW_MIX_REASON.format(floor=MIN_DEVICE_LINES_FOR_MIX)),
            "top_models": models[:5],
            # EVERY model, for `model_rollup`'s estate view. FOUND BY DRIVING THE REPORT: the rollup
            # first read `top_models`, so a model that was nobody's top five was missing from the
            # estate list entirely — an estate-grain answer assembled from truncated per-rep lists is
            # not the same question. `build` drops this key before returning so the payload stays
            # small, and the rollup reads it rather than re-walking the cells.
            "all_models": models,
            "ranked": boxes >= MIN_BOXES_TO_RANK,
        })
    return out


# ── THE TWO-SIGNAL VERDICT ────────────────────────────────────────────────────────────────────────
# WHAT IS COMPARED WITH WHAT, and why it is not the traffic band. §59 bands STORES by footfall because
# a store's box count depends on how many people walk in. Neither of the two numbers here does:
# accessory $ per box and ports per box are RATES over the sales a rep actually made, so a rep at a
# quiet door and a rep at a busy one are already comparable. The basis is therefore the median across
# every ranked rep in the caller's scope, and the payload says so.
#
# BOTH SIGNALS, NEVER A COMPOSITE. Measured live, port share and accessory-per-box are INDEPENDENT
# (r = +0.12, re-measured 2026-10-09), so a single blended score would average two unrelated things and
# hide the rep who ports well and attaches nothing. A rep is flagged `both` only
# when they are below the median on accessory-per-box AND on port share — which is precisely the
# "co-relate the two" the owner asked for — and `accessory_only` / `port_only` name the one-signal
# cases rather than burying them.
VERDICTS = (
    ("both", "Low on both"),
    ("accessory_only", "Low accessory per box"),
    ("port_only", "Low port share"),
    ("clear", "At or above the median"),
)


def rank(rows, *, min_boxes=MIN_BOXES_TO_RANK, ports_available=True):
    """Score every rep row against the median of the ranked reps. Mutates and returns (rows, basis).

    A rep below the ranking floor keeps `verdict: None` and carries the reason — never a verdict off
    a ratio the window cannot support. A metric with no median (nobody clears the floor) flags
    nobody: the basis says so instead of the report accusing people against nothing."""
    ranked = [r for r in rows if int(r.get("boxes") or 0) >= min_boxes]
    apb_med = _pc._median([r.get("accessory_per_box") for r in ranked])
    port_med = (_pc._median([r.get("port_share") for r in ranked]) if ports_available else None)
    # `_median` skips None, and the mix gate above is what produces those Nones — so this median is
    # over the reps whose mix is measurable, without a second floor test here.
    cheap_med = _pc._median([r.get("cheap_share") for r in ranked])
    for r in rows:
        if int(r.get("boxes") or 0) < min_boxes:
            r["verdict"] = None
            r["ranked"] = False
            r["reason"] = LOW_VOLUME_REASON.format(floor=min_boxes)
            continue
        r["ranked"] = True
        apb, ps = r.get("accessory_per_box"), r.get("port_share")
        low_acc = bool(apb_med is not None and apb is not None and apb < apb_med)
        low_port = bool(port_med is not None and ps is not None and ps < port_med)
        r["low_accessory"], r["low_port"] = low_acc, low_port
        r["verdict"] = ("both" if (low_acc and low_port)
                        else "accessory_only" if low_acc
                        else "port_only" if low_port else "clear")
        r["accessory_shortfall_pct"] = (
            round(((apb_med - apb) / apb_med) * 100.0, 1)
            if (low_acc and apb_med) else 0.0)
        r["port_shortfall_pct"] = (
            round(((port_med - ps) / port_med) * 100.0, 1)
            if (low_port and port_med) else 0.0)
        # `severity` exists so a surface can sort without re-deciding. CRITICAL needs BOTH signals low
        # AND one of them more than CRITICAL_SHORTFALL below the median — the §59 cut, dereferenced.
        worst = max(r["accessory_shortfall_pct"], r["port_shortfall_pct"]) / 100.0
        r["severity"] = ("critical" if (r["verdict"] == "both" and worst > CRITICAL_SHORTFALL)
                         else "warning" if r["verdict"] != "clear" else None)
    basis = {
        "ranked_reps": len(ranked), "listed_reps": len(rows), "min_boxes": min_boxes,
        "min_device_lines": MIN_DEVICE_LINES_FOR_MIX,
        "mix_ranked_reps": sum(1 for r in ranked if r.get("mix_ranked")),
        "accessory_per_box_median": apb_med,
        "port_share_median": port_med,
        "cheap_share_median": cheap_med,
        "comparison": ("Each rep is measured against the median of the "
                       f"{len(ranked)} rep(s) in view who sold at least {min_boxes} boxes. Both "
                       "numbers are rates over the sales the rep actually made, so unlike a store's "
                       "box count they do not depend on footfall and need no traffic band."),
        "port_basis": (None if ports_available else NO_PORT_BASIS_REASON),
    }
    return rows, basis


def rep_prompt(row):
    """The coaching sentence for ONE flagged rep — the two numbers that make it arguable, and the mix
    that explains it, never an accusation. PURE. Empty string when there is nothing to say."""
    r = row or {}
    if not r.get("verdict") or r["verdict"] == "clear":
        return ""
    bits = []
    if r.get("low_accessory") and r.get("accessory_per_box") is not None:
        bits.append(f"${r['accessory_per_box']:,.2f} accessory per box")
    if r.get("low_port") and r.get("port_share") is not None:
        bits.append(f"{r['port_share'] * 100:.0f}% of their boxes ported in")
    if not bits:
        return ""
    head = f"{r.get('rep')} at {r.get('store')} is at " + " and ".join(bits) + "."
    mix = ""
    if r.get("mix_ranked") and r.get("cheap_share") is not None and r.get("device_lines"):
        mix = (f" {r['cheap_share'] * 100:.0f}% of the {r['device_lines']} devices they sold were in "
               f"the cheap bands")
        top = (r.get("top_models") or [None])[0]
        if top and top.get("avg_price") is not None:
            mix += (f", most often {top['model']} at an average ${top['avg_price']:,.2f} to the "
                    f"customer")
        mix += "."
    return head + mix


def correlations(rows, *, ports_available=True):
    """THE MEASURED relationship between the device mix and the two outcomes, over the ranked reps.
    PURE. `r` is Pearson; `None` when a side does not vary or there are too few reps to say anything.

    This is here because the owner's directive is a causal CLAIM ("pushing cheaper phones … not
    trying to sell higher end devices which bring more accessory sales"), and a report that assumed
    it would be unfalsifiable. The coefficient is recomputed every run, so if the claim stops holding
    on this tenant's data the report says so instead of continuing to coach from it.

    A correlation is not a cause and the payload says that too: a rep at a door whose customers
    cannot afford a $900 handset has the same mix as a rep who never offers one."""
    # BOTH floors, not one: a rep is in the correlation only when their ratios AND their mix are
    # measurable. See MIN_DEVICE_LINES_FOR_MIX for what including the others did to the coefficient.
    ranked = [r for r in rows if r.get("ranked") and r.get("mix_ranked")]
    pairs = [("cheap_share", "accessory_per_box", "Cheap-device share vs accessory $ per box"),
             ("free_share", "accessory_per_box", "Free-device share vs accessory $ per box")]
    if ports_available:
        pairs += [("port_share", "accessory_per_box", "Port-in share vs accessory $ per box"),
                  ("cheap_share", "port_share", "Cheap-device share vs port-in share")]
    out = []
    for x, y, label in pairs:
        xs, ys = [], []
        for r in ranked:
            a, b = r.get(x), r.get(y)
            if a is None or b is None:
                continue
            xs.append(float(a))
            ys.append(float(b))
        out.append({"x": x, "y": y, "label": label, "n": len(xs), "r": _pearson(xs, ys)})
    return out


MIN_CORRELATION_N = 5


def _pearson(xs, ys):
    """Pearson r, or None when it cannot be stated: fewer than MIN_CORRELATION_N pairs, or no spread
    on one side (a constant column makes the coefficient undefined, not zero). PURE."""
    n = len(xs)
    if n < MIN_CORRELATION_N or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    if not sx or not sy:
        return None
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy), 3)


def store_rollup(rows, *, ports_available=True):
    """The rep rows → one row per STORE, for the store-level half of the report and for the metric
    handed to §59's band machinery. PURE.

    The store's accessory-per-box and port share are computed from the store's OWN totals, not as an
    average of its reps' ratios — a mean of ratios weights a two-box rep the same as a sixty-box one.
    """
    agg = {}
    for r in rows:
        s = r.get("store") or ""
        a = agg.setdefault(s, {"store": s, "reps": 0, "flagged_reps": 0, "boxes": 0,
                               "accessory_revenue": 0.0, "ports": 0, "device_lines": 0,
                               "cheap_devices": 0, "ranked_reps": 0})
        a["reps"] += 1
        a["ranked_reps"] += 1 if r.get("ranked") else 0
        a["flagged_reps"] += 1 if r.get("verdict") == "both" else 0
        a["boxes"] += int(r.get("boxes") or 0)
        a["accessory_revenue"] += float(r.get("accessory_revenue") or 0.0)
        a["ports"] += int(r.get("ports") or 0)
        a["device_lines"] += int(r.get("device_lines") or 0)
        a["cheap_devices"] += int(r.get("cheap_devices") or 0)
    out = []
    for s in sorted(agg):
        a = agg[s]
        a["accessory_revenue"] = round(a["accessory_revenue"], 2)
        a["accessory_per_box"] = _pc._ratio(a["accessory_revenue"], a["boxes"])
        a["port_share"] = (_pc._ratio(a["ports"], a["boxes"]) if ports_available else None)
        a["cheap_share"] = _pc._ratio(a["cheap_devices"], a["device_lines"])
        if not ports_available:
            a["ports"] = None
        out.append(a)
    return out


MODEL_LIST_LIMIT = 25


def model_rollup(rows, limit=None):
    """Every device MODEL sold in the window, most-sold first, with what the customer paid on average
    and how much of it went out free. PURE.

    This is the owner's first sentence — "sales by each store as per the product sold … selling more
    of a particular phone at a cheaper price or free" — at estate grain, so a manager can see which
    handset the estate leans on before looking at any individual."""
    agg = {}
    for r in rows:
        for m in (r.get("all_models") if r.get("all_models") is not None
                  else r.get("top_models")) or ():
            a = agg.setdefault(m["model"], {"model": m["model"], "lines": 0, "free_lines": 0,
                                            "cheap_lines": 0, "_price_sum": 0.0, "stores": set()})
            a["lines"] += int(m.get("lines") or 0)
            a["free_lines"] += int(m.get("free_lines") or 0)
            a["cheap_lines"] += int(m.get("cheap_lines") or 0)
            if m.get("avg_price") is not None:
                a["_price_sum"] += float(m["avg_price"]) * int(m.get("lines") or 0)
            a["stores"].add(r.get("store"))
    out = []
    for a in agg.values():
        out.append({"model": a["model"], "lines": a["lines"], "stores": len(a["stores"]),
                    "free_lines": a["free_lines"], "cheap_lines": a["cheap_lines"],
                    "free_share": _pc._ratio(a["free_lines"], a["lines"]),
                    "avg_price": (round(a["_price_sum"] / a["lines"], 2) if a["lines"] else None)})
    out.sort(key=lambda m: (-m["lines"], m["model"]))
    return out[:max(0, int(limit))] if limit else out


CORRELATION_CAVEAT = (
    "A correlation is not a cause. A rep at a door whose customers cannot afford a $900 handset shows "
    "the same device mix as a rep who never offers one, so the mix column is evidence for a "
    "conversation, not a verdict on its own. The two signals the verdict uses — accessory $ per box "
    "and port share — are the rep's own sell-through on the sales they did make.")

MIX_NOT_A_PARTITION = (
    "The device mix is counted beside the box total, not inside it. Boxes additionally count the "
    "activation buckets this tenant configured (a customer-phone activation with no device line is "
    "still a box), and a box department can carry lines that are not a handset at all. So device "
    "lines are fewer than boxes by design and the two are not expected to agree.")


def mix_caveats(rows, *, cuts=HOUSE_PRICE_CUTS, ports_available=True, unclassified_note=None):
    """What this report cannot answer, from measured facts rather than guesses. PURE."""
    out = []
    uncl = sum(int(r.get("unclassified_lines") or 0) for r in rows)
    dev = sum(int(r.get("device_lines") or 0) for r in rows)
    if uncl:
        out.append({"column": "device_lines", "severity": "partial",
                    "message": (f"{uncl} line(s) in a box department are not in the price mix because "
                                f"the activation predicate classifies them as nothing — a kit charge "
                                f"or a service rather than a handset. {dev} line(s) are banded. "
                                f"Counting the rest as cheap phones would put devices in this report "
                                f"where none were sold." + (f" {unclassified_note}"
                                                            if unclassified_note else ""))})
    unnamed = sum(int(r.get("unnamed_lines") or 0) for r in rows)
    if unnamed:
        out.append({"column": "models", "severity": "partial",
                    "message": (f"{unnamed} device line(s) carry no product description, so they are "
                                f"in the price bands but name no model. The model list therefore "
                                f"holds {dev - unnamed} of the {dev} banded device lines. Found by "
                                f"driving the report on production: the two totals disagreed by "
                                f"exactly this count, which is why it is now reported rather than "
                                f"leaving the model list quietly short.")})
    credit = sum(int(r.get("credit_lines") or 0) for r in rows)
    if credit:
        out.append({"column": "bands", "severity": "partial",
                    "message": (f"{credit} device line(s) carry a negative price — a credit against a "
                                f"device, not a cheap device — and are excluded from the bands.")})
    if not ports_available:
        out.append({"column": "port_share", "severity": "cannot_answer",
                    "message": NO_PORT_BASIS_REASON})
    nomix = [r for r in rows if r.get("ranked") and not r.get("mix_ranked")]
    if nomix:
        out.append({"column": "cheap_share", "severity": "partial",
                    "message": (f"{len(nomix)} ranked rep(s) sold fewer than "
                                f"{MIN_DEVICE_LINES_FOR_MIX} classified device lines, so their cheap "
                                f"and free shares are withheld and they are left out of the measured "
                                f"correlation. Their boxes came largely from activation buckets that "
                                f"carry no device line, so a mix verdict would be about two or three "
                                f"phones. Their accessory and port verdicts are unaffected.")})
    unranked = [r for r in rows if not r.get("ranked")]
    if unranked:
        out.append({"column": "verdict", "severity": "partial",
                    "message": (f"{len(unranked)} of {len(rows)} rep(s) sold fewer than "
                                f"{MIN_BOXES_TO_RANK} boxes in this window. They are listed with "
                                f"their numbers but carry no verdict: a ratio over a handful of "
                                f"boxes moves by tens of dollars on one sale.")})
    return out


def build(cells, *, store_of=None, cuts=None, ports_available=True, period=None,
          window_label=None, carrier_port=None, params=None, unclassified_note=None):
    """THE payload. PURE apart from the injected store resolver.

    `cells`          — `router._sales_cell_agg(rows, acfg, exec_cfg=…, price_cfg=…)`. BOTH extras are
                       required for a complete report: `exec_cfg` fills `_port` (so port share exists
                       at all) and `price_cfg` fills the device mix. A caller that omits either gets
                       the column withheld with its reason, never a silent zero.
    `carrier_port`   — {store: percent} from `kpi_failing.port_in_rate` over `raw_dlar_store`: the
                       CARRIER's own port-in rate, which this platform has ingested since migration
                       002 and never shown anywhere. Blank means not reported, never 0%.
    """
    cuts = cuts if isinstance(cuts, tuple) else resolve_price_cuts(cuts)
    rows = rep_rows(cells, store_of=store_of, cuts=cuts, ports_available=ports_available)
    rows, basis = rank(rows, ports_available=ports_available)
    for r in rows:
        r["prompt"] = rep_prompt(r)
    # THE WHOLE LIST FIRST, then the slice — with the slice's own coverage stated. FOUND BY DRIVING
    # THE REPORT: the list was sliced inside the rollup, so its line total was 9 short of the banded
    # device lines with nothing saying why (102 distinct models, 25 shown). A truncated list is fine;
    # a truncated list that looks complete is not.
    all_models = model_rollup(rows)
    models = all_models[:MODEL_LIST_LIMIT]
    models_meta = {
        "distinct": len(all_models),
        "shown": len(models),
        "lines_shown": sum(m["lines"] for m in models),
        "lines_total": sum(m["lines"] for m in all_models),
    }
    models_meta["note"] = (
        None if models_meta["shown"] >= models_meta["distinct"] else
        f"The {models_meta['shown']} most-sold of {models_meta['distinct']} models, covering "
        f"{models_meta['lines_shown']} of {models_meta['lines_total']} banded device lines.")
    for r in rows:
        r.pop("all_models", None)       # see `rep_rows`: the estate view has been taken off it
    stores = store_rollup(rows, ports_available=ports_available)
    cp = carrier_port or {}
    for s in stores:
        # The carrier's own figure BESIDE ours, never merged into it: they count different months and
        # different denominators, and a reader comparing them is the point.
        s["carrier_port_pct"] = cp.get(s["store"])
    flagged = [r for r in rows if r.get("verdict") == "both"]
    flagged.sort(key=lambda r: -(max(r.get("accessory_shortfall_pct") or 0,
                                     r.get("port_shortfall_pct") or 0)))
    return {
        "period": period,
        "window_label": window_label,
        "price_cuts": list(cuts),
        "bands": band_labels(cuts),
        "cheap_bands": list(CHEAP_BANDS),
        "verdicts": [{"key": k, "label": l} for k, l in VERDICTS],
        "basis": basis,
        "reps": rows,
        "flagged": flagged,
        "stores": stores,
        "models": models,
        "models_meta": models_meta,
        "correlations": correlations(rows, ports_available=ports_available),
        "correlation_caveat": CORRELATION_CAVEAT,
        "mix_not_a_partition": MIX_NOT_A_PARTITION,
        "caveats": mix_caveats(rows, cuts=cuts, ports_available=ports_available,
                               unclassified_note=unclassified_note),
        "params": params or {},
        "note": (None if rows else
                 "No rep sold anything in this window, so there is nothing to compare. The device "
                 "mix comes from the same sales lines the Sales Report reads — check the period, and "
                 "that the daily feed has loaded."),
    }
