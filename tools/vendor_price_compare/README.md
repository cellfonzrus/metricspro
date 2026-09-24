# Vendor Price Compare — test kit (runs on your own computer)

Logs in to each supply vendor you list, reads every product it can see (name, item #, price, pack size,
in-stock status), matches the same product across vendors, and writes an Excel file showing who is
cheaper. It also builds an order plan from a short shopping list.

**It never places an order.** It only reads pages, and it refuses every "add to cart", "buy now",
"checkout" or "log off" link. You place the order yourself on the vendor site.

This is the phase-1 test kit for the UPS Store tenant. Once it's proven on the real sites, the same logic
moves into MetricsPro as the Product Pricing module.

---

## What you need (one time)

- A Windows PC or a Mac.
- **Python 3.10 or newer.**
  - Windows: install it from https://www.python.org/downloads/ and **tick "Add python.exe to PATH"** on
    the first install screen.
  - Mac: install it from the same page. On newer Macs `python3` may already be there. To check, open
    Terminal and type `python3 --version`.

## Step 1 — get the kit onto your computer

Download `vendor_price_compare.zip` and unzip it anywhere, for example your Desktop. You'll get a folder
called `vendor_price_compare`.

## Step 2 — put in your logins

1. In the folder, make a copy of `credentials_TEMPLATE.csv` and name the copy **`credentials.csv`**.
   (The Windows starter does this for you the first time it runs.)
2. Open `credentials.csv` in Excel or Notepad and fill in the user id and password for each vendor:

   | vendor_key  | username      | password      |
   |-------------|---------------|---------------|
   | afflink     | *your id*     | *your pwd*    |
   | myboxchoice | *your email*  | *your pwd*    |

3. Save it. If Excel asks about the format, keep **CSV**.

This file stays on your computer. It is never uploaded anywhere and can't be committed to the code
repository. If you leave it empty, the kit asks for the login when it runs, and the password is hidden
as you type.

## Step 3 (optional) — make a shopping list

To get an order plan, copy `shopping_list_TEMPLATE.csv` to **`shopping_list.csv`**. Write one item per
row, with a few words to search for and the quantity (counted the way the vendor sells it: bundles or
cases). For example:

```
search,qty
12x12x12 box,25
packing tape,6
```

## Step 4 — run it

- **Windows:** double-click **`run_windows.bat`**.
- **Mac:** open Terminal, type `cd ` (with a space), drag the folder onto the Terminal window, press
  Enter, then type `bash run_mac_linux.sh` and press Enter.

The first run takes a few minutes because it installs what it needs, including its own copy of Chrome.
Later runs start straight away.

A Chrome window opens and you can watch it work:

1. It goes to each vendor's login page and fills in your login.
2. **If a vendor shows a captcha, a "verify it's you" code, or a login screen it can't figure out**, the
   black window says so. Finish logging in yourself in the Chrome window, then go back to the black
   window and press **Enter**. It carries on from there.
3. It then walks through the vendor's catalog pages. Each line in the black window shows the page it's on
   and how many products it has found.

It reads at most `max_pages` pages per vendor (set in `vendors.json`). For a quick first test, run it
with fewer pages:

- Windows: open Command Prompt in the folder and type `run_windows.bat --max-pages 30`
- Mac: `bash run_mac_linux.sh --max-pages 30`

Press **Ctrl+C** at any time to stop. It keeps everything found so far and still writes the Excel file.

## Step 5 — read the results

When it finishes, it prints the path of the results file. Results go into
`output/<date_time>/price_comparison.xlsx`, which has these sheets:

| Sheet | What it shows |
|-------|---------------|
| **Summary** | Whether each login worked, pages read, products found, any problems |
| **Comparison** | Products sold by 2 or more vendors. The **green** price is the cheapest vendor that is in stock. *Save $* is how much less it costs than the next vendor. |
| **All products** | Every product from every vendor. Use the filter arrows to search or sort. |
| **One vendor only** | Products only one vendor carries |
| **Order plan** | Your shopping list: the best pick per line and a total per vendor |

How to read the match columns:

- **Match = exact:** both vendors show the same item or part number.
- **likely:** same size and a very similar name.
- **possible — check:** close, but please compare the two items yourself before ordering (the cell is
  highlighted yellow).
- **Basis = per unit:** both vendors state the pack size (for example 25/bundle), so the prices are
  compared per piece. Otherwise the listed prices are compared.
- **Availability = unknown:** the vendor page didn't say. It is *not* assumed to be in stock.

Prices are what your logged-in account saw at that moment. Always check the vendor's cart total before
you pay.

To change the shopping list without logging in again:

```
python compare_prices.py --compare-only output/<date_time>/products.json
```

(Run this from inside the folder. On Windows, run `.venv\Scripts\activate` first; on Mac, run
`. .venv/bin/activate` first.)

## If a vendor's products come out wrong or empty

Each vendor site is built differently, and this kit was built without being able to open these two
sites. If one comes out empty or garbled:

1. Zip the folder `output/<date_time>/snapshots/<vendor>` (copies of the pages it read, plus a few
   screenshots).
2. Send it back. The vendor's exact product layout then goes into `vendors.json` (the optional
   `selectors` block) or into the in-app version.

The snapshots are pages from your own account and can include your account name and prices, so share
them only with people you trust.

## Adding or changing a vendor (nothing is hard-coded)

Vendors are listed in **`vendors.json`**, one block each. To add a third vendor:

1. Copy one block.
2. Give it a new `key` and `name`, its login page `url`, and one or more `start_urls` (catalog pages to
   start reading from).
3. Add a row with the same `vendor_key` to `credentials.csv`.

Set `"enabled": false` to switch a vendor off without deleting it.

Optional settings per vendor:

| Setting | Meaning |
|---------|---------|
| `max_pages` | Most pages to read per run |
| `delay_seconds` | Pause between pages (be polite to the vendor's site) |
| `follow_patterns` / `skip_patterns` | Which links to follow or skip (matched against the web address) |
| `strip_params` | Session codes to remove from links (for example `zenid`) |
| `selectors` | Exact CSS selectors for the page parts: `product_selector`, `name_selector`, `price_selector`, `sku_selector`, `stock_selector` |
| `login.username_selector`, `login.password_selector`, `login.submit_selector` | Exact login boxes, if automatic detection misses them |
| `snapshot_pages` | How many page copies to keep for troubleshooting |

## Files in this kit

| File | What it is |
|------|------------|
| `compare_prices.py` | The program |
| `pricecompare/core.py` | Price, pack, stock reading and matching (proven by `backend/harness_vendor_price_compare.py`) |
| `pricecompare/scrape.py` | Browser login and page reading (read-only) |
| `pricecompare/report.py` | Excel output |
| `vendors.json` | Your vendor list |
| `credentials_TEMPLATE.csv` | Copy this to `credentials.csv` and fill it in |
| `shopping_list_TEMPLATE.csv` | Copy this to `shopping_list.csv` (optional) |
| `run_windows.bat` / `run_mac_linux.sh` | One-click starters |
