"""Szenarien (Sequenzen) aus YAML laden und prüfen."""
import re
from dataclasses import dataclass, field
from pathlib import Path

import pyautogui
import yaml

from . import negotiation, ocr, screen
from .config import DEFAULTS
from .human import offset_px

# Aktion -> ob der Wert ein Bildname ist
ACTIONS = {
    "click": True,
    "double_click": True,
    "right_click": True,
    "move": True,
    "drag_over": True,      # alle Treffer mit gedrückter linker Maustaste abfahren, bis keiner mehr zu sehen ist
    "wait_for": True,
    "expect": True,
    "expect_not": True,
    "if_seen": True,        # führt 'then' nur aus, wenn das Bild erscheint
    "if_counter": False,    # Leiste (Bild oder Liste) finden, "x/total" rechts lesen; 'then', wenn 'when' zutrifft
    "replace_damaged": False,   # {army: bild, pool: bild}: beschädigte Einheiten gegen gesunde gleicher Art tauschen
    "negotiate": False,     # {suggestions, menu, pay, success}: Vorschläge der Tabelle übernehmen
    "first_seen": False,    # Liste von {if: bild, then: [...]}: das zuerst erscheinende Bild gewinnt
    "type": False,
    "press": False,
    "scroll": False,
    "wait": False,
    "end": False,           # beendet den Ablauf sofort erfolgreich; Wert = optionaler Grund
    "click_here": False,    # so oft schnell dort klicken, wo die Maus steht (0 = bis Stopp)
    "repeat": False,        # verschachtelte Schleife: Wert wie repeat der Sequenz, Schritte unter 'steps'
}
OPTIONS = {"timeout", "threshold", "offset", "times", "note", "then", "grayscale", "skip_if", "steps", "think",
           "when", "if_missing", "check_end"}


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
        elif self.action == "if_counter":
            text = "if_counter: " + " | ".join(self.value)
        elif self.action == "replace_damaged":
            text = f"replace_damaged: {self.value['army']} ← {self.value['pool']}"
        elif self.action == "negotiate":
            text = f"negotiate: {self.value['suggestions']}"
        elif self.action == "drag_over":
            text = "drag_over: " + " | ".join(self.value)
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
    params: dict = field(default_factory=dict)     # name -> {"options": [...], "default": wert}; Werte als $name


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

    if action == "drag_over":   # ein Bild oder eine Liste von Varianten desselben Symbols
        value = [str(v) for v in value] if isinstance(value, list) else [str(value)]
        for img in value:
            if not (images_dir / img).exists():
                raise ScenarioError(f"{where}: Bild nicht gefunden: {images_dir / img}")
    elif ACTIONS[action] and not (images_dir / str(value)).exists():
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
    if action == "click_here" and not (_is_param_ref(value)
                                       or isinstance(value, int) and not isinstance(value, bool) and value >= 0):
        raise ScenarioError(f"{where}: click_here erwartet die Anzahl Klicks (0 = bis Stopp) oder $einstellung")
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
    has_image = ACTIONS[action] or action in ("first_seen", "if_counter")
    if "grayscale" in options and not (has_image and isinstance(options["grayscale"], bool)):
        raise ScenarioError(f"{where}: grayscale erwartet true/false und gilt nur für Aktionen mit Bild")
    if action == "if_counter":
        try:
            ocr.compile_condition(options.get("when", ""))
        except (ValueError, SyntaxError) as e:
            raise ScenarioError(f"{where}: when erwartet eine Bedingung wie 'x >= total - 2' ({e})") from None
        value = [str(v) for v in value] if isinstance(value, list) else [str(value)]
        for img in value:
            if not (images_dir / img).exists():
                raise ScenarioError(f"{where}: Bild nicht gefunden: {images_dir / img}")
            try:
                ocr.fill_profiles(screen.imread(images_dir / img))
            except ValueError as e:
                raise ScenarioError(f"{where}: {img}: {e}") from None
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
    elif action == "replace_damaged":
        if not isinstance(value, dict) or not {"army", "pool"} <= set(value) <= {"army", "pool", "empty"}:
            raise ScenarioError(f"{where}: replace_damaged erwartet 'army: bild.png', 'pool: bild.png' "
                                f"und optional 'empty: bild.png' (leeres Feld)")
        for img in value.values():
            if not (images_dir / str(img)).exists():
                raise ScenarioError(f"{where}: Bild nicht gefunden: {images_dir / str(img)}")
        value = {k: str(v) for k, v in value.items()}
        if "if_missing" in options:
            if not isinstance(options["if_missing"], list) or not options["if_missing"]:
                raise ScenarioError(f"{where}: if_missing erwartet eine Liste von Schritten")
            options["if_missing"] = [_parse_step(r, f"{where} / if_missing #{j + 1}", images_dir)
                                     for j, r in enumerate(options["if_missing"])]
    elif action == "negotiate":
        keys = {"suggestions", "menu", "pay", "success"}
        if not isinstance(value, dict) or set(value) != keys:
            raise ScenarioError(f"{where}: negotiate erwartet die Bilder {', '.join(sorted(keys))}")
        for img in value.values():
            if not (images_dir / str(img)).exists():
                raise ScenarioError(f"{where}: Bild nicht gefunden: {images_dir / str(img)}")
        value = {k: str(v) for k, v in value.items()}
        try:
            negotiation.header_rows(screen.imread(images_dir / value["suggestions"]))
        except ValueError as e:
            raise ScenarioError(f"{where}: {e}") from None
    if action != "replace_damaged" and "if_missing" in options:
        raise ScenarioError(f"{where}: 'if_missing' gibt es nur bei replace_damaged")
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
    if action not in ("click", "double_click", "right_click", "move", "wait_for", "expect") and "check_end" in options:
        raise ScenarioError(f"{where}: 'check_end' gibt es nur bei click, move, wait_for und expect")
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
    if not _is_param_ref(max_rounds) and (not isinstance(max_rounds, int) or isinstance(max_rounds, bool)
                                          or max_rounds < 0):
        raise ScenarioError(f"{where}: max erwartet eine ganze Zahl >= 0 (0 = unbegrenzt) oder $einstellung")
    if not isinstance(raw.get("grayscale", True), bool):
        raise ScenarioError(f"{where}: grayscale erwartet true/false")
    return {"while": images, "until": until, "grayscale": raw.get("grayscale"),
            "timeout": float(raw.get("timeout", 5)),
            "threshold": raw.get("threshold"), "max": max_rounds}


