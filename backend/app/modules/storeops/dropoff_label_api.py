"""THE ENDPOINTS for rescuing a drop-off package whose label barcode will not scan.

Owner 2026-11-18: *"we have packages sitting in the store which are not scanned ... the user should
be able to upload the picture to our platform and the platform should generate a barcode using the
reference numbers this way, the barcodes can then be scanned back into the UPS system"*.

WHAT IS HERE AND WHAT IS DELIBERATELY NOT. Every decision already lives in a pure module and is
dereferenced here, never re-derived:

    what a barcode IS (encode/decode)       `core/code128`                 (index §CORE-37)
    finding bars in a photograph            `storeops/dropoff_photo`       (index §STOREOPS-12)
    which string is THE REFERENCE           `storeops/dropoff_label`       (index §STOREOPS-12)
    who may spend the vision key            `core/ai_gate`                 (migs 972/983)
    what that call cost                     `billing/ai_meter`

So this file is I/O and HTTP shape.

THE TENANT RULE, which shapes every response here (CLAUDE.md, owner 2026-10-30). An organisation's
label types are its OWN declarations. `GET /dropoff-label/profiles` returns what THIS organisation
declared and nothing else; a read for an organisation that has declared none says
`declared: 0` and the screen says so, rather than quietly matching another tenant's patterns.
Decoding the BARS needs no declaration and works for every tenant from the first minute, because a
published symbology is a policy, not anybody's vocabulary.

DEGRADES UNTIL ITS MIGRATION LANDS. Code deploys on merge; migration 1127 is applied later by the
owner. So every endpoint probes for its table and answers `available: false` with the migration
named, instead of 500-ing at a person holding a package. The probe never rewrites a declaration, so
the feature switches itself on the moment the SQL runs, with no second step.
"""
from fastapi import APIRouter, Body, Header, HTTPException, Response

from app.core.config import settings
from app.core.database import get_supabase
from app.modules.core import ai_gate as _ai_gate
from app.modules.core.code128 import Code128Error
from app.modules.storeops import dropoff_label as _label
# THE one "draw this reference" home: which symbology a label type uses is the tenant's declaration,
# and a second copy of that choice in an endpoint is the duplicate the build gate forbids.
from app.modules.storeops import dropoff_render as _render
from app.modules.storeops import dropoff_photo as _photo

router = APIRouter()

ORG_ID = "00000000-0000-0000-0000-000000000001"
MIGRATION = "1127_dropoff_label_rescue.sql"
AI_PURPOSE = "dropoff_label_transcribe"

# The same shape of cap the closing module's deposit-slip reader learned to carry (ai_limits.py): a
# vision call with no ceiling inherits the SDK's 600s x 2 retries and pins a worker thread for half
# an hour. A store photographing a shelf of stranded packages sends these in a burst.
TRANSCRIBE_TIMEOUT_S = 45.0
TRANSCRIBE_MAX_RETRIES = 1
# A photo bigger than this is refused by size rather than read slowly: a modern phone photo is
# 2-5 MB, and anything past 12 MB is a mistake (a video frame dump, a scanned PDF page set).
MAX_PHOTO_BYTES = 12 * 1024 * 1024
# A shelf of stranded packages in one go. 25 is a working batch, not a ceiling anyone should hit:
# past it the person is better served by two batches than by one request that may time out at a
# gateway and lose the lot.
MAX_PHOTOS = 25
# The whole batch's budget. Reading a photo that HAS a barcode costs well under a second; the
# expensive case is a photo that has none, and a batch of those must still come back. Photos past
# the budget are REPORTED as unread rather than dropped — a silent short answer here means packages
# left on the shelf, which is the defect this feature exists to fix.
BATCH_SECONDS = 25.0


def sb():
    """SCHEMA-SCOPED to `storeops` — both tables this surface touches are storeops tables. An
    unscoped client reads `public`, finds nothing, and the screen renders "no label types declared"
    at a tenant that has declared several: absence rendered as zero."""
    return get_supabase().schema("storeops")


