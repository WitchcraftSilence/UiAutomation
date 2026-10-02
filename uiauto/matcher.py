"""Bildausschnitt im Screenshot suchen (OpenCV Template Matching)."""
from dataclasses import dataclass

import cv2

from . import screen


@dataclass
class Match:
    x: int          # linke obere Ecke in Bildschirm-Koordinaten
    y: int
    w: int
    h: int
    score: float

    @property
    def center(self):
        return self.x + self.w / 2, self.y + self.h / 2


class Matcher:
    def __init__(self, images_dir, grayscale=True):
        self.images_dir = images_dir
        self.grayscale = grayscale
        self._cache = {}
        self._virtual = {}      # name -> Bild, das nicht als Datei existiert (z. B. Ausschnitt einer Vorlage)

    def register(self, name, img):
        self._virtual[name] = img

    def template(self, name):
        if name in self._virtual:
            return self._virtual[name]
        path = self.images_dir / name
        key = (str(path), path.stat().st_mtime)
        if key not in self._cache:
            self._cache[key] = screen.imread(path)
        return self._cache[key]

    def find(self, name, region, threshold, shot=None, grayscale=None):
        """Bester Treffer im Bereich oder None. shot = bereits gemachter Screenshot von region.

        grayscale=None nimmt die Voreinstellung; False vergleicht in Farbe.
        """
        tpl = self.template(name)
        if shot is None:
            shot = screen.grab(region)
        th, tw = tpl.shape[:2]
        if shot.shape[0] < th or shot.shape[1] < tw:
            return None
        if self.grayscale if grayscale is None else grayscale:
            hay = cv2.cvtColor(shot, cv2.COLOR_BGR2GRAY)
            needle = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
        else:
            hay, needle = shot, tpl
        res = cv2.matchTemplate(hay, needle, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        if score < threshold:
            return None
        return Match(region[0] + loc[0], region[1] + loc[1], tw, th, float(score))

    def best_score(self, name, region, shot=None, grayscale=None):
        """Nur für Fehlermeldungen: wie nah war der beste Kandidat?"""
        m = self.find(name, region, threshold=-1.0, shot=shot, grayscale=grayscale)
        return m.score if m else 0.0
