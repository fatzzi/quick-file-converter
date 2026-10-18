"""ShiftDrop - select files in Explorer, press Q, click a format to convert them.

Runs in the background. Select one or more files in File Explorer or on the
desktop and press Q: a format picker pops up at the mouse. Click a format and
converted copies are written next to the originals (the originals are untouched).
Esc or a click anywhere else closes the picker.

    pythonw shiftdrop.py          start (no console)
    python  shiftdrop.py --quit   stop a running instance
"""

import ctypes
import ctypes.wintypes as wt
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk

import comtypes.client
import comtypes.client.dynamic

try:
    from PIL import Image
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
except ImportError:
    Image = None

# ---------------------------------------------------------------- settings ---

HOTKEY_VK = 0x51          # Q  (virtual-key code; e.g. 0x43 for C)
# Q only opens the picker while one of these windows is in front ...
EXPLORER_CLASSES = {"CabinetWClass", "ExploreWClass", "Progman", "WorkerW"}
# ... and the file list itself has focus, so typing a Q in the address bar,
# search box or while renaming a file still types a Q.
FILE_LIST_FOCUS = {"DirectUIHWND", "SysListView32"}
POLL_MS = 30
CONTROL_PORT = 47821      # localhost port used for single-instance / --quit
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shiftdrop.log")

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff",
             ".ico", ".heic", ".heif"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".wmv", ".flv", ".m4v"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".wma"}
DOCUMENT_EXT = {".doc", ".docx", ".rtf", ".odt"}   # converted by LibreOffice
PDF_EXT = {".pdf"}

# (label, output extension, kinds of input it accepts)
TARGETS = [
    ("PDF", ".pdf", {"image", "document"}),
    ("DOCX", ".docx", {"pdf", "document"}),
    ("PNG", ".png", {"image"}),
    ("JPG", ".jpg", {"image"}),
    ("WEBP", ".webp", {"image"}),
    ("ICO", ".ico", {"image"}),
    ("GIF", ".gif", {"image", "video"}),
    ("MP4", ".mp4", {"video"}),
    ("MP3", ".mp3", {"video", "audio"}),
    ("WAV", ".wav", {"video", "audio"}),
    ("M4A", ".m4a", {"video", "audio"}),
]
COLUMNS = 6

BG, TILE, TILE_HOT, TILE_OFF, FG, FG_OFF = (
    "#1e1e24", "#34343e", "#ff8a3d", "#26262c", "#f2f2f2", "#666670")


def log(msg):
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 200_000:
            os.remove(LOG_PATH)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


# ------------------------------------------------------------- win32 bits ---

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
VK_LBUTTON, VK_ESCAPE = 0x01, 0x1B
MODIFIER_VKS = (0x10, 0x11, 0x12, 0x5B, 0x5C)  # Shift, Ctrl, Alt, Win L/R
GA_ROOT = 2
WH_KEYBOARD_LL, WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN = 13, 0x100, 0x101, 0x104

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = LRESULT
kernel32.GetModuleHandleW.restype = wt.HMODULE
user32.GetForegroundWindow.restype = wt.HWND
user32.GetAncestor.restype = wt.HWND
user32.WindowFromPoint.restype = wt.HWND


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hwndActive", wt.HWND),
                ("hwndFocus", wt.HWND), ("hwndCapture", wt.HWND),
                ("hwndMenuOwner", wt.HWND), ("hwndMoveSize", wt.HWND),
                ("hwndCaret", wt.HWND), ("rcCaret", wt.RECT)]


