"""Google Reviews module (Phase 1) — mod-people, migrations 411-413.

Owner directive 2026-07-27: pull each store's Google rating from its address, highlight it on the
employee dashboard for whoever is scheduled there, name-match employees in the review text (congrats
on a good mention / prompt to review the page on a bad one), and require an action plan when a
store's rating falls below its (tenant-configurable, default 4.7) target — reviewed by the DM, who
can push it back with comments and a due date.

WHY THIS FILE EXISTS SEPARATE FROM router.py: same shape as pto_accrual.py / payroll_expenses.py —
DB-touching but framework-free logic that `storeops/router.py`'s endpoints call into, so it is
unit-testable without booting FastAPI (see harness_people_google_reviews.py). It does NOT define its
own APIRouter (unlike hr/letters.py's sub-router) because it needs `storeops/router.py`'s OWN caller-
identity/span helpers (`_caller_identity`, `_caller_span_codes`, `_require_manager`, …) for every
endpoint here, and importing those back into this module would either (a) risk the circular-import
trap letters.py's docstring explicitly calls out, or (b) mean duplicating a large, security-sensitive
chunk of org-span logic — worse than keeping the endpoints in router.py itself, which already has
those helpers in scope.

HONEST LIMITATION (surface this in the UI too, never claim otherwise): Google Places API (New) Text
Search + Place Details returns only Google's own curated "most relevant" review subset (typically
~5), not every review ever left. The Phase-2 upgrade for ALL reviews + reply capability is the Google
Business Profile API (OAuth; the tenant would need to connect/own their own listing) — a real product
decision, not something to silently paper over here.

Name matching is intentionally CONSERVATIVE (word-boundary, case-insensitive, first-name-length
gated) and every hit is labeled 'possible' — never a certainty claim. See match_employees_in_text.
"""
from __future__ import annotations

import hashlib
import math as _math
import re as _re
import re
from datetime import datetime, timedelta, timezone, date as _date

import requests

DEFAULT_TARGET = 4.7
TARGET_MIN, TARGET_MAX = 1.0, 5.0
MIN_FIRST_NAME_LEN = 3          # names shorter than this are skipped entirely — too many false positives
DEFAULT_AREA_KEY = "google_reviews"
ACTION_PLAN_STATUSES = ("required", "submitted", "pushed_back", "in_progress", "completed")

# Phase 1.5 (owner directive 2026-08-06, "google reviews everywhere"): how far back a per-EMPLOYEE
# store-set lookup looks for a shift, before today, when deciding which store(s)' ratings to show
# for that employee (home_store is always included regardless). RULE TWO — tenant-tunable
# (storeops.google_review_config.lookback_days, migration 420), never a bare constant. The forward
# window stays the existing hard-coded 14 days (same as /google-reviews/my — not reopened here).
DEFAULT_LOOKBACK_DAYS = 30
LOOKBACK_MIN, LOOKBACK_MAX = 1, 365
DEFAULT_FORWARD_DAYS = 14

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"


# ── small pure helpers ──────────────────────────────────────────────────────────────────────────
def mask_api_key(key: str | None) -> str | None:
    """Never return the raw key. Shows only a trailing 4-char hint, e.g. '••••••••WxYz' — but a key
    SHORTER than 8 chars gets an all-star mask with NO hint at all (Gate-1 N6: a <4-char key used to
    be revealed WHOLE via `key[-4:]`; any short key is masked opaque instead, since 4 of a <8-char
    key is a large fraction of it)."""
    key = (key or "").strip()
    if not key:
        return None
    if len(key) < 8:
        return "•" * 8
    return "•" * 8 + key[-4:]


def clamp_target(v, default=DEFAULT_TARGET) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return max(TARGET_MIN, min(TARGET_MAX, f))


def clamp_lookback_days(v, default=DEFAULT_LOOKBACK_DAYS) -> int:
    """Always a valid 1-365 integer (RULE TWO: a tunable window with a sane default, never a crash
    and never unbounded). Garbage/missing -> `default` (which callers pass as the code default,
    30 — see get_config's degrade-gracefully shape when migration 420 hasn't run)."""
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return default
    return max(LOOKBACK_MIN, min(LOOKBACK_MAX, n))


def effective_target(store_row: dict | None, org_target_default) -> float:
    """Per-store target_override wins; else the org's target_default; else the code default. Always
    a valid 1.0-5.0 number (RULE TWO: a tunable threshold with a sane default, never a crash)."""
    if store_row and store_row.get("target_override") is not None:
        return clamp_target(store_row["target_override"])
    return clamp_target(org_target_default, DEFAULT_TARGET)


