"""Aufnahmewerkzeug für Referenzbilder.

Friert den Bildschirm ein, der Benutzer zieht mit der Maus ein Rechteck auf,
wählt den Sequenzordner und gibt einen Namen ein, und der Ausschnitt wird pixelgenau unter images/ gespeichert.
Der relative Pfad landet in der Zwischenablage, zum Einfügen in die YAML-Datei.
"""
import re
import tkinter as tk
from datetime import datetime
from tkinter import ttk

import cv2
from PIL import Image, ImageEnhance, ImageTk

from . import screen


class Recorder:
    def __init__(self, root, images_dir, notify, on_done=lambda: None):
        self.root = root
        self.images_dir = images_dir
        self.notify = notify
        self.on_done = on_done          # nach Speichern oder Abbrechen (z. B. Steuerfenster wieder zeigen)

    def start(self):
        left, top, width, height = screen.virtual_screen()
        shot = screen.grab((left, top, width, height))
        self._shot = shot

        rgb = Image.fromarray(cv2.cvtColor(shot, cv2.COLOR_BGR2RGB))
        dimmed = ImageEnhance.Brightness(rgb).enhance(0.6)
        self._rgb = rgb
        self._dim = ImageTk.PhotoImage(dimmed)

        win = self._win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.geometry(f"{width}x{height}{left:+d}{top:+d}")
        canvas = self._canvas = tk.Canvas(win, width=width, height=height,
                                          highlightthickness=0, cursor="crosshair")
        canvas.pack()
        canvas.create_image(0, 0, image=self._dim, anchor="nw")
        self._hint = canvas.create_text(
            20, 20, anchor="nw", fill="white", font=("Segoe UI", 14, "bold"),
            text="Bereich mit der Maus aufziehen · Esc = Abbrechen")
        self._crop = None
        self._rect = None
        self._label = None
        self._start = None

        canvas.bind("<ButtonPress-1>", self._on_press)
        canvas.bind("<B1-Motion>", self._on_drag)
        canvas.bind("<ButtonRelease-1>", self._on_release)
        win.bind("<Escape>", lambda e: (self._close(), self.on_done()))
        win.focus_force()

    def _on_press(self, event):
        self._start = (event.x, event.y)

    def _on_drag(self, event):
        if not self._start:
            return
        x0, y0 = self._start
        x1, y1 = event.x, event.y
        c = self._canvas
        for item in (self._crop, self._rect, self._label):
            if item:
                c.delete(item)
        # Auswahl hell anzeigen, Rest bleibt abgedunkelt
        l, t, r, b = min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
        self._crop_img = None
        if r - l > 0 and b - t > 0:
            self._crop_img = ImageTk.PhotoImage(self._rgb.crop((l, t, r, b)))
            self._crop = c.create_image(l, t, image=self._crop_img, anchor="nw")
        self._rect = c.create_rectangle(l, t, r, b, outline="#ff3b30", width=1)
        self._label = c.create_text(l, t - 4, anchor="sw", fill="white",
                                    font=("Segoe UI", 10), text=f"{r - l} × {b - t}")

    def _on_release(self, event):
        if not self._start:
            return
        x0, y0 = self._start
        l, t, r, b = min(x0, event.x), min(y0, event.y), max(x0, event.x), max(y0, event.y)
        self._start = None
        if r - l < 5 or b - t < 5:
            return   # versehentlicher Klick
        crop = self._shot[t:b, l:r].copy()
        self._close()

        default = f"bild_{datetime.now():%Y%m%d_%H%M%S}"
        answer = self._ask_name(default)
        if not answer:
            self.on_done()
            return
        folder, name = answer
        # Ordner aus der Liste (oder neu eingetippt) plus Name; "/" im Namen geht auch
        parts = [re.sub(r'[<>:"|?*]+', "_", p.strip()) for p in re.split(r"[/\\]",f"{folder}/{name}")]
        parts = [p for p in parts if p and p not in (".", "..")]
        if not parts or not name.strip():
            self.on_done()
            return
        path = self.images_dir.joinpath(*parts[:-1], f"{parts[-1]}.png")
        path.parent.mkdir(parents=True, exist_ok=True)
        screen.imwrite(path, crop)
        rel = path.relative_to(self.images_dir).as_posix()
        self.root.clipboard_clear()
        self.root.clipboard_append(rel)
        self.notify("Referenzbild gespeichert", f"{rel} ({r - l}×{b - t}) – Name in Zwischenablage")
        self.on_done()

    NO_FOLDER = "(kein Ordner)"
    LAST_FILE = ".letzter_ordner"     # unter images/, merkt sich die letzte Ordnerwahl

    def _folders(self):
        dirs = sorted(p.relative_to(self.images_dir).as_posix()
                      for p in self.images_dir.rglob("*") if p.is_dir())
        return [self.NO_FOLDER] + dirs

    def _last_folder(self):
        try:
            return (self.images_dir / self.LAST_FILE).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _ask_name(self, default):
        """Dialog mit Ordnerliste (Sequenzordner unter images/) und Namen. Gibt (ordner, name)
        oder None zurück. Der Ordner kann auch neu eingetippt werden; die Wahl wird gemerkt."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Referenzbild speichern")
        dlg.attributes("-topmost", True)
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)

        folders = self._folders()
        last = self._last_folder()
        ttk.Label(frm, text="Ordner:").grid(row=0, column=0, sticky="w", pady=(0, 6))
        folder = tk.StringVar(value=last if last in folders else self.NO_FOLDER)
        ttk.Combobox(frm, textvariable=folder, values=folders, width=40).grid(
            row=0, column=1, sticky="we", pady=(0, 6))
        ttk.Label(frm, text="Name (ohne .png):").grid(row=1, column=0, sticky="w", padx=(0, 8))
        name = tk.StringVar(value=default)
        entry = ttk.Entry(frm, textvariable=name, width=43)
        entry.grid(row=1, column=1, sticky="we")
        ttk.Label(frm, text="Gleicher Name ersetzt das vorhandene Bild.", foreground="gray").grid(
            row=2, column=1, sticky="w", pady=(4, 0))

        result = []

        def ok(_=None):
            f = folder.get().strip()
            f = "" if f == self.NO_FOLDER else f
            result.append((f, name.get()))
            try:
                (self.images_dir / self.LAST_FILE).write_text(f, encoding="utf-8")
            except OSError:
                pass
            dlg.destroy()

        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Speichern", command=ok).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Abbrechen", command=dlg.destroy).pack(side="left")
        dlg.bind("<Return>", ok)
        dlg.bind("<Escape>", lambda e: dlg.destroy())

        dlg.update_idletasks()
        x = (dlg.winfo_screenwidth() - dlg.winfo_width()) // 2
        y = (dlg.winfo_screenheight() - dlg.winfo_height()) // 3
        dlg.geometry(f"+{x}+{y}")
        dlg.focus_force()
        entry.focus_set()
        entry.select_range(0, "end")
        dlg.grab_set()
        dlg.wait_window()
        return result[0] if result else None

    def _close(self):
        if self._win:
            self._win.destroy()
            self._win = None
