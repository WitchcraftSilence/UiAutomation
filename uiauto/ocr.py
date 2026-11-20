"""Zahlen aus Bildausschnitten lesen, mit der in Windows 10/11 eingebauten Texterkennung.

Kleine Spielschrift wird je nach Vergrößerung unterschiedlich gut erkannt. Darum wird
jeder Ausschnitt mehrfach gelesen (verschiedene Vergrößerungen) und nur ein Ergebnis
angenommen, das die Mehrheit bestätigt.
"""
import ast
import asyncio
import operator
import re
from collections import Counter

import cv2
import numpy as np
from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

# (Faktor, Interpolation): bei der Geisterschul-Leiste lesen 4-6x zuverlässig
VARIANTS = [(f, i) for f in (4, 5, 6) for i in (cv2.INTER_CUBIC, cv2.INTER_NEAREST)]
MIN_VOTES = 4                       # so viele Lesungen müssen übereinstimmen
EARLY_VOTES = 3                     # oder so viele, wenn bis dahin alle Lesungen einig sind
_FRACTION = re.compile(r"(\d+)/(\d+)")

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = OcrEngine.try_create_from_user_profile_languages()
        if _engine is None:
            raise RuntimeError("Windows-Texterkennung nicht verfügbar (keine OCR-Sprache installiert)")
    return _engine


async def _recognize(img):
    bgra = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    h, w = bgra.shape[:2]
    writer = DataWriter()
    writer.write_bytes(bgra.tobytes())
    bitmap = SoftwareBitmap(BitmapPixelFormat.BGRA8, w, h, BitmapAlphaMode.PREMULTIPLIED)
    bitmap.copy_from_buffer(writer.detach_buffer())
    result = await _get_engine().recognize_async(bitmap)
    return result.text


def read_text(img):
    """Ein einzelner Leseversuch, wie er ist."""
    return asyncio.run(_recognize(img))


def _tally(texts):
    """Stimmen je (x, total). Ein Text ohne "/" zählt für (x, total), wenn er genau "x1total" lautet:
    Die Texterkennung liest den Schrägstrich bei kleinen Zahlen oft als 1 ("5/120" -> "51120")."""
    votes = Counter()
    for t in texts:
        m = _FRACTION.search(re.sub(r"\s+", "", t))
        if m:
            votes[(int(m.group(1)), int(m.group(2)))] += 1
    digits = [re.sub(r"\D", "", t) for t in texts if "/" not in t]
    for value in list(votes):
        votes[value] += sum(d == f"{value[0]}1{value[1]}" for d in digits)
    return votes


def read_fraction(img):
    """Liest 'x/total' aus einem BGR-Ausschnitt.

    Liest nacheinander in mehreren Vergrößerungen (VARIANTS). Sind die ersten EARLY_VOTES Lesungen
    einig, reicht das (spart Zeit, jede Lesung kostet etwa 75 ms); sonst braucht es MIN_VOTES.
    Gibt ((x, total), stimmen, alle_lesungen) zurück; (x, total) ist None, wenn sich keine
    Mehrheit findet.
    """
    async def read(factor, interp):
        big = cv2.resize(img, None, fx=factor, fy=factor, interpolation=interp)
        big = cv2.copyMakeBorder(big, 30, 30, 30, 30, cv2.BORDER_REPLICATE)
        return await _recognize(big)

    texts, votes = [], Counter()
    for factor, interp in VARIANTS:
        texts.append(asyncio.run(read(factor, interp)))
        votes = _tally(texts)
        if votes:
            value, n = votes.most_common(1)[0]
            if value[0] <= value[1] and (n >= MIN_VOTES or (n == len(texts) >= EARLY_VOTES)):
                return value, n, texts
    return None, max(votes.values(), default=0), texts


# ---------------------------------------------------------------- Zähler in einer Leiste

ICON_W = 35             # Breite des Symbolfelds links in der Vorlage (Symbol und Hintergrundfarbe wechseln je nach Gilde)
BAR_X = ICON_W - 3      # ab hier wird verglichen: heller Trennstrich (2 px), dann der schwarze Rand der Leiste
FILL_ROWS = slice(4, 9) # Zeilen direkt unter dem oberen Rahmen: Füllfarbe der Leiste, nie Text
FILL_COLS = slice(6, -6)


