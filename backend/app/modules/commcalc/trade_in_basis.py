"""TRADE-IN BASIS — the ONE home for "how many trade-ins does this rep get paid for".

Owner ruling 2026-10-12 (decision card, option "Point of sale"): the trade-in spiff counts
trade-ins **as recorded at the till**, not as recorded on the carrier statement.

THE CLASS, not the instance (owner directive 2026-09-20). The general fact that was wrong is not
"Boost stopped sending trade-in rows". It is that **a carrier's reimbursement record was being read
as a record of what the store did.** A trade-in happens at the till; whether the carrier later
reimburses it, under what name, or at all, is a separate fact that may never arrive. The same shape
has bitten this codebase before — a sweep's status is not a record of what it did (§29.12), a
distributor's claim is not a second receipt (§19.x), a `component` without its `basis` is not
evidence (§66). So the question "which record answers this?" becomes a DECLARED per-org fact with
one home, and `calculator.py` dereferences it instead of carrying its own answer.

Measured on production (house org, 2026-10-12), which is why the default is the till:

| month | carrier rows matching the declared type | POS trade-in transactions | paid under the old basis |
|---|---|---|---|
| Jun 2026 | 0 | — | $0 |
| Jul 2026 | 0 | — | $0 |
| Aug 2026 | 0 (statement COMPLETE: 23,871 rows, 44 types) | 31 | $0 |
| Sep 2026 | 50 | 67 | $1,000 |
| Oct 2026 | 1 | 53 | $5 |

RULE TWO (config, never code): no carrier, tenant or department NAME appears here. Which departments,
categories or product words mean "trade-in" is the org's declaration; the house defaults below are
defaults, not branches, and they are the values the house org's feed actually carries.

CASE FOLDING IS DELIBERATE. `box_departments` matching is case-SENSITIVE and the index records that
as a live defect — a feed spelling a department in another case silently contributes nothing (same
class as the `b-1115` phantom store, §29.12). This home folds case on both sides so it cannot inherit
that defect. It does NOT change `box_departments`; that repair moves which lines count as boxes on
every tenant and wants its own measurement.

COUNT DISTINCT TRANSACTIONS, NEVER LINES. One trade-in is one transaction and may be many feed line
rows — this codebase's line-item multiplicity. The `acima_count` set-comprehension beside the caller
exists precisely because summing rows over-paid (20 ACIMA line items read as $500). October happens
to be exactly one line per transaction; nothing may rely on that.

A TRADE-IN WHOSE REP CANNOT BE RESOLVED IS REPORTED, NEVER DROPPED AND NEVER PAID TO NOBODY
(`unresolved_pos_count`). The engine's own rep map is the resolver — this module adds no second one.
"""

# ── The declared basis ────────────────────────────────────────────────────────────────────────────
BASIS_POS = "pos"          # the till: the store's own record of the trade-in
BASIS_CARRIER = "carrier"  # the statement: the carrier's reimbursement record
BASIS_HIGHER = "higher"    # whichever record shows more, per rep

BASES = (BASIS_POS, BASIS_CARRIER, BASIS_HIGHER)

#: The house default. The owner's ruling is about the MECHANISM, not about one carrier: the till is
#: where a trade-in happens. An org that has not declared a basis reads as the till.
DEFAULT_BASIS = BASIS_POS

#: What the house org's sales feed actually spells (measured 2026-10-12: `department` and `category`
#: both exactly 'Trade In', one spelling across all 152 rows ever). Defaults, never branches.
DEFAULT_POS_DEPARTMENTS = ("trade in",)
DEFAULT_POS_CATEGORIES = ("trade in",)
DEFAULT_POS_KEYWORDS = ()

#: The carrier side's historic predicate, preserved exactly: a payment type CONTAINING this.
DEFAULT_CARRIER_PAYMENT_TYPES = ("trade",)


def resolve_basis(value):
    """The declared basis, as an ALLOWLIST — an unruled value reads as the house default.

    Same posture as `carrier_dollar_class.component_is_evidence` (§66): a basis nobody ruled on must
    not silently become a third behaviour. A typo in a config row falls back to the till and the
    engine reports which basis it used, rather than paying on a value nothing defines.
    """
    v = str(value or "").strip().lower()
    return v if v in BASES else DEFAULT_BASIS


def _fold(values, default):
    """A declared list as a case-folded set of non-empty tokens; empty declaration → the default.

    `None` and the string spellings a feed or a JSON round-trip produces for an empty cell are
    DROPPED, not folded. `str(None)` is `'none'`, which is truthy after stripping — so the obvious
    one-liner turns a NULL element of a config array into a department literally named "none", and
    any feed row whose department is blank-spelled-as-None would then be paid as a trade-in. Caught
    by this module's own harness (§C5) before it shipped.
    """
    out = set()
    for v in (values or ()):
        if v is None:
            continue
        t = str(v).strip().lower()
        if not t or t in ("none", "null", "nan", "nat"):
            continue
        out.add(t)
    return out or {str(v).strip().lower() for v in default}


