# CharLUFS — How to Use

A small Mac app that takes any audio file you drop into one folder and writes a louder, evened-out copy next to it. By default it aims at standard podcast loudness (-16 LUFS), but you can drag the slider to whatever target you want — from quiet broadcast (-23) to loud-as-fuck (-8).

You install it once. You run it whenever you want to normalize files. Quit when you're done.

## Install (one time)

1. Double-click `CharLUFS.dmg` (the file I sent you).
2. A window opens showing the app icon and an Applications folder. **Drag the app onto the Applications folder.** Done — close that window.
3. Open your **Applications** folder and double-click **CharLUFS** to launch it. macOS may ask once whether you want to open an app downloaded from the internet — click **Open**.
4. From now on, you can launch it like any other app (Spotlight, Dock, double-click).

## Use it

1. Launch **CharLUFS**.
2. The window shows a folder it's watching — by default `~/CharLUFS/` (the app creates it if it doesn't exist). Click **Open** next to the folder path to reveal it in Finder. Click **Change folder…** to point it elsewhere.
3. (Optional) Set your target loudness with the slider, or click one of the preset labels (EBU R128, Audible, Podcast, Spotify, Loud, Loud as fuck). The big number above the slider is what every file will be normalized to.
4. **Drag any audio file into that folder.** Wait. The status line will progress through messages like:
   - `Waiting for episode42.wav…`
   - `Measuring episode42.wav`
   - `Normalizing episode42.wav`
   - `Done: episode42_normalized.wav (-16.0 LUFS)`
5. A new file with `_normalized` added to the name appears next to the original. Use that one.
6. Drop as many files as you want. They process one at a time. The full history is in the log at the bottom of the window.
7. Quit the app (⌘Q or close the window) when you're done.

## Supported formats

`.wav`, `.mp3`, `.m4a`, `.aac`, `.flac`, `.aif`, `.aiff`, `.ogg`, `.opus`, `.wma`

The output keeps the same format as the input, except `.ogg` / `.opus` / `.wma` get written as `.wav` (so they don't lose more quality re-compressing).

## Switching folders

If you'd rather watch a different folder (say, a project folder you keep on Dropbox):
- Click **Change folder…** in the app.
- Pick the folder.
- The app remembers your choice next time you launch.

## When something looks wrong

The app keeps a log of everything it does, visible in the box at the bottom of the window. If a file doesn't process or the result sounds off:

1. Click **Copy log** in the top-right corner of the log box.
2. Paste it into a message and send it to me.

That's enough for me to figure out what happened.

## A few tips

- **Don't rename the originals while the app is processing them.** Wait for "Done:" to appear.
- **The app won't re-process files it already normalized** — files with `_normalized` in the name are skipped.
- **If you drop a really big file from a slow drive (Dropbox, network), the app waits until the copy is fully finished before processing.** The status line will say "Waiting…" while it waits.
- **The app doesn't run in the background.** Quit it when you don't need it; relaunch when you do. No menu-bar daemons, no startup items.
