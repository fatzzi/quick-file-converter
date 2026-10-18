# ShiftDrop

Select one or more files in File Explorer (or on the desktop) and press **Q**.
A format picker pops up at the mouse; click a format and converted copies are
saved next to the originals (`photo.heic` -> `photo.jpg`). The originals are
never moved or changed, and existing files are never overwritten
(`photo (1).jpg` is used instead). Esc, Q again, or a click anywhere else closes
the picker.

| From | To |
|---|---|
| DOC, DOCX, RTF, ODT (Word documents) | PDF, DOCX |
| PDF | DOCX (editable Word document) |
| PNG, JPG, WEBP, BMP, GIF, TIFF, ICO, HEIC (iPhone photos) | PNG, JPG, WEBP, ICO, PDF, GIF |
| MP4, MOV, MKV, AVI, WEBM, ... | MP4, GIF, MP3, WAV, M4A |
| MP3, WAV, M4A, OGG, FLAC, ... | MP3, WAV, M4A |

Formats that don't fit the selected files are greyed out.

Documents don't need Microsoft Word:

- **PDF -> DOCX** uses [pdf2docx](https://github.com/ArtifexSoftware/pdf2docx)
  (installed with ShiftDrop). Text, tables and images come across as editable
  content. A scanned PDF (pages that are just photos) becomes a DOCX of
  images, because there is no text in it to recover.
- **DOC/DOCX -> PDF** (and old DOC -> DOCX) uses the free
  [LibreOffice](https://www.libreoffice.org/), running hidden in the
  background. `install.bat` offers to install it, or run
  `winget install TheDocumentFoundation.LibreOffice`.
- Document conversions take a few seconds each. The very first one after
  installing LibreOffice can take about 30 seconds while it sets itself up.

Q only opens the picker while Explorer's file list or the desktop is focused.
Typing Q anywhere else - other apps, the Explorer address bar or search box,
or while renaming a file - types a Q as normal. (Inside the file list, Q no
longer jumps to files starting with "q".) To use another key, change
`HOTKEY_VK` at the top of `shiftdrop.py`.

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
shortcut point at the old location). Problems are written to `shiftdrop.log`.

## Files

- `shiftdrop.py` - the whole app (Q hotkey, format picker, converters)
- `requirements.txt` - Python packages: Pillow, pillow-heif, comtypes, pdf2docx
- `install.bat`, `start.bat`, `stop.bat`, `uninstall.bat` - helpers above
