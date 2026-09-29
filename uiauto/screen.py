"""Screenshots mit mss sowie Bilder laden/speichern (auch bei Umlauten im Pfad)."""
import threading
from pathlib import Path

import cv2
import mss
import numpy as np

_local = threading.local()


def _sct():
    # mss-Instanzen sind nicht threadübergreifend nutzbar
    if not hasattr(_local, "sct"):
        _local.sct = mss.mss()
    return _local.sct


def grab(region=None):
    """Screenshot als BGR-Array. region = (left, top, width, height), None = alle Monitore."""
    sct = _sct()
    if region is None:
        mon = sct.monitors[0]
        region = (mon["left"], mon["top"], mon["width"], mon["height"])
    left, top, width, height = region
    raw = sct.grab({"left": left, "top": top, "width": width, "height": height})
    return np.ascontiguousarray(np.array(raw)[:, :, :3])


def virtual_screen():
    mon = _sct().monitors[0]
    return mon["left"], mon["top"], mon["width"], mon["height"]


def imread(path):
    # cv2.imread kann unter Windows keine Pfade mit Umlauten
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Bild nicht lesbar: {path}")
    return img


def imwrite(path, img):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(path.suffix or ".png", img)
    if not ok:
        raise IOError(f"Bild konnte nicht gespeichert werden: {path}")
    buf.tofile(str(path))
