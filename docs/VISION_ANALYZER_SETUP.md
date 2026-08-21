# Setting up a store analyzer

Start to finish on a computer with **no Python installed**. Roughly 40 minutes, most of
it waiting on downloads.

Written 2026-08-21 against `main`. The commands are stable; if the app's wording drifts
from this page, the app is right.

---

## 0 · What you are building

The analyzer is a small program that runs **in the store**. It holds the camera feed
locally and sends only numbers back — how many people came in, how long they stayed,
where they stood. No video ever leaves the building, which is what keeps the bandwidth
cost near zero no matter how many cameras you add.

One analyzer can cover every camera in a store, and one can cover several stores if the
machine is fast enough. Step 4 is what tells you which.

> **Do step 4 before you buy anything.** The benchmark needs no account, no camera and no
> enrollment code — just Python. If an existing back-office PC passes, you are done
> spending money.

---

## 1 · Install Python

The server runs Python 3.12. Anything from 3.11 up is fine.

**Windows** — download the installer from <https://www.python.org/downloads/> and run it.

> ⚠️ **Tick "Add python.exe to PATH" on the first screen.** It is off by default and easy
> to miss. Without it every command below fails with *"python is not recognized"*, and the
> fix is to rerun the installer.

**macOS**

```
brew install python@3.12
```

No Homebrew? Use the macOS installer from python.org instead.

**Linux (Debian / Ubuntu)**

```
sudo apt update && sudo apt install -y python3 python3-pip python3-venv
```

### Confirm it worked

Open a **fresh** terminal — Command Prompt on Windows (press `Win`, type `cmd`), Terminal
on macOS or Linux:

```
python --version
```

Expect `Python 3.12.x`. On macOS and Linux use `python3` everywhere `python` appears
below. A terminal opened *before* installing will not see Python — open a new one.

---

## 2 · Get the code onto the machine

The analyzer is one file, but it imports the counting rules from the folder around it —
so you need the whole `backend` folder, not just the script.

Sign in to GitHub, open <https://github.com/cellfonzrus/metricspro>, then
**Code → Download ZIP**. Extract it somewhere you can find again, for example
`C:\metricspro`.

If the machine has `git` and you would rather clone:

```
git clone https://github.com/cellfonzrus/metricspro.git
```

### The full path you will be typing

```
Windows        C:\metricspro\backend\vision_edge_analyzer.py
macOS / Linux  ~/metricspro/backend/vision_edge_analyzer.py
```

A downloaded ZIP extracts to `metricspro-main`, so your path may read
`C:\metricspro-main\backend\…`. Use whatever the folder is actually called.

> ⚠️ **Do not move the script out of `backend`.** It loads the door-counting geometry from
> `backend/app/` beside it. On its own it stops with *"No module named app"*.

---

## 3 · Install the libraries

```
Windows
  cd C:\metricspro
  python -m pip install --upgrade pip
  python -m pip install numpy opencv-python ultralytics requests aiortc

macOS / Linux
  cd ~/metricspro
  python3 -m pip install --upgrade pip
  python3 -m pip install numpy opencv-python ultralytics requests aiortc
```

**This is the slow part.** `ultralytics` pulls in PyTorch — expect **2–3 GB** and ten
minutes or more on a shop connection. It is worth it: the alternative detector is markedly
less accurate at counting people who are seated or partly hidden, and newer OpenCV
releases have removed it entirely.

| Package | What it does |
|---|---|
| `ultralytics` | Finds people in the frame. The real detector. |
| `opencv-python` | Reads and resizes frames. |
| `numpy` | Frame maths. |
| `aiortc` | Talks WebRTC — every current Nest camera needs it. |
| `requests` | Sends the counts back, signed. |

---

## 4 · Benchmark the machine

Measures how fast this computer can actually detect people, and converts that into how
many cameras it can carry.

```
Windows        python backend\vision_edge_analyzer.py --benchmark
macOS / Linux  python3 backend/vision_edge_analyzer.py --benchmark
```

The first run downloads the detection model — about 6 MB, once. Then it reports the
detector in use, milliseconds per frame, and a camera capacity.

> **Send that output before going further.** It decides whether this machine runs the
> analyzer, whether it can cover more than one store, or whether a dedicated box is
> needed — and every later decision follows from it.

If it reports the *fallback* detector, `ultralytics` did not install. Fix that before
trusting the number.

---

## 5 · Register the analyzer and enroll it

In the app: **Vision → Settings → 5 · Edge analyzers**. Pick the store, press
**Register analyzer**. You get a code like `J9YS-K343-9Q88-P3ES`, and the row shows
*waiting to enroll*.

On the store machine, within 30 minutes:

```
Windows
  python backend\vision_edge_analyzer.py ^
    --api https://metricspro-production.up.railway.app ^
    --enroll J9YS-K343-9Q88-P3ES

macOS / Linux
  python3 backend/vision_edge_analyzer.py \
    --api https://metricspro-production.up.railway.app \
    --enroll J9YS-K343-9Q88-P3ES
```

It trades the code for its own signing key, writes it owner-only, and exits. The settings
row changing from *waiting to enroll* to a real last-seen time is your confirmation.

The code works **once** and expires in 30 minutes, so register when you are already at the
machine. If it lapses, press **New code**. Nobody ever handles the signing key itself —
that is the point of enrolling rather than copying a secret around.

---

## 6 · Run it

From now on it needs no code and no key — it reads its own credentials:

```
Windows        python backend\vision_edge_analyzer.py --api https://metricspro-production.up.railway.app
macOS / Linux  python3 backend/vision_edge_analyzer.py --api https://metricspro-production.up.railway.app
```

Leave the window open. Closing it stops the counting. To have it survive a reboot, use
Task Scheduler on Windows or a `systemd` service on Linux — worth doing once the first
store is proven.

It runs at low process priority by default, so it yields to whatever the staff are doing
on that machine.

---

## 7 · When something goes wrong

| Message | Cause and fix |
|---|---|
| `python is not recognized` | "Add python.exe to PATH" was not ticked. Rerun the installer and tick it, then open a new terminal. |
| `No module named app` | The script was moved out of `backend`, or you are running a copied file. Run it from inside the extracted folder. |
| `No module named ultralytics` | Step 3 did not finish. Rerun the install and watch for errors at the end. |
| Benchmark names the *fallback* detector | Same cause. The number it prints is not the one to plan against. |
| `That enrollment code is not valid` | Expired, already used, or mistyped. Press **New code** and retry — codes work once. |
| `not enrolled on this machine` | Step 5 has not been done on *this* computer. Credentials do not transfer between machines. |
| Runs, but no numbers appear | No camera is ticked as **Entrance** in Vision → Settings. Traffic and the heat map both come from entrance cameras. |

---

## 8 · Reference

| | |
|---|---|
| Repository | <https://github.com/cellfonzrus/metricspro> |
| Script | `backend/vision_edge_analyzer.py` |
| API | `https://metricspro-production.up.railway.app` |
| Credentials | `%USERPROFILE%\.metricspro\vision-agent.json` · `~/.metricspro/vision-agent.json` |
| Python | 3.11 or newer — 3.12 matches the server |
| Settings | Vision → Settings → 5 · Edge analyzers |

### Useful flags

| Flag | Use |
|---|---|
| `--benchmark` | Time this machine. No credentials needed. |
| `--enroll CODE` | Claim credentials. Once per machine. |
| `--probe` | Save one frame from a camera, to aim it or draw zones. |
| `--dry-run` | Count without sending, to sanity-check a new install. |
| `--verbose` | Full detail when something is unclear. |
| `--help` | Everything, including flags not listed here. |
