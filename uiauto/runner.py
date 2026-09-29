"""Führt ein Szenario im Zielfenster aus."""
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

import cv2
import pyautogui

from . import config, report, screen, window
from .control import Aborted
from .human import Human, UserInterference, click_point, uniform
from .matcher import Matcher

log = logging.getLogger(__name__)


class StepFailed(Exception):
    def __init__(self, message, match_region=None):
        super().__init__(message)
        self.match_region = match_region


@dataclass
class StepResult:
    phase: str
    index: int
    description: str
    status: str = "skipped"
    duration: float = 0.0
    detail: str = ""
    screenshot: str = ""


@dataclass
class RunResult:
    scenario: str
    started: datetime
    status: str = "ok"
    message: str = ""
    duration: float = 0.0
    steps: list = field(default_factory=list)
    report_path: object = None


class Runner:
    def __init__(self, cfg, control, notify=lambda title, text: None):
        self.cfg = cfg
        self.control = control
        self.notify = notify
        self.matcher = Matcher(cfg["paths"]["images"], cfg["matching"]["grayscale"])
        self.hwnd = None

    # ------------------------------------------------------------ Hilfsfunktionen

    def _region(self):
        return window.rect(self.hwnd)

    def _activate(self):
        window.activate(self.hwnd)
        time.sleep(0.2)

    def _wait_image(self, name, timeout, threshold, present=True):
        region = self._region()
        end = time.perf_counter() + timeout
        while True:
            self.control.check()
            shot = screen.grab(region)
            m = self.matcher.find(name, region, threshold, shot=shot)
            if (m is not None) == present:
                return m
            if time.perf_counter() >= end:
                if present:
                    best = self.matcher.best_score(name, region, shot=shot)
                    raise StepFailed(f"Bild '{name}' nicht gefunden nach {timeout:.1f} s "
                                     f"(beste Übereinstimmung {best:.2f}, Schwelle {threshold:.2f})")
                raise StepFailed(f"Bild '{name}' ist nach {timeout:.1f} s immer noch sichtbar "
                                 f"(Übereinstimmung {m.score:.2f})", (m.x, m.y, m.w, m.h))
            self.control.sleep(self.cfg["matching"]["poll_interval"])
            region = self._region()

    def _execute(self, step, human, default_timeout):
        opts = step.options
        timeout = float(opts.get("timeout", default_timeout))
        threshold = float(opts.get("threshold", self.cfg["matching"]["threshold"]))
        a, v = step.action, step.value

        if a in ("click", "double_click", "right_click", "move"):
            m = self._wait_image(v, timeout, threshold)
            x, y = click_point(m, human.cfg, opts.get("offset"))
            size = min(m.w, m.h)
            if a == "move":
                human.move_to(x, y, size)
            else:
                human.click(x, y,
                            button="right" if a == "right_click" else "left",
                            clicks=2 if a == "double_click" else 1,
                            target_size=size)
            return f"Treffer {m.score:.2f}, geklickt bei ({x}, {y})"
        if a in ("wait_for", "expect"):
            m = self._wait_image(v, timeout, threshold)
            return f"Treffer {m.score:.2f} bei ({m.x}, {m.y})"
        if a == "expect_not":
            self._wait_image(v, timeout, threshold, present=False)
            return "nicht sichtbar"
        if a == "type":
            human.type_text(str(v))
            return f"{len(str(v))} Zeichen"
        if a == "press":
            human.press(str(v), int(opts.get("times", 1)))
            return ""
        if a == "scroll":
            human.scroll(v)
            return ""
        if a == "wait":
            seconds = uniform(v) if isinstance(v, list) else float(v)
            self.control.sleep(seconds)
            return f"{seconds:.2f} s"
        raise StepFailed(f"Unbekannte Aktion {a}")

    def _failure_screenshot(self, run_dir, name, mark=None):
        try:
            region = self._region()
            shot = screen.grab(region)
            if mark:
                x, y, w, h = mark
                cv2.rectangle(shot, (x - region[0], y - region[1]),
                              (x - region[0] + w, y - region[1] + h), (0, 0, 255), 2)
            screen.imwrite(run_dir / name, shot)
            return name
        except Exception:  # Screenshot darf den Bericht nicht verhindern
            log.exception("Fehler-Screenshot fehlgeschlagen")
            return ""

    # ------------------------------------------------------------ Ablauf

    def run(self, scenario):
        human_cfg = config.merged_human(self.cfg, scenario.human)
        human = Human(self.control, human_cfg)
        result = RunResult(scenario.name, datetime.now())
        safe_name = re.sub(r"[^\w\-]+", "_", scenario.name)
        run_dir = self.cfg["paths"]["reports"] / f"{result.started:%Y%m%d_%H%M%S}_{safe_name}"
        run_dir.mkdir(parents=True, exist_ok=True)
        t_start = time.perf_counter()

        planned = ([("Startzustand", i + 1, s) for i, s in enumerate(scenario.precondition)]
                   + [("Ablauf", i + 1, s) for i, s in enumerate(scenario.steps)])
        result.steps = [StepResult(phase, idx, s.describe()) for phase, idx, s in planned]

        def on_resume():
            human.forget_position()
            self._activate()

        self.control.reset()
        self.control.on_resume = on_resume
        log.info("Starte Szenario '%s'", scenario.name)

        try:
            self.hwnd = window.find(self.cfg["window"]["class"], self.cfg["window"]["title_contains"])
            log.info("Zielfenster: %s", window.title(self.hwnd))
            self._activate()
        except window.WindowError as e:
            result.status, result.message = "fail", str(e)
        else:
            for (phase, idx, step), sr in zip(planned, result.steps):
                is_pre = phase == "Startzustand"
                default_timeout = self.cfg["matching"]["precondition_timeout" if is_pre else "timeout"]
                t0 = time.perf_counter()
                try:
                    while True:
                        try:
                            if not is_pre:
                                human.think()
                            sr.detail = self._execute(step, human, default_timeout)
                            break
                        except UserInterference:
                            log.info("Mausbewegung durch Benutzer erkannt, pausiere")
                            self.control.pause("Maus wurde bewegt. Weiter mit Pause-Hotkey oder Tray-Menü.")
                            self.control.check()   # blockiert bis Weiter oder Stopp
                    sr.status = "ok"
                except StepFailed as e:
                    sr.status, sr.detail = "fail", str(e)
                    sr.screenshot = self._failure_screenshot(run_dir, f"fehler_{phase}_{idx}.png", e.match_region)
                    result.status = "fail"
                    result.message = ("Startzustand stimmt nicht: " if is_pre else f"Schritt {idx}: ") + str(e)
                except (Aborted, pyautogui.FailSafeException) as e:
                    sr.status = "aborted"
                    result.status = "aborted"
                    result.message = ("Not-Aus (Maus in Bildschirmecke)"
                                      if isinstance(e, pyautogui.FailSafeException) else "Vom Benutzer abgebrochen")
                except Exception as e:
                    log.exception("Unerwarteter Fehler")
                    sr.status, sr.detail = "fail", f"{type(e).__name__}: {e}"
                    result.status, result.message = "fail", sr.detail
                finally:
                    sr.duration = time.perf_counter() - t0
                    log.info("[%s %d] %s -> %s %s", phase, idx, sr.description, sr.status, sr.detail)
                if sr.status != "ok":
                    break

        self.control.on_resume = None
        result.duration = time.perf_counter() - t_start
        if result.status == "ok":
            result.message = f"Alle {len(scenario.steps)} Schritte erfolgreich."
        result.report_path = run_dir / "bericht.html"
        report.write(result, result.report_path)
        log.info("Szenario '%s' beendet: %s – %s", scenario.name, result.status, result.message)
        return result
