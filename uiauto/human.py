"""Menschenähnliche Maus- und Tastatureingabe.

- Klickpunkt zufällig im Bild (Normalverteilung um die Mitte, Rand wird nie getroffen)
- Mausweg als leicht gekrümmte Bézier-Kurve mit glockenförmigem Geschwindigkeitsprofil
- Dauer nach Fitts' Gesetz: weite Wege / kleine Ziele dauern länger
- gelegentliches Überschießen mit kurzer Korrektur
- Verweilen vor dem Klick, variable Haltedauer der Tasten, unregelmäßiges Tipptempo
"""
import math
import random
import re
import time

import pyautogui
from pynput.keyboard import Controller as KeyController

pyautogui.FAILSAFE = True   # Maus in eine Bildschirmecke = sofortiger Abbruch
pyautogui.PAUSE = 0         # Timing steuern wir selbst

WHEEL_DELTA = 120


def uniform(rng):
    lo, hi = rng
    return random.uniform(lo, hi)


# ---------------------------------------------------------------- Berechnung (ohne Seiteneffekte)

_REL_OFFSET = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?)\s*([wh])\s*$", re.IGNORECASE)


def offset_px(value, w, h):
    """Ein offset-Wert in Pixeln: Zahl = Pixel, '1.5h' = 1,5 × Bildhöhe, '-0.5w' = halbe Bildbreite nach links.

    Wirft ValueError bei ungültiger Angabe.
    """
    if isinstance(value, bool):
        raise ValueError(value)
    if isinstance(value, (int, float)):
        return float(value)
    m = _REL_OFFSET.match(str(value))
    if not m:
        raise ValueError(value)
    return float(m.group(1)) * (w if m.group(2).lower() == "w" else h)


def click_point(match, human, offset=None):
    """Zufälliger Klickpunkt innerhalb des Treffers, bzw. relativ zu dessen Mitte bei offset."""
    if offset is not None:
        cx = match.x + match.w / 2 + offset_px(offset[0], match.w, match.h)
        cy = match.y + match.h / 2 + offset_px(offset[1], match.w, match.h)
        j = human["offset_jitter"]
        return round(cx + random.uniform(-j, j)), round(cy + random.uniform(-j, j))

    margin = human["click_margin"]
    half_w = match.w / 2 * (1 - 2 * margin)
    half_h = match.h / 2 * (1 - 2 * margin)
    cx, cy = match.center
    dx = max(-half_w, min(half_w, random.gauss(0, half_w * human["click_spread"])))
    dy = max(-half_h, min(half_h, random.gauss(0, half_h * human["click_spread"])))
    return round(cx + dx), round(cy + dy)


def movement_duration(distance, target_size, speed):
    """Fitts' Gesetz: T = a + b * log2(D / W + 1), mit etwas Zufall."""
    width = max(target_size, 8)
    t = 0.08 + 0.11 * math.log2(distance / width + 1)
    t *= random.uniform(0.85, 1.25)
    return max(0.08, min(1.6, t / max(speed, 0.05)))


def _min_jerk(tau):
    # Glockenförmiges Geschwindigkeitsprofil wie bei menschlichen Zielbewegungen
    return 10 * tau ** 3 - 15 * tau ** 4 + 6 * tau ** 5


def bezier_path(start, end, duration, hz=120):
    """Liste von (t_sekunden, x, y) entlang einer leicht gekrümmten Kurve."""
    (x0, y0), (x3, y3) = start, end
    dx, dy = x3 - x0, y3 - y0
    dist = math.hypot(dx, dy)
    if dist < 1:
        return [(0.0, x3, y3)]
    # Senkrechte zur Bewegungsrichtung für die Krümmung
    px, py = -dy / dist, dx / dist
    bend = random.uniform(0.05, 0.2) * dist * random.choice((-1, 1))
    bend = max(-120, min(120, bend))
    c1 = (x0 + dx * random.uniform(0.2, 0.4) + px * bend * random.uniform(0.6, 1.0),
          y0 + dy * random.uniform(0.2, 0.4) + py * bend * random.uniform(0.6, 1.0))
    c2 = (x0 + dx * random.uniform(0.6, 0.8) + px * bend * random.uniform(0.3, 0.8),
          y0 + dy * random.uniform(0.6, 0.8) + py * bend * random.uniform(0.3, 0.8))

    steps = max(8, int(duration * hz))
    points = []
    for i in range(1, steps + 1):
        s = _min_jerk(i / steps)
        u = 1 - s
        x = u ** 3 * x0 + 3 * u ** 2 * s * c1[0] + 3 * u * s ** 2 * c2[0] + s ** 3 * x3
        y = u ** 3 * y0 + 3 * u ** 2 * s * c1[1] + 3 * u * s ** 2 * c2[1] + s ** 3 * y3
        # leichtes Zittern unterwegs, an Start und Ziel null
        tremor = math.sin(math.pi * s) * 0.6
        x += random.uniform(-tremor, tremor)
        y += random.uniform(-tremor, tremor)
        points.append((duration * i / steps, x, y))
    points[-1] = (duration, x3, y3)
    return points


# ---------------------------------------------------------------- Ausführung

class UserInterference(Exception):
    """Der Benutzer hat die Maus während des Ablaufs bewegt."""


