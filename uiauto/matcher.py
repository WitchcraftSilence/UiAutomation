"""Bildausschnitt im Screenshot suchen (OpenCV Template Matching)."""
from dataclasses import dataclass

import cv2
import numpy as np

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
        self._masks = {}        # name -> Maske: nur diese Pixel zählen, Vergleich dann über Farbabstand

    def register(self, name, img, mask=None):
        self._virtual[name] = img
        if mask is not None:
            self._masks[name] = mask

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
        if name in self._masks:
            return self._find_masked(name, tpl, shot, region, threshold)
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

    def find_all_masked(self, name, region, threshold, shot, limit=5):
        """Mehrere Treffer einer maskierten Vorlage, bester zuerst."""
        tpl, mask = self.template(name), self._masks[name]
        if shot.shape[0] < tpl.shape[0] or shot.shape[1] < tpl.shape[1]:
            return []
        res = cv2.matchTemplate(shot, tpl, cv2.TM_SQDIFF, mask=cv2.merge([mask] * 3))
        n = np.count_nonzero(mask) * 3
        th, tw = tpl.shape[:2]
        found = []
        while len(found) < limit:
            sq, _, loc, _ = cv2.minMaxLoc(res)
            score = 1 - (max(sq, 0) / n) ** 0.5 / 100
            if score < threshold:
                break
            found.append(Match(region[0] + loc[0], region[1] + loc[1], tw, th, float(score)))
            x, y = loc
            res[max(0, y - th // 2):y + th // 2 + 1, max(0, x - tw // 2):x + tw // 2 + 1] = np.inf
        return found

    def _find_masked(self, name, tpl, shot, region, threshold):
        """Vergleich nur der maskierten Pixel, immer in Farbe.

        Score = 1 - mittlerer Farbabstand / 100, also 1.0 bei gleichen Farben und 0.9 bei
        durchschnittlich 10 Helligkeitsstufen Abweichung je Farbkanal.
        """
        mask = self._masks[name]
        res = cv2.matchTemplate(shot, tpl, cv2.TM_SQDIFF, mask=cv2.merge([mask] * 3))
        sq, _, loc, _ = cv2.minMaxLoc(res)
        score = 1 - (max(sq, 0) / (np.count_nonzero(mask) * 3)) ** 0.5 / 100
        if score < threshold:
            return None
        th, tw = tpl.shape[:2]
        return Match(region[0] + loc[0], region[1] + loc[1], tw, th, float(score))

    def best_score(self, name, region, shot=None, grayscale=None):
        """Nur für Fehlermeldungen: wie nah war der beste Kandidat?"""
        m = self.find(name, region, threshold=-1.0, shot=shot, grayscale=grayscale)
        return m.score if m else 0.0
