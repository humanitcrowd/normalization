# CLAUDE.md — CharLUFS build context

Context for an AI agent (or human) picking this project up cold. It captures
what CharLUFS is, how it's built, **every meaningful design decision and the
reasoning behind it**, the non-obvious gotchas, and how to build/ship it.

---

## 1. What it is

A small **macOS desktop app** that loudness-normalizes audio files. The user
drags one or more audio files onto the window, picks a target loudness, clicks
**Start**, and each file is rewritten **in place** at that loudness using a
**single linear gain — no compression or limiting, ever** — held under an
adjustable true-peak ceiling. The pristine original of every file is preserved
in a sibling `CharBackup/` folder, and any file can be reverted with a
**Recover** button.

Target users: audio producers (podcast/broadcast/ad mastering). No terminal,
no Python install — it ships as a self-contained signed `.app`.

## 2. Tech stack

- **Python 3.10+** backend.
- **pywebview** → renders the UI in a native macOS **WKWebView**.
- **Vanilla React** frontend via `React.createElement` (aliased `h`) — **no JSX,
  no Babel, no build step**. React is vendored as plain min.js files.
- **ffmpeg** (bundled static universal2 binary) for all audio measurement and
  processing.
- **py2app** to package; **codesign + notarytool** to sign/notarize.
- **PyObjC** (Cocoa/WebKit) for the native drag-and-drop hook.

## 3. Architecture & data flow

```
app_launcher.py (py2app entry) ─┐
src/__main__.py (dev entry)  ───┴─> src/webapp.py : WebApp.run()
```

- **src/webapp.py** — owns the pywebview window. Exposes a JS-callable `Api`
  (`get_initial_state`, `set_target_lufs`, `set_true_peak`, `start_processing`,
  `clear_queue`, `remove_from_queue`, `recover_file`, `reveal_in_finder`,
  `copy_log`). Pushes events to JS via `window.evaluate_js(...dispatchEvent...)`
  as `charlufs:<event>` CustomEvents (`status`, `queue`, `counter`, `log`,
  `drag_reset`). Installs the **native AppKit drag handler** after load.
- **src/jobqueue.py** — `JobQueue`: a parallel worker pool. Holds `_Item` rows
  (pending/processing/done/error + measure_state). On `add()` it spawns
  background **measure-on-drop** threads; `start()` spins up N workers;
  `_process()` runs the normalizer; `recover()` restores from backup;
  history is seeded on construction so past jobs reappear as `done` rows.
- **src/normalizer.py** — two paths:
  - **App path**: `measure_loudness()` (ebur128) + `apply_linear_gain()`
    (volume filter), orchestrated by `normalize_in_place()`.
  - **Legacy path (tests only)**: `measure()` + `apply_two_pass()`/
    `apply_single_pass()` (loudnorm) via `normalize()` → writes a
    `_normalized` sibling. NOT used by the app.
- **src/history.py** — `processed.json` persistence (list of entries) powering
  Recover across launches.
- **src/config.py** — `Config(target_lufs, true_peak, watch_folder)`. `load()`
  always resets `target_lufs` to default but **restores `true_peak`**.
- **src/log.py** — rotating file log (`~/Library/Logs/CharLUFS/normalizer.log`)
  + in-memory ring buffer mirrored to the UI log pane. Logger name `charlufs`.