def _decode_image(image_b64: str) -> tuple[bytes, str]:
    """Accept a bare base64 string or a data URL. Mirrors `pos/router._decode_image` deliberately —
    the two take the same thing from the same kind of camera upload."""
    import base64 as _b64
    s = image_b64 or ""
    ext = "jpg"
    if s.startswith("data:"):
        header, _, s = s.partition(",")
        if "png" in header:
            ext = "png"
    try:
        raw = _b64.b64decode(s)
    except Exception:
        raise HTTPException(400, "the photo must be base64-encoded")
    if not raw:
        raise HTTPException(400, "the photo is empty")
    if len(raw) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "that photo is too large — take it again at normal quality")
    return raw, ext


def _profiles(org_id: str) -> tuple[list[dict], dict | None]:
    """This organisation's declared label types, and the reason if they could not be read.

    A read FAILURE and an empty declaration are different answers and must never print the same: the
    first means "we could not look", the second means "nobody has described your labels yet".
    """
    try:
        rows = (sb().table("dropoff_label_profile")
                .select("id,label_name,reference_pattern,symbology,notes,active")
                .eq("org_id", org_id).eq("active", True).execute().data or [])
        return rows, None
    except Exception as e:
        return [], {"table": "dropoff_label_profile", "reason": str(e)[:200],
                    "migration": MIGRATION}


@router.get("/dropoff-label/profiles")
def list_profiles(org_id: str = ORG_ID):
    """The label types THIS organisation has declared. Never another tenant's."""
    rows, blocker = _profiles(org_id)
    return {
        "available": blocker is None,
        "declared": len(rows),
        "profiles": rows,
        "blocker": blocker,
        "barcode_read_available": True,   # decoding bars needs no declaration (see module header)
        "text_read_available": bool(rows) and bool(settings.ANTHROPIC_API_KEY),
    }


@router.post("/dropoff-label/profiles")
def create_profile(body: dict = Body(...), authorization: str = Header(default=""),
                   org_id: str = ORG_ID):
    """Declare a label type: {label_name, reference_pattern, notes?}.

    The pattern is COMPILED before it is stored. A declaration that cannot be used is refused at the
    moment it is made, with the regular-expression error quoted, rather than silently matching
    nothing later — which would read on the screen as "this label has no reference on it".
    """
    name = str(body.get("label_name") or "").strip()
    pattern = str(body.get("reference_pattern") or "").strip()
    if not name or not pattern:
        raise HTTPException(400, "a label type needs a name and a reference pattern")
    try:
        _label._compiled({"label_name": name, "reference_pattern": pattern})
    except _label.ProfileError as e:
        raise HTTPException(400, str(e))
    row = {"org_id": org_id, "label_name": name[:120], "reference_pattern": pattern[:400],
           "notes": (str(body.get("notes") or "").strip() or None),
           "created_by": _caller(authorization, org_id)}
    try:
        saved = sb().table("dropoff_label_profile").insert(row).execute().data or []
    except Exception as e:
        raise HTTPException(400, f"could not save the label type — migration {MIGRATION} "
                                 f"may not be applied yet ({str(e)[:160]})")
    return {"saved": True, "profile": (saved[0] if saved else row)}


@router.delete("/dropoff-label/profiles/{profile_id}")
def retire_profile(profile_id: str, org_id: str = ORG_ID):
    """Stop using a label type. It is deactivated, not deleted: scan rows point at it, and an audit
    trail whose declarations vanish answers nothing."""
    try:
        (sb().table("dropoff_label_profile").update({"active": False})
         .eq("id", profile_id).eq("org_id", org_id).execute())
    except Exception as e:
        raise HTTPException(400, f"could not retire that label type ({str(e)[:160]})")
    return {"retired": True, "id": profile_id}


def _symbology_for(reading: dict, profiles: list[dict], explicit: str | None = None) -> str:
    """Which symbology to draw this reference in: what the caller asked for, else what the label
    type the reading matched declares, else the house default (Code 128 — a POLICY, see
    dropoff_render)."""
    if explicit:
        return _render.normalize(explicit)
    pid = (reading or {}).get("profile_id")
    for p in profiles or []:
        if pid and str(p.get("id")) == str(pid):
            return _render.normalize(p.get("symbology"))
    return _render.normalize(None)


