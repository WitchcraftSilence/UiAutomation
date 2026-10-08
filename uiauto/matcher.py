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

    def _load(self, name):
        """(bild, alphamaske oder None); der Cache hängt an Pfad und mtime."""
        if name in self._virtual:
            return self._virtual[name], None
        path = self.images_dir / name
        key = (str(path), path.stat().st_mtime)
        if key not in self._cache:
            self._cache[key] = screen.imread(path, alpha=True)
        return self._cache[key]

    def template(self, name):
        return self._load(name)[0]

    def _scores(self, name, shot, grayscale):
        """TM_CCOEFF_NORMED-Ergebnis. Hat das PNG transparente Pixel, zählen nur die übrigen
        (Symbol ohne Hintergrund, der je nach Stelle im Spiel anders aussieht)."""
        tpl, alpha = self._load(name)
        if self.grayscale if grayscale is None else grayscale:
            hay = cv2.cvtColor(shot, cv2.COLOR_BGR2GRAY)
            needle = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
            mask = alpha
        else:
            hay, needle = shot, tpl
            mask = None if alpha is None else cv2.merge([alpha] * 3)
        if mask is None:
            return cv2.matchTemplate(hay, needle, cv2.TM_CCOEFF_NORMED)
        res = cv2.matchTemplate(hay, needle, cv2.TM_CCOEFF_NORMED, mask=mask)
        return np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)   # einfarbige Stellen ergeben NaN/inf

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
        res = self._scores(name, shot, grayscale)
        _, score, _, loc = cv2.minMaxLoc(res)
        if score < threshold:
            return None
        return Match(region[0] + loc[0], region[1] + loc[1], tw, th, float(score))

    def find_all(self, name, region, threshold, shot, grayscale=None, limit=100):
        """Alle Treffer (ohne Überlappung um mehr als die halbe Bildgröße), bester zuerst."""
        tpl = self.template(name)
        th, tw = tpl.shape[:2]
        if shot.shape[0] < th or shot.shape[1] < tw:
            return []
        res = self._scores(name, shot, grayscale)
        found = []
        while len(found) < limit:
            _, score, _, (x, y) = cv2.minMaxLoc(res)
            if score < threshold:
                break
            found.append(Match(region[0] + x, region[1] + y, tw, th, float(score)))
            res[max(0, y - th // 2):y + th // 2 + 1, max(0, x - tw // 2):x + tw // 2 + 1] = -1
        return found

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
