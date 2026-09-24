#!/usr/bin/env python3
"""Vendor price comparison — log in to each vendor in vendors.json, read the catalog, compare.

    python compare_prices.py                     # all vendors, opens a visible browser
    python compare_prices.py --vendors KEY       # just one vendor (its "key" in vendors.json)
    python compare_prices.py --max-pages 50      # quick trial run
    python compare_prices.py --compare-only output/2026-09-24_1530/products.json   # re-compare, no login

Results land in output/<date_time>/ : price_comparison.xlsx, products.csv, products.json and
snapshots/ (copies of the pages it read — send those back if a vendor's products come out wrong).
"""
from __future__ import annotations

import argparse
import csv
import getpass
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from pricecompare import core, report  # noqa: E402


def load_vendors(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    vendors = [v for v in data["vendors"] if v.get("enabled", True)]
    for v in vendors:
        if not v.get("key") or not v.get("start_urls"):
            raise SystemExit(f"vendors.json: every vendor needs 'key' and 'start_urls' (problem in {v})")
    return vendors


def load_credentials(path):
    creds = {}
    p = Path(path)
    if p.exists():
        with open(p, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                k = (row.get("vendor_key") or "").strip()
                if k and (row.get("username") or "").strip():
                    creds[k] = ((row.get("username") or "").strip(), row.get("password") or "")
    return creds


def load_shopping_list(path):
    p = Path(path)
    if not p.exists():
        return []
    out = []
    with open(p, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            s = (row.get("search") or "").strip()
            if s:
                try:
                    qty = int(float(row.get("qty") or 1))
                except ValueError:
                    qty = 1
                out.append({"search": s, "qty": qty})
    return out


def scrape_all(vendors, creds, out_dir, args):
    from playwright.sync_api import sync_playwright
    from pricecompare.scrape import VendorScraper

    products, info = [], {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless)
        for v in vendors:
            print(f"\n=== {v.get('name', v['key'])} ===")
            user, pwd = creds.get(v["key"], (None, None))
            if not user:
                if args.headless:
                    print("  no credentials in credentials.csv — skipped")
                    info[v["key"]] = {"login_ok": False, "notes": ["no credentials"], "pages": 0, "products": 0}
                    continue
                user = input(f"  user id for {v.get('name', v['key'])}: ").strip()
                pwd = getpass.getpass("  password (hidden): ")
            ctx = browser.new_context(viewport={"width": 1366, "height": 900})
            page = ctx.new_page()
            s = VendorScraper(page, v, user, pwd, out_dir, interactive=not args.headless)
            try:
                if s.login():
                    rows = s.crawl(args.max_pages)
                    products.extend(rows)
            except KeyboardInterrupt:
                s.notes.append("stopped by you (Ctrl+C) — results so far kept")
                products.extend(s.rows())
            except Exception as e:  # keep the other vendors going
                s.notes.append(f"error: {e.__class__.__name__}: {e}")
                products.extend(s.rows())
                print(f"  ✗ {e}")
            info[v["key"]] = {"login_ok": s.login_ok, "pages": s.pages_seen, "products": s.count(), "notes": s.notes}
            ctx.close()
        browser.close()
    return products, info


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vendors", help="comma-separated vendor keys from vendors.json (default: all enabled)")
    ap.add_argument("--max-pages", type=int, help="pages to read per vendor (overrides vendors.json)")
    ap.add_argument("--headless", action="store_true", help="hide the browser (no manual-login fallback)")
    ap.add_argument("--compare-only", metavar="PRODUCTS_JSON", help="skip logging in; re-compare a saved products.json")
    ap.add_argument("--config", default=str(HERE / "vendors.json"))
    ap.add_argument("--credentials", default=str(HERE / "credentials.csv"))
    ap.add_argument("--shopping-list", default=str(HERE / "shopping_list.csv"))
    args = ap.parse_args()

    vendors = load_vendors(args.config)
    if args.vendors:
        want = {k.strip() for k in args.vendors.split(",")}
        vendors = [v for v in vendors if v["key"] in want]
        if not vendors:
            raise SystemExit(f"none of {sorted(want)} are in {args.config}")

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    out_dir = HERE / "output" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.compare_only:
        saved = json.loads(Path(args.compare_only).read_text(encoding="utf-8"))
        products, info = saved["products"], saved.get("run_info", {}).get("vendors", {})
        keys = {v["key"] for v in vendors}
        products = [p for p in products if p["vendor"] in keys]
    else:
        products, info = scrape_all(vendors, load_credentials(args.credentials), out_dir, args)

    run_info = {"run_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "vendors": info}
    (out_dir / "products.json").write_text(json.dumps({"run_info": run_info, "products": products}, indent=1), encoding="utf-8")

    groups = core.group_products(products)
    compared, singles = core.comparison_rows(groups, [v["key"] for v in vendors])
    plan = core.plan_order(load_shopping_list(args.shopping_list), products)

    xlsx = out_dir / "price_comparison.xlsx"
    report.write_workbook(xlsx, vendors, products, compared, singles, plan, run_info)
    report.write_csv(out_dir / "products.csv", products,
                     ["vendor", "name", "sku", "price", "list_price", "pack_qty", "availability", "stock_qty", "stock_text", "url"])

    print("\n" + "=" * 70)
    for v in vendors:
        r = info.get(v["key"], {})
        print(f"  {v.get('name', v['key']):28s} login={'ok' if r.get('login_ok') else 'FAILED':6s} "
              f"pages={r.get('pages', 0):4d} products={r.get('products', 0):5d}")
        for n in r.get("notes") or []:
            print(f"      note: {n}")
    print(f"\n  compared (sold by 2+ vendors): {len(compared)}    one vendor only: {len(singles)}")
    print(f"\n  ► Open this file:  {xlsx}")
    print("=" * 70)


if __name__ == "__main__":
    main()