def _caller(authorization: str, org_id: str) -> str | None:
    try:
        who = _ai_gate.resolve_caller(get_supabase(), authorization, org_id) or {}
        return (who.get("email") or who.get("id") or None)
    except Exception:
        return None


@router.post("/dropoff-label/read")
def read_label(body: dict = Body(...), authorization: str = Header(default=""),
               org_id: str = ORG_ID):
    """A photo of a label → what the reference appears to be. WRITES NOTHING.

    Body: {image (base64 or data URL), ext?, skip_text?}. The answer always names its provenance:
    `source: "barcode"` is a decoded, check-verified fact; `source: "text"` is a reading of printed
    characters that a person must confirm; `source: null` means say so and let them type it.
    """
    raw, ext = _decode_image(body.get("image"))
    profiles, blocker = _profiles(org_id)

    # 1. The bars. Free, offline, self-verifying, and needs nothing declared.
    try:
        barcode = _photo.decode_barcode_image(raw)
    except _photo.PhotoReadUnavailable as e:
        barcode = {"best": None, "found": [], "votes": 0, "unavailable": str(e)}

    text, ai_meta = "", {"skipped": "not_attempted"}
    if not barcode.get("best") and not body.get("skip_text"):
        text, ai_meta = _transcribe(raw, ext, authorization=authorization, org_id=org_id,
                                    declared=bool(profiles))

    reading = _label.resolve_reading(barcode=barcode, text=text, profiles=profiles)
    return {
        "reading": reading,
        "barcode": {k: v for k, v in barcode.items() if k != "found"},
        "text_read": bool(text),
        "ai": {k: v for k, v in ai_meta.items() if k != "response"},
        "declared_label_types": len(profiles),
        "blocker": blocker,
    }


def _transcribe(raw: bytes, ext: str, *, authorization: str, org_id: str, declared: bool):
    """The vision read, gated and metered like every other outbound AI call on this platform.

    Not attempted at all when the organisation has declared no label types: without a declaration
    there is nothing the transcription could be matched against, so spending the tenant's AI budget
    on it would buy a result this platform is not allowed to use.
    """
    if not declared:
        return "", {"skipped": "no_label_types_declared"}
    client = get_supabase()
    try:
        caller = _ai_gate.resolve_caller(client, authorization, org_id)
        decision, _cfg = _ai_gate.decide(client, org_id=org_id, purpose=AI_PURPOSE, caller=caller)
        if not getattr(decision, "allowed", decision.get("allowed") if isinstance(decision, dict) else False):
            reason = (decision.get("reason") if isinstance(decision, dict)
                      else getattr(decision, "reason", "refused"))
            return "", {"skipped": f"ai_gate:{reason}"}
    except Exception as e:
        # A guard that cannot decide must refuse — reading the text is a convenience, and the
        # person can always type the reference in.
        return "", {"skipped": f"ai_gate_unavailable:{type(e).__name__}"}

    text, meta = _label.transcribe_label(
        raw, ext, model=settings.ACCOUNT_ENGINE_MODEL, timeout_s=TRANSCRIBE_TIMEOUT_S,
        max_retries=TRANSCRIBE_MAX_RETRIES, api_key=settings.ANTHROPIC_API_KEY)
    try:
        from app.modules.billing import ai_meter as _ai_meter
        _ai_meter.record(AI_PURPOSE, settings.ACCOUNT_ENGINE_MODEL, meta.get("response"),
                         org_id=org_id, error=meta.get("error"))
    except Exception:
        pass
    return text, meta


