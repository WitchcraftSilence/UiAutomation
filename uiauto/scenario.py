"""Szenarien (Sequenzen) aus YAML laden und prüfen."""
from dataclasses import dataclass, field
from pathlib import Path

import pyautogui
import yaml

from . import ocr, screen
from .config import DEFAULTS
from .human import offset_px

# Aktion -> ob der Wert ein Bildname ist
ACTIONS = {
    "click": True,
    "double_click": True,
    "right_click": True,
    "move": True,
    "wait_for": True,
    "expect": True,
    "expect_not": True,
    "if_seen": True,        # führt 'then' nur aus, wenn das Bild erscheint
    "if_counter": True,     # liest "x/total" rechts in der Leiste; 'then' nur, wenn 'when' zutrifft
    "first_seen": False,    # Liste von {if: bild, then: [...]}: das zuerst erscheinende Bild gewinnt
    "type": False,
    "press": False,
    "scroll": False,
    "wait": False,
    "end": False,           # beendet den Ablauf sofort erfolgreich; Wert = optionaler Grund
    "repeat": False,        # verschachtelte Schleife: Wert wie repeat der Sequenz, Schritte unter 'steps'
}
OPTIONS = {"timeout", "threshold", "offset", "times", "note", "then", "grayscale", "skip_if", "steps", "think",
           "when"}


class ScenarioError(Exception):
    pass


@dataclass
class Step:
    action: str
    value: object
    options: dict = field(default_factory=dict)

    def describe(self):
        if self.action == "end" and self.value is None:
            text = "end"
        elif self.action == "repeat":
            text = "repeat while: " + " | ".join(self.value["while"])
            if self.value["until"]:
                text += f" until: {self.value['until']}"
        elif self.action == "first_seen":
            text = "first_seen: " + " | ".join(c["if"] for c in self.value)
        else:
            text = f"{self.action}: {self.value}"
        if self.options.get("note"):
            text += f"  ({self.options['note']})"
        return text


@dataclass
class Scenario:
    name: str
    path: Path
    hotkey: str = ""
    human: dict = field(default_factory=dict)
    matching: dict = field(default_factory=dict)   # überschreibt matching aus config.yaml
    precondition: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    repeat: dict = None     # {"while", "until", "grayscale", "timeout", "threshold", "max"}; None = einmal