GRAY_BAR = "grau"        # if_counter: grau = die graue Leiste der eigenen Gilde suchen, ohne Vorlage
GRAY_NEUTRAL = 8        # max. Abstand R-G und G-B: neutrales Grau (Leisten anderer Gilden sind farbig)
GRAY_RANGE = (55, 200)  # Helligkeit der grauen Leiste (offen etwa 75, gefüllt 85-175)
GRAY_MIN_W = 180        # so breit ist die Leiste mindestens (100 %: etwa 257 px); graue Knöpfe sind schmaler
GRAY_H = (12, 40)       # Höhe der Leiste in px


def find_gray_bars(img):
    """Leisten der eigenen Gilde im Bild: neutral graue Fortschrittsbalken mit schwarzem Rand links
    und rechts. Die Leisten anderer Gilden sind farbig (grün, rot …) und fallen heraus.

    Gibt eine Liste von (x, y, w, h) zurück: der graue Innenteil ohne Rand.
    """
    s = img[:, :, :3].astype(int)
    b, g, r = s[..., 0], s[..., 1], s[..., 2]
    neutral = ((np.abs(r - g) < GRAY_NEUTRAL) & (np.abs(g - b) < GRAY_NEUTRAL)
               & (r > GRAY_RANGE[0]) & (r < GRAY_RANGE[1]))
    dark = s.sum(2) < 90
    h, w = neutral.shape
    rows = []
    for y in np.nonzero(neutral.sum(1) >= GRAY_MIN_W)[0]:
        d = np.diff(np.concatenate([[0], neutral[y].astype(np.int8), [0]]))
        starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
        i = int(np.argmax(ends - starts))
        x0, x1 = int(starts[i]), int(ends[i])
        # Zeilen ohne Text gehen von Rand zu Rand (schwarz, 1-2 px daneben)
        if (x1 - x0 >= GRAY_MIN_W and any(x0 - k >= 0 and dark[y, x0 - k] for k in (1, 2))
                and any(x1 - 1 + k < w and dark[y, x1 - 1 + k] for k in (1, 2))):
            rows.append((int(y), x0, x1))
    bars = []                   # je Leiste [x0, x1, erste Zeile, letzte Zeile, Anzahl Randzeilen]
    for y, x0, x1 in rows:      # Randzeilen über und unter dem Text gehören zur selben Leiste
        last = bars[-1] if bars else None
        if last and y - last[3] <= GRAY_H[1] and abs(x0 - last[0]) <= 3 and abs(x1 - last[1]) <= 3:
            last[3] = y
            last[4] += 1
        else:
            bars.append([x0, x1, y, y, 1])
    return [(x0, y0, x1 - x0, y1 - y0 + 1) for x0, x1, y0, y1, n in bars
            if n >= 4 and GRAY_H[0] <= y1 - y0 + 1 <= GRAY_H[1]]