def resolve_declaration(cfg):
    """The org's POS trade-in declaration: `{'departments', 'categories', 'keywords'}`, case-folded.

    `keywords` is a SUBSTRING test over the product description, like the accessory and setup-fee
    declarations beside it; departments and categories are exact (folded) matches. Keywords default
    to EMPTY on purpose — a substring test is the loosest of the three and nothing in the house feed
    needs it, so it stays off until a tenant asks for it.
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    return {
        "departments": _fold(cfg.get("trade_in_departments"), DEFAULT_POS_DEPARTMENTS),
        "categories": _fold(cfg.get("trade_in_categories"), DEFAULT_POS_CATEGORIES),
        "keywords": {str(v).strip().lower() for v in (cfg.get("trade_in_keywords") or ()) if str(v).strip()},
    }


def resolve_carrier_payment_types(cfg):
    """The declared carrier payment-type substrings; empty declaration → the historic `('trade',)`."""
    cfg = cfg if isinstance(cfg, dict) else {}
    return _fold(cfg.get("trade_in_payment_types"), DEFAULT_CARRIER_PAYMENT_TYPES)


# ── The two counts ────────────────────────────────────────────────────────────────────────────────
def is_pos_trade_in(department, category, product="", decl=None):
    """Does this sales LINE record a trade-in at the till? Case-folded on both sides."""
    decl = decl if isinstance(decl, dict) else resolve_declaration(None)
    d = str(department or "").strip().lower()
    c = str(category or "").strip().lower()
    if d and d in decl["departments"]:
        return True
    if c and c in decl["categories"]:
        return True
    kws = decl["keywords"]
    if kws:
        p = str(product or "").strip().lower()
        if p and any(k in p for k in kws):
            return True
    return False


def _txn(row):
    """A feed row's transaction id, spelled the one way this codebase spells it."""
    return str((row or {}).get("trans_id", "")).replace(".0", "").strip()


def pos_trade_in_txns(sales_rows, decl=None):
    """The DISTINCT transaction ids of the trade-ins in `sales_rows`, as a set.

    A row with no `trans_id` cannot be deduplicated, so it is counted ONCE under a key of its own
    (its index) rather than dropped — the same rule `box_count`'s guard settled on (§59.8 §H8). The
    caller passes a rep's own already-resolved sales rows, so attribution is the engine's, not ours.
    """
    decl = decl if isinstance(decl, dict) else resolve_declaration(None)
    out = set()
    for i, r in enumerate(sales_rows or ()):
        if not is_pos_trade_in(r.get("department"), r.get("category"), r.get("product_desc"), decl):
            continue
        tid = _txn(r)
        out.add(tid if tid else ("__no_txn__", i))
    return out


def is_carrier_trade_in(payment_row, types=None):
    """Does this carrier payment row record a trade-in? THE per-row predicate.

    The engine's payment loop groups rows by rep login as it goes, so it needs the predicate rather
    than an aggregate — and `carrier_trade_in_count` below dereferences this same function, so
    "what counts as a trade-in on the statement" has one answer and not two. Preserves the historic
    behaviour exactly: a payment type merely CONTAINING the declared token counts.
    """
    types = types if types else resolve_carrier_payment_types(None)
    pt = str((payment_row or {}).get("payment_type", "")).lower()
    return any(t in pt for t in types)


def carrier_trade_in_count(payment_rows, types=None):
    """How many carrier payment rows record a trade-in. Rows, not transactions: a statement line IS
    the carrier's unit of reimbursement and has no transaction id to deduplicate on."""
    types = types if types else resolve_carrier_payment_types(None)
    return sum(1 for p in (payment_rows or ()) if is_carrier_trade_in(p, types))


def count_for_pay(basis, pos_count, carrier_count):
    """`(count, which)` — the number this rep is paid for, and which record it came from.

    `which` is returned so a surface can print the basis beside the number: a count without its
    basis is the §66 defect (a placement read as evidence). `higher` names the record that won, and
    names the till on a tie, because the till is the house default and a tie is not evidence that
    the statement arrived.
    """
    p, c = max(0, int(pos_count or 0)), max(0, int(carrier_count or 0))
    basis = resolve_basis(basis)
    if basis == BASIS_CARRIER:
        return c, BASIS_CARRIER
    if basis == BASIS_HIGHER:
        return (c, BASIS_CARRIER) if c > p else (p, BASIS_POS)
    return p, BASIS_POS


def unresolved_pos_count(all_sales_rows, resolved_txns, decl=None):
    """How many till trade-ins reached NO rep — reported, never paid to nobody, never dropped.

    `all_sales_rows` is the period's whole valid feed; `resolved_txns` is the union of every rep's
    `pos_trade_in_txns`. The engine skips a row whose `salesperson` is blank or the admin account,
    so such a trade-in is real money the store earned that no rep can be paid for, and the owner has
    to see it rather than have it silently vanish into a smaller count.
    """
    everything = pos_trade_in_txns(all_sales_rows, decl)
    return len(everything - set(resolved_txns or ()))