@router.post("/dropoff-label/read-batch")
def read_labels(body: dict = Body(...), authorization: str = Header(default=""),
                org_id: str = ORG_ID):
    """Several photos at once → every reference each one yields. WRITES NOTHING.

    Owner 2026-11-18: *"the screen should be able to read the picture as I sent not one by one and
    create bar codes in bulk"*. Body: {photos: [{name?, image}], skip_text?}.

    Each photo is answered on its own terms: its references with their provenance, or the reason it
    gave none. A batch never folds a failure into a total — "22 read" with three silently missing is
    three packages still on the shelf.
    """
    photos = body.get("photos")
    if not isinstance(photos, list) or not photos:
        raise HTTPException(400, "send one or more photos")
    if len(photos) > MAX_PHOTOS:
        raise HTTPException(400, f"that is more than {MAX_PHOTOS} photos — send them in two batches")
    profiles, blocker = _profiles(org_id)

    import time as _time
    deadline = _time.monotonic() + BATCH_SECONDS
    items: list[dict] = []
    for i, photo in enumerate(photos):
        name = str((photo or {}).get("name") or f"photo {i + 1}")[:200]
        if _time.monotonic() > deadline:
            items.append({"name": name, "readings": [], "reason": "not_read_yet", "skipped": True})
            continue
        try:
            raw, ext = _decode_image((photo or {}).get("image"))
        except HTTPException as e:
            items.append({"name": name, "readings": [], "reason": str(e.detail), "skipped": False})
            continue
        left = max(1, len(photos) - i)
        try:
            barcode = _photo.decode_barcode_image(
                raw, expect_many=True, budget_s=max(0.5, (deadline - _time.monotonic()) / left))
        except _photo.PhotoReadUnavailable:
            barcode = {"best": None, "references": []}
        text, ai_meta = "", {"skipped": "not_attempted"}
        if not barcode.get("references") and not body.get("skip_text"):
            text, ai_meta = _transcribe(raw, ext, authorization=authorization, org_id=org_id,
                                        declared=bool(profiles))
        readings = _label.resolve_readings(barcode=barcode, text=text, profiles=profiles)
        items.append({
            "name": name,
            "readings": readings,
            "reason": None if readings else (
                "no_label_types_declared" if not profiles else "nothing_readable"),
            "skipped": False,
            "ai": {k: v for k, v in ai_meta.items() if k != "response"},
        })
    found = sum(len(i["readings"]) for i in items)
    return {"items": items, "photos": len(photos), "references_found": found,
            "unread_photos": [i["name"] for i in items if not i["readings"]],
            "declared_label_types": len(profiles), "blocker": blocker}


@router.post("/dropoff-label/barcodes")
def make_barcodes(body: dict = Body(...), authorization: str = Header(default=""),
                  org_id: str = ORG_ID):
    """Several CONFIRMED references → a barcode each, and one audit row each.

    Body: {items: [{reference, reading?, photo_name?}], store_code?, record?}. One refused reference
    does not sink the batch: it comes back named, with its reason, beside the ones that printed.
    """
    items = body.get("items")
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "send one or more references")
    if len(items) > MAX_PHOTOS * 4:
        raise HTTPException(400, "that is too many references for one sheet — split it")
    store_code = body.get("store_code")
    who = _caller(authorization, org_id)
    profiles, _blocker = _profiles(org_id)
    made: list[dict] = []
    refused: list[dict] = []
    rows: list[dict] = []
    seen: set[str] = set()
    for it in items:
        reference = str((it or {}).get("reference") or "").strip()
        if not reference or reference in seen:
            continue
        seen.add(reference)
        reading = (it or {}).get("reading") if isinstance((it or {}).get("reading"), dict) else {}
        symbology = _symbology_for(reading, profiles, (it or {}).get("symbology"))
        try:
            svg = _render.render(reference, symbology)
        except Code128Error as e:
            refused.append({"reference": reference, "reason": str(e)})
            continue
        made.append({"reference": reference, "svg": svg, "symbology": symbology,
                     # Says whether this is a proven DUPLICATE of what the label carried, or the
                     # platform's reconstruction from the reference it read. Different promises.
                     "verified_symbology": _render.verified_symbology(symbology),
                     "source": reading.get("source"), "label_name": reading.get("label_name")})
        rows.append(_label.scan_row(
            org_id=org_id, store_code=store_code, reading=reading, confirmed_reference=reference,
            source_photo_name=(it or {}).get("photo_name"), read_by=who))
    note = None
    recorded = 0
    if rows and body.get("record", True):
        try:
            saved = sb().table("dropoff_label_scan").insert(rows).execute().data or []
            recorded = len(saved)
        except Exception as e:
            note = f"not recorded — migration {MIGRATION} may not be applied ({str(e)[:140]})"
    return {"items": made, "refused": refused, "recorded": recorded, "note": note}