def key_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def cursor_pos():
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_title(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def focused_class(hwnd):
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    gi = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    if not user32.GetGUIThreadInfo(tid, ctypes.byref(gi)) or not gi.hwndFocus:
        return ""
    return class_name(gi.hwndFocus)


class HotkeyHook(threading.Thread):
    """Low-level keyboard hook on its own thread (it needs a message loop).

    When Q is pressed with Explorer's file list focused, the key is swallowed
    (so Explorer doesn't jump to a file starting with "q") and the Explorer
    window is handed to `on_hotkey`. Every other key passes straight through.
    """

    def __init__(self, on_hotkey, on_escape):
        super().__init__(daemon=True)
        self.on_hotkey = on_hotkey
        self.on_escape = on_escape
        self.held = False  # swallow auto-repeat while Q is held down
        self.proc = HOOKPROC(self.callback)  # keep a reference alive

    def callback(self, code, wparam, lparam):
        try:
            if code == 0:
                kb = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if kb.vkCode == VK_ESCAPE and wparam == WM_KEYDOWN:
                    self.on_escape()  # Esc still reaches the app underneath
                elif kb.vkCode == HOTKEY_VK:
                    if wparam == WM_KEYUP and self.held:
                        self.held = False
                        return 1
                    if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        if self.held:
                            return 1
                        hwnd = self.explorer_target()
                        if hwnd:
                            self.held = True
                            self.on_hotkey(hwnd)
                            return 1
        except Exception as exc:  # never let an error break the keyboard
            log(f"hook error: {exc!r}")
        return user32.CallNextHookEx(None, code, wparam, lparam)

    @staticmethod
    def explorer_target():
        if any(key_down(vk) for vk in MODIFIER_VKS):
            return None
        fg = user32.GetForegroundWindow()
        if not fg or class_name(fg) not in EXPLORER_CLASSES:
            return None
        focus = focused_class(fg)
        if focus not in FILE_LIST_FOCUS:
            return None
        return fg

    def run(self):
        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self.proc,
                                        kernel32.GetModuleHandleW(None), 0)
        if not hook:
            log(f"could not install keyboard hook (error {ctypes.GetLastError()})")
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


# ----------------------------------------------------- reading the selection ---

class Selection:
    """Reads the selected files of an Explorer window or the desktop via COM."""

    SWC_DESKTOP, SWFO_NEEDDISPATCH = 8, 1

    def __init__(self):
        self.shell = comtypes.client.CreateObject("Shell.Application", dynamic=True)
        shdocvw = comtypes.client.GetModule("shdocvw.dll")
        self.windows = comtypes.client.CreateObject(
            shdocvw.ShellWindows, interface=shdocvw.IShellWindows)

    def files(self, hwnd):
        if class_name(hwnd) in ("Progman", "WorkerW"):
            view = self.desktop_view()
        else:
            view = self.explorer_view(hwnd)
        if view is None:
            return []
        paths = [item.Path for item in view.Document.SelectedItems()]
        return [p for p in paths if os.path.isfile(p)]

    def desktop_view(self):
        found = self.windows.FindWindowSW(0, 0, self.SWC_DESKTOP, self.SWFO_NEEDDISPATCH)
        disp = found[1] if isinstance(found, tuple) else found
        return comtypes.client.dynamic.Dispatch(disp)

    def explorer_view(self, hwnd):
        # Each Explorer tab is its own "window" but they share one HWND; the
        # title bar shows the active tab's folder name, so match on that.
        title = window_title(hwnd)
        tabs = []
        wins = self.shell.Windows()
        for i in range(wins.Count):
            w = wins.Item(i)
            try:
                if w is not None and int(w.HWND) == int(hwnd):
                    tabs.append(w)
            except Exception:
                continue
        for w in tabs:
            if title.startswith(w.LocationName):
                return w
        return tabs[0] if tabs else None


# ------------------------------------------------------------- converting ---