def _parse_step(raw, where, images_dir):
    if raw == "end":            # Kurzform "- end"
        raw = {"end": None}
    if not isinstance(raw, dict):
        raise ScenarioError(f"{where}: Schritt muss ein Eintrag 'aktion: wert' sein, nicht {raw!r}")
    actions = [k for k in raw if k in ACTIONS]
    unknown = [k for k in raw if k not in ACTIONS and k not in OPTIONS]
    if unknown:
        raise ScenarioError(f"{where}: unbekannt: {', '.join(unknown)}")
    if not actions and raw:
        raise ScenarioError(f"{where}: keine Aktion, nur {', '.join(raw)}. Gehört das zum Schritt davor oder "
                            f"danach? Dann ohne '-' unter diesen Schritt einrücken.")
    if len(actions) != 1:
        raise ScenarioError(f"{where}: genau eine Aktion erwartet, gefunden: {actions or 'keine'}")
    action = actions[0]
    value = raw[action]
    options = {k: v for k, v in raw.items() if k in OPTIONS}

    if ACTIONS[action] and not (images_dir / str(value)).exists():
        raise ScenarioError(f"{where}: Bild nicht gefunden: {images_dir / str(value)}")
    if action == "press":
        for key in str(value).lower().split("+"):
            if key.strip() not in pyautogui.KEYBOARD_KEYS:
                raise ScenarioError(f"{where}: unbekannte Taste '{key}'")
    if "think" in options:
        t = options["think"]
        ok = (isinstance(t, (int, float)) and not isinstance(t, bool) and t >= 0) or (
            isinstance(t, list) and len(t) == 2 and all(isinstance(x, (int, float)) and x >= 0 for x in t)
            and t[0] <= t[1])
        if not ok:
            raise ScenarioError(f"{where}: think erwartet Sekunden oder [min, max], z. B. think: [0.05, 0.12]")
        if action == "repeat":
            raise ScenarioError(f"{where}: think gilt für einzelne Schritte, nicht für repeat")
    if action == "wait" and not (isinstance(value, (int, float))
                                 or (isinstance(value, list) and len(value) == 2)):
        raise ScenarioError(f"{where}: wait erwartet Sekunden oder [min, max]")
    if action == "scroll" and not isinstance(value, int):
        raise ScenarioError(f"{where}: scroll erwartet ganze Zahl (Rasten, negativ = nach unten)")
    if "offset" in options:
        off = options["offset"]
        try:
            if not (isinstance(off, list) and len(off) == 2):
                raise ValueError(off)
            for v in off:
                offset_px(v, 1, 1)
        except ValueError:
            raise ScenarioError(f"{where}: offset erwartet [dx, dy] in Pixeln oder relativ wie [0, 1.5h]") from None
    has_image = ACTIONS[action] or action == "first_seen"
    if "grayscale" in options and not (has_image and isinstance(options["grayscale"], bool)):
        raise ScenarioError(f"{where}: grayscale erwartet true/false und gilt nur für Aktionen mit Bild")
    if action == "if_counter":
        try:
            ocr.compile_condition(options.get("when", ""))
        except (ValueError, SyntaxError) as e:
            raise ScenarioError(f"{where}: when erwartet eine Bedingung wie 'x >= total - 2' ({e})") from None
        try:
            ocr.counter_layout(screen.imread(images_dir / str(value)))
        except ValueError as e:
            raise ScenarioError(f"{where}: im Bild '{value}' keinen Zähler gefunden: {e}") from None
    elif "when" in options:
        raise ScenarioError(f"{where}: 'when' gibt es nur bei if_counter")
    if action in ("if_seen", "if_counter"):
        then = options.get("then")
        if not isinstance(then, list) or not then:
            raise ScenarioError(f"{where}: {action} erwartet eine Liste von Schritten unter 'then'")
        options["then"] = [_parse_step(r, f"{where} / then #{j + 1}", images_dir)
                           for j, r in enumerate(then)]
        if "skip_if" in options and not (images_dir / str(options["skip_if"])).exists():
            raise ScenarioError(f"{where}: Bild nicht gefunden: {images_dir / str(options['skip_if'])}")
    elif action == "first_seen":
        value = _parse_cases(value, where, images_dir)
    elif action == "repeat":
        value = _parse_repeat(value, f"{where} / repeat", images_dir)
        steps = options.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ScenarioError(f"{where}: repeat-Schritt erwartet eine Liste von Schritten unter 'steps' "
                                f"(auf gleicher Höhe wie 'repeat', nicht darunter eingerückt)")
        options["steps"] = [_parse_step(r, f"{where} / steps #{j + 1}", images_dir)
                            for j, r in enumerate(steps)]
    if action != "repeat" and "steps" in options:
        raise ScenarioError(f"{where}: 'steps' gibt es nur beim repeat-Schritt")
    if action not in ("if_seen", "if_counter") and "then" in options:
        raise ScenarioError(f"{where}: 'then' gibt es nur bei if_seen und if_counter")
    if action != "if_seen" and "skip_if" in options:
        raise ScenarioError(f"{where}: 'skip_if' gibt es nur bei if_seen")
    return Step(action, value, options)


def _parse_cases(raw, where, images_dir):
    """first_seen: [{if: bild, then: [schritte], grayscale: bool?}, ...]"""
    if not isinstance(raw, list) or len(raw) < 2:
        raise ScenarioError(f"{where}: first_seen erwartet mindestens zwei Einträge '- if: bild.png' mit 'then:'")
    cases = []
    for k, case in enumerate(raw):
        w = f"{where} / Fall #{k + 1}"
        if not isinstance(case, dict) or "if" not in case:
            raise ScenarioError(f"{w}: erwartet 'if: bild.png' und 'then:'")
        unknown = set(case) - {"if", "then", "grayscale"}
        if unknown:
            raise ScenarioError(f"{w}: unbekannt: {', '.join(sorted(unknown))}")
        image = str(case["if"])
        if not (images_dir / image).exists():
            raise ScenarioError(f"{w}: Bild nicht gefunden: {images_dir / image}")
        if not isinstance(case.get("grayscale", True), bool):
            raise ScenarioError(f"{w}: grayscale erwartet true/false")
        then = case.get("then") or []
        if not isinstance(then, list):
            raise ScenarioError(f"{w}: then erwartet eine Liste von Schritten")
        cases.append({"if": image, "grayscale": case.get("grayscale"),
                      "then": [_parse_step(r, f"{w} / then #{j + 1}", images_dir) for j, r in enumerate(then)]})
    return cases


