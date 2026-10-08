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


def imread(path, alpha=False):
    """Bild als BGR. alpha=True: (bild, maske), maske = nicht transparente Pixel oder None ohne Alphakanal."""
    # cv2.imread kann unter Windows keine Pfade mit Umlauten
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED if alpha else cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Bild nicht lesbar: {path}")
    if not alpha:
        return img
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR), None
    if img.shape[2] == 4:
        mask = (img[:, :, 3] > 0).astype(np.uint8) * 255
        return img[:, :, :3].copy(), (mask if mask.min() == 0 else None)
    return img, None


def imwrite(path, img):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(path.suffix or ".png", img)
    if not ok:
        raise IOError(f"Bild konnte nicht gespeichert werden: {path}")
    buf.tofile(str(path))
