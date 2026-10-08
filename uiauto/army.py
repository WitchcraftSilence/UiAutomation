"""Einheiten-Kacheln mit Lebensbalken erkennen (Aktion replace_damaged).

Eine Kachel ist ein Porträt mit einem Lebensbalken darunter. Der Balken besteht aus
Segmenten: grün = Leben, rot = verloren. "Voll" heißt: kein rotes Segment im Balken. (Früher
wurde nur geprüft, ob die letzten Pixel rechts grün sind. Das hing an der genauen Lage des
Balkens, und schon 2 px Versatz machten aus einer gesunden Einheit eine beschädigte.)
"""
from dataclasses import dataclass

import cv2
import numpy as np

PORTRAIT_H = 60         # Höhe des Porträts über dem Balken (px)
BAR_GAP = 2             # Abstand zwischen Porträt und Balken
RED_MIN = 6             # so viele rote Pixel im Balken heißen beschädigt (voll: 0, ein rotes Segment: ≥ 14)
SAME_UNIT = 0.85        # Mindest-Ähnlichkeit zweier Porträts für "gleiche Einheit"


@dataclass
class Tile:
    x: int              # linke Kante des Balkens (= der Kachel), im übergebenen Bild
    bar_y: int
    width: int
    full: bool
    red: int            # rote Pixel im Balken

    @property
    def portrait_box(self):
        """(x, y, w, h) des Porträts im übergebenen Bild."""
        top = max(0, self.bar_y - BAR_GAP - PORTRAIT_H)
        return self.x, top, self.width, self.bar_y - BAR_GAP - top


def _green(img):
    b, g, r = (img[..., i].astype(int) for i in range(3))
    return (g > 120) & (g > r + 25) & (g > b + 40)


def _red(img):
    b, g, r = (img[..., i].astype(int) for i in range(3))
    return (r > 110) & (r > g + 50) & (r > b + 50)


def _bar(img, x, y, width):
    """Balkenbereich mit etwas Rand, damit wenige Pixel Versatz nichts ausmachen."""
    return img[max(0, y - 1):y + 7, max(0, x - 2):x + width + 2]


def red_pixels(img, x, y, width):
    return int(_red(_bar(img, x, y, width)).sum())