def _parse_matching(raw, where):
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise ScenarioError(f"{where}: erwartet Einträge wie 'grayscale: false'")
    unknown = set(raw) - set(DEFAULTS["matching"])
    if unknown:
        raise ScenarioError(f"{where}: unbekannt: {', '.join(sorted(unknown))}")
    return raw


def _parse_repeat(raw, where, images_dir):
    if raw is None:
        return None
    if not isinstance(raw, dict) or "while" not in raw:
        raise ScenarioError(f"{where}: repeat erwartet mindestens 'while: bild.png'")
    unknown = set(raw) - {"while", "until", "timeout", "threshold", "max", "grayscale"}
    if "steps" in unknown:
        raise ScenarioError(f"{where}: 'steps' ist unter 'repeat' eingerückt. Beim repeat-Schritt gehört "
                            f"'steps:' auf dieselbe Höhe wie 'repeat:'")
    if unknown:
        raise ScenarioError(f"{where}: unbekannt: {', '.join(sorted(unknown))}")
    # while: ein Bild oder eine Liste; die Runde startet, wenn eines davon zu sehen ist
    images = raw["while"] if isinstance(raw["while"], list) else [raw["while"]]
    images = [str(i) for i in images]
    if not images:
        raise ScenarioError(f"{where}: while erwartet ein Bild oder eine Liste von Bildern")
    until = str(raw["until"]) if raw.get("until") else None
    for img in images + ([until] if until else []):
        if not (images_dir / img).exists():
            raise ScenarioError(f"{where}: Bild nicht gefunden: {images_dir / img}")
    max_rounds = raw.get("max", 0)
    if not isinstance(max_rounds, int) or max_rounds < 0:
        raise ScenarioError(f"{where}: max erwartet eine ganze Zahl >= 0 (0 = unbegrenzt)")
    if not isinstance(raw.get("grayscale", True), bool):
        raise ScenarioError(f"{where}: grayscale erwartet true/false")
    return {"while": images, "until": until, "grayscale": raw.get("grayscale"),
            "timeout": float(raw.get("timeout", 5)),
            "threshold": raw.get("threshold"), "max": max_rounds}


def load(path, images_dir):
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    name = data.get("name", path.stem)

    def parse_list(key):
        items = data.get(key) or []
        if isinstance(items, dict):
            items = [items]
        return [_parse_step(raw, f"{path.name} / {key} #{i + 1}", images_dir)
                for i, raw in enumerate(items)]

    scenario = Scenario(
        name=name,
        path=path,
        hotkey=str(data.get("hotkey", "") or ""),
        human=data.get("human") or {},
        matching=_parse_matching(data.get("matching"), f"{path.name} / matching"),
        precondition=parse_list("precondition"),
        steps=parse_list("steps"),
        repeat=_parse_repeat(data.get("repeat"), f"{path.name} / repeat", images_dir),
    )
    if not scenario.steps:
        raise ScenarioError(f"{path.name}: keine Schritte (steps) definiert")
    return scenario


def load_all(scenarios_dir, images_dir):
    """Gibt (szenarien, fehlermeldungen) zurück; fehlerhafte Dateien werden übersprungen."""
    scenarios, errors = [], []
    for path in sorted(Path(scenarios_dir).glob("*.y*ml")):
        try:
            scenarios.append(load(path, images_dir))
        except (ScenarioError, yaml.YAMLError, OSError) as e:
            errors.append(str(e))
    return scenarios, errors
