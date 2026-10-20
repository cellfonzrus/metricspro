# Receipt helper for the till PC

This little program is what lets a receipt go **straight to the receipt printer** — no print dialog,
and the slip cut at the end. It runs on the computer at the counter, next to the printer.

It is optional. Without it, receipts print the way they always have: the print dialog opens and you
pick the printer. Staff can choose either route on any receipt, and nothing changes until a manager
switches the store over.

## What you need

- The receipt printer already working on this PC — i.e. its own driver installed (Epson's, Citizen's,
  whichever make the counter has) and a test page printing from Windows or macOS.
- Node.js 18 or newer on this PC (<https://nodejs.org>, the "LTS" download).

## Step by step

1. **Find the printer's name, exactly as the computer lists it.**
   - Windows: open PowerShell and run `wmic printer get name`
   - macOS or Linux: run `lpstat -p`

   Copy the name exactly, spaces and all.

2. **Put this folder on the till PC**, for example in `C:\receipt-helper`.

3. **Start it**, using the name from step 1:

   ```
   node receipt-helper.js --printer "EPSON TM-T20III Receipt"
   ```

   It prints one line saying it is ready. Leave the window open while the shop is trading.

4. **Turn it on for this store** in the app: **POS → Settings → Receipt Content & Printing**, pick the
   store at the top, then switch on *"Print straight to the receipt printer at this store"*. Fill in
   *"Receipt printer at this store"* with the make and model so staff can see what the slip is sized
   for. Leave *"Till helper address"* as it is unless you changed the port below.

5. **Ring up a test sale and press Print.** The slip should come out and cut, with no dialog. If
   anything is wrong, the screen says so and prints through the dialog instead — it never fails
   silently.

## Keeping it running after a restart

- **Windows:** press `Win+R`, type `shell:startup`, and put a shortcut to a one-line `.cmd` file in
  that folder:

  ```
  node C:\receipt-helper\receipt-helper.js --printer "EPSON TM-T20III Receipt"
  ```

- **macOS:** `System Settings → General → Login Items`, add a small shell script with the same line.

## Options

| Option | What it does |
| --- | --- |
| `--printer "<name>"` | Required. The printer as the operating system lists it. |
| `--port 9100` | The port it listens on. Change it only if 9100 is taken — and then change *Till helper address* in the app's settings to match. |
| `--origin https://…` | An extra address of the app allowed to send it receipts. |

Each has an environment variable too: `RECEIPT_HELPER_PRINTER`, `RECEIPT_HELPER_PORT`.

## What it does and does not do

- It listens on `127.0.0.1` only, so nothing outside this PC can reach it.
- It accepts exactly one request — *print these bytes* — and hands them to the printer through the
  operating system's own spooler. It runs no other command, opens no other file, holds no password
  and talks to no database.
- The driver stays with Windows or macOS. The app never installs one and never needs to know which
  make the counter has beyond the label you typed in for staff to read.

## If it will not print

| What you see | What it usually is |
| --- | --- |
| "Could not reach the receipt printer helper" on screen | The window in step 3 is closed, or the port in the app's settings does not match `--port`. |
| "The printer refused it" | The name in `--printer` is not exactly what step 1 showed, or the printer is off, out of paper or not connected. |
| The slip prints but does not cut | The printer's cutter is switched off in its own settings, or that model has none. |
| The slip is too wide or too narrow | Set *Receipt paper width at this store* (58 mm or 80 mm) in the same settings page. |
