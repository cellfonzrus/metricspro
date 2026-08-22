# Hosting the MetricsPro website on Bluehost

This folder is the **entire website**. It is plain HTML, CSS and JavaScript — no Node, no build step,
no database. Upload it and it works. It is deliberately independent of the platform: nothing here
runs on the same servers as the app, and the only thing it asks the platform for is your published
price list (one read-only request that the page works fine without).

---

## 1. What I need from you

Nothing below is something I can find in the code — each one is a business fact or a decision.

### To finish the pages (blocking — the launch script fails until these are filled)

| # | What | Where it goes | Notes |
|---|---|---|---|
| 1 | ~~Which legal entity publishes this~~ | Everywhere | **ANSWERED — no action needed.** The company is **IT Solutions of LI Inc**; **Cellfonz R Us** is the first tenant, i.e. a customer of the platform like any other. That is what every legal page already says. The internal biometric policy, which had named Cellfonz R Us as the platform operator, has been corrected to match. |
| 2 | **State of incorporation** | Terms §1 | |
| 3 | **Registered business address** | Terms §1, §23; Privacy §12; EULA §12 | A real address is required for contract notices, and Apple requires contact info in the EULA. A PO box is generally not sufficient. |
| 4 | **Governing-law state, and county for venue** | Terms §22 | Usually where you are based. If that is Long Island, this is New York and the county is Nassau or Suffolk. |
| 5 | **Arbitration provider and venue city** | Terms §20 | AAA is the common choice; the venue should be convenient to you, not to a customer. |
| 6 | **Effective date** for each document | Top of every legal page | The day you publish. |
| 7 | **Payment terms**, price-change notice, non-renewal notice | Terms §5, §6 | e.g. net 15, 30 days, 30 days. |
| 8 | **Data retention windows** — after trial, after termination, and for security logs | Terms §4, §8; Privacy §7 | 30 / 30 / 12 months are ordinary choices. |
| 9 | **DMCA agent** name, address and email | Terms §21 | See §4 below — the page alone does not give you the safe harbour. |
| 10 | ~~Your web host's legal name~~ | — | **No longer needed.** The Service Providers page lists provider *categories* rather than company names, so there is nothing to fill in. |

### To point the site at the right places

| # | What | Notes |
|---|---|---|
| 11 | **The domain this will live on** | I have assumed **www.metricspro.tech**, because a comment in the API says you own metricspro.tech and use it for email today. If it is a different domain, tell me — it appears in the canonical link, `robots.txt`, `sitemap.xml` and the `.htaccess` redirect. |
| 12 | **Confirm the app URL** | Links point at `https://metricspro-five.vercel.app`. If you have a custom domain for the app, give it to me. |
| 13 | **Confirm the API URL** | `assets/config.js` points at `https://metricspro-production.up.railway.app`. If your Railway URL differs, correct it there — it is one line. |

### One change on the platform side (I can make it, or you can)

| # | What | Why |
|---|---|---|
| 14 | Add the website domain to **`CORS_ORIGINS`** on the Railway backend | Without it the browser blocks the price-list request and the site quietly shows the "priced against your operation" card instead of your published prices. Nothing breaks — you just don't get live prices. See §5 below. |

---

## 2. Upload it (10 minutes)

**Option A — cPanel File Manager (no extra software)**

1. Bluehost → **Advanced** (or **Hosting** → **cPanel**) → **File Manager**.
2. Open the document root for the domain: `public_html` for a primary domain, or
   `public_html/<subdomain-or-addon-domain>` if the site is on an addon domain or subdomain.
3. If anything is already there (Bluehost drops a default `index.html` or a WordPress install into
   new accounts), **move it aside first** — do not merge. An abandoned WordPress in the same folder is
   the single most common way a static site like this gets defaced.
4. Zip this folder on your computer, **Upload** the zip, then **Extract** it in place.
5. Confirm the structure is right — `index.html` must sit directly in the document root, not inside a
   `website/` subfolder:

   ```
   public_html/
     index.html
     404.html
     robots.txt
     sitemap.xml
     .htaccess
     assets/   styles.css  config.js  site.js
     legal/    terms.html  privacy.html  eula.html  acceptable-use.html
               biometric-policy.html  subprocessors.html  cookies.html
   ```
6. File Manager hides dotfiles by default — **Settings → Show Hidden Files** — and confirm `.htaccess`
   arrived. Nothing visibly breaks without it; you simply lose HTTPS enforcement, the security headers
   and the pretty URLs.

**Option B — FTP/SFTP**

Create an FTP account in cPanel, connect with FileZilla, and put the *contents* of this folder (not
the folder itself) into the document root. Upload `.htaccess` explicitly — many clients skip dotfiles.

**Option C — automatic, from the repository (recommended once the pages are finished)**

