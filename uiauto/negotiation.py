"""Verhandlung im Gildenexpeditions-Modus (Aktion negotiate).

Die Vorschlagstabelle (Kopfzeile "Person 1 … Person 5") zeigt je Runde eine neue Zeile.
Ältere Zeilen sind blass; nur die aktuelle hat Güter-Symbole auf einem fast schwarzen
Kreis. Über diesen Kreis findet man die aktuelle Zeile und schneidet das Symbol aus. Das
Symbol wird danach im Menü "Ressource auswählen" gesucht. Welche Güter dort stehen, hängt
von der Verhandlung ab; darum wird zur Laufzeit verglichen und nicht gegen feste Bilder.
"""
from dataclasses import dataclass

import cv2
import numpy as np

PERSONS = 5
DARK = 60               # Summe B+G+R, unter der ein Pixel zum schwarzen Kreis gehört
ICON_H = (38, 64)       # Höhe eines Kreises (mit Abzeichen) in px
ICON_D = 40             # Mindest-Kantenlänge; ist der Kreis oben vom Gut verdeckt, wird von unten mindestens so hoch geschnitten
MIN_DARK_PX = 30        # so viele schwarze Pixel braucht eine Spalte für ein Symbol
SAME_ROW = 8            # px: Symbole in dieser Höhe gehören zur selben Zeile
BORDER = 0.9            # Spalten, die über diesen Anteil der Zeilenhöhe dunkel sind, gelten als Rand
ICON_GAP = 8           # px: Lücken im Kreis bis zu dieser Höhe überbrücken (helle Güter verdecken ihn teilweise)
SCALES = np.arange(0.8, 1.61, 0.05)     # Symbol im Menü ist etwa gleich groß, je nach Zoom etwas größer
SAME_GOOD = 0.75        # Mindest-Übereinstimmung Vorschlag ↔ Menü (richtig ≥ 0,87, falsches Gut ≤ 0,61)
# Dünne Strichzeichnungen (Spirale) sind im Menü leicht anders gezeichnet und erreichen nur
# etwa 0,67, alle anderen Stellen im Menü aber nur 0,2. Darum reicht auch ein klarer Vorsprung.
MIN_GOOD = 0.55         # darunter nie
LEAD = 0.25             # so weit muss der Treffer vor der besten anderen Stelle im Menü liegen
TILE_HSV = ((5, 120, 110), (30, 255, 255))   # Orange der Kacheln im Menü (OpenCV-HSV)
TILE_MIN_PX = 300       # kleinere orange Flecken sind keine Kachel
TILE_SIMILAR = 0.7      # Kacheln sind mindestens so breit/hoch wie dieser Anteil der größten
MENU_KEYS = 8           # Tasten 1 … MENU_KEYS wählen im offenen Menü die Kachel (zeilenweise); weitere werden angeklickt


@dataclass
class Icon:
    person: int         # 1-5
    x: int              # linke obere Ecke des Kreises, im übergebenen Bild
    y: int
    d: int              # Kantenlänge (Kreis samt Abzeichen)

    def crop(self, img):
        return img[self.y:self.y + self.d, self.x:self.x + self.d]


def header_rows(template):
    """Höhe der dunklen Kopfzeile ("Person 1 … Person 5") oben im Bild der Vorschlagstabelle."""
    med = np.median(template.astype(int).sum(2), axis=1)
    for y in range(2, len(med)):
        if abs(med[y] - med[1]) > 30:
            return y
    raise ValueError("keine Kopfzeile erkannt (dunkle Zeile mit 'Person 1 … Person 5' oben im Bild)")


def _bands(rows, gap=3):
    """Zusammenhängende Zeilenbereiche (Lücken bis gap px werden überbrückt): Liste von (y0, y1)."""
    ys = np.nonzero(rows)[0]
    if len(ys) == 0:
        return []
    parts = np.split(ys, np.where(np.diff(ys) > gap)[0] + 1)
    return [(p[0], p[-1]) for p in parts]


def table_bottom(img):
    """Unterer Rand der Tabelle: erste Zeile, die über die ganze Breite gleichmäßig dunkel ist.

    Darunter beginnt das Spielbild, das ebenfalls fast schwarze Pixel hat. Ohne Rand
    (zugeschnittenes Bild) zählt das ganze Bild.
    """
    s = img.astype(int).sum(2)
    border = lambda y: (s[y] < 120).mean() > 0.95 and s[y].std() < 20
    y = 0
    while y < len(s) and border(y):     # Trennlinie direkt unter der Kopfzeile
        y += 1
    while y < len(s) and not border(y):
        y += 1
    return y


def current_icons(img):
    """Symbole der aktuellen (untersten) Vorschlagszeile. img = Bereich unter der Kopfzeile,
    so breit wie die Kopfzeile. Personen ohne Symbol fehlen in der Liste (schon richtig)."""
    img = img[:table_bottom(img)]
    h, w = img.shape[:2]
    dark = img.astype(int).sum(2) < DARK
    # Durchgehend dunkle Spalten sind Fensterränder (liegt der Assistent 1 px anders, ragt sein
    # rechter Rand in die Spalte von Person 5 und macht das Symbol dort zu hoch), nie ein Symbol
    dark[:, dark.mean(0) > BORDER] = False
    found = []      # (person, x, Unterkante, Höhe)
    for p in range(PERSONS):
        x0, x1 = p * w // PERSONS, (p + 1) * w // PERSONS
        col = dark[:, x0:x1]
        for y0, y1 in _bands(col.sum(1) > 0, ICON_GAP):
            if ICON_H[0] <= y1 - y0 + 1 <= ICON_H[1] and col[y0:y1 + 1].sum() >= MIN_DARK_PX:
                xs = np.nonzero(col[y0:y1 + 1].any(0))[0]
                found.append((p + 1, x0 + int(xs[0]), int(y1), int(y1 - y0 + 1)))
    if not found:
        return []
    bottom = max(f[2] for f in found)
    found = [f for f in found if bottom - f[2] <= SAME_ROW]
    # Kantenlänge aus der ganzen Zeile (Median), nicht fest: hängt vom Browser-Zoom ab
    # (100 %: etwa 42 px, 110 %: etwa 48 px). Einzelne Güter verdecken den Kreis oben oder
    # ragen darüber hinaus; die Unterkante (Abzeichen) ist verlässlicher als die Oberkante.
    d = max(int(np.median([f[3] for f in found])), ICON_D)
    return [Icon(p, x, max(0, y1 - d + 1), d) for p, x, y1, _ in found]


