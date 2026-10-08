"""Globale Einstellungen aus config.yaml, mit Standardwerten."""
import copy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "window": {
        "class": "MozillaWindowClass",   # Firefox
        "title_contains": "",            # z. B. Titel der App, falls mehrere Fenster offen
    },
    "notifications": {
        "seconds": 2,                   # so lange bleiben Meldungen stehen; 0 = keine (nur im Log)
        "error_seconds": 6,             # Fehler bleiben länger stehen und kommen auch bei seconds: 0
    },
    "paths": {
        "scenarios": "scenarios",
        "images": "images",
        "reports": "reports",
        "logs": "logs",
    },
    "hotkeys": {
        "stop": "ctrl+alt+f12",
        "pause": "ctrl+alt+f11",
        "record": "ctrl+alt+f10",
    },
    "matching": {
        "threshold": 0.90,
        "grayscale": True,
        "timeout": 10.0,                # Sekunden, wie lange auf ein Bild gewartet wird
        "precondition_timeout": 2.0,
        "poll_interval": 0.25,
    },
    "human": {
        "think_time": [0.4, 1.2],       # Pause vor jedem Schritt (s)
        "move_speed": 1.0,              # 1 = normal, 2 = doppelt so schnell, 0.5 = halb so schnell
        "click_margin": 0.15,           # Randbereich des Bildes, der nie getroffen wird (Anteil)
        "click_spread": 0.35,           # Streuung um die Mitte (Anteil der halben Breite/Höhe)
        "offset_jitter": 3,             # Streuung in px, wenn mit offset geklickt wird
        "overshoot_chance": 0.25,       # Wahrscheinlichkeit, leicht übers Ziel hinauszuschießen
        "hover_time": [0.08, 0.25],     # Verweilen über dem Ziel vor dem Klick
        "click_hold": [0.05, 0.13],     # Dauer Maustaste gedrückt
        "double_click_gap": [0.08, 0.16],
        "typing_delay": [0.06, 0.20],   # Abstand zwischen Zeichen
        "key_hold": [0.03, 0.09],
        "typing_pause_chance": 0.05,    # gelegentliches kurzes Stocken beim Tippen
        "typing_pause": [0.25, 0.8],
        "scroll_delay": [0.04, 0.14],   # Abstand zwischen Mausrad-Rasten
        "interference_tolerance": 30,   # px; weicht die Maus mehr ab, hat der Benutzer sie bewegt (10 war zu knapp: 11 px Drift ohne Berührung)
        # click_here: schnelles Klicken an der Mausposition (schneller Mensch: etwa 7-9 Klicks/s)
        "rapid_click_hold": [0.04, 0.08],   # Maustaste gedrückt
        "rapid_click_gap": [0.05, 0.10],    # Pause bis zum nächsten Klick
        "rapid_jitter": 1,                  # px, um die die Maus gelegentlich verrutscht (0 = nie)
        "rapid_jitter_chance": 0.2,         # Anteil der Klicks, bei denen sie verrutscht
        "rapid_pause_chance": 0.03,         # gelegentliches kurzes Stocken
        "rapid_pause": [0.15, 0.4],
    },
}


def _merge(base, override):
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load(path=ROOT / "config.yaml"):
    cfg = copy.deepcopy(DEFAULTS)
    if Path(path).exists():
        with open(path, encoding="utf-8") as f:
            _merge(cfg, yaml.safe_load(f) or {})
    for key, rel in cfg["paths"].items():
        cfg["paths"][key] = (ROOT / rel).resolve()
        cfg["paths"][key].mkdir(parents=True, exist_ok=True)
    return cfg


def merged_human(cfg, overrides):
    return _merge(copy.deepcopy(cfg["human"]), overrides or {})
