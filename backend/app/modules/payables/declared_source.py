"""A DECLARED SOURCE THAT CANNOT BE READ IS REPORTED — never read as "nothing was declared".

OWNER-FACING DEFECT, 2026-10-23. `commcalc.payable_source_map` declared Total's device-cost evidence
feed as `raw_ma_fulfillment.sku`. That table has no `sku` column, so the read raised, the caller's
`except` returned an empty price map, and all 2,923 unsold Luxelink devices were written with
`owed = NULL` and `owed_source = 'unconfigured'` — the byte-identical outcome to a carrier nobody had
configured at all. The screen said "no invoice has priced this device", which is a sentence about the
DATA, while the truth was a sentence about the SETUP. Nothing anywhere said so.

THE CLASS, NOT THE INSTANCE. The config names a table and a column; the reader swallows the failure
into an empty result; the empty result is then indistinguishable from "this carrier declares nothing".
Three readers in `payables/engine.py` had exactly that shape, and each one's silence is a different
wrong answer:

  · the device-cost EVIDENCE feed  -> every device unpriced, reading as "nobody invoiced it"
  · `owed_link` (mig 620)          -> every device unpriced, same
  · the sold-match feed            -> every device reads UNSOLD and storeless, which is the one
                                      thing the out-of-pocket verdict must never get wrong

ONE FACT, ONE HOME. "Which declared reads failed, and what did that cost" lives here. Callers pass a
`Complaints` and get the SAME fail-soft they had — a rebuild is never aborted by one bad config row —
but the failure now travels out with the result instead of dying in a `print`.

DEGRADE, DON'T DIE. A failure reading the EVIDENCE for a price is not a failure to price. The price
home (`account/device_cogs.ma_unit_price_map`) documents its own behaviour with no evidence: it falls
back to its order-type allowlist. The old caller threw that fallback away along with the evidence —
73% coverage became 0%. `evidence_degraded` is that distinction, stated once.

WHO SEES WHAT. A complaint's note names the table and column, because the person who must fix it is
the platform admin reading the rebuild response. It is emitted under a MESSAGE KEY (`warnings`), so
the existing §19.36 boundary (`core/setup_notice.py`) rewrites it to the one customer sentence for
anyone who is not the super admin, and logs the technical text server-side. No second copy of that
decision is made here.

PURE — no imports beyond the standard library, no client, no config read. Proved by
`backend/harness_payables_declared_source.py`.
"""

#: What was being read. One per declared (table, column) pair a payables build dereferences.
KIND_PRICE_EVIDENCE = "price_evidence"
KIND_PRICE_LIST = "price_list"
KIND_OWED_LINK = "owed_link"
KIND_SOLD_MATCH = "sold_match"

#: What the silence costs, per kind — the sentence a reader of the rebuild response needs in order to
#: know whether the numbers in front of them can be trusted. Keyed by kind so a new declared read
#: states its own consequence here rather than inventing wording at the call site.
COST = {
    KIND_PRICE_EVIDENCE: ("device costs fell back to the order-type allowlist, so some models that "
                          "should price are unpriced"),
    KIND_PRICE_LIST: "no device carries a cost, so every out-of-pocket amount reads as not measured",
    KIND_OWED_LINK: "devices this report would have priced carry no amount",
    KIND_SOLD_MATCH: ("devices read as UNSOLD and with no store, so the out-of-pocket list is "
                      "overstated"),
}

#: `owed_source` when a cost source WAS declared and produced nothing. Distinct from 'unconfigured',
#: which must keep meaning what it has always meant: this carrier declares no cost source at all.
OWED_SOURCE_UNCONFIGURED = "unconfigured"
OWED_SOURCE_UNREADABLE = "price_source_unreadable"


def owed_source_label(declared, have_prices):
    """The `owed_source` stamped on a row whose own feed carries no amount.

    `declared` — the map asked for a cost source. `have_prices` — one was actually built. Declared
    with nothing built is the SETUP defect above and says so; not declared stays 'unconfigured'."""
    if declared and not have_prices:
        return OWED_SOURCE_UNREADABLE
    return OWED_SOURCE_UNCONFIGURED


def note(kind, table="", field="", detail="", label=""):
    """One line naming the carrier, the declared table and column, the consequence, and the cause.

    Goes out under a `warnings` key, so the §19.36 boundary decides what a non-admin sees."""
    where = ".".join([p for p in (str(table or "").strip(), str(field or "").strip()) if p])
    bits = [f"{label or 'carrier'}:"]
    bits.append(f"the declared {kind.replace('_', ' ')} source" + (f" ({where})" if where else "")
                + " could not be read")
    cost = COST.get(kind)
    if cost:
        bits.append(f"— {cost}")
    d = str(detail or "").strip()
    if d:
        bits.append(f"[{d[:200]}]")
    return " ".join(bits)


class Complaints:
    """The declared reads that failed during one build. Append-only; never raises."""

    def __init__(self, label=""):
        self.label = str(label or "")
        self.items = []

    def record(self, kind, table="", field="", detail="", label=None):
        self.items.append({
            "kind": kind,
            "table": str(table or "") or None,
            "field": str(field or "") or None,
            "note": note(kind, table, field, detail, label if label is not None else self.label),
        })
        return self

    def kinds(self):
        return [i["kind"] for i in self.items]

    def notes(self):
        return [i["note"] for i in self.items]

    def __bool__(self):
        return bool(self.items)

    def __len__(self):
        return len(self.items)


def read_declared(complaints, kind, table, field, read, empty, label=None):
    """Run `read()`; on ANY failure record a complaint and return `empty`.

    The fail-soft the callers already had, with the failure kept. `read` is the only thing here that
    touches a client, which is what keeps this module provable with a lambda that raises."""
    try:
        return read()
    except Exception as e:                           # noqa: BLE001 - one bad config never aborts a build
        if complaints is not None:
            complaints.record(kind, table, field, f"{type(e).__name__}: {e}", label)
        return empty


def merge(result, complaints):
    """Fold one carrier's complaints into a build result dict, under the message key `warnings`."""
    if not complaints:
        return result
    out = list(result.get("warnings") or [])
    out.extend(complaints.notes())
    result["warnings"] = out
    return result
