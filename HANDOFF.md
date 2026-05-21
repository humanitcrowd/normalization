# CharLUFS — How to Use

A small Mac app that takes any audio file you drop into one folder and writes a louder, evened-out copy next to it at the standard podcast loudness (-16 LUFS).

You install it once. You run it whenever you want to normalize files. Quit when you're done.

## Install (one time)

1. Double-click `CharLUFS.dmg` (the file I sent you).
2. A window opens showing the app icon and an Applications folder. **Drag the app onto the Applications folder.** Done — close that window.
3. Open your **Applications** folder and double-click **CharLUFS** to launch it. macOS may ask once whether you want to open an app downloaded from the internet — click **Open**.
4. From now on, you can launch it like any other app (Spotlight, Dock, double-click).

## Use it

1. Launch **CharLUFS**.
2. The window shows a folder it's watching — by default `~/CharLUFS/` (the app creates it if it doesn't exist). Click **Open folder** to reveal it in Finder.
3. **Drag any audio file into that folder.** Wait. The status line at the top of the app window will say things like:
   - `Waiting for episode42.wav to finish copying…`
   - `Processing: episode42.wav (pass 1/2)`
   - `Processing: episode42.wav (pass 2/2)`
   - `Done: episode42_normalized.wav (-16.0 LUFS)`
4. A new file with `_normalized` added to the name appears next to the original. Use that one.
5. Drop as many files as you want. They'll process one at a time.
6. Quit the app (⌘Q or close the window) when you're done.

## Supported formats

`.wav`, `.mp3`, `.m4a`, `.aac`, `.flac`, `.aif`, `.aiff`, `.ogg`, `.opus`, `.wma`

The output keeps the same format as the input, except `.ogg` / `.opus` / `.wma` get written as `.wav` (so they don't lose more quality re-compressing).

## Switching folders

If you'd rather watch a different folder (say, a project folder you keep on Dropbox):
- Click **Change folder…** in the app.
- Pick the folder.
- The app remembers your choice next time you launch.

## When something looks wrong

The app keeps a log of everything it does. If a file doesn't process or the result sounds off:

1. Click the **Copy log** button at the bottom of the window.
2. Paste it into a message and send it to me.

That's enough for me to figure out what happened.

## A few tips

- **Don't rename the originals while the app is processing them.** Wait for "Done:" to appear.
- **The app won't re-process files it already normalized** — files with `_normalized` in the name are skipped.
- **If you drop a really big file from a slow drive (Dropbox, network), the app waits until the copy is fully finished before processing.** The status line will say "Waiting…" while it waits.
- **The app doesn't run in the background.** Quit it when you don't need it; relaunch when you do. No menu-bar daemons, no startup items.
