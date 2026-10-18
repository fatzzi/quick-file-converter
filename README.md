# ShiftDrop

Drag a file in File Explorer (or on the desktop), hold **Shift**, and a format
picker pops up next to the cursor. Drop the file on a format; the converted copy
is saved next to the original (`photo.heic` -> `photo.jpg`). The original is
never moved or changed, and existing files are never overwritten
(`photo (1).jpg` is used instead).

| From | To |
|---|---|
| PNG, JPG, WEBP, BMP, GIF, TIFF, ICO, HEIC (iPhone photos) | PNG, JPG, WEBP, ICO, PDF, GIF |
| MP4, MOV, MKV, AVI, WEBM, ... | MP4, GIF, MP3, WAV, M4A |
| MP3, WAV, M4A, OGG, FLAC, ... | MP3, WAV, M4A |

Formats that don't fit the file you're dragging turn grey when you hover them.
You can drop several files at once.

## Install

Needs Windows 10/11 and [Python 3.10+](https://www.python.org/downloads/).

1. Double-click **`install.bat`**. It creates a private Python environment in
   `.venv`, installs the packages from `requirements.txt`, offers to install
   ffmpeg (only needed for video/audio), and offers to start ShiftDrop when you
   sign in to Windows.
2. That's it - ShiftDrop is now running in the background.

## Use

| File | What it does |
|---|---|
| `start.bat` | start ShiftDrop in the background |
| `stop.bat` | stop it |
| `uninstall.bat` | stop it and remove it from Windows startup |

If you move this folder, run `install.bat` again (the `.venv` and the startup
shortcut point at the old location).

## Files

- `shiftdrop.py` - the whole app (mouse/Shift watcher, format picker, converters)
- `requirements.txt` - Python packages: Pillow, pillow-heif, tkinterdnd2
- `install.bat`, `start.bat`, `stop.bat`, `uninstall.bat` - helpers above
