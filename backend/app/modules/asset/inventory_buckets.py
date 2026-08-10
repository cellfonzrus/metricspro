"""Device-type buckets for inventory reconciliation — the ONE classifier, in a leaf module.

Extracted from `asset/router.py` (2026-08-10) so the commcalc ingest can bucket an Inventory Aging
file into the SAME five device types the On-Inventory recon compares against. A second copy of these
keyword rules is exactly the "model-name-prone keyword matcher" class of bug the edge/financing
mix-up came from, so this stays single-sourced. No imports, so no module can create a cycle by using it.
"""


def inv_bucket(s):
    """Map a device model OR a b2bsoft category label to one of the 5 reconciled
    buckets (or None to exclude: SIM kits, accessories, anything else)."""
    t = (s or "").lower()
    if not t:
        return None
    if "watch" in t:
        return "watch"
    if "ipad" in t or "tablet" in t or " tab" in t or t.endswith("tab") or "tab " in t:
        return "tablet"
    if any(w in t for w in ("hotspot", "mifi", "jetpack", "modem", "internet")):
        return "hotspot"
    if "iphone" in t:
        return "iphone"
    if any(w in t for w in ("samsung", "galaxy", "motorola", "moto ", "google", "pixel",
                            "android", "celero", "oneplus", "tcl", "nokia", "blu ")):
        return "android"
    if "apple" in t:   # bare Apple inventory that isn't iPad/Watch -> iPhone
        return "iphone"
    return None


BUCKETS = ("iphone", "android", "tablet", "watch", "hotspot")