def icon_mask(crop):
    """Nur das Gut selbst: innerhalb des Kreises, ohne schwarzen Hintergrund und ohne Abzeichen rechts unten."""
    d = crop.shape[0]
    yy, xx = np.mgrid[:d, :crop.shape[1]]
    c = (d - 1) / 2
    m = (yy - c) ** 2 + (xx - c) ** 2 <= (0.42 * d) ** 2
    m &= ~((xx > 0.6 * d) & (yy > 0.62 * d))
    m &= crop.astype(int).sum(2) >= 90
    return m.astype(np.uint8) * 255


def menu_span(row, x0, x1):
    """Links/rechts der grauen Überschriftsleiste "Ressource auswählen" in einer Bildzeile.

    x0..x1 = gefundene Überschrift (liegt in der Leiste). Die Leiste ist so breit wie das Menü;
    nur darunter wird gesucht, sonst trifft man das gewählte Gut im Knopf einer anderen Person.
    """
    px = row.astype(int)
    gray = lambda x: (px[x].max() - px[x].min() < 20) and 100 <= px[x].sum() <= 260
    left, right = x0, x1 - 1
    while left > 0 and gray(left - 1):
        left -= 1
    while right < len(px) - 1 and gray(right + 1):
        right += 1
    return left, right + 1


def menu_tiles(menu):
    """Orangefarbene Kacheln im Menü "Ressource auswählen", zeilenweise von links oben: Liste von (x, y, w, h).

    Die Reihenfolge entspricht den Tasten 1, 2, 3 …, mit denen man im offenen Menü ein Gut wählt.
    Orange Güter (Kupfer, Gold) ergeben kleine Flecken in der Kachel; die fallen über die Größe heraus.
    """
    hsv = cv2.cvtColor(menu, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, TILE_HSV[0], TILE_HSV[1])
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
    boxes = [tuple(int(v) for v in s[:4]) for s in stats[1:] if s[4] >= TILE_MIN_PX]
    if not boxes:
        return []
    w_max, h_max = max(b[2] for b in boxes), max(b[3] for b in boxes)
    boxes = [b for b in boxes if b[2] >= TILE_SIMILAR * w_max and b[3] >= TILE_SIMILAR * h_max]
    boxes.sort(key=lambda b: b[1])
    rows = []
    for b in boxes:
        if rows and b[1] - rows[-1][0][1] < h_max / 2:
            rows[-1].append(b)
        else:
            rows.append([b])
    return [b for row in rows for b in sorted(row)]


def tile_number(tiles, x, y):
    """Nummer (1, 2, …) der Kachel, in der (x, y) liegt, sonst None."""
    for i, (tx, ty, tw, th) in enumerate(tiles, 1):
        if tx <= x < tx + tw and ty <= y < ty + th:
            return i
    return None


def find_good(icon, menu):
    """Symbol aus dem Vorschlag im Menü suchen (mehrere Größen). Gibt (übereinstimmung, (x, y), zweitbeste)
    zurück, (x, y) = Mitte des Treffers im Menü-Bild, zweitbeste = beste Übereinstimmung an einer
    anderen Stelle des Menüs (mehr als eine halbe Symbolbreite entfernt)."""
    mask = icon_mask(icon)
    best = (-1.0, None, 0)
    results = []
    for s in SCALES:
        t = cv2.resize(icon, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        if t.shape[0] >= menu.shape[0] or t.shape[1] >= menu.shape[1]:
            break
        mk = cv2.resize(mask, (t.shape[1], t.shape[0]), interpolation=cv2.INTER_NEAREST)
        res = cv2.matchTemplate(menu, t, cv2.TM_CCOEFF_NORMED, mask=cv2.merge([mk] * 3))
        res[~np.isfinite(res)] = -1
        results.append((res, t.shape[1]))
        _, v, _, loc = cv2.minMaxLoc(res)
        if v > best[0]:
            best = (float(v), (loc[0] + t.shape[1] // 2, loc[1] + t.shape[0] // 2), t.shape[1])
    score, pos, w = best
    if pos is None:
        return score, pos, -1.0
    second = -1.0
    for res, tw in results:
        cx = np.arange(res.shape[1]) + tw // 2      # Mitte des Treffers je Spalte
        far = np.abs(cx - pos[0]) > w / 2
        if far.any():
            second = max(second, float(res[:, far].max()))
    return score, pos, second


def accepted(score, second):
    """Gut gilt als gefunden: hohe Übereinstimmung oder klarer Vorsprung vor allen anderen Stellen."""
    return score >= SAME_GOOD or (score >= MIN_GOOD and score - second >= LEAD)