- **web/** — `index.html`, `app.js` (the whole UI, one IIFE), `charlie.js`
  (the dog SVG), `styles.css`, `vendor/` (react + react-dom min).

Runtime locations:
- Backups: `<file dir>/CharBackup/<name>` (never auto-deleted).
- `~/Library/Application Support/CharLUFS/config.json` and `processed.json`.
- `~/Library/Logs/CharLUFS/normalizer.log`.

## 4. Design decisions & WHY (the important part)

1. **Drag-and-drop, whole window is the drop zone** (replaced an earlier
   watch-folder design). Simpler mental model; explicit user action.
2. **Native AppKit drag handler is mandatory.** WKWebView delivers HTML5 drop
   events to JS but **strips the local file path** from the `File` objects
   (sandbox/privacy). Since `CharBackup` needs real paths, we monkey-patch
   `WKWebView.performDragOperation_` via PyObjC to read `NSURL` paths off the
   pasteboard and route them into the queue. JS only renders the drag-over
   visual. Because the native handler swallows the WebKit drop event, JS never
   sees `drop`/`dragleave`, so Python fires a `charlufs:drag_reset` event to
   clear the overlay.
3. **In-place rewrite + CharBackup.** First time a file is processed we copy the
   original into `CharBackup/`; thereafter the backup is the **source of truth**
   — re-runs always read from it, so repeated normalizations never stack. The
   file at the original path is always the latest output.
4. **Recover** = delete current file, copy backup back over it, drop the history
   entry. Backups are never auto-removed (Clear and the per-row × only forget
   the in-app entry, not the on-disk backup).
5. **Pure linear gain — NO compression, ever** (the headline guarantee, v1.3.0).
   `gain = min(target_lufs − measured_LUFS, true_peak_ceiling − measured_TP)`,
   applied with the `volume` filter. Loudness and true peak both scale exactly
   with a linear gain, so the output is **mathematically guaranteed** never to
   exceed the ceiling. A peaky source that can't reach the loudness target
   without breaching the ceiling lands a little **quieter** rather than being
   limited. **Why we dropped loudnorm from the app path:** loudnorm's
   `linear=true` mode *silently reverts to dynamic (limiting)* when it can't hit
   target under the TP ceiling — exactly the compression we must avoid.
6. **Sample rate preserved in == out** (no resample on the output path).
   Resampling shifts inter-sample peaks (breaking the exact TP guarantee) and is
   itself an "audio change" we don't want. (Only the legacy loudnorm path still
   forces 48k.)
7. **ebur128 for all displayed/decision numbers**, not loudnorm's readout.
   ebur128 is a faithful BS.1770 meter — tracks RX/YouLean within ~0.1 LU and
   applies correct multichannel weighting (L/C/R 0 dB, surrounds +1.5 dB, LFE
   excluded) from the channel layout. loudnorm's measurement is looser.
8. **Measure-on-drop**: each queued file is measured in the background (capped
   at `parallelism` via a semaphore) so the row shows input LUFS + true peak
   before the user commits. That measurement is reused at Start so the source
   isn't decoded twice. Measured against the *source* (backup if present).
9. **Parallel processing**: `min(8, max(2, cpu_count // 2))` concurrent files
   (4 on M3 base, 8 on M3 Max). loudnorm/ebur128/gain are single-threaded per
   process; per-file work is disjoint (different dirs), so it parallelizes
   cleanly. New drops auto-join a running pool.
10. **Settings persistence asymmetry**: `target_lufs` resets to -16 every launch
    (a per-session creative choice — don't surprise the user with a stale
    value); `true_peak` persists (a delivery-spec ceiling set once).
11. **Multichannel**: no `-ac`/downmix anywhere, so 5.1 in → 5.1 out with a
    uniform gain; layout preserved; ebur128 weights it per spec.

## 5. Non-obvious gotchas (things that cost us time)

- **Don't draw a fake window inside the OS window.** An early build rendered its
  own titlebar + traffic lights inside the WKWebView → "window-in-window" look.
  Let the OS window be the window; fill `100vw/100vh`.
- **True peak is an oversampled *estimate*, not a measured sample.** BS.1770
  mandates ≥4× oversampling; ffmpeg/RX/Ozone/YouLean each reconstruct slightly
  differently, so TP readings legitimately differ **0.1–0.3 dB** between meters,
  more on transient material. This is not a bug and can't be eliminated. Our
  ceiling is enforced against *ffmpeg's* estimate; a downstream meter may read a
  hair over — lower the ceiling for hard headroom.
- **LUFS of a full-scale 1 kHz sine ≈ −3 to −3.7** (K-weighting + the −0.691
  offset). Matters when synthesizing test signals. To make a file with a target
  LUFS *and* a target true peak you need a high-crest signal (a quiet continuous
  bed + a brief smooth peak), because a tone's crest factor is fixed ~3.7 dB.
- **Build/run footguns:**
  - The venv **must** be active or `build_and_sign.sh` fails (`python` vs
    `python3`). It refuses to run otherwise.
  - `python setup.py py2app` alone produces an **ad-hoc** (unsigned) bundle —
    fine locally, blocked by Gatekeeper elsewhere. Use `build_and_sign.sh` for a
    real Developer-ID + notarized build.
  - macOS **Launch Services can hijack** `open dist/CharLUFS.app` and launch a
    stale `/Applications/CharLUFS.app` with the same bundle id instead. Use
    `killall CharLUFS; open -n dist/CharLUFS.app`.
  - Shipped artifact is a **notarized `.zip`** (via `ditto`), not a `.dmg`.
- **Folder name is `CharBackup`** (CamelCase, no space) — was `char backup`
  early on; renamed everywhere.

## 6. Build / sign / ship

```bash
# from a clean clone, with the venv active:
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# ffmpeg must be present (static universal2) before building:
curl -L -o /tmp/ffmpeg.zip https://evermeet.cx/ffmpeg/getrelease/zip
unzip -o /tmp/ffmpeg.zip -d resources/ && chmod +x resources/ffmpeg

# one-shot build + sign + notarize + zip (needs Developer ID + notarytool profile):
TEAM_ID=<TEAMID> ./scripts/build_and_sign.sh

# verify:
xcrun stapler validate dist/CharLUFS.app
spctl -a -vvv -t install dist/CharLUFS.app   # want: source=Notarized Developer ID
```

`build_and_sign.sh` builds with py2app, signs every inner Mach-O bottom-up
(loose `.so`/`.dylib`, the embedded python, bundled ffmpeg), seals the bundle
with `resources/entitlements.plist` (hardened runtime), notarizes via the
`charlufs-profile` keychain entry, staples, and produces `CharLUFS.zip`.

Dev run (no build): `python -m src`.

## 7. Testing

- `tests/` exercise the **legacy** `normalize()` (loudnorm sibling output) and
  the deprecated `FolderWatcher`. They synthesize signals, normalize, re-measure
  with ffmpeg, and assert ±0.5–0.7 LU of target. They **skip** if ffmpeg isn't
  found.
- **Gap:** the actual app path (`normalize_in_place` linear gain) is **not yet
  covered** by the test suite — its gain math has only been checked manually.
  Adding tests for it is the obvious next task.

## 8. Known limitations / cleanup backlog

- `src/app.py` (old Tk UI) and `src/watcher.py` (folder watcher) are **dead
  code**, kept only because tests import them. Safe to delete once tests are
  ported.
- Lossy output (`.mp3`/`.m4a`) re-encodes, which reshapes the waveform, so the
  TP guarantee is only **exact for lossless** (WAV/AIFF/FLAC). Broadcast WAV
  workflows are unaffected.
- WAV output is forced to **24-bit** (`pcm_s24le`); input bit depth isn't
  preserved (a deliberate mastering default, but worth noting).
- TP ceiling is enforced against ffmpeg's estimate (see gotchas).

## 9. Version history

- **1.1.0** — pywebview UI redesign + icon.
- **1.2.0** — drag-and-drop replaces watch-folder; in-place rewrite + CharBackup
  + Recover; parallel queue; measure-on-drop; ebur128 for displayed numbers.
- **1.3.0** — pure linear-gain normalization (no compression); adjustable,
  persisted true-peak ceiling; sample rate preserved in == out.
