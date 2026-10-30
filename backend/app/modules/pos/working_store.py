"""WHICH STORE IS THIS SALE FOR, AND IS THAT A CONFIRMED FACT? — the ONE home (index §POS-14).

THE INSTANCE (owner, 2026-10-30). *"since we have multiple stores under 1 tenant, the user if
assigned to multiple stores should confirm which store they are working in so the receipt and the
sale are logged for that store."*

THE CLASS. The question had no home at all, and four POS screens each answered it with the SAME
ladder spelled four times — the terminal's saved pick, else the login's own `store_code`, else
`stores[0].store_code`. Three things follow from that, and all three were live:

  · a GUESS was indistinguishable from an ANSWER. Nothing on the screen, and nothing in the sale,
    recorded that no human had chosen the store the receipt was about to be printed under;
  · the saved pick was a bare `localStorage` key with no user in it, so the next cashier to sign in
    at that till inherited the last one's store;
  · `POST /pos/sales/checkout` never compared the sale's store to the caller's span. It accepted any
    store in the tenant, and accepted NO store — and `pos.checkout` writes NULL when the caller
    omits it, which `_span_filter` then shows to every store-scoped manager in the tenant.

So this module does not hold a dropdown's state. It holds the fact and its BASIS, and it refuses
rather than guessing. Every caller — the register, the activations screen, the inventory screen, the
special-orders screen, and the checkout endpoint that takes the money — dereferences it.

PURE. Rows in, verdict out: no Supabase client, no request, no `Header`. The caller fetches the
roster and the keyset (`storeops.scope_keyset`, where `None` means unrestricted) and hands them
over, which is what lets the proof harness and the unit tests run with no database at all.
"""
from __future__ import annotations

#: The cashier picked this store, on this terminal, under this login.
BASIS_CONFIRMED = "confirmed"
#: Exactly one store is ringable, so there is nothing to confirm — and no guess either.
BASIS_SOLE = "sole_store"
#: Nobody has chosen, and more than one store is possible. NEVER carries a store_code.
BASIS_UNCONFIRMED = "unconfirmed"

#: What a cashier is told. Written once, so two screens cannot word one refusal differently.
ASK_SENTENCE = ("Confirm which store you are working in before you ring up a sale — the receipt "
                "and the sale are logged against it.")
NO_STORE_SENTENCE = ("Your login is not granted any store that can ring up a sale. Ask an admin to "
                     "add your stores in Roles & Access.")


def _up(v) -> str:
    return str(v or "").strip().upper()


def _in_keyset(keyset, *vals) -> bool:
    """True when unrestricted (`keyset` None) or any of `vals` matches an allowed store key.

    Deliberately the same contract as `core.scope.in_keyset`, which this cannot import without
    dragging a Supabase client into a pure module; §E of the harness RUNS both over the same rows
    and fails the build if the two ever disagree, so the agreement is proved rather than asserted.
    """
    if keyset is None:
        return True
    return any(_up(v) in keyset for v in vals)


def span(roster, keyset, login_store=None) -> dict:
    """The stores this caller may ring a sale at, from the roster and the caller's reporting span.

    `roster` is `storeops.stores` rows (`store_code` + `address`; see §POS-10 for why no `name`).
    `keyset` is `storeops.scope_keyset`'s answer — `None` for an unrestricted caller, else a set of
    UPPER keys that holds each granted store's code AND its address, which is why both are offered
    to the match.

    `must_confirm` is True whenever the caller cannot be ringing at exactly one known store. An
    EMPTY span counts as must_confirm: a caller who can ring nowhere needs an answer, and False
    there would read as "go ahead with no store".
    """
    codes, seen = [], set()
    for row in roster or []:
        code = str((row or {}).get("store_code") or "").strip()
        if not code or _up(code) in seen:
            continue
        if _in_keyset(keyset, code, (row or {}).get("address")):
            codes.append(code)
            seen.add(_up(code))
    pinned = str(login_store or "").strip()
    return {"stores": codes,
            "login_store": next((c for c in codes if _up(c) == _up(pinned)), None) if pinned else None,
            "must_confirm": len(codes) != 1}


def resolve(span_: dict, requested) -> dict:
    """The working store and WHY it is the working store.

    `requested` is what the terminal says was picked — nothing else is ever consulted. There is no
    positional fallback here on purpose: `stores[0]` was the defect, and a sole-store span is the
    only case where an unpicked store is still a known fact rather than a guess.

    Resolves to the ROSTER'S OWN SPELLING of the code, not the spelling that was typed: store codes
    are compared without case folding elsewhere in the platform, and a lower-cased code on a batch
    of shifts once became a phantom store holding a day's money (see the index, store-code case).
    """
    codes = list((span_ or {}).get("stores") or [])
    asked = str(requested or "").strip()
    if asked:
        hit = next((c for c in codes if _up(c) == _up(asked)), None)
        if hit:
            return {"store_code": hit, "basis": BASIS_CONFIRMED, "refusal": None}
        return {"store_code": None, "basis": BASIS_UNCONFIRMED,
                "refusal": f"{asked} is not a store you can ring up a sale at. "
                           f"Pick one of your own stores."}
    if not codes:
        return {"store_code": None, "basis": BASIS_UNCONFIRMED, "refusal": NO_STORE_SENTENCE}
    if len(codes) == 1:
        return {"store_code": codes[0], "basis": BASIS_SOLE, "refusal": None}
    return {"store_code": None, "basis": BASIS_UNCONFIRMED, "refusal": ASK_SENTENCE}


def sale_refusal(span_: dict, store_code) -> str | None:
    """Why this sale may NOT be written at this store — None when it may. What checkout enforces.

    A sale with no store is refused even when the span holds exactly one, so the stored column
    carries the store rather than leaving every later reader (the receipt, the day's closing, the
    P&L) to infer it from who was signed in.
    """
    asked = str(store_code or "").strip()
    if not asked:
        codes = list((span_ or {}).get("stores") or [])
        return NO_STORE_SENTENCE if not codes else ASK_SENTENCE
    r = resolve(span_, asked)
    return r["refusal"] if r["basis"] != BASIS_CONFIRMED else None