def _is_param_ref(value):
    return isinstance(value, str) and re.fullmatch(r"\$\w+", value) is not None


def _parse_params(raw, where):
    """params: {name: [optionen]} oder {name: {options: [...], default: wert}}; Vorgabe sonst die erste Option."""
    params = {}
    for name, spec in (raw or {}).items():
        if isinstance(spec, list):
            spec = {"options": spec}
        if not isinstance(spec, dict) or not isinstance(spec.get("options"), list) or not spec["options"]:
            raise ScenarioError(f"{where} / {name}: erwartet eine Liste von Möglichkeiten, z. B. [5, 10, 25]")
        default = spec.get("default", spec["options"][0])
        if default not in spec["options"]:
            raise ScenarioError(f"{where} / {name}: Vorgabe {default!r} ist keine der Möglichkeiten")
        params[str(name)] = {"options": spec["options"], "default": default}
    return params


def _check_count_ref(ref, params, where, what):
    """$name einer Anzahl (click_here, repeat/max): muss definiert sein und nur ganze Zahlen >= 0 enthalten."""
    name = ref[1:]
    if name not in params:
        raise ScenarioError(f"{where}: {ref} ist unter params nicht definiert")
    if not all(isinstance(o, int) and not isinstance(o, bool) and o >= 0 for o in params[name]["options"]):
        raise ScenarioError(f"{where}: params/{name} muss für {what} ganze Zahlen >= 0 enthalten")


def _check_param_refs(steps, params, where):
    """Jedes $name muss in params stehen; bei click_here und repeat/max müssen alle Möglichkeiten passende Zahlen sein."""
    for st in steps:
        if st.action == "repeat" and _is_param_ref(st.value["max"]):
            _check_count_ref(st.value["max"], params, where, "repeat/max")
        if st.action == "click_here" and _is_param_ref(st.value):
            _check_count_ref(st.value, params, where, "click_here")
        elif _is_param_ref(st.value) and st.value[1:] not in params:
            raise ScenarioError(f"{where}: {st.value} ist unter params nicht definiert")
        for key in ("then", "if_missing", "steps"):
            if isinstance(st.options.get(key), list):
                _check_param_refs(st.options[key], params, where)
        if st.action == "first_seen":
            for case in st.value:
                _check_param_refs(case["then"], params, where)


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
        params=_parse_params(data.get("params"), f"{path.name} / params"),
    )
    _check_param_refs(scenario.precondition + scenario.steps, scenario.params, path.name)
    if scenario.repeat and _is_param_ref(scenario.repeat["max"]):
        _check_count_ref(scenario.repeat["max"], scenario.params, path.name, "repeat/max")
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