def reviews_needed_for_target(rating, review_count, target, best: float = 5.0):
    """How many further 5.0 reviews lift a store from `rating` to `target` (OWNER 2026-08-10: "if the
    reviews are below 4.7 ... tell the employee how many 5.0 reviews they need to get above 4.7").

    A Google rating is the MEAN of its reviews, so with R over N reviews, adding k perfect ones gives
    (R·N + best·k) / (N + k). Requiring that to reach T and solving for k:

        k >= N·(T - R) / (best - T)

    rounded UP, since you cannot leave half a review. With T=4.7 the denominator is 0.3, which is why
    the number climbs so fast: a 4.3 over 6 reviews needs 8 more, not 1 or 2. That steepness is the
    honest, useful part of the answer — it is the difference between "ask a few customers" and "this
    needs a sustained push".

    Returns None (not 0) when the question does not apply: already at/above target, no rating yet, no
    reviews yet, or a target at/above `best` (mathematically unreachable — no number of 5.0s averages
    above 5.0). None means "no answer", 0 would wrongly read as "you are there"."""
    try:
        r = float(rating)
        n = int(review_count)
        t = float(target)
    except (TypeError, ValueError):
        return None
    if n <= 0 or t >= best or r >= t:
        return None
    # EPSILON, not decoration: 4.3 over 6 reviews toward 4.7 is EXACTLY 8 (25.8 + 40) / 14 = 4.70, but
    # in binary (4.7 - 4.3) is 0.40000000000000036 and (5.0 - 4.7) is 0.2999999999999998, so the
    # quotient lands at 8.000000000000012 and a bare ceil() answers 9. Telling an employee to chase a
    # ninth review when the eighth already gets them there is the kind of small lie that makes people
    # stop believing the number. Shave a hair before rounding up.
    k = (n * (t - r)) / (best - t)
    return max(1, int(_math.ceil(k - 1e-9)))


def rating_status(rating, target) -> str:
    """'above' | 'below' | 'unknown' (no rating fetched yet)."""
    if rating is None:
        return "unknown"
    try:
        return "above" if float(rating) >= float(target) else "below"
    except (TypeError, ValueError):
        return "unknown"


def review_hash(review_ref: str | None, author: str, text: str, publish_time: str | None) -> str:
    """Dedupe key: Google's own stable review resource id when the API gave one ('places/…/reviews/…'),
    else a content hash of (author, text, publish_time) so a re-sweep never duplicates a review."""
    if review_ref:
        return review_ref
    basis = f"{author or ''}|{text or ''}|{publish_time or ''}"
    return "h:" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


# ── name matching (conservative, "possible mention" only) ──────────────────────────────────────
_WORD_RE_CACHE: dict = {}


def _word_pattern(word: str):
    pat = _WORD_RE_CACHE.get(word)
    if pat is None:
        pat = re.compile(r"\b" + re.escape(word) + r"\b", re.IGNORECASE)
        _WORD_RE_CACHE[word] = pat
    return pat


def match_employees_in_text(text: str, candidates: list[dict]) -> dict:
    """Conservative match of employee names in review `text`, scoped to `candidates`
    ([{employee_id, name}, ...] — already filtered to employees scheduled/home at the store).

    Rules (all deliberately conservative — false NEGATIVES are the safe failure mode here, not
    false positives):
      - a first name shorter than MIN_FIRST_NAME_LEN is skipped entirely (too many false positives:
        "Al", "Jo", "Bo", ...).
      - match is a whole-WORD, case-insensitive hit on the first name, OR the "First L" form
        (first name + last initial as a separate word) which is treated as a STRONGER signal.
      - if exactly one candidate matches -> {employee_id, name, confidence:'possible', note:None}.
      - if zero match -> {employee_id: None, ...}.
      - if 2+ DIFFERENT candidates match and none is disambiguated by a "First L" hit that the others
        don't also have -> ambiguous: {employee_id: None, note: "ambiguous: also matches X, Y"}
        (never guesses which one).
    Returns a dict, never raises."""
    text = text or ""
    hits = []   # list of (employee_id, name, strength)  strength: 2 = "First L", 1 = first-name-only
    for c in candidates or []:
        name = (c.get("name") or "").strip()
        eid = c.get("employee_id")
        if not name or not eid:
            continue
        parts = name.split()
        first = parts[0]
        if len(first) < MIN_FIRST_NAME_LEN:
            continue
        strength = 0
        if len(parts) > 1:
            last_initial = parts[-1][0]
            strong_pat = re.compile(r"\b" + re.escape(first) + r"\s+" + re.escape(last_initial) + r"\b",
                                     re.IGNORECASE)
            if strong_pat.search(text):
                strength = 2
        if strength == 0 and _word_pattern(first).search(text):
            strength = 1
        if strength:
            hits.append((eid, name, strength))
    if not hits:
        return {"employee_id": None, "name": None, "confidence": None, "note": None}
    best_strength = max(h[2] for h in hits)
    top = [h for h in hits if h[2] == best_strength]
    # de-dupe same employee appearing twice (shouldn't happen, defensive)
    uniq = {h[0]: h for h in top}.values()
    if len(uniq) == 1:
        eid, name, _s = next(iter(uniq))
        return {"employee_id": eid, "name": name, "confidence": "possible", "note": None}
    others = ", ".join(sorted(h[1] for h in uniq))
    return {"employee_id": None, "name": None, "confidence": None,
            "note": f"ambiguous: also matches {others}"}