def find_tiles(img):
    """Alle Kacheln mit Lebensbalken im Bild, sortiert nach Zeile und Spalte.

    Ein Balken beginnt grün und wird nach rechts über anschließende rote Segmente verlängert. Nur mit
    Grün wäre die Breite zu kurz, wenn fast alle Einheiten verwundet sind (Median ohne das rote Ende),
    und das letzte Segment fiele heraus. Rote Stücke allein zählen nicht, sonst würde rote Kleidung
    in den Porträts zu Balken.
    """
    green = _green(img)
    colored = green | _red(img)
    _, _, stats, _ = cv2.connectedComponentsWithStats(green.astype(np.uint8))
    parts = sorted((s for s in stats[1:] if 3 <= s[3] <= 9 and s[2] >= 3), key=lambda s: (s[1], s[0]))

    # grüne Stücke derselben Zeile, die nah beieinander liegen, gehören zu einem Balken
    bars = []
    for x, y, w, h, _ in parts:
        for bar in bars:
            if abs(bar["y"] - y) <= 3 and bar["x"] <= x <= bar["x"] + 60:
                bar["end"] = max(bar["end"], x + w)
                bar["h"] = max(bar["h"], h)
                break
        else:
            bars.append({"x": x, "y": y, "end": x + w, "h": h})
    for bar in bars:                        # über rote Segmente verlängern (Lücken zwischen Segmenten ≤ 3 px)
        row = colored[min(bar["y"] + bar["h"] // 2, img.shape[0] - 1)]
        while bar["end"] < len(row) and row[bar["end"]:bar["end"] + 4].any() and bar["end"] - bar["x"] < 70:
            bar["end"] += 1

    widths = [b["end"] - b["x"] for b in bars if b["end"] - b["x"] >= 45]
    width = int(np.median(widths)) if widths else 54
    tiles = []
    for bar in bars:
        if bar["end"] - bar["x"] < 12 or bar["y"] < PORTRAIT_H // 2:
            continue                        # zu kurz für einen Balken bzw. kein Platz für ein Porträt
        red = red_pixels(img, bar["x"], bar["y"], width)
        tiles.append(Tile(int(bar["x"]), int(bar["y"]), width, red < RED_MIN, red))
    tiles.sort(key=lambda t: (t.bar_y // 20, t.x))
    return tiles


def slot_area(tile, margin=10):
    """Bereich eines Platzes (Porträt + Balken + Rand) als (x, y, w, h)."""
    x, y, w, _ = tile.portrait_box
    return x - margin, y - margin, w + 2 * margin, tile.bar_y + 7 - y + 2 * margin


def slot_state(img, tile, empty_tpl=None):
    """Zustand eines festen Platzes: 'empty', 'full' oder 'damaged'.

    Anders als find_tiles erkennt das auch Einheiten mit fast leerem Balken, weil der Platz
    bekannt ist und nicht über ein langes grünes Stück gesucht werden muss.
    """
    if empty_tpl is not None:
        x, y, w, h = slot_area(tile)
        area = img[max(0, y):y + h, max(0, x):x + w]
        if find_empty(area, empty_tpl):
            return "empty"
    red = red_pixels(img, tile.x, tile.bar_y, tile.width)
    if red >= RED_MIN:
        return "damaged"                    # auch ganz roter Balken (kein Grün mehr)
    if not _green(_bar(img, tile.x, tile.bar_y, tile.width)).any():
        return "empty"                      # kein Balken, also keine Einheit
    return "full"


def find_empty(img, empty_tpl, threshold=0.85):
    """Leere Felder (Rahmen ohne Einheit) im Bild; gibt eine Liste von (x, y, w, h) zurück."""
    if img.shape[0] < empty_tpl.shape[0] or img.shape[1] < empty_tpl.shape[1]:
        return []
    res = cv2.matchTemplate(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.cvtColor(empty_tpl, cv2.COLOR_BGR2GRAY),
                            cv2.TM_CCOEFF_NORMED)
    h, w = empty_tpl.shape[:2]
    found = []
    while True:     # bester Treffer, Umgebung löschen, nächster (mehrere Felder)
        _, score, _, (x, y) = cv2.minMaxLoc(res)
        if score < threshold:
            return found
        found.append((x, y, w, h))
        res[max(0, y - h // 2):y + h // 2, max(0, x - w // 2):x + w // 2] = -1


def _key(portrait):
    """Oberer, mittlerer Teil des Porträts: ohne Abzeichen unten rechts und Anzahl ("x8") unten links."""
    h, w = portrait.shape[:2]
    return portrait[4:int(h * 0.6), 6:w - 6]


def similarity(portrait_a, img_b, tile_b, slack=4):
    """Wie ähnlich ist Porträt a dem Porträt der Kachel b? Erlaubt einige Pixel Versatz."""
    key = cv2.cvtColor(_key(portrait_a), cv2.COLOR_BGR2GRAY)
    x, y, w, h = tile_b.portrait_box
    x0, y0 = max(0, x - slack), max(0, y - slack)
    area = img_b[y0:y + h + slack, x0:x + w + slack]
    area = cv2.cvtColor(area, cv2.COLOR_BGR2GRAY)
    if area.shape[0] < key.shape[0] or area.shape[1] < key.shape[1]:
        return 0.0
    return float(cv2.minMaxLoc(cv2.matchTemplate(area, key, cv2.TM_CCOEFF_NORMED))[1])


def crop(img, box):
    x, y, w, h = box
    return img[y:y + h, x:x + w]
