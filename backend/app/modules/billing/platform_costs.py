"""Platform (vendor) COST connectors — the OPERATOR's own spend to run MetricsPro.

Super-admin only. Each connector is one platform we pay for (Anthropic, Railway, Supabase, Vercel,
Resend, Meta, Bluehost, proxies…). Platforms with a cost API pull LIVE; the rest carry a manual
monthly figure so the super-admin total is always complete. Credentials live in the config row
(server-side), are never logged, and are masked in API responses.

This is the framework: `fetch_cost(connector)` dispatches to a per-provider fetcher and falls back to
the manual flat figure. Anthropic is wired live first (cleanest cost API); other providers return
their flat figure until their connector is added — no code change needed to start tracking them.
"""
import json
from datetime import datetime, timezone

# UI dropdown — provider key → label, whether a LIVE fetcher exists, and the credential hint.
PROVIDERS = [
    {"key": "anthropic", "label": "Anthropic (Claude API)", "live": True,
     "hint": "Admin API key (sk-ant-admin…) — Console → Settings → Admin keys. A regular key won't work."},
    {"key": "railway", "label": "Railway (backend hosting)", "live": False,
     "hint": "Account/team token — live connector coming; set a flat monthly figure for now."},
    {"key": "supabase", "label": "Supabase (database)", "live": False,
     "hint": "Management API token — live connector coming; flat monthly for now."},
    {"key": "vercel", "label": "Vercel (frontend hosting)", "live": False,
     "hint": "Account token — live connector coming; flat monthly for now."},
    {"key": "resend", "label": "Resend (email)", "live": False, "hint": "API key (usage only) — flat monthly for now."},
    {"key": "meta", "label": "Meta / WhatsApp", "live": False, "hint": "Business API — flat monthly for now."},
    {"key": "bluehost", "label": "Bluehost (email / IMAP)", "live": False, "hint": "No cost API — set a flat monthly figure."},
    {"key": "proxy", "label": "Proxy / egress", "live": False, "hint": "Depends on provider — flat monthly."},
    {"key": "other", "label": "Other / manual", "live": False, "hint": "Any other vendor — flat monthly figure."},
]
LIVE_PROVIDERS = {p["key"] for p in PROVIDERS if p["live"]}


def mask(cred) -> str:
    if not cred:
        return ""
    cred = str(cred)
    return "••••" if len(cred) <= 8 else cred[:4] + "…" + cred[-4:]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _anthropic_cost(cred: str) -> dict:
    """Anthropic Admin Cost report → this calendar month's total. Defensive parse (schema may evolve)."""
    import httpx
    now = datetime.now(timezone.utc)
    start = _month_start(now)
    headers = {"x-api-key": cred, "anthropic-version": "2023-06-01"}
    params = {"starting_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "ending_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": "31"}
    url = "https://api.anthropic.com/v1/organizations/cost_report"
    total, currency, seen = 0.0, "USD", False
    with httpx.Client(timeout=30) as c:
        for _ in range(20):  # paginate defensively
            r = c.get(url, headers=headers, params=params)
            if r.status_code in (401, 403):
                return {"cost": None, "currency": currency, "status": "error",
                        "detail": "auth failed — this needs an ADMIN key (sk-ant-admin…), not a regular API key"}
            if r.status_code == 404:
                return {"cost": None, "currency": currency, "status": "error",
                        "detail": "cost_report endpoint not found for this org (Admin API may be unavailable)"}
            r.raise_for_status()
            body = r.json()
            for bucket in (body.get("data") or []):
                for res in (bucket.get("results") or []):
                    amt = _num(res.get("amount") if res.get("amount") is not None
                               else res.get("cost") if res.get("cost") is not None
                               else res.get("amount_usd"))
                    if amt is not None:
                        total += amt
                        seen = True
                    if res.get("currency"):
                        currency = res["currency"]
            if body.get("has_more") and body.get("next_page"):
                params = {**params, "page": body["next_page"]}
            else:
                break
    if not seen:
        return {"cost": 0.0, "currency": currency, "status": "ok",
                "detail": f"month-to-date since {start.date()} — no usage yet"}
    return {"cost": round(total, 2), "currency": currency, "status": "ok",
            "detail": f"month-to-date since {start.date()}"}


def fetch_cost(connector: dict) -> dict:
    """Dispatch to the provider's live fetcher; fall back to the connector's manual flat figure.
    Returns {cost, currency, status, detail}. status ∈ ok | manual | error | unconfigured."""
    provider = (connector.get("provider") or "").lower()
    cred = (connector.get("credential") or "").strip()
    flat = _num(connector.get("flat_monthly_cost"))

    if provider == "anthropic" and cred:
        try:
            out = _anthropic_cost(cred)
            if out.get("status") == "ok":
                return out
            if flat is not None:  # live failed but we have a manual figure
                return {"cost": flat, "currency": "USD", "status": "manual",
                        "detail": (out.get("detail") or "live fetch failed") + " — showing flat figure"}
            return out
        except Exception as e:
            if flat is not None:
                return {"cost": flat, "currency": "USD", "status": "manual", "detail": f"live error: {str(e)[:120]}"}
            return {"cost": None, "currency": "USD", "status": "error", "detail": str(e)[:200]}

    # No live fetcher wired (or no credential) → use the manual flat figure if set.
    if flat is not None:
        return {"cost": flat, "currency": "USD", "status": "manual", "detail": "flat monthly figure"}
    return {"cost": None, "currency": "USD", "status": "unconfigured",
            "detail": "no live connector wired and no flat monthly cost set"}