# ── employees scheduled/home at a store (for matching + notifications) ─────────────────────────
def employees_for_store(client, org_id: str, store_code: str, address: str | None = None,
                        window_days: int = 10) -> list[dict]:
    """Employees whose home_store resolves to this store, UNION anyone with a (non-deleted) shift at
    this store in [today-2, today+window_days]. Same 'home ∪ scheduled' shape the clock-in
    eligibility rule already uses (people.md / AGENT_CONTRACT domain map), minus the floater set
    (not needed here — reviews are about who actually works the floor). Returns
    [{'employee_id','name','email'}], deduped by employee_id. Never raises — a lookup failure just
    returns []; the caller degrades to 'no one to notify/match against'."""
    store_code = (store_code or "").strip()
    if not store_code:
        return []
    keys = {store_code.upper()}
    if address:
        keys.add(address.strip().upper())
    today = datetime.now(timezone.utc).date()
    since = (today - timedelta(days=2)).isoformat()
    upto = (today + timedelta(days=window_days)).isoformat()
    out: dict[str, dict] = {}
    try:
        emps = (client.table("employees").select("employee_id,name,email,home_store,is_active")
                .eq("org_id", org_id).limit(5000).execute().data) or []
    except Exception:
        emps = []
    for e in emps:
        if e.get("is_active") is False:
            continue
        if (e.get("home_store") or "").strip().upper() in keys and e.get("employee_id"):
            out[str(e["employee_id"])] = {"employee_id": str(e["employee_id"]), "name": e.get("name"),
                                           "email": e.get("email")}
    try:
        shifts = (client.table("shifts").select("employee_id,employee_name")
                  .eq("org_id", org_id).eq("store_code", store_code).eq("is_deleted", False)
                  .gte("shift_date", since).lte("shift_date", upto)
                  .limit(5000).execute().data) or []
    except Exception:
        shifts = []
    emp_by_id = {str(e.get("employee_id")): e for e in emps if e.get("employee_id")}
    for s in shifts:
        eid = str(s.get("employee_id") or "").strip()
        if not eid or eid in out:
            continue
        src = emp_by_id.get(eid)
        out[eid] = {"employee_id": eid, "name": (src or {}).get("name") or s.get("employee_name"),
                     "email": (src or {}).get("email")}
    return list(out.values())


