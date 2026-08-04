"""READ-ONLY live API helper for MetricsPro (mod-commission diagnostic 2026-08-04).

HARD RULE: GET only. The single POST is the Supabase login. Nothing here writes.
Usage:  python3 mp.py <path> [k=v ...]      e.g. python3 mp.py /commcalc/commissions/July%202026
Env:    MP_ORG=house|lux (default lux)
"""
import json, os, sys, urllib.parse, urllib.request

BACKEND = "https://metricspro-production.up.railway.app/api/v1"
SUPA = "https://etxdalernqqtwjcrtcuj.supabase.co"
ANON = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImV0eGRhbGVy"
        "bnFxdHdqY3J0Y3VqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM1MTc0MDMsImV4cCI6MjA4OTA5MzQwM30."
        "UhklXkrVneev69tEzYf4nEbjktGlY5SUBd2MHOnjn0Q")
ORGS = {"house": "00000000-0000-0000-0000-000000000001",
        "lux": "854f6d7b-6590-4e4d-88ab-646f560d4f4c"}
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".token")


def _login():
    creds = {}
    with open("/home/codespace/.metricspro_training_creds") as fh:
        for ln in fh:
            if "=" in ln:
                k, v = ln.strip().split("=", 1)
                creds[k] = v
    body = json.dumps({"email": creds["METRICSPRO_TRAINING_USER"],
                       "password": creds["METRICSPRO_TRAINING_PASS"]}).encode()
    req = urllib.request.Request(SUPA + "/auth/v1/token?grant_type=password", data=body,
                                 headers={"apikey": ANON, "Content-Type": "application/json"})
    tok = json.load(urllib.request.urlopen(req, timeout=60))["access_token"]
    with open(CACHE, "w") as fh:
        fh.write(tok)
    return tok


def token(fresh=False):
    if not fresh and os.path.exists(CACHE):
        t = open(CACHE).read().strip()
        if t:
            return t
    return _login()


def get(path, params=None, org="lux", timeout=180, raw=False):
    """GET only. Returns parsed JSON (or text when raw)."""
    org_id = ORGS.get(org, org)
    p = dict(params or {})
    p.setdefault("org_id", org_id)
    url = BACKEND + path + ("?" + urllib.parse.urlencode(p) if p else "")
    for attempt in (0, 1):
        req = urllib.request.Request(url, method="GET", headers={
            "Authorization": "Bearer " + token(fresh=bool(attempt)),
            "x-active-org": org_id, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace")
            return body if raw else json.loads(body)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code == 401 and attempt == 0:
                continue
            return {"__http_error": e.code, "__body": body[:800], "__url": url}
        except Exception as e:  # noqa: BLE001
            return {"__error": repr(e), "__url": url}


if __name__ == "__main__":
    path = sys.argv[1]
    params = dict(a.split("=", 1) for a in sys.argv[2:] if "=" in a and not a.startswith("org="))
    org = next((a.split("=", 1)[1] for a in sys.argv[2:] if a.startswith("org=")), "lux")
    out = get(path, params, org=org)
    print(json.dumps(out, indent=1, default=str)[:6000])
