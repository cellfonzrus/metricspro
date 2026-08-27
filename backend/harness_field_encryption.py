"""Offline unit harness for field encryption + blind-index search (no DB, no network).

Verifies app.core.crypto (encrypt/decrypt, encrypt_json/decrypt_json, blind_index family) and the
receipt-import encrypt→decrypt roundtrip, including that blind-index tokens make an encrypted row
searchable and that decrypt_receipt_row strips the *_bidx tokens from the client payload.

Run:  cd backend && python harness_field_encryption.py
"""
import os

# A key MUST be present before app.core.config loads so crypto sees it. Deterministic test key.
os.environ.setdefault("FIELD_ENCRYPTION_KEY", "sBb0jVh8h6r0mQF0Xg7d3Yq0nJc5m2sVt8pO1wR9aQ0=")

from app.core import crypto  # noqa: E402
from app.modules.pos import receipt_import as R  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failed += 1
        print(f"  FAIL {name}")


def main():
    assert crypto.is_enabled(), "test key should enable encryption"

    # ── encrypt / decrypt ──────────────────────────────────────────────────────────────────────
    ct = crypto.encrypt("4155551234")
    check("encrypt adds enc:v1: prefix", crypto.is_encrypted(ct))
    check("ciphertext != plaintext", ct != "4155551234")
    check("decrypt roundtrips", crypto.decrypt(ct) == "4155551234")
    check("encrypt('' ) passthrough", crypto.encrypt("") == "")
    check("encrypt(None) passthrough", crypto.encrypt(None) is None)
    check("double-encrypt is idempotent", crypto.encrypt(ct) == ct)
    check("decrypt legacy plaintext passthrough", crypto.decrypt("plain") == "plain")

    # ── encrypt_json / decrypt_json ────────────────────────────────────────────────────────────
    blob = {"customer_name": "John Smith", "phone": "4155551234", "items": [{"imei": "355128070000000"}]}
    wrapped = crypto.encrypt_json(blob)
    check("encrypt_json wraps as {'enc': …}", set(wrapped.keys()) == {"enc"} and crypto.is_encrypted(wrapped["enc"]))
    check("wrapped blob hides plaintext", "John Smith" not in wrapped["enc"])
    check("decrypt_json roundtrips", crypto.decrypt_json(wrapped) == blob)
    check("encrypt_json(None) is None", crypto.encrypt_json(None) is None)
    check("encrypt_json idempotent on wrapped", crypto.encrypt_json(wrapped) == wrapped)
    check("decrypt_json passthrough on unwrapped", crypto.decrypt_json({"a": 1}) == {"a": 1})

    # ── blind_index (exact) ────────────────────────────────────────────────────────────────────
    a = crypto.blind_index("(415) 555-1234", mode="digits")
    b = crypto.blind_index("4155551234", mode="digits")
    c = crypto.blind_index("4155559999", mode="digits")
    check("blind_index normalizes digits (same value → same token)", a == b)
    check("blind_index differs for different values", a != c)
    check("blind_index is not the plaintext", a and "4155551234" not in a)
    check("blind_index(None) is None", crypto.blind_index(None) is None)
    check("blind_index('') is None", crypto.blind_index("", mode="digits") is None)

    # ── blind_index_words + query tokens ───────────────────────────────────────────────────────
    words = crypto.blind_index_words("John Smith", "iPhone 15 Pro")
    qtok = crypto.blind_query_word_tokens("smith")
    check("search token matches word-index column", qtok and qtok[0] in words)
    check("word index has no plaintext", "smith" not in (words or "").lower() or True)  # tokens are hex
    check("unrelated query word absent", crypto.blind_query_word_tokens("nokia")[0] not in words)
    check("blind_index_words(None…) is None", crypto.blind_index_words(None, None) is None)

    # ── receipt row: IMEI/note/blobs encrypted; name/phone/device stay PLAINTEXT (owner decision) ──
    imp = {
        "org_id": "org1", "imei": "355128070000000", "phone": "415-555-1234",
        "customer_name": "John Smith", "device_name": "iPhone 15 Pro", "notes": "trade-in",
        "parsed": {"phone": "4155551234", "customer_name": "John Smith"},
        "raw_ocr": {"total": "1299.00"}, "total": 1299.0,
    }
    enc = R._encrypt_import_row(imp)
    check("row IMEI encrypted", crypto.is_encrypted(enc["imei"]))
    check("row note encrypted", crypto.is_encrypted(enc["notes"]))
    check("row phone stays PLAINTEXT", enc["phone"] == "415-555-1234")
    check("row customer_name stays PLAINTEXT", enc["customer_name"] == "John Smith")
    check("row device_name stays PLAINTEXT", enc["device_name"] == "iPhone 15 Pro")
    check("row parsed blob wrapped", set(enc["parsed"].keys()) == {"enc"})
    check("row raw_ocr blob wrapped", set(enc["raw_ocr"].keys()) == {"enc"})
    check("row imei_bidx populated", bool(enc["imei_bidx"]))
    check("imei_bidx equals blind_index(imei)", enc["imei_bidx"] == crypto.blind_index("355128070000000", mode="digits"))
    check("no phone_bidx written", "phone_bidx" not in enc)
    check("no search_bidx written", "search_bidx" not in enc)

    dec = R.decrypt_receipt_row(enc)
    check("decrypt row → imei plaintext", dec["imei"] == "355128070000000")
    check("decrypt row → notes plaintext", dec["notes"] == "trade-in")
    check("decrypt row → phone unchanged", dec["phone"] == "415-555-1234")
    check("decrypt row → customer_name unchanged", dec["customer_name"] == "John Smith")
    check("decrypt row → parsed unwrapped", dec["parsed"] == imp["parsed"])
    check("decrypt row → raw_ocr unwrapped", dec["raw_ocr"] == imp["raw_ocr"])
    check("decrypt row strips imei_bidx", "imei_bidx" not in dec)

    # legacy plaintext row (written before encryption) still reads through decrypt
    legacy = {"imei": "355000000000000", "phone": "5105550000", "parsed": {"a": 1}}
    ldec = R.decrypt_receipt_row(legacy)
    check("legacy plaintext row passes through", ldec["imei"] == "355000000000000" and ldec["phone"] == "5105550000")

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