def gray_bar_counter(img, box):
    """Ausschnitt mit dem Zähler einer grauen Leiste (find_gray_bars): von der Lücke nach dem
    Gildennamen bis zum schwarzen Rand rechts (der Zähler ragt manchmal über das Grau hinaus),
    nur die Textzeilen."""
    x, y, w, h = box
    bar = img[y:y + h, x:x + w, :3]
    right = x + w
    mid = img[y + h // 2, :, :3].astype(int).sum(1)
    while right < min(len(mid), x + w + 8) and mid[right] >= 90:
        right += 1
    m = int(h * 0.2)
    return img[y + m:y + h - m, x + counter_x_live(bar):right, :3]


def bar_part(template):
    """Die Vorlage ohne Gilden-Symbolfeld, ab dem hellen Trennstrich.

    Das Symbolfeld wechselt je nach Gilde Symbol und Farbe, und der Browser rundet seine Breite
    je nach Lage (bei 110 % Zoom mal 28, mal 29 px). Es wird darum gar nicht verglichen.
    """
    return template[:, BAR_X:]


def bar_frame(part):
    """Maske für die Teile einer Leiste (bar_part), die sich nie ändern: heller Rahmen oben/unten
    und heller Trennstrich links (der schwarze Rand dahinter ist je nach Rundung nicht überall schwarz).

    Der rechte Rand ist nicht bei allen Leisten gleich aufgebaut und wird nicht verglichen.
    Die Leiste selbst ist ein Fortschrittsbalken (hell = erreicht, dunkel = offen) und wird
    darum hier nicht verglichen, sondern danach mit bar_fill_ok geprüft.
    """
    mask = np.zeros(part.shape[:2], np.uint8)
    mask[:3] = 255
    mask[-3:] = 255
    mask[:, :2] = 255
    return mask


def height_variants(part):
    """Die Vorlage in ihrer Höhe sowie 1 px höher und niedriger (Zeile in der Mitte verdoppelt/entfernt);
    der Browser rundet die Höhe je nach Lage der Leiste."""
    mid = part.shape[0] // 2
    return {0: part,
            +1: np.insert(part, mid, part[mid], axis=0),
            -1: np.delete(part, mid, axis=0)}


def fill_profiles(part):
    """Die beiden Füllfarben der Leiste (gefüllt, offen) als Spaltenprofile aus der Vorlage (bar_part).

    Die Vorlage muss teilweise gefüllt sein, sonst fehlt eine der beiden Farben.
    """
    band = part[FILL_ROWS, FILL_COLS].astype(float)            # Zeilen x Spalten x BGR
    light = band.mean(axis=(0, 2))
    cut = (light.max() + light.min()) / 2
    if light.max() - light.min() < 25:
        raise ValueError("Leiste in der Vorlage nur in einer Farbe; bitte eine teilweise gefüllte Leiste aufnehmen")
    return [np.median(band[:, light >= cut], axis=1), np.median(band[:, light < cut], axis=1)]


def bar_fill_ok(profiles, crop, max_rms=15.0):
    """Hat jede Spalte der Leiste (bar_part) eine der beiden Füllfarben? Gibt den Anteil passender Spalten zurück."""
    band = crop[FILL_ROWS, FILL_COLS].astype(float)
    best = np.min([np.sqrt(((band - prof[:, None, :]) ** 2).mean(axis=(0, 2))) for prof in profiles], axis=0)
    return float((best < max_rms).mean())


def counter_x_live(crop):
    """Beginn des Zählers in der aktuell sichtbaren Leiste (nach der letzten Textlücke)."""
    try:
        return counter_layout(crop)[1]
    except ValueError:
        return max(6, crop.shape[1] - 110)


def counter_layout(template):
    """Teilt das Bild einer Leiste in festen Teil (Anker) und Zähler.

    Erwartet hellen Text auf dunklem Grund mit dem Zähler als letzter Textgruppe rechts,
    z. B. "[Symbol] Geisterschule        2/132". Gibt (anker_breite, zähler_x) zurück.
    """
    inner = template[6:-6, 4:-4].astype(int)
    b, g, r = inner[:, :, 0], inner[:, :, 1], inner[:, :, 2]
    bright = (r > 200) & (g > 190) & (b > 150) & (r - b < 80)
    cols = np.where(bright.sum(0) > 0)[0]
    if len(cols) == 0:
        raise ValueError("kein heller Text im Bild gefunden")
    groups = np.split(cols, np.where(np.diff(cols) > 8)[0] + 1)
    if len(groups) < 2:
        raise ValueError("Zähler nicht von der Beschriftung zu trennen (nur eine Textgruppe)")
    label_end = groups[-2][-1] + 4
    return label_end + 6, label_end + 8


# ---------------------------------------------------------------- Bedingungen wie "x >= total - 2"

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.FloorDiv: operator.floordiv,
        ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Lt: operator.lt, ast.LtE: operator.le,
        ast.Eq: operator.eq, ast.NotEq: operator.ne}
NAMES = {"x", "total"}


def compile_condition(text):
    """Prüft eine Bedingung wie 'x >= total - 2' und gibt eine Funktion (x, total) -> bool zurück."""
    tree = ast.parse(str(text), mode="eval").body
    if not isinstance(tree, ast.Compare):
        raise ValueError("Vergleich erwartet, z. B. x >= total - 2")

    def check(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
            return
        if isinstance(node, ast.Name) and node.id in NAMES:
            return
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            check(node.left), check(node.right)
            return
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            check(node.operand)
            return
        raise ValueError(f"nicht erlaubt: {ast.unparse(node)} (nur x, total, ganze Zahlen, + - * //)")

    check(tree.left)
    for op, right in zip(tree.ops, tree.comparators):
        if type(op) not in _OPS:
            raise ValueError(f"Vergleich nicht erlaubt: {ast.unparse(tree)}")
        check(right)

    def ev(node, env):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return env[node.id]
        if isinstance(node, ast.UnaryOp):
            return -ev(node.operand, env)
        return _OPS[type(node.op)](ev(node.left, env), ev(node.right, env))

    def condition(x, total):
        env = {"x": x, "total": total}
        left = ev(tree.left, env)
        for op, right in zip(tree.ops, tree.comparators):
            r = ev(right, env)
            if not _OPS[type(op)](left, r):
                return False
            left = r
        return True

    return condition