class Human:
    def __init__(self, control, human_cfg):
        self.control = control
        self.cfg = human_cfg
        self.keyboard = KeyController()
        self._expected = None       # wo die Maus sein müsste, wenn niemand eingreift

    def forget_position(self):
        self._expected = None

    def _check_interference(self):
        if self._expected is None:
            return
        x, y = pyautogui.position()
        dist = math.hypot(x - self._expected[0], y - self._expected[1])
        if dist > self.cfg["interference_tolerance"]:
            expected, self._expected = self._expected, None
            raise UserInterference(f"Maus bei ({x}, {y}) statt {tuple(expected)}, {dist:.0f} px daneben")

    def think(self, override=None):
        """Denkpause vor einem Schritt; override = Sekunden oder [min, max] nur für diesen Schritt."""
        rng = self.cfg["think_time"] if override is None else override
        self.control.sleep(uniform(rng) if isinstance(rng, list) else float(rng))

    def move_to(self, x, y, target_size=20):
        self.control.check()
        self._check_interference()
        start = pyautogui.position()
        dist = math.hypot(x - start[0], y - start[1])
        speed = self.cfg["move_speed"]

        if dist > 150 and random.random() < self.cfg["overshoot_chance"]:
            # übers Ziel hinaus, dann kurze Korrektur
            ux, uy = (x - start[0]) / dist, (y - start[1]) / dist
            over = random.uniform(4, 14)
            ox = x + ux * over + random.uniform(-4, 4)
            oy = y + uy * over + random.uniform(-4, 4)
            self._follow(bezier_path(start, (ox, oy), movement_duration(dist, target_size, speed)))
            self.control.sleep(random.uniform(0.04, 0.12))
            self._follow(bezier_path((ox, oy), (x, y), movement_duration(over, target_size, speed) * 0.8))
        else:
            self._follow(bezier_path(start, (x, y), movement_duration(dist, target_size, speed)))

    def _follow(self, path):
        t0 = time.perf_counter()
        for t, px, py in path:
            self.control.check()
            self._check_interference()
            wait = t0 + t - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            pos = (round(px), round(py))
            pyautogui.moveTo(*pos, _pause=False)
            self._expected = pos

    def click_here(self, anchor):
        """Ein schneller Klick an der Ausgangsposition anchor, mit minimalem Zittern wie bei einer Hand."""
        self.control.check()
        self._check_interference()
        j = self.cfg["rapid_jitter"]
        if j and random.random() < self.cfg["rapid_jitter_chance"]:
            pos = (anchor[0] + random.randint(-j, j), anchor[1] + random.randint(-j, j))
        else:
            pos = tuple(anchor)             # meist bleibt die Hand ruhig
        if pos != tuple(pyautogui.position()):
            pyautogui.moveTo(*pos, _pause=False)
        self._expected = pos
        pyautogui.mouseDown(_pause=False)
        time.sleep(uniform(self.cfg["rapid_click_hold"]))
        pyautogui.mouseUp(_pause=False)
        gap = uniform(self.cfg["rapid_click_gap"])
        if random.random() < self.cfg["rapid_pause_chance"]:
            gap += uniform(self.cfg["rapid_pause"])
        self.control.sleep(gap)

    def mouse_down(self):
        """Linke Taste an der aktuellen Stelle drücken und halten (nach kurzem Verweilen)."""
        self.control.sleep(uniform(self.cfg["hover_time"]))
        self._check_interference()
        pyautogui.mouseDown(_pause=False)

    def mouse_up(self):
        """Linke Taste loslassen, auch nach Not-Aus (Maus in der Ecke), damit sie nicht gedrückt bleibt."""
        failsafe, pyautogui.FAILSAFE = pyautogui.FAILSAFE, False
        try:
            pyautogui.mouseUp(_pause=False)
        finally:
            pyautogui.FAILSAFE = failsafe

    def click(self, x, y, button="left", clicks=1, target_size=20):
        self.move_to(x, y, target_size)
        self.control.sleep(uniform(self.cfg["hover_time"]))
        for i in range(clicks):
            self._check_interference()
            pyautogui.mouseDown(button=button, _pause=False)
            time.sleep(uniform(self.cfg["click_hold"]))
            pyautogui.mouseUp(button=button, _pause=False)
            if i < clicks - 1:
                time.sleep(uniform(self.cfg["double_click_gap"]))

    def scroll(self, notches):
        direction = 1 if notches > 0 else -1
        for _ in range(abs(int(notches))):
            self.control.check()
            self._check_interference()
            pyautogui.scroll(direction * WHEEL_DELTA, _pause=False)
            self.control.sleep(uniform(self.cfg["scroll_delay"]))

    def type_text(self, text):
        # pynput tippt Unicode (ä, ö, ü, ß, @, €) unabhängig vom Tastaturlayout
        previous = ""
        for ch in text:
            self.control.check()
            self.keyboard.press(ch)
            time.sleep(uniform(self.cfg["key_hold"]))
            self.keyboard.release(ch)
            delay = uniform(self.cfg["typing_delay"])
            if previous == " " or ch in ".,;:!?":
                delay *= random.uniform(1.2, 1.8)
            if random.random() < self.cfg["typing_pause_chance"]:
                delay += uniform(self.cfg["typing_pause"])
            self.control.sleep(delay)
            previous = ch

    def press(self, keys, times=1):
        """keys z. B. 'enter' oder 'ctrl+shift+s' (Namen wie in pyautogui.KEYBOARD_KEYS)."""
        parts = [k.strip().lower() for k in keys.split("+")]
        for _ in range(times):
            self.control.check()
            for k in parts:
                pyautogui.keyDown(k, _pause=False)
                time.sleep(uniform(self.cfg["key_hold"]))
            for k in reversed(parts):
                pyautogui.keyUp(k, _pause=False)
                time.sleep(uniform(self.cfg["key_hold"]) / 2)
            self.control.sleep(uniform(self.cfg["typing_delay"]))