# ── stores for a batch of employees (Phase 1.5, "google reviews everywhere") — the inverse of
# employees_for_store: given employee id(s), which store(s)' ratings should show for them. Powers
# GET /google-reviews/employee/{id} AND the batched GET /google-reviews/employee-summary — both
# call this ONCE with the whole id list (2 queries total: employees + shifts), never per-employee,
# per AGENT_CONTRACT's "aggregate in Postgres/batch, never N round-trips" rule. ─────────────────
def stores_for_employees(client, org_id: str, employee_ids: list[str],
                          lookback_days: int = DEFAULT_LOOKBACK_DAYS,
                          forward_days: int = DEFAULT_FORWARD_DAYS,
                          store_rows: list[dict] | None = None) -> dict[str, list[str]]:
    """{employee_id: [store_code, ...]} for EVERY id in `employee_ids` (an id with no resolvable
    store maps to an empty list — never omitted, never raises). Store set = home_store UNION any
    (non-deleted) shift's store_code in [today-lookback_days, today+forward_days].

    `home_store` on the employee row may hold either a real store_code OR a free-text address
    (legacy data) — resolved against the org's store roster the SAME way /google-reviews/my already
    does (by store_code OR address, case-insensitive), never trusted as a bare code. Pass
    `store_rows` (already-fetched `[{'store_code','address',...}, ...]`) to skip a duplicate stores
    query when the caller already has the roster loaded (the batch summary endpoint does)."""
    ids = sorted({str(e).strip() for e in (employee_ids or []) if str(e).strip()})
    out: dict[str, set] = {i: set() for i in ids}
    if not ids:
        return {}
    if store_rows is None:
        try:
            store_rows = (client.table("stores").select("store_code,address")
                          .eq("org_id", org_id).execute().data) or []
        except Exception:
            store_rows = []
    by_code = {(s.get("store_code") or "").strip().upper(): s["store_code"]
               for s in store_rows if s.get("store_code")}
    by_addr = {(s.get("address") or "").strip().upper(): s["store_code"]
               for s in store_rows if s.get("address") and s.get("store_code")}
    try:
        emps = (client.table("employees").select("employee_id,home_store")
                .eq("org_id", org_id).in_("employee_id", ids).execute().data) or []
    except Exception:
        emps = []
    for e in emps:
        eid = str(e.get("employee_id") or "").strip()
        hs = (e.get("home_store") or "").strip()
        if eid not in out or not hs:
            continue
        matched = by_code.get(hs.upper()) or by_addr.get(hs.upper())
        if matched:
            out[eid].add(matched)
    today = datetime.now(timezone.utc).date()
    since = (today - timedelta(days=lookback_days)).isoformat()
    upto = (today + timedelta(days=forward_days)).isoformat()
    try:
        shifts = (client.table("shifts").select("employee_id,store_code")
                  .eq("org_id", org_id).in_("employee_id", ids).eq("is_deleted", False)
                  .gte("shift_date", since).lte("shift_date", upto)
                  .limit(5000).execute().data) or []
    except Exception:
        shifts = []
    for s in shifts:
        eid = str(s.get("employee_id") or "").strip()
        sc = (s.get("store_code") or "").strip()
        if eid in out and sc:
            out[eid].add(sc)
    return {eid: sorted(codes) for eid, codes in out.items()}


# ── Google Places API (New) — pure HTTP calls, mockable by monkeypatching these two names ──────
def street_number(addr: str) -> str:
    """The leading street number of an address ('104-08 Lefferts Blvd' -> '104-08'). Keeps hyphens,
    because Queens-style numbers ARE hyphenated and '104-08' vs '10408' must not be conflated."""
    m = _re.match(r"\s*([0-9][0-9\-]*)", str(addr or ""))
    return (m.group(1).rstrip("-") if m else "")


def wrong_street_number(query_address: str, formatted_address: str) -> bool:
    """True when Google answered with a DIFFERENT street number than we asked for — i.e. it matched a
    neighbouring business, not ours.

    Found live 2026-08-10 and the reason this guard exists: the house store is stored as the bare
    street line "1115 Liberty Ave" (no city/state). Searching it plain returned "1115 Liberty Ave,
    Liberty, INDIANA"; searching it with the brand token returned "WIRELESS WORLD, 113-03 Liberty Ave"
    — a real wireless shop with 62 reviews, at a different address. Caching that would have attributed
    another business's ratings to this store and driven its employees' action plans off them. Wrong
    data is worse than no data, so an auto-resolution that fails this check is REFUSED rather than
    stored; a human can still set the Place ID by hand.

    Only fires when BOTH sides expose a street number — a genuinely number-less address (a mall unit,
    say) is left to the postal-address check rather than being blocked on a comparison we cannot make."""
    a, b = street_number(query_address), street_number(formatted_address)
    return bool(a and b and a != b)


def looks_like_postal_address(display_name: str, address: str) -> bool:
    """True when Google handed back the STREET ADDRESS rather than the business at it — the failure mode
    mig 430 exists for. Detected structurally (the display name is just the leading part of the address)
    rather than by keyword, so it holds for any country's address format."""
    dn = (display_name or "").strip().lower()
    ad = (address or "").strip().lower()
    if not dn:
        return False
    return bool(ad) and (ad.startswith(dn) or dn == ad.split(",")[0].strip())