`.github/workflows/deploy-website.yml` uploads `website/` to Bluehost over FTPS whenever a change to
that folder lands on `main`. It needs three repository secrets — an FTP account, its username and its
password — and the setup is written out in the comment at the top of that file. Until those secrets
exist the workflow does nothing and reports that it did nothing, so it cannot start failing your
builds before you are ready.

Two things make this worth the five minutes it costs. The repository becomes the record of what is
published, so "which version of the terms was in force in March?" is a `git log` rather than a guess.
And the placeholder gate runs on every deploy, so an unfinished legal document cannot reach the
public site even if someone forgets to run the script by hand.

You can also run it on demand from **Actions → deploy-website → Run workflow**, which offers a
**dry run** (shows what would be uploaded, uploads nothing) and a **delete extras** option (removes
remote files no longer in `website/`). Leave delete off unless the FTP account is rooted at a
document root holding nothing but this site.

## 3. Domain and SSL

1. **DNS.** If the domain is registered at Bluehost, point it at the hosting account there. If it is
   registered elsewhere, set the nameservers or A record Bluehost gives you. DNS changes take
   anywhere from minutes to 48 hours.
2. **SSL.** Bluehost → **Security → SSL/TLS Status** → issue the free AutoSSL certificate for both
   `metricspro.tech` and `www.metricspro.tech`. **Wait until `https://` actually loads** before
   relying on the HTTPS redirect in `.htaccess` — redirecting to a certificate that does not exist
   yet produces a browser security warning on your own homepage.
3. **Pick one canonical hostname.** `.htaccess` currently sends the bare domain to `www`. If you
   prefer the bare domain, flip those two lines and update the `<link rel="canonical">` in each page
   to match.
4. Once HTTPS has been clean for about a week, uncomment the `Strict-Transport-Security` line in
   `.htaccess`. Read the warning above it first — it is not quickly reversible.

## 4. Before you publish

```sh
cd website
sh check-before-launch.sh
```

It fails while any `[[CONFIRM: ...]]` placeholder remains. They render as **loud red boxes** in the
browser precisely so an unfinished document cannot slip out unnoticed.

Then read `LEGAL-CHECKLIST.md`. It covers the things a script cannot check — chiefly that a lawyer in
your state has reviewed the documents, and that your DMCA agent is **registered with the U.S.
Copyright Office** (about $6). Naming an agent on a web page without registering gets you no safe
harbour at all.

## 5. Connecting the price list (optional but recommended)

The pricing section shows whatever you publish in the app under **Admin → Pricing & Free Trial**.
For the browser to be allowed to read it, the API must name this website as an allowed origin.

On the Railway backend, set the environment variable:

```
CORS_ORIGINS=https://metricspro-five.vercel.app,https://www.metricspro.tech,https://metricspro.tech
```

That variable already exists and overrides the default list, so this is a settings change, not a
deploy. Keep the app's own URL in the list or **you will break the application** — this variable is
the app's allow-list too.

If you skip this, nothing errors: the site shows the built-in trial-led card, exactly as it does when
no package has been published.

## 6. Changing the site later

**Who can change what.** Prices, packages and the trial length are *not* in these files — they live in
the app and the page reads them at load time, so changing a price never involves a deploy. Everything
else is a file edit.

| You want to change | Do this |
|---|---|
| A price, a package, the trial length, the pricing headline | **Nothing here.** Change it in the app under Admin → Pricing & Free Trial; the site picks it up on the next page load. |
| Words on the homepage | Edit `index.html`. With Option C, commit and merge; otherwise re-upload that one file. |
| A legal document | Edit the page, **update its "Last updated" date**, and keep a dated copy of the version it replaces — being able to show which terms were in force on a given date is what makes them enforceable. Option C keeps those copies for you in git history. |
| Where the app or API lives | Edit `assets/config.js`. |

**Can this be updated for you, without you touching cPanel?** Yes, but only through the repository.
An assistant session cannot reach your Bluehost account: it has no FTP credentials and its network
access is restricted to a small set of allowed hosts, so it cannot open a connection to your host at
all — by design, and worth keeping that way. What it *can* do is edit these files and push them. With
Option C configured, that push is the deploy: the change reaches the live site through GitHub, using a
credential that lives in your GitHub secrets and is never handed to anyone. Every change is reviewable
in a pull request before it goes out, and revertable afterwards.

If you would rather nothing reach the public site without your hand on it, keep Option A or B — or
configure Option C and simply not merge until you have read the diff. The pull request is the approval
step in either case.

## 7. What this site does not do

No forms, no logins, no database, no email sending, no analytics, no cookies. That is deliberate: a
static site with no server-side code has almost no attack surface, and no cookies means no consent
banner and no cookie-law exposure. If you later add a contact form or analytics, both of those change
— tell me and I will handle the disclosure and consent changes that come with it.
