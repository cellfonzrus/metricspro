"""NOTHING WAS FILED IS A FACT WITH NO ROW — the declaration for /closing/closing-pickup-status
(§CORE-17).

This report answers the owner's two questions of 2026-10-14: which stores did not submit a closing,
and whose store cash is still sitting in the drawer. Its tiles are counts of store-days and three
cash figures, and the audit's finding was the one every work queue has: a manager reading
"Not picked up: 14" or "Cash still in stores $9,480" cannot see which fourteen, or whose money.

**THE ROWS THIS REPORT EXISTS FOR HAVE NO EVIDENCE BEHIND THEM, AND THAT IS THE POINT.** Every other
report in this audit drills from a figure to the rows it was read out of. Here the most important
rows were read out of NOTHING: the store-day is on the report because the roster expected that store
to file and nothing at all was recorded for it — no closing sheet, no turned-away submit, no pickup
envelope, no count. So those rows name no table, which the shared fold reports as a STATED,
COUNTABLE untraceable origin, and they carry `not_filed` as their reason plus a field saying in words
that the roster is the only thing that put them there. Naming a table for them would be a provenance
claim nothing checked; leaving them out would hide the report's own subject.

PROVENANCE IS READ PER ROW, and on this report a row can have anywhere from zero to five tables
behind it, because each of the five stages either ran or did not:

- `daily_closing` where a closing was actually filed,
- `closing_attempt` where somebody tried — whether or not a row survived the close gate, because a
  turned-away submit is real evidence that a person came in,
- `daily_closing_verification` where a DM answered for the store-day,
- `cash_pickup` where an envelope was recorded,
- `envelope_count` where management counted it.

**REPORTED, NOT PAPERED OVER — the "No cash to collect" tile counts store-days nobody filed.**
`deposit_accountability.cash_pickup_state` reaches `nothing_to_collect` when there are no declared
envelopes AND nothing was collected, and a store-day with no closing at all has no declared
envelopes. So an unfiled store-day lands under a tile that reads "No cash to collect" when the truth
is that nobody told us what was in the drawer. The figure is UNCHANGED — a figure is never moved to
make a screen read better — and the panel says which of its rows are the unfiled ones.

**THE THREE CASH FIGURES REFUSE TO COUNT AN UNFILED STORE-DAY AT $0.00.** An unfiled day contributes
0.00 to declared, picked and awaiting today; the SUM is right and the presentation is not, because
"$0 still in store" reads as "no money is waiting" when the drawer was never counted. Those rows are
shown in all three panels and NOT counted, with `not_filed` stated. The tie-out still holds exactly,
because what they contributed was zero.

THE STATE VOCABULARIES ARE NOT RE-SPELLED HERE. The closing states come from `unfinished_day.STATES`
/ `LABELS` and the pickup states from `deposit_accountability.PICKUP_STATES` / `PICKUP_LABELS`, so a
state renamed in its own home renames the tile, and this file cannot drift from the report it
explains. The figures themselves are the endpoint's own `chain_summary` output, dereferenced — never
a second fold of the same rows.
"""
from app.modules.core import report_drilldown

from . import deposit_accountability as _da
from . import unfinished_day as _ufd

#: One store-day's address — the grain of every figure on this report.
ROW_KEYS = ("store_code", "day")

#: Where each of the five stages is recorded. Named only on the rows that stage actually answered for.
CLOSING = "daily_closing"
ATTEMPT = "closing_attempt"
VERIFICATION = "daily_closing_verification"
PICKUP = "cash_pickup"
COUNT = "envelope_count"

#: What a store-day with no evidence at all is, in the reader's words. It is not a blank: it is the
#: report's subject.
_NO_EVIDENCE = "nothing recorded — the store roster expected this store to file"


def _stage(r, key):
    """One stage cell of a row, BY KEY. The stages ride as a list in chain order, so reading one by
    position would quietly point at another stage the day the chain gains a stage — which is exactly
    what the screen's own `cell()` helper avoids on the client side."""
    for st in ((r or {}).get("stages") or []):
        if (st or {}).get("key") == key:
            return st
    return {}


def _filed(r):
    """Did a closing sheet actually land for this store-day? Read off the closing stage's own row
    count, which is the one place that is decided."""
    return int(_stage(r, "closing").get("rows") or 0) > 0


def _tried(r):
    """Did somebody attempt a submit, whether or not it survived the close gate? `unfinished_day`
    already counts both, and a turned-away submit is evidence a person was in the store."""
    c = _stage(r, "closing")
    return int(c.get("tries") or 0) > 0 or int(c.get("turned_away") or 0) > 0


def _tables(r):
    """A store-day's provenance: the tables that ACTUALLY answered for it, which on this report can
    be none of them. An empty tuple is what the shared fold turns into a stated, countable
    untraceable origin — see this module's docstring for why that is the honest answer here."""
    r = r or {}
    out = []
    if _filed(r):
        out.append(CLOSING)
    if _tried(r):
        out.append(ATTEMPT)
    v = _stage(r, "dm_verify")
    if v.get("done") or v.get("note") or v.get("at"):
        out.append(VERIFICATION)
    if int(_stage(r, "pickup").get("envelopes") or 0) > 0:
        out.append(PICKUP)
    if int(_stage(r, "mgmt_review").get("counts") or 0) > 0:
        out.append(COUNT)
    return tuple(out)