def text_search_place(address: str, api_key: str, timeout: int = 15, brand: str | None = None) -> dict:
    """Text Search -> the best-matching place_id for a store. Raises RuntimeError on any failure (no key,
    no result, HTTP error) — the caller decides how to surface that.

    `brand` (google_review_config.search_brand, mig 430) is prepended to the query. WITHOUT it a bare
    address resolves to the POSTAL ADDRESS — verified live 2026-08-10: "104-08 Lefferts Blvd, South
    Richmond Hill, NY" returned the address itself with NO rating, while "wireless store 104-08 Lefferts
    Blvd" returned "Total Wireless" at 4.3 with 6 reviews. The token does not have to name the right
    brand; Google matches the business at that address either way."""
    address = (address or "").strip()
    if not address:
        raise RuntimeError("No store address to search")
    if not api_key:
        raise RuntimeError("No Google Places API key configured")
    query = f"{(brand or '').strip()} {address}".strip()
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.id,places.formattedAddress,places.displayName",
    }
    r = requests.post(PLACES_SEARCH_URL, json={"textQuery": query}, headers=headers, timeout=timeout)
    r.raise_for_status()
    data = r.json() or {}
    places = data.get("places") or []
    if not places:
        raise RuntimeError(f"No Google Place found for address: {address}")
    p = places[0]
    dn = (p.get("displayName") or {}).get("text")
    fa = p.get("formattedAddress")
    if wrong_street_number(address, fa):
        raise RuntimeError(
            f"Google matched a different address ({fa}) than the store's ({address}) — most likely "
            "because the stored address has no city/state, so the search drifted to a neighbouring "
            "business. Nothing was saved. Complete this store's address, or set its Place ID manually.")
    return {"place_id": p.get("id"), "formatted_address": fa,
            "display_name": dn, "query": query,
            # Surfaced, not swallowed: a postal-address match yields a place with no ratings, and the
            # store page can then say "set a brand token or override the place ID" instead of showing
            # a permanently blank rating that reads as "this store has no reviews".
            "looks_like_address": looks_like_postal_address(dn, address)}


def place_details(place_id: str, api_key: str, timeout: int = 15) -> dict:
    """Rating + review_count + Google's own curated review subset (see the module docstring's
    HONEST LIMITATION). Raises RuntimeError on any failure."""
    if not place_id:
        raise RuntimeError("No place_id to look up")
    if not api_key:
        raise RuntimeError("No Google Places API key configured")
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": ("id,rating,userRatingCount,reviews.name,reviews.rating,reviews.text,"
                              "reviews.originalText,reviews.authorAttribution,reviews.publishTime,"
                              "reviews.relativePublishTimeDescription"),
    }
    r = requests.get(PLACES_DETAILS_URL.format(place_id=place_id), headers=headers, timeout=timeout)
    r.raise_for_status()
    data = r.json() or {}
    reviews = []
    for rv in (data.get("reviews") or []):
        txt_obj = rv.get("text") or rv.get("originalText") or {}
        txt = txt_obj.get("text") if isinstance(txt_obj, dict) else (txt_obj or "")
        author = (rv.get("authorAttribution") or {}).get("displayName") or "Google user"
        reviews.append({
            "review_ref": rv.get("name"),
            "author_name": author,
            "rating": rv.get("rating"),
            "text": txt,
            "publish_time": rv.get("publishTime"),
            "relative_time": rv.get("relativePublishTimeDescription"),
        })
    return {"rating": data.get("rating"), "review_count": data.get("userRatingCount"), "reviews": reviews}


# ── action-plan state machine (pure decision helpers; router.py does the actual persistence) ────
def can_submit(status: str) -> bool:
    return status == "required"


def can_push_back(status: str) -> bool:
    return status in ("submitted", "in_progress")   # a DM may re-push-back if the work isn't done


def can_employee_mark_done(status: str) -> bool:
    return status in ("pushed_back", "in_progress")


def can_dm_confirm(status: str, employee_marked_done_at) -> bool:
    return status == "in_progress" and bool(employee_marked_done_at)


def trigger_detail_text(rating, target, as_of: str | None = None) -> str:
    as_of = as_of or _date.today().isoformat()
    r = f"{float(rating):.1f}" if rating is not None else "unrated"
    return f"Store rating {r} vs target {float(target):.1f} on {as_of}"


# ── config + sweep-config reads (DB-touching; `client` is the caller's already storeops-scoped
# supabase client, i.e. router.py's `sb()`) — every read degrades to a code-default shape so a
# missing migration/row never crashes a caller, per AGENT_CONTRACT §5. ──────────────────────────
def get_config(client, org_id: str) -> dict:
    try:
        rows = (client.table("google_review_config").select("*").eq("org_id", org_id)
                .limit(1).execute().data) or []
        if rows:
            return rows[0]
    except Exception:
        pass
    return {"org_id": org_id, "api_key": None, "enabled": False, "target_default": DEFAULT_TARGET,
            "notify_on_new_reviews": True, "lookback_days": DEFAULT_LOOKBACK_DAYS,
            "search_brand": None}


def public_config(cfg: dict) -> dict:
    """Never includes the raw api_key."""
    return {"enabled": bool(cfg.get("enabled")), "target_default": clamp_target(cfg.get("target_default")),
            "notify_on_new_reviews": cfg.get("notify_on_new_reviews", True),
            "lookback_days": clamp_lookback_days(cfg.get("lookback_days")),
            "has_api_key": bool(cfg.get("api_key")), "api_key_hint": mask_api_key(cfg.get("api_key")),
            "search_brand": cfg.get("search_brand"),
            "updated_at": cfg.get("updated_at")}