@router.post("/dropoff-label/barcode")
def make_barcode(body: dict = Body(...), authorization: str = Header(default=""),
                 org_id: str = ORG_ID):
    """A CONFIRMED reference → a printable barcode, and the audit row that says who confirmed it.

    Body: {reference, reading?, store_code?, photo_name?, record?}. `reference` is what the PERSON
    accepted — never silently the machine's reading — and the row keeps both, so a package that goes
    back to the wrong vendor can be traced to the character that was wrong and to whoever approved
    it. Recording never blocks the barcode: paper in hand is the job.
    """
    reference = str(body.get("reference") or "").strip()
    if not reference:
        raise HTTPException(400, "a reference is required")
    if len(reference) > 64:
        raise HTTPException(400, "that reference is too long to print as one barcode")
    reading = body.get("reading") if isinstance(body.get("reading"), dict) else {}
    profiles, _blocker = _profiles(org_id)
    symbology = _symbology_for(reading, profiles, body.get("symbology"))
    try:
        svg = _render.render(reference, symbology)
    except Code128Error as e:
        raise HTTPException(400, str(e))

    recorded, note = None, None
    if body.get("record", True):
        row = _label.scan_row(
            org_id=org_id, store_code=body.get("store_code"), reading=reading,
            confirmed_reference=reference, source_photo_name=body.get("photo_name"),
            read_by=_caller(authorization, org_id))
        try:
            saved = sb().table("dropoff_label_scan").insert(row).execute().data or []
            recorded = (saved[0].get("id") if saved else None)
        except Exception as e:
            # Under the message key `note`, so the §19.36 boundary neutralises the migration name
            # and the database's own words for a tenant, and keeps them whole for a platform admin.
            note = f"not recorded — migration {MIGRATION} may not be applied ({str(e)[:140]})"
    return {"reference": reference, "svg": svg, "symbology": symbology,
            "verified_symbology": _render.verified_symbology(symbology),
            "recorded_id": recorded, "note": note,
            "corrected": bool(reading.get("reference") and reading.get("reference") != reference)}


@router.get("/dropoff-label/barcode.svg")
def barcode_svg(reference: str, symbology: str = "", org_id: str = ORG_ID):
    """The same code as an image, for printing. Reads nothing and writes nothing — it is the
    renderer, so a print dialog can reload it without creating a second audit row."""
    try:
        svg = _render.render(reference.strip(), symbology or None)
    except Code128Error as e:
        raise HTTPException(400, str(e))
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


@router.get("/dropoff-label/scans")
def list_scans(store_code: str = "", date_from: str = "", date_to: str = "", limit: int = 100,
               org_id: str = ORG_ID):
    """What this organisation has rescued, newest first — the stores, dates and references.

    Org-scoped at the query, not in the page: a filter the client could drop is not a boundary.
    """
    try:
        q = (sb().table("dropoff_label_scan")
             .select("id,store_code,read_source,read_reference,confirmed_reference,corrected,"
                     "label_name,photo_name,read_by,created_at")
             .eq("org_id", org_id).order("created_at", desc=True)
             .limit(max(1, min(int(limit or 100), 500))))
        if store_code.strip():
            q = q.eq("store_code", store_code.strip())
        if date_from.strip():
            q = q.gte("created_at", date_from.strip())
        if date_to.strip():
            q = q.lte("created_at", date_to.strip())
        rows = q.execute().data or []
    except Exception as e:
        return {"available": False, "scans": [],
                "blocker": {"table": "dropoff_label_scan", "reason": str(e)[:200],
                            "migration": MIGRATION}}
    return {"available": True, "scans": rows, "count": len(rows),
            "corrected_count": sum(1 for r in rows if r.get("corrected"))}