def _label(r):
    r = r or {}
    return " · ".join(str(x) for x in (
        r.get("store_name") or r.get("store_code") or "?",
        str(r.get("day") or "?")[:10],
    ))


def _fields(r):
    """What identifies and explains one store-day. Every value is read off the row the report already
    built — the states, the labels and the money are the chain's own, so this cannot disagree with
    the line the reader is looking at."""
    r = r or {}
    _p = _stage(r, "pickup")
    return {"store_code": r.get("store_code"),
            "closing": r.get("closing_label"),
            "pickup": r.get("pickup_label"),
            "stuck_at": r.get("stuck_label"),
            "declared": r.get("cash_declared"),
            "picked": r.get("cash_picked"),
            "awaiting": r.get("cash_awaiting"),
            "envelopes": _p.get("envelopes"),
            "evidence": None if _tables(r) else _NO_EVIDENCE}


def _one(r):
    """A store-day count figure's contribution: one store-day."""
    return 1 if r else None


def _money(field):
    """A cash figure's contribution — and None, NEVER 0.00, for a store-day nobody filed. The drawer
    of an unfiled day is unknown, not empty, and a counted zero says the opposite."""
    def read(r):
        if not _filed(r):
            return None
        return float((r or {}).get(field) or 0)
    return read


def _money_reason(r):
    return None if _filed(r) else "not_filed"


def _in_closing_state(state):
    def read(r):
        return (r or {}).get("closing_state") == state
    return read


def _in_pickup_state(state):
    def read(r):
        return (r or {}).get("pickup_state") == state
    return read


_UNFILED_NOTE = ("A store-day nobody filed a closing for is shown here and NOT counted: the drawer "
                 "was never counted, so it contributes nothing to this figure, which is not the same "
                 "as contributing $0.00.")

_NOTHING_NOTE = ("This count includes store-days nobody filed a closing for. With no closing there "
                 "are no declared envelopes, so the chain reads \"no cash to collect\" when the "
                 "truth is that nobody told us what was in the drawer. Those rows say so.")


def catalogue():
    """Every figure on the report's tile strip, from ONE declaration. The two state vocabularies are
    dereferenced from their own homes, so a state added or renamed there moves these tiles with it
    rather than leaving a tile counting a key nothing produces."""
    common = {"row_label": _label, "keys": ROW_KEYS, "schema": "commcalc", "fields": _fields,
              "tables": _tables}
    count = dict(common, unit=report_drilldown.UNIT_COUNT, value=_one)
    out = [
        dict(count, key="store_days", total="store_days", label="Store-days on this report",
             note=("Every active store appears for every day in the range, so a store that filed "
                   "nothing is listed rather than left out — which is why some of these rows have "
                   "no record behind them at all.")),
    ]
    for st in _ufd.STATES:
        out.append(dict(count, key="closing_%s" % st, total="closing_%s" % st,
                        label="Store-days: %s" % _ufd.LABELS.get(st, st),
                        in_figure=_in_closing_state(st)))
    for st in _da.PICKUP_STATES:
        out.append(dict(count, key="pickup_%s" % st, total="pickup_%s" % st,
                        label="Store-days: %s" % _da.PICKUP_LABELS.get(st, st),
                        in_figure=_in_pickup_state(st),
                        **({"note": _NOTHING_NOTE} if st == _da.NOTHING_TO_COLLECT else {})))
    for key, label in (("cash_declared", "Cash the reps declared"),
                       ("cash_picked", "Cash collected"),
                       ("cash_awaiting", "Cash still in stores")):
        out.append(dict(common, key=key, total=key, label=label,
                        unit=report_drilldown.UNIT_MONEY, value=_money(key),
                        in_figure=_filed, reason=_money_reason, note=_UNFILED_NOTE))
    return tuple(out)


def figures(summary):
    """PURE: the endpoint's own `chain_summary` output, FLATTENED to the keys the catalogue ties
    against. It folds nothing: every number here is read out of the summary the screen is already
    showing, so a tile and its panel cannot be two different arithmetics.

    A state the summary did not report reads as 0 rather than being absent, because `chain_summary`
    already emits every state key even at zero and a tile that vanished would be a tile nobody could
    ask about."""
    s = summary or {}
    out = {"store_days": int(s.get("store_days") or 0)}
    for row in (s.get("closing_states") or []):
        out["closing_%s" % (row or {}).get("key")] = int((row or {}).get("store_days") or 0)
    for row in (s.get("pickup_states") or []):
        out["pickup_%s" % (row or {}).get("key")] = int((row or {}).get("store_days") or 0)
    for st in _ufd.STATES:
        out.setdefault("closing_%s" % st, 0)
    for st in _da.PICKUP_STATES:
        out.setdefault("pickup_%s" % st, 0)
    for key in ("cash_declared", "cash_picked", "cash_awaiting"):
        out[key] = round(float(s.get(key) or 0), 2)
    return out


def drilldowns(rows, summary, *, cap=None):
    return report_drilldown.tiles(catalogue(), rows, figures(summary),
                                  cap=cap or report_drilldown.ROW_CAP)