def kind_of(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in DOCUMENT_EXT:
        return "document"
    if ext in PDF_EXT:
        return "pdf"
    return None


def find_ffmpeg():
    found = shutil.which("ffmpeg")
    if found:
        return found
    # winget installs Gyan.FFmpeg here without always fixing PATH for old shells
    base = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
    if os.path.isdir(base):
        for root, _dirs, files in os.walk(base):
            if "ffmpeg.exe" in files:
                return os.path.join(root, "ffmpeg.exe")
    return None


FFMPEG = find_ffmpeg()

PIL_FORMATS = {".png": "PNG", ".webp": "WEBP", ".gif": "GIF", ".bmp": "BMP"}
FFMPEG_FORMATS = {".mp4": "mp4", ".gif": "gif", ".mp3": "mp3", ".wav": "wav",
                  ".m4a": "ipod"}


def free_name(src, ext):
    stem = os.path.splitext(src)[0]
    out, n = stem + ext, 1
    while os.path.exists(out):
        out = f"{stem} ({n}){ext}"
        n += 1
    return out


def convert_image(src, dst, ext):
    img = Image.open(src)
    if ext in (".jpg", ".pdf", ".bmp"):
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            flat = Image.new("RGB", img.size, "white")
            flat.paste(img, mask=img.getchannel("A"))
            img = flat
        else:
            img = img.convert("RGB")
    # the format is always given explicitly: dst is a ".part" temp name
    if ext == ".jpg":
        img.save(dst, "JPEG", quality=92)
    elif ext == ".ico":
        img.save(dst, "ICO", sizes=[(s, s) for s in (16, 32, 48, 64, 128, 256)])
    elif ext == ".pdf":
        img.save(dst, "PDF", resolution=150)
    else:
        img.save(dst, PIL_FORMATS[ext])


def convert_media(src, dst, ext):
    if not FFMPEG:
        raise RuntimeError("ffmpeg not found - install it with:\n"
                           "winget install Gyan.FFmpeg")
    if ext == ".gif":
        args = ["-vf", "fps=12,scale=480:-1:flags=lanczos,split[a][b];"
                       "[a]palettegen[p];[b][p]paletteuse"]
    elif ext == ".mp4":
        args = ["-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k"]
    elif ext == ".mp3":
        args = ["-vn", "-c:a", "libmp3lame", "-q:a", "2"]
    elif ext == ".m4a":
        args = ["-vn", "-c:a", "aac", "-b:a", "192k"]
    else:  # .wav
        args = ["-vn"]
    result = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", src, *args,
         "-f", FFMPEG_FORMATS[ext], dst],
        capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[-400:] or "ffmpeg failed")


def find_soffice():
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base:
            path = os.path.join(base, "LibreOffice", "program", "soffice.exe")
            if os.path.exists(path):
                return path
    return shutil.which("soffice")


# LibreOffice keeps its settings here, apart from any LibreOffice you have
# open yourself, so a conversion never collides with it.
LO_PROFILE = os.path.join(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()),
                          "ShiftDrop", "libreoffice-profile")
LO_FILTERS = {".pdf": "pdf", ".docx": "docx:MS Word 2007 XML"}


def convert_pdf_to_docx(src, out):
    from pdf2docx import Converter
    cv = Converter(src)
    try:
        cv.convert(out)
    finally:
        cv.close()


def convert_with_libreoffice(src, out_dir, ext):
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice is needed for Word documents - install it with:\n"
                           "winget install TheDocumentFoundation.LibreOffice")
    profile = pathlib.Path(LO_PROFILE).as_uri()
    result = subprocess.run(
        [soffice, f"-env:UserInstallation={profile}", "--headless", "--norestore",
         "--convert-to", LO_FILTERS[ext], "--outdir", out_dir, src],
        capture_output=True, text=True, timeout=300,
        creationflags=subprocess.CREATE_NO_WINDOW)
    out = os.path.join(out_dir, os.path.splitext(os.path.basename(src))[0] + ext)
    if not os.path.exists(out):
        raise RuntimeError("LibreOffice couldn't convert it: "
                           + (result.stderr.strip()[-300:] or f"exit code {result.returncode}"))
    return out


