# CharLUFS — How to Use

A small Mac app that takes any audio file you drag onto it and rewrites it at your target loudness. By default it aims at standard podcast loudness (-16 LUFS), but you can drag the slider to anything from quiet broadcast (-23) to loud-as-fuck (-8). It only ever applies a **clean volume change — no compression or limiting, ever** — held just under a true-peak ceiling you can set. The pristine original is **always** preserved in a `CharBackup/` folder next to your file, so you can revert any normalization with a single click.

You install it once. You run it whenever you want to normalize files. Quit when you're done.

## Install (one time)

1. Double-click `CharLUFS.zip` (the file I sent you). macOS will unzip it and leave **CharLUFS** sitting next to it — usually in your Downloads folder.
2. **Drag CharLUFS into your Applications folder.** (Open Finder → Applications, then drop it in.)
3. Double-click **CharLUFS** in Applications to launch it. macOS may ask once whether you want to open an app downloaded from the internet — click **Open**.
4. From now on, launch it like any other app (Spotlight, Dock, double-click).

## Use it

1. Launch **CharLUFS**. The target slider always starts at **-16 LUFS** (standard podcast loudness). If you want something else, set it now.
2. Pick your target loudness with the slider or one of the preset labels (EBU R128, Audible, Podcast, Spotify, Loud, Loud as fuck). The big number is what every file aims for. Below it, the **True peak ceiling** stepper sets the hard peak limit (default -1.5 dBTP) — CharLUFS will never push a file past it. (This ceiling sticks between launches; the loudness slider resets to -16.)

   **No compression, ever.** CharLUFS only applies a clean volume change. If a file is so peaky that reaching your loudness target would push it past the ceiling, it stops at the ceiling and lands a touch quieter than target instead of squashing the audio. You'll see that as a "Done" level slightly above your target.
3. **Drag one or more audio files anywhere onto the app window.** Within a few seconds each row shows its current loudness and true peak (e.g. `-22.3 LUFS · -6.1 dBTP`) so you can see what you're starting from before committing. A true peak hotter than -1.0 dBTP is flagged in red.
4. Click **Start**. CharLUFS processes files in parallel — on a modern Mac it'll burn through 4–8 files at once. Each row shows progress: its measured level → Processing → Done.
5. Each finished file is **rewritten in place** — same name, same folder. The original lands in a sibling folder called `CharBackup`. You'll see `CharBackup/episode42.wav` next to your `episode42.wav`.
6. While files process, you can drop more — they auto-join the queue.

The slider's setting **does not stick between launches** — quit and reopen the app and you're back at -16. (If that ever bites you, let me know.)

## Recover an original

Done files stay in the queue across app launches, each with a **Recover** button. Click it and CharLUFS:

- deletes the current (normalized) file, and
- copies the pristine original from `CharBackup/` back into its place.

You can also recover by hand — the `CharBackup` folder is a regular Finder folder, and the file inside is the untouched original.

## Re-normalize a file at a new target

Drop it again. The row flips back to **Pending**; CharLUFS reads from `CharBackup/` (not from the already-normalized current file), so every re-run starts from the true original. Adjust the slider, click Start.

## Clear the list

The **Clear** button wipes the queue — Pending, Done, and Error rows all go away (anything currently encoding is left alone). The `CharBackup` folders on disk are **never touched** by Clear, so even after clearing you can still recover any file by hand from Finder. You just lose the in-app Recover buttons for those rows.

## Supported formats

`.wav`, `.mp3`, `.m4a`, `.aac`, `.flac`, `.aif`, `.aiff`, `.ogg`, `.opus`, `.wma`

For most formats the output keeps the same extension. `.ogg` / `.opus` / `.wma` are written as `.wav` (so they don't lose more quality re-compressing) — the original lossy file still lives in `CharBackup` with its original extension.

## When something looks wrong

The app keeps a text log of everything it does, visible in the box at the bottom of the window. If a file doesn't process or the result sounds off:

1. Click **Copy log** in the top-right corner of the log box.
2. Paste it into a message and send it to me.

That's enough for me to figure out what happened.

## A few tips

- **Don't rename the originals while the app is processing them.** Wait for "Done" to appear.
- **The Recover button is your safety net.** Click it any time you want the original back. The `CharBackup` folder is never automatically cleaned up by CharLUFS.
- **If you drop a really big file from a slow drive (Dropbox, network), wait for it to fully sync before clicking Start.** The app processes whatever's on disk.
- **The app doesn't run in the background.** Quit it when you don't need it; relaunch when you do. No menu-bar daemons, no startup items.
- **Loudness numbers may differ by ~0.1–0.3 from another meter** (RX, YouLean, Ozone). That's normal — true peak especially is an *estimate* of the level between samples, and every meter reconstructs it a little differently, more so on sharp/transient material. CharLUFS measures with the same EBU R128 standard they do; small disagreements aren't errors.
