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


def read_fraction(img):
    """Liest 'x/total' aus einem BGR-Ausschnitt.

    Gibt ((x, total), stimmen, alle_lesungen) zurück; (x, total) ist None, wenn sich keine
    Mehrheit findet.
    """
    async def all_variants():
        texts = []
        for factor, interp in VARIANTS:
            big = cv2.resize(img, None, fx=factor, fy=factor, interpolation=interp)
            big = cv2.copyMakeBorder(big, 30, 30, 30, 30, cv2.BORDER_REPLICATE)
            texts.append(await _recognize(big))
        return texts

    texts = asyncio.run(all_variants())
    votes = Counter()
    for t in texts:
        m = _FRACTION.search(re.sub(r"\s+", "", t))
        if m:
            votes[(int(m.group(1)), int(m.group(2)))] += 1
    if votes:
        value, n = votes.most_common(1)[0]
        if n >= MIN_VOTES and value[0] <= value[1]:
            return value, n, texts
    return None, max(votes.values(), default=0), texts


# ---------------------------------------------------------------- Zähler in einer Leiste

ICON_W = 35             # Breite des Symbolfelds links (Symbol wechselt, Hintergrund nicht)
FILL_ROWS = slice(4, 9) # Zeilen direkt unter dem oberen Rahmen: Füllfarbe der Leiste, nie Text


def bar_frame(template):
    """Maske für die Teile einer Leiste, die sich nie ändern: Rahmen oben/unten und Rand des Symbolfelds.

    Die Leiste selbst ist ein Fortschrittsbalken (hell = erreicht, dunkel = offen) und wird
    darum hier nicht verglichen, sondern danach mit bar_fill_ok geprüft.
    """
    mask = np.zeros(template.shape[:2], np.uint8)
    mask[:3] = 255
    mask[-3:] = 255
    mask[3:7, 2:ICON_W - 3] = 255
    mask[-7:-3, 2:ICON_W - 3] = 255
    return mask


def height_variants(template):
    """Die Vorlage in ihrer Höhe sowie 1 px niedriger und höher (Zeile in der Mitte entfernt/verdoppelt)."""
    mid = template.shape[0] // 2
    return {0: template,
            -1: np.delete(template, mid, axis=0),
            +1: np.insert(template, mid, template[mid], axis=0)}


def fill_profiles(template):
    """Die beiden Füllfarben der Leiste (gefüllt, offen) als Spaltenprofile aus der Vorlage.

    Die Vorlage muss teilweise gefüllt sein, sonst fehlt eine der beiden Farben.
    """
    band = template[FILL_ROWS, ICON_W + 3:-6].astype(float)            # Zeilen x Spalten x BGR
    light = band.mean(axis=(0, 2))
    cut = (light.max() + light.min()) / 2
    if light.max() - light.min() < 25:
        raise ValueError("Leiste in der Vorlage nur in einer Farbe; bitte eine teilweise gefüllte Leiste aufnehmen")
    return [np.median(band[:, light >= cut], axis=1), np.median(band[:, light < cut], axis=1)]


def bar_fill_ok(profiles, crop, max_rms=15.0, min_share=0.9):
    """Hat jede Spalte der Leiste eine der beiden Füllfarben? Gibt den Anteil passender Spalten zurück."""
    band = crop[FILL_ROWS, ICON_W + 3:-6].astype(float)
    best = np.min([np.sqrt(((band - prof[:, None, :]) ** 2).mean(axis=(0, 2))) for prof in profiles], axis=0)
    return float((best < max_rms).mean())


def counter_x_live(crop):
    """Beginn des Zählers in der aktuell sichtbaren Leiste (nach der letzten Textlücke)."""
    try:
        return counter_layout(crop)[1]
    except ValueError:
        return max(ICON_W, crop.shape[1] - 110)


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