def convert_office(src, dst, ext):
    """PDF -> DOCX with pdf2docx; DOC/DOCX/RTF/ODT -> PDF or DOCX with LibreOffice."""
    # both tools pick the format from the file name, so work in a temp folder
    # under real names and move the result into place at the end
    out_dir = tempfile.mkdtemp(prefix="shiftdrop-")
    try:
        if kind_of(src) == "pdf":
            out = os.path.join(out_dir, "out.docx")
            try:
                convert_pdf_to_docx(src, out)
            except Exception as exc:
                raise RuntimeError(f"couldn't read this PDF ({exc})")
        else:
            out = convert_with_libreoffice(src, out_dir, ext)
        shutil.move(out, dst)
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def convert(src, ext):
    """Convert one file, return the output path. Raises on failure."""
    kind = kind_of(src)
    src_ext = os.path.splitext(src)[1].lower().replace(".jpeg", ".jpg")
    if src_ext == ext:
        raise RuntimeError("file is already " + ext.lstrip(".").upper())
    if not any(e == ext and kind in kinds for _n, e, kinds in TARGETS):
        raise RuntimeError(f"can't make {ext.lstrip('.').upper()} from this file")
    dst = free_name(src, ext)
    # Write under a ".part" name and rename at the end, so Explorer never sees
    # (and tries to thumbnail) a half-written image or video.
    tmp = dst + ".part"
    try:
        if kind == "image":
            if Image is None:
                raise RuntimeError("Pillow is not installed")
            convert_image(src, tmp, ext)
        elif kind in ("video", "audio"):
            convert_media(src, tmp, ext)
        elif kind in ("document", "pdf"):
            convert_office(src, tmp, ext)
        else:
            raise RuntimeError("unsupported file type")
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return dst


# --------------------------------------------------------------------- UI ---

