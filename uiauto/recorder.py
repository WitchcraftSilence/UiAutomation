"""Aufnahmewerkzeug für Referenzbilder.

Friert den Bildschirm ein, der Benutzer zieht mit der Maus ein Rechteck auf,
gibt einen Namen ein, und der Ausschnitt wird pixelgenau unter images/ gespeichert.
Der relative Pfad landet in der Zwischenablage, zum Einfügen in die YAML-Datei.
"""
import re
import tkinter as tk
from datetime import datetime
from tkinter import simpledialog

import cv2
from PIL import Image, ImageEnhance, ImageTk

from . import screen


class Recorder:
    def __init__(self, root, images_dir, notify):
        self.root = root
        self.images_dir = images_dir
        self.notify = notify

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
        win.bind("<Escape>", lambda e: self._close())
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
        name = simpledialog.askstring("Referenzbild speichern",
                                      "Name des Bildes (ohne .png):",
                                      initialvalue=default, parent=self.root)
        if not name:
            return
        name = re.sub(r'[<>:"/\\|?*]+', "_", name.strip())
        path = self.images_dir / f"{name}.png"
        screen.imwrite(path, crop)
        rel = path.relative_to(self.images_dir).as_posix()
        self.root.clipboard_clear()
        self.root.clipboard_append(rel)
        self.notify("Referenzbild gespeichert", f"{rel} ({r - l}×{b - t}) – Name in Zwischenablage")

    def _close(self):
        if self._win:
            self._win.destroy()
            self._win = None