def get_sweep_config(client, org_id: str) -> dict:
    try:
        rows = (client.table("google_review_sweep_config").select("*").eq("org_id", org_id)
                .limit(1).execute().data) or []
        if rows:
            return rows[0]
    except Exception:
        pass
    return {"org_id": org_id, "enabled": False, "frequency": "daily", "day_of_week": 0, "hour": 6,
            "timezone": "America/New_York", "next_run_at": None, "last_run_at": None,
            "last_attempt_at": None, "last_status": None, "last_detail": None}


def next_run_at(frequency, day_of_week, hour, tzname) -> str:
    """Next run (UTC ISO) after now, in `tzname`. day_of_week 0=Mon..6=Sun. Only 'daily'/'weekly'
    are supported today (matches the sweep-config UI) — anything else falls back to daily."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tzname or "America/New_York")
    except Exception:
        tz = timezone.utc
    now = datetime.now(tz)
    hour = int(hour if hour is not None else 6)
    if frequency == "weekly":
        target = int(day_of_week if day_of_week is not None else 0)
        nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        nxt += timedelta(days=(target - nxt.weekday()) % 7)
        if nxt <= now:
            nxt += timedelta(days=7)
    else:  # daily (default/fallback)
        nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
    return nxt.astimezone(timezone.utc).isoformat()


# ── place resolution ─────────────────────────────────────────────────────────────────────────────
def resolve_place_for_store(client, org_id: str, store_code: str, address: str, api_key: str,
                            brand: str | None = None) -> dict:
    """Text Search on the store's address (prefixed with the tenant's `search_brand`, mig 430), cached
    (upserted) as an 'auto' place_id. Raises RuntimeError on failure — the caller (router.py) turns that
    into a clear HTTP 400. A MANUAL place_id set on the store row always wins and is never overwritten
    here; this only runs when there is nothing cached."""
    result = text_search_place(address, api_key, brand=brand)
    row = {"org_id": org_id, "store_code": store_code, "place_id": result["place_id"],
           "place_id_source": "auto", "resolved_address": result.get("formatted_address"),
           "resolved_display_name": result.get("display_name"),
           "last_place_lookup_at": datetime.now(timezone.utc).isoformat()}
    client.table("google_review_store").upsert(row, on_conflict="org_id,store_code").execute()
    return row


# ── action-plan materialization ─────────────────────────────────────────────────────────────────
def ensure_required_action_plans(client, org_id: str, store_code: str, employees: list[dict],
                                  rating, target, area_key: str = DEFAULT_AREA_KEY,
                                  as_of: str | None = None) -> list[dict]:
    """When a store is BELOW target, materialize a 'required' storeops.action_plan row for every
    employee scheduled/home there who doesn't already have an OPEN (status != 'completed') cycle for
    this area — the DB's own partial unique index (migration 413) is the source of truth for 'open',
    this is just the read-then-insert-only-the-gap path so a normal sweep never tries (and fails) a
    duplicate insert. Returns only the NEWLY created rows (empty list = nothing new — this is also
    the edge-trigger dedupe for the 'store below target' notification, see build_notifications).
    Never raises."""
    if rating_status(rating, target) != "below" or not employees:
        return []
    try:
        existing = (client.table("action_plan").select("employee_id,status").eq("org_id", org_id)
                    .eq("store_code", store_code).eq("area_key", area_key)
                    .neq("status", "completed").execute().data) or []
    except Exception:
        return []
    open_ids = {str(r.get("employee_id")) for r in existing}
    detail = trigger_detail_text(rating, target, as_of)
    new_rows = []
    for e in employees:
        eid = str(e.get("employee_id") or "").strip()
        if not eid or eid in open_ids:
            continue
        new_rows.append({"org_id": org_id, "employee_id": eid, "employee_name": e.get("name"),
                          "store_code": store_code, "area_key": area_key, "status": "required",
                          "trigger_detail": detail})
        open_ids.add(eid)   # guard duplicate employee entries in the input list itself
    if not new_rows:
        return []
    try:
        return client.table("action_plan").insert(new_rows).execute().data or []
    except Exception:
        return []


# ── notification composition (pure — router.py does the actual sending) ────────────────────────
def build_notifications(store_code: str, rating, target, new_items: list[dict],
                        employees: list[dict], plan_just_required: bool) -> list[dict]:
    """One consolidated notification per affected employee for this sweep pass (never one email per
    review — that would spam). Rules (owner directive 2026-07-27):
      - a NEW review that matched an employee AND rated <=3 -> 'concern' tone, prompts them to
        review the store's Google rating page.
      - a NEW review that matched an employee AND rated >=4 -> 'praise'/congratulatory tone.
      - the store's rating is BELOW target AND a 'required' action-plan row was just newly created
        this pass (edge-triggered — never repeats on every sweep) -> a general nudge to every OTHER
        scheduled employee not already covered by a personal mention above.
    Returns [{employee_id, email, kind, subject, body}, ...]; skips anyone with no email on file."""
    if not new_items and not plan_just_required:
        return []
    by_emp: dict[str, list[dict]] = {}
    for it in new_items:
        eid = it.get("matched_employee_id")
        if eid:
            by_emp.setdefault(str(eid), []).append(it)
    emp_by_id = {str(e.get("employee_id")): e for e in employees}
    notes = []
    handled: set[str] = set()
    for eid, items in by_emp.items():
        e = emp_by_id.get(eid)
        if not e or not e.get("email"):
            continue
        bad = [i for i in items if (i.get("rating") or 0) <= 3]
        good = [i for i in items if (i.get("rating") or 0) >= 4]
        if bad:
            snippet = (bad[0].get("review_text") or "")[:280]
            notes.append({"employee_id": eid, "email": e["email"], "kind": "concern",
                          "subject": f"A recent Google review at {store_code} needs your attention",
                          "body": (f"A recent Google review at {store_code} that may mention you was "
                                   f"rated {bad[0].get('rating')}/5: “{snippet}”. Take a look "
                                   f"at the store's Google rating page in your Employee Dashboard and "
                                   f"see if there's anything to follow up on.")})
        elif good:
            snippet = (good[0].get("review_text") or "")[:280]
            notes.append({"employee_id": eid, "email": e["email"], "kind": "praise",
                          "subject": f"Great mention in a Google review at {store_code}!",
                          "body": (f"Nice work — a recent Google review at {store_code} that may "
                                   f"mention you was rated {good[0].get('rating')}/5: "
                                   f"“{snippet}”. Keep it up!")})
        handled.add(eid)
    if plan_just_required:
        for e in employees:
            eid = str(e.get("employee_id") or "")
            if not eid or eid in handled or not e.get("email"):
                continue
            notes.append({"employee_id": eid, "email": e["email"], "kind": "store_below_target",
                          "subject": f"{store_code}'s Google rating is below target",
                          "body": (f"{store_code}'s Google rating is {rating}/5, below the target of "
                                   f"{target}/5. Check the Employee Dashboard — an action plan may be "
                                   f"required from you.")})
    return notes


# ── sweep orchestration ──────────────────────────────────────────────────────────────────────────
def sweep_store(client, org_id: str, store_row: dict, org_cfg: dict) -> dict:
    """Pull one store's Google rating + reviews, persist a snapshot, dedupe/insert new review items
    (with name matching), materialize any newly-required action plans, and build (but not send)
    notification payloads. NEVER raises — every FATAL failure (no key, no place, lookup failed) is
    captured in result['error'] with result['ok']=False/status='error'. A non-fatal write failure
    (the snapshot insert, or one review-item insert, failing while everything else succeeded) does
    NOT flip ok=False — Gate-1 N5: it used to be silently swallowed and still reported ok=True with
    no signal at all; now it's reported as status='partial' with a count in result['partial_detail']
    (self-healing: a skipped row is simply re-attempted, and re-deduped, on the next sweep)."""
    store_code = store_row.get("store_code")
    address = store_row.get("address")
    api_key = org_cfg.get("api_key")
    result = {"store_code": store_code, "ok": False, "status": "error", "new_reviews": 0,
              "rating": None, "review_count": None, "target": None, "notifications": [],
              "new_action_plans": 0, "error": None, "partial_detail": None}
    if not api_key:
        result["error"] = "No Google Places API key configured"
        return result
    try:
        cached = (client.table("google_review_store").select("*").eq("org_id", org_id)
                  .eq("store_code", store_code).limit(1).execute().data) or []
    except Exception:
        cached = []
    place_id = cached[0].get("place_id") if cached else None
    if not place_id:
        if not address:
            result["error"] = "No place_id cached and no store address to auto-resolve"
            return result
        try:
            resolved = resolve_place_for_store(client, org_id, store_code, address, api_key,
                                               brand=org_cfg.get("search_brand"))
            place_id = resolved["place_id"]
            if resolved.get("looks_like_address"):
                # Not fatal — carry on and read the details, which will simply have no rating. Naming it
                # here is what turns "this store shows nothing" into an actionable instruction.
                result["partial_detail"] = (
                    f"Google matched the postal address ({resolved.get('display_name')}), not a "
                    "business, so this store has no rating to read. Set a business token in the Google "
                    "Reviews settings (e.g. \"wireless store\"), or set this store's Place ID manually.")
        except Exception as e:
            result["error"] = f"Could not resolve a Google Place for this store: {e}"
            return result
    try:
        details = place_details(place_id, api_key)
    except Exception as e:
        result["error"] = f"Google Place Details lookup failed: {e}"
        return result
    rating, review_count = details.get("rating"), details.get("review_count")
    reviews = details.get("reviews") or []
    result["rating"], result["review_count"] = rating, review_count
    now_iso = datetime.now(timezone.utc).isoformat()
    snapshot_ok = True
    try:
        client.table("google_review_snapshot").insert({
            "org_id": org_id, "store_code": store_code, "place_id": place_id,
            "rating": rating, "review_count": review_count, "fetched_at": now_iso}).execute()
    except Exception:
        snapshot_ok = False
    employees = employees_for_store(client, org_id, store_code, address)
    new_items = []
    item_write_failures = 0
    for rv in reviews:
        h = review_hash(rv.get("review_ref"), rv.get("author_name"), rv.get("text"), rv.get("publish_time"))
        try:
            existing = (client.table("google_review_item").select("id").eq("org_id", org_id)
                        .eq("store_code", store_code).eq("review_hash", h)
                        .limit(1).execute().data) or []
        except Exception:
            existing = []
        if existing:
            continue
        m = match_employees_in_text(rv.get("text") or "", employees)
        row = {"org_id": org_id, "store_code": store_code, "review_hash": h,
               "author_name": rv.get("author_name"), "rating": rv.get("rating"),
               "review_text": rv.get("text"), "review_time": rv.get("publish_time"),
               "relative_time": rv.get("relative_time"), "matched_employee_id": m.get("employee_id"),
               "matched_employee_name": m.get("name"), "match_confidence": m.get("confidence"),
               "match_note": m.get("note"), "fetched_at": now_iso, "first_seen_at": now_iso}
        try:
            client.table("google_review_item").insert(row).execute()
        except Exception:
            item_write_failures += 1
            continue
        new_items.append(row)
    result["new_reviews"] = len(new_items)
    target = effective_target(cached[0] if cached else None, org_cfg.get("target_default"))
    result["target"] = target
    plans = ensure_required_action_plans(client, org_id, store_code, employees, rating, target)
    result["new_action_plans"] = len(plans)
    if org_cfg.get("notify_on_new_reviews", True):
        result["notifications"] = build_notifications(store_code, rating, target, new_items,
                                                       employees, bool(plans))
    result["ok"] = True
    partial_notes = []
    if not snapshot_ok:
        partial_notes.append("snapshot write failed")
    if item_write_failures:
        partial_notes.append(f"{item_write_failures} review-item write(s) failed (will retry next sweep)")
    if partial_notes:
        result["status"] = "partial"
        result["partial_detail"] = "; ".join(partial_notes)
    else:
        result["status"] = "ok"
    return result


def sweep_org(client, org_id: str, only_store_codes: list[str] | None = None) -> dict:
    """Sweep every active store for an org (or just `only_store_codes`, when given). Skips entirely
    (result['skipped']=True) when the integration isn't enabled or has no API key — never treated as
    an error, since that's the expected state before an admin configures it."""
    cfg = get_config(client, org_id)
    if not cfg.get("enabled") or not cfg.get("api_key"):
        return {"ok": False, "skipped": True, "reason": "not enabled / no API key", "stores": []}
    try:
        stores = (client.table("stores").select("store_code,address,market,is_active")
                  .eq("org_id", org_id).limit(2000).execute().data) or []
    except Exception:
        stores = []
    # NULL-SAFE active filter, done here in Python rather than a `.eq("is_active", True)` query filter
    # — is_active is NULLABLE (DEFAULT true), so a blanket `.eq(True)` would silently drop any store
    # whose flag was never explicitly set, same class of trap storeops/router.py's `_store_is_active`
    # fixed for the store pickers (2026-08-06). Only an EXPLICIT is_active=false is excluded here.
    stores = [s for s in stores if s.get("is_active") is not False]
    if only_store_codes:
        wanted = {str(s).upper() for s in only_store_codes}
        stores = [s for s in stores if (s.get("store_code") or "").upper() in wanted]
    results = [sweep_store(client, org_id, s, cfg) for s in stores]
    return {"ok": True, "skipped": False, "stores": results}
