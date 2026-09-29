"""Szenarien (Sequenzen) aus YAML laden und prüfen."""
from dataclasses import dataclass, field
from pathlib import Path

import pyautogui
import yaml

# Aktion -> ob der Wert ein Bildname ist
ACTIONS = {
    "click": True,
    "double_click": True,
    "right_click": True,
    "move": True,
    "wait_for": True,
    "expect": True,
    "expect_not": True,
    "type": False,
    "press": False,
    "scroll": False,
    "wait": False,
}
OPTIONS = {"timeout", "threshold", "offset", "times", "note"}


class ScenarioError(Exception):
    pass


@dataclass
class Step:
    action: str
    value: object
    options: dict = field(default_factory=dict)

    def describe(self):
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
    precondition: list = field(default_factory=list)
    steps: list = field(default_factory=list)


def _parse_step(raw, where, images_dir):
    if not isinstance(raw, dict):
        raise ScenarioError(f"{where}: Schritt muss ein Eintrag 'aktion: wert' sein, nicht {raw!r}")
    actions = [k for k in raw if k in ACTIONS]
    unknown = [k for k in raw if k not in ACTIONS and k not in OPTIONS]
    if unknown:
        raise ScenarioError(f"{where}: unbekannt: {', '.join(unknown)}")
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
    if action == "wait" and not (isinstance(value, (int, float))
                                 or (isinstance(value, list) and len(value) == 2)):
        raise ScenarioError(f"{where}: wait erwartet Sekunden oder [min, max]")
    if action == "scroll" and not isinstance(value, int):
        raise ScenarioError(f"{where}: scroll erwartet ganze Zahl (Rasten, negativ = nach unten)")
    if "offset" in options and not (isinstance(options["offset"], list) and len(options["offset"]) == 2):
        raise ScenarioError(f"{where}: offset erwartet [dx, dy]")
    return Step(action, value, options)


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
        precondition=parse_list("precondition"),
        steps=parse_list("steps"),
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
