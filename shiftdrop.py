"""ShiftDrop - drag a file in Explorer, hold Shift, drop it on a format to convert it.

Runs in the background. While you drag a file from Explorer or the desktop with
Shift held, a format picker pops up at the cursor. Drop the file on a format and
the converted copy is written next to the original (the original is untouched).

    pythonw shiftdrop.py          start (no console)
    python  shiftdrop.py --quit   stop a running instance
"""

import ctypes
import ctypes.wintypes as wt
import os
import shutil
import socket
import subprocess
import sys
import threading
import tkinter as tk

from tkinterdnd2 import DND_FILES, TkinterDnD

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

# Only pop up for drags that start in these windows (Explorer + desktop), so
# Shift-select drags in editors and browsers are left alone.
DRAG_SOURCE_CLASSES = {"CabinetWClass", "ExploreWClass", "Progman", "WorkerW"}
MIN_DRAG_PX = 12          # mouse must travel this far before we react
POLL_MS = 30
CONTROL_PORT = 47821      # localhost port used for single-instance / --quit

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff",
             ".ico", ".heic", ".heif"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".wmv", ".flv", ".m4v"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".wma"}

# (label, output extension, kinds of input it accepts)
TARGETS = [
    ("PNG", ".png", {"image"}),
    ("JPG", ".jpg", {"image"}),
    ("WEBP", ".webp", {"image"}),
    ("ICO", ".ico", {"image"}),
    ("PDF", ".pdf", {"image"}),
    ("GIF", ".gif", {"image", "video"}),
    ("MP4", ".mp4", {"video"}),
    ("MP3", ".mp3", {"video", "audio"}),
    ("WAV", ".wav", {"video", "audio"}),
    ("M4A", ".m4a", {"video", "audio"}),
]

BG, TILE, TILE_HOT, TILE_OFF, FG, FG_OFF = (
    "#1e1e24", "#34343e", "#ff8a3d", "#26262c", "#f2f2f2", "#666670")

# ------------------------------------------------------------- win32 bits ---

user32 = ctypes.windll.user32
VK_LBUTTON, VK_SHIFT = 0x01, 0x10
GA_ROOT = 2


def key_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def cursor_pos():
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def window_class_at(x, y):
    hwnd = user32.WindowFromPoint(wt.POINT(x, y))
    root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(root, buf, 256)
    return buf.value


# ------------------------------------------------------------- converting ---

def kind_of(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
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
        self.root = TkinterDnD.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.96)
        self.root.configure(bg=BG)

        self.tiles = []  # (frame, label, ext, kinds)
        self.status = tk.Label(self.root, text="Drop on a format", bg=BG, fg=FG_OFF,
                               font=("Segoe UI", 9))
        self.status.grid(row=0, column=0, columnspan=5, pady=(8, 2))
        for i, (name, ext, kinds) in enumerate(TARGETS):
            lbl = tk.Label(self.root, text=name, width=6, height=2, bg=TILE, fg=FG,
                           font=("Segoe UI Semibold", 11))
            lbl.grid(row=1 + i // 5, column=i % 5, padx=4, pady=4)
            lbl.drop_target_register(DND_FILES)
            lbl.dnd_bind("<<DropEnter>>", lambda e, l=lbl, k=kinds: self.on_enter(e, l, k))
            lbl.dnd_bind("<<DropPosition>>", lambda e: "copy")
            lbl.dnd_bind("<<DropLeave>>", lambda e, l=lbl: self.on_leave(l))
            lbl.dnd_bind("<<Drop>>", lambda e, x=ext, k=kinds: self.on_drop(e, x, k))
            self.tiles.append((lbl, ext, kinds))
        tk.Frame(self.root, height=6, bg=BG).grid(row=3, column=0)

        self.visible = False
        self.press_pos = None
        self.press_ok = False
        self.was_down = False
        self.busy = 0
        self.hide_job = None
        self.toast = None

        self.root.after(POLL_MS, self.poll)

    # --- show / hide driven by polling the mouse + Shift state ---

    def poll(self):
        down = key_down(VK_LBUTTON)
        if down and not self.was_down:  # button just pressed
            self.press_pos = cursor_pos()
            self.press_ok = window_class_at(*self.press_pos) in DRAG_SOURCE_CLASSES
        if down and self.press_ok and not self.visible and key_down(VK_SHIFT):
            x, y = cursor_pos()
            px, py = self.press_pos
            if abs(x - px) + abs(y - py) >= MIN_DRAG_PX:
                self.show(x, y)
        if not down and self.was_down and self.visible:
            # let a pending OLE drop reach us before the window goes away
            self.hide_job = self.root.after(400, self.hide)
        self.was_down = down
        self.root.after(POLL_MS, self.poll)

    def show(self, x, y):
        if self.hide_job:
            self.root.after_cancel(self.hide_job)
            self.hide_job = None
        for lbl, _ext, _kinds in self.tiles:
            lbl.configure(bg=TILE, fg=FG)
        self.status.configure(text="Drop on a format", fg=FG_OFF)
        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        # sit just above-right of the cursor so the drag isn't covered at once
        nx = min(max(x + 30, 0), sw - w)
        ny = min(max(y - h - 30, 0), sh - h)
        self.root.geometry(f"+{nx}+{ny}")
        self.root.deiconify()
        self.root.lift()
        self.visible = True

    def hide(self):
        self.hide_job = None
        self.root.withdraw()
        self.visible = False

    # --- drag-and-drop handlers ---

    def paths(self, event):
        try:
            return [p for p in self.root.tk.splitlist(event.data) if os.path.isfile(p)]
        except (tk.TclError, TypeError):
            return []

    def on_enter(self, event, lbl, kinds):
        files = self.paths(event)
        ok = not files or all(kind_of(p) in kinds for p in files)
        lbl.configure(bg=TILE_HOT if ok else TILE_OFF, fg=FG if ok else FG_OFF)
        return "copy" if ok else "refuse_drop"

    def on_leave(self, lbl):
        lbl.configure(bg=TILE, fg=FG)

    def on_drop(self, event, ext, kinds):
        # Explorer is blocked inside its drag-and-drop call until this returns,
        # so answer at once and do the window work (hide, toast) afterwards.
        files = self.paths(event)
        if not files:
            return "refuse_drop"
        bad = [p for p in files if kind_of(p) not in kinds]
        if bad:
            msg = f"Can't make {ext[1:].upper()} from {os.path.basename(bad[0])}"
            self.root.after(150, self.notify, msg, True)
            return "refuse_drop"
        self.root.after(150, self.start_work, files, ext)
        return "copy"  # never "move" - the original stays where it was

    def start_work(self, files, ext):
        self.notify(f"Converting {len(files)} file(s) to {ext[1:].upper()}...")
        threading.Thread(target=self.work, args=(files, ext), daemon=True).start()

    def work(self, files, ext):
        done, errors = [], []
        for path in files:
            try:
                done.append(convert(path, ext))
            except Exception as exc:  # report every failure, keep going
                errors.append(f"{os.path.basename(path)}: {exc}")
        if errors:
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