class ShiftDrop:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.96)
        self.root.configure(bg=BG)

        self.status = tk.Label(self.root, text="", bg=BG, fg=FG_OFF,
                               font=("Segoe UI", 9))
        self.status.grid(row=0, column=0, columnspan=COLUMNS, pady=(8, 2))
        self.tiles = []  # (label, ext, kinds)
        for i, (name, ext, kinds) in enumerate(TARGETS):
            lbl = tk.Label(self.root, text=name, width=6, height=2, bg=TILE, fg=FG,
                           font=("Segoe UI Semibold", 11), cursor="hand2")
            lbl.grid(row=1 + i // COLUMNS, column=i % COLUMNS, padx=4, pady=4)
            lbl.bind("<Enter>", lambda e, l=lbl: self.on_hover(l, True))
            lbl.bind("<Leave>", lambda e, l=lbl: self.on_hover(l, False))
            lbl.bind("<ButtonRelease-1>", lambda e, x=ext: self.on_pick(x))
            self.tiles.append((lbl, ext, kinds))
        tk.Label(self.root, text="Esc to close", bg=BG, fg=FG_OFF,
                 font=("Segoe UI", 8)).grid(row=3, column=0, columnspan=COLUMNS, pady=(0, 6))

        self.selection = Selection()
        self.pending_hwnd = None   # set by the hook thread, read by poll()
        self.pending_close = False
        self.files = []
        self.enabled = set()
        self.visible = False
        self.was_down = False
        self.toast = None

        HotkeyHook(self.request, self.request_close).start()
        self.root.after(POLL_MS, self.poll)

    # --- hotkey -> picker ---

    # these two run on the hook thread: just hand over, never touch Tk/COM there
    def request(self, hwnd):
        self.pending_hwnd = hwnd

    def request_close(self):
        self.pending_close = True

    def poll(self):
        hwnd, self.pending_hwnd = self.pending_hwnd, None
        close, self.pending_close = self.pending_close, False
        if hwnd:
            if self.visible:
                self.hide()  # pressing Q again closes it
            else:
                self.open_for(hwnd)
        if self.visible:
            down = key_down(VK_LBUTTON)
            if close:
                self.hide()
            elif down and not self.was_down and not self.cursor_inside():
                self.hide()  # clicked somewhere else
            self.was_down = down
        self.root.after(POLL_MS, self.poll)

    def open_for(self, hwnd):
        try:
            files = self.selection.files(hwnd)
        except Exception as exc:
            log(f"reading selection failed: {exc!r}")
            self.notify("Couldn't read the selected files", error=True)
            return
        if not files:
            self.notify("Select a file first, then press Q")
            return
        self.files = files
        kinds = {kind_of(p) for p in files}
        exts = {os.path.splitext(p)[1].lower().replace(".jpeg", ".jpg") for p in files}
        # a format is offered if every file can become it and it isn't what
        # they all already are
        self.enabled = {ext for _n, ext, ok in TARGETS
                        if None not in kinds and kinds <= ok and exts != {ext}}
        for lbl, ext, _kinds in self.tiles:
            on = ext in self.enabled
            lbl.configure(bg=TILE if on else TILE_OFF, fg=FG if on else FG_OFF,
                          cursor="hand2" if on else "arrow")
        name = os.path.basename(files[0])
        label = name if len(files) == 1 else f"{len(files)} files selected"
        if not self.enabled:
            label += " - can't convert this type"
        self.status.configure(text=label)
        self.show(*cursor_pos())

    def show(self, x, y):
        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        nx = min(max(x - w // 2, 0), sw - w)
        ny = min(max(y + 16, 0), sh - h)
        self.root.geometry(f"+{nx}+{ny}")
        self.root.deiconify()
        self.root.lift()
        self.visible = True
        self.was_down = key_down(VK_LBUTTON)

    def hide(self):
        self.root.withdraw()
        self.visible = False

    def cursor_inside(self):
        x, y = cursor_pos()
        rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
        return rx <= x < rx + self.root.winfo_width() and ry <= y < ry + self.root.winfo_height()

    def on_hover(self, lbl, inside):
        for tile, ext, _kinds in self.tiles:
            if tile is lbl and ext in self.enabled:
                tile.configure(bg=TILE_HOT if inside else TILE)

    def on_pick(self, ext):
        if ext not in self.enabled:
            return
        files = self.files
        self.hide()
        self.notify(f"Converting {len(files)} file(s) to {ext[1:].upper()}...")
        threading.Thread(target=self.work, args=(files, ext), daemon=True).start()

    def work(self, files, ext):
        done, errors = [], []
        for path in files:
            if os.path.splitext(path)[1].lower().replace(".jpeg", ".jpg") == ext:
                continue  # mixed selection: this one is already in that format
            try:
                done.append(convert(path, ext))
            except Exception as exc:  # report every failure, keep going
                errors.append(f"{os.path.basename(path)}: {exc}")
        if errors:
            log("conversion errors: " + " | ".join(errors))
            msg, err = "\n".join(errors[:3]), True
        elif len(done) == 1:
            msg, err = "Saved " + os.path.basename(done[0]), False
        else:
            msg, err = f"Saved {len(done)} files", False
        self.root.after(0, self.notify, msg, err)

    # --- little toast in the bottom-right corner ---

    def notify(self, text, error=False):
        if self.toast:
            self.toast.destroy()
        t = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        t.configure(bg=BG)
        tk.Label(t, text=text, bg=BG, fg="#ff6b6b" if error else FG, justify="left",
                 font=("Segoe UI", 10), padx=14, pady=10, wraplength=360).pack()
        t.update_idletasks()
        sw, sh = t.winfo_screenwidth(), t.winfo_screenheight()
        t.geometry(f"+{sw - t.winfo_reqwidth() - 20}+{sh - t.winfo_reqheight() - 70}")
        t.bind("<Button-1>", lambda e: t.destroy())
        self.toast = t
        t.after(6000 if error else 3000, lambda: t.winfo_exists() and t.destroy())

    def run(self):
        self.root.mainloop()


# ------------------------------------------------- single instance / quit ---

def serve_control(app):
    srv = socket.socket()
    srv.bind(("127.0.0.1", CONTROL_PORT))
    srv.listen(1)

    def loop():
        while True:
            conn, _ = srv.accept()
            if conn.recv(16) == b"quit":
                app.root.after(0, app.root.destroy)
            conn.close()

    threading.Thread(target=loop, daemon=True).start()


def send_quit():
    try:
        with socket.create_connection(("127.0.0.1", CONTROL_PORT), timeout=1) as s:
            s.sendall(b"quit")
        return True
    except OSError:
        return False


def main():
    if "--quit" in sys.argv:
        print("stopped" if send_quit() else "not running")
        return
    try:
        probe = socket.create_connection(("127.0.0.1", CONTROL_PORT), timeout=0.3)
        probe.close()
        return  # already running
    except OSError:
        pass
    app = ShiftDrop()
    serve_control(app)
    app.run()


if __name__ == "__main__":
    main()
