"""Führt ein Szenario im Zielfenster aus."""
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

import cv2
import pyautogui

from . import army, config, ocr, report, screen, window
from .control import Aborted
from .human import Human, UserInterference, click_point, uniform
from .matcher import Match, Matcher

log = logging.getLogger(__name__)


class StepFailed(Exception):
    def __init__(self, message, match_region=None):
        super().__init__(message)
        self.match_region = match_region


class EndReached(Exception):
    """Endbild (repeat/until) erschienen oder end-Schritt erreicht.

    owner = Kennung der Schleife, deren until-Bild erschienen ist; None beim end-Schritt.
    """

    def __init__(self, reason, owner=None):
        super().__init__(reason)
        self.owner = owner


class NotSeen(Exception):
    """if_seen: Bild ist nicht erschienen, die then-Schritte entfallen."""

    def __init__(self, reason="nicht erschienen"):
        super().__init__(reason)
        self.reason = reason


@dataclass
class StepResult:
    phase: str
    index: int
    description: str
    status: str = "skipped"
    duration: float = 0.0
    detail: str = ""
    screenshot: str = ""
    then: list = None       # if_seen/first_seen: danach auszuführende Schritte


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
        self.matching = cfg["matching"]     # je Ablauf ggf. durch das Szenario überschrieben
        self.matcher = Matcher(cfg["paths"]["images"], self.matching["grayscale"])
        self.hwnd = None
        self._ends = []         # (schleife, find()-Argumente) je until-Bild, bei jeder Bildsuche mitgeprüft

    # ------------------------------------------------------------ Hilfsfunktionen

    def _region(self):
        return window.rect(self.hwnd)

    def _activate(self):
        window.activate(self.hwnd)
        time.sleep(0.2)

    def _wait_image(self, name, timeout, threshold, present=True, grayscale=None, skip_if=None):
        """Wartet, bis das Bild erscheint (bzw. bei present=False verschwindet).

        skip_if: zweites Bild; ist es zu sehen (und name nicht), wird NotSeen geworfen.
        """
        region = self._region()
        end = time.perf_counter() + timeout
        while True:
            self.control.check()
            shot = screen.grab(region)
            for owner, end_args in self._ends:
                if self.matcher.find(region=region, shot=shot, **end_args):
                    raise EndReached(f"Endbild '{end_args['name']}' erkannt", owner)
            m = self.matcher.find(name, region, threshold, shot=shot, grayscale=grayscale)
            if (m is not None) == present:
                return m
            if skip_if and self.matcher.find(skip_if, region, threshold, shot=shot):
                raise NotSeen(f"nicht erschienen, stattdessen '{skip_if}' sichtbar")
            if time.perf_counter() >= end:
                if present:
                    best = self.matcher.best_score(name, region, shot=shot, grayscale=grayscale)
                    raise StepFailed(f"Bild '{name}' nicht gefunden nach {timeout:.1f} s "
                                     f"(beste Übereinstimmung {best:.2f}, Schwelle {threshold:.2f})")
                raise StepFailed(f"Bild '{name}' ist nach {timeout:.1f} s immer noch sichtbar "
                                 f"(Übereinstimmung {m.score:.2f})", (m.x, m.y, m.w, m.h))
            self.control.sleep(self.matching["poll_interval"])
            region = self._region()

    def _wait_first(self, cases, timeout, threshold, grayscale=None):
        """Wartet, bis eines der Bilder erscheint. Gibt (fall, treffer) zurück; bei Gleichstand gewinnt der erste Fall."""
        region = self._region()
        end = time.perf_counter() + timeout
        while True:
            self.control.check()
            shot = screen.grab(region)
            for owner, end_args in self._ends:
                if self.matcher.find(region=region, shot=shot, **end_args):
                    raise EndReached(f"Endbild '{end_args['name']}' erkannt", owner)
            for case in cases:
                gs = grayscale if case["grayscale"] is None else case["grayscale"]
                m = self.matcher.find(case["if"], region, threshold, shot=shot, grayscale=gs)
                if m:
                    return case, m
            if time.perf_counter() >= end:
                scores = ", ".join(
                    f"'{c['if']}' {self.matcher.best_score(c['if'], region, shot=shot, grayscale=grayscale if c['grayscale'] is None else c['grayscale']):.2f}"
                    for c in cases)
                raise StepFailed(f"Keines der Bilder erschienen nach {timeout:.1f} s "
                                 f"(beste Übereinstimmungen: {scores}; Schwelle {threshold:.2f})")
            self.control.sleep(self.matching["poll_interval"])
            region = self._region()

    def _execute(self, step, human, default_timeout):
        opts = step.options
        timeout = float(opts.get("timeout", default_timeout))
        threshold = float(opts.get("threshold", self.matching["threshold"]))
        gs = opts.get("grayscale")
        a, v = step.action, step.value

        if a in ("click", "double_click", "right_click", "move"):
            m = self._wait_image(v, timeout, threshold, grayscale=gs)
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
            m = self._wait_image(v, timeout, threshold, grayscale=gs)
            return f"Treffer {m.score:.2f} bei ({m.x}, {m.y})"
        if a == "if_seen":
            try:
                m = self._wait_image(v, timeout, threshold, grayscale=gs, skip_if=opts.get("skip_if"))
            except StepFailed:
                raise NotSeen() from None
            return f"erschienen (Treffer {m.score:.2f})", opts["then"]
        if a == "if_counter":
            m, bar = self._find_bar(v, timeout, threshold)
            cx = ocr.counter_x_live(bar)
            crop = bar[8:-7, cx:-4]     # nur die Textzeilen, ohne Rahmenstrich rechts (würde als "1" gelesen)
            value, votes, texts = ocr.read_fraction(crop)
            if value is None:
                raise NotSeen(f"Zähler nicht lesbar (gelesen: {', '.join(repr(t) for t in texts)})")
            x, total = value
            if ocr.compile_condition(opts["when"])(x, total):
                return f"Zähler {x}/{total} ({votes}/{len(texts)} Lesungen): '{opts['when']}' erfüllt", opts["then"]
            raise NotSeen(f"Zähler {x}/{total} ({votes}/{len(texts)} Lesungen): '{opts['when']}' nicht erfüllt")
        if a == "replace_damaged":
            return self._replace_damaged(v, opts, human, timeout, threshold, gs)
        if a == "first_seen":
            case, m = self._wait_first(v, timeout, threshold, grayscale=gs)
            return f"'{case['if']}' erschienen (Treffer {m.score:.2f})", case["then"]
        if a == "end":
            raise EndReached(f"Ende: {v}" if v not in (None, True) else "end-Schritt erreicht")
        if a == "expect_not":
            self._wait_image(v, timeout, threshold, present=False, grayscale=gs)
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

    def _find_bar(self, names, timeout, threshold):
        """Leiste (Fortschrittsbalken) finden: Rahmen und Symbolfeld vergleichen, dann prüfen,
        ob die Leiste nur aus den beiden Füllfarben der Vorlage besteht. Gibt (treffer, bild) zurück."""
        bars = []
        for name in names:
            tpl = self.matcher.template(name)
            profiles = ocr.fill_profiles(tpl)
            # Der Browser rundet die Höhe der Leiste je nach Lage um 1 px auf oder ab
            for dh, variant in ocr.height_variants(tpl).items():
                frame = f"{name} [Rahmen {dh:+d}]"
                self.matcher.register(frame, variant, mask=ocr.bar_frame(variant))
                bars.append((frame, profiles))
        region = self._region()
        end = time.perf_counter() + timeout
        best_fill = 0.0
        while True:
            self.control.check()
            shot = screen.grab(region)
            for owner, end_args in self._ends:
                if self.matcher.find(region=region, shot=shot, **end_args):
                    raise EndReached(f"Endbild '{end_args['name']}' erkannt", owner)
            for frame, profiles in bars:
                for m in self.matcher.find_all_masked(frame, region, threshold, shot):
                    x, y = m.x - region[0], m.y - region[1]
                    crop = shot[y:y + m.h, x:x + m.w]
                    share = ocr.bar_fill_ok(profiles, crop)
                    best_fill = max(best_fill, share)
                    if share >= 0.9:
                        return m, crop
            if time.perf_counter() >= end:
                raise NotSeen(f"Leiste {' / '.join(names)} nicht gefunden"
                              + (f" (Rahmen gefunden, Farben passen nur zu {best_fill:.0%})" if best_fill else ""))
            self.control.sleep(self.matching["poll_interval"])
            region = self._region()

    # ------------------------------------------------------------ Einheiten tauschen

    HEADER_ROWS = {"army": 25, "pool": 40}   # fester oberer Teil der Bereichsbilder (Überschrift, Reiter)

    def _find_panel(self, name, rows, timeout, threshold, gs):
        """Bereich über seinen festen oberen Teil finden; gibt (x, y, w, h) auf dem Bildschirm zurück."""
        tpl = self.matcher.template(name)
        anchor = f"{name} [Kopf]"
        self.matcher.register(anchor, tpl[:rows])
        m = self._wait_image(anchor, timeout, threshold, grayscale=gs)
        return m.x, m.y, tpl.shape[1], tpl.shape[0]

    def _poll(self, condition, timeout=3.0):
        end = time.perf_counter() + timeout
        while True:
            self.control.check()
            if condition():
                return True
            if time.perf_counter() >= end:
                return False
            self.control.sleep(self.matching["poll_interval"])

    @staticmethod
    def _best_replacement(pool_img, portraits):
        """Gesunde Kachel im Pool, die einem der Porträts am ähnlichsten ist: (kachel, ähnlichkeit) oder None."""
        best = None
        for t in army.find_tiles(pool_img):
            if not t.full:
                continue
            s = max(army.similarity(p, pool_img, t) for p in portraits)
            if s >= army.SAME_UNIT and (best is None or s > best[1]):
                best = (t, s)
        return best

    def _click_tile(self, human, region, tile):
        x, y, w, h = tile.portrait_box
        px, py = click_point(Match(region[0] + x, region[1] + y, w, h, 1.0), human.cfg)
        human.click(px, py, target_size=min(w, h))

    @staticmethod
    def _missing(opts, text):
        """Kein Ersatz: if_missing-Schritte ausführen, sonst Fehler."""
        if opts.get("if_missing"):
            return text, opts["if_missing"]
        raise StepFailed(text[0].upper() + text[1:])

    def _replace_damaged(self, panels, opts, human, timeout, threshold, gs):
        """Leere Felder auffüllen und beschädigte Einheiten gegen gesunde derselben Art tauschen.

        Die Plätze der Armee kommen aus dem Armee-Bild (feste Positionen im Bereich), so werden
        auch Einheiten mit fast leerem Balken erkannt. Beim ersten fehlenden Ersatz endet der
        Schritt (if_missing bzw. Fehler); herausgenommen wird nur, wenn vorher Ersatz gefunden wurde.
        """
        reg_a = self._find_panel(panels["army"], self.HEADER_ROWS["army"], timeout, threshold, gs)
        reg_p = self._find_panel(panels["pool"], self.HEADER_ROWS["pool"], timeout, threshold, gs)
        slots = army.find_tiles(self.matcher.template(panels["army"]))
        empty_tpl = self.matcher.template(panels["empty"]) if panels.get("empty") else None
        states = lambda img: [army.slot_state(img, t, empty_tpl) for t in slots]
        n_empty = lambda: states(screen.grab(reg_a)).count("empty")
        replaced = filled = 0
        summary = lambda: f"{filled} leere Felder gefüllt, {replaced} ersetzt"

        for _ in range(2 * len(slots) + 2):     # Schutz gegen Endlosschleife
            img_a = screen.grab(reg_a)
            st = states(img_a)
            empty = st.count("empty")

            # leere Felder nur auffüllen, mit einer Einheit, deren Art schon in der Armee steht
            if empty:
                portraits = [army.crop(img_a, t.portrait_box) for t, s in zip(slots, st) if s != "empty"]
                best = self._best_replacement(screen.grab(reg_p), portraits) if portraits else None
                if best is None:
                    return self._missing(opts, f"keine gesunde Einheit für ein leeres Feld ({summary()})")
                self._click_tile(human, reg_p, best[0])
                if not self._poll(lambda: n_empty() == empty - 1):
                    raise StepFailed("Einheit wurde nicht in das leere Feld übernommen")
                filled += 1
                log.info("Leeres Feld gefüllt (Ähnlichkeit %.2f)", best[1])
                human.think()
                continue

            if "damaged" not in st:
                return f"{summary()}, alle {len(slots)} Einheiten voll"
            slot = st.index("damaged")
            tile = slots[slot]
            portrait = [army.crop(img_a, tile.portrait_box)]
            if self._best_replacement(screen.grab(reg_p), portrait) is None:
                return self._missing(opts, f"kein gesunder Ersatz für die Einheit bei Platz {slot + 1} ({summary()})")

            self._click_tile(human, reg_a, tile)                     # herausnehmen
            if not self._poll(lambda: n_empty() == 1):
                raise StepFailed(f"Einheit bei Platz {slot + 1} wurde nicht herausgenommen")
            best = self._best_replacement(screen.grab(reg_p), portrait)   # Pool hat sich verändert
            if best is None:
                return self._missing(opts, f"Ersatz für Platz {slot + 1} nach dem Herausnehmen nicht mehr gefunden "
                                           f"({summary()})")
            human.think()
            self._click_tile(human, reg_p, best[0])                  # einsetzen
            if not self._poll(lambda: n_empty() == 0):
                raise StepFailed("Ersatz-Einheit wurde nicht in die Armee übernommen")
            replaced += 1
            log.info("Einheit bei Platz %d ersetzt (Ähnlichkeit %.2f)", slot + 1, best[1])
        raise StepFailed(f"Nach {replaced} Tauschvorgängen immer noch beschädigte Einheiten, breche ab")

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

    @staticmethod
    def _set_aborted(result, sr, e):
        sr.status = result.status = "aborted"
        result.message = ("Not-Aus (Maus in Bildschirmecke)"
                          if isinstance(e, pyautogui.FailSafeException) else "Vom Benutzer abgebrochen")

    def _run_step(self, result, run_dir, human, phase, idx, step, is_pre):
        """Einen Schritt ausführen und protokollieren. Gibt das StepResult zurück."""
        sr = StepResult(phase, idx, step.describe())
        result.steps.append(sr)
        default_timeout = self.matching["precondition_timeout" if is_pre else "timeout"]
        t0 = time.perf_counter()
        try:
            while True:
                try:
                    # Startzustand ohne Denkpause, außer der Schritt verlangt ausdrücklich eine
                    if not is_pre or "think" in step.options:
                        human.think(step.options.get("think"))
                    out = self._execute(step, human, default_timeout)
                    sr.detail, sr.then = out if isinstance(out, tuple) else (out, None)
                    break
                except UserInterference:
                    log.info("Mausbewegung durch Benutzer erkannt, pausiere")
                    self.control.pause("Maus wurde bewegt. Weiter mit Pause-Hotkey oder Tray-Menü.")
                    self.control.check()   # blockiert bis Weiter oder Stopp
            sr.status = "ok"
        except NotSeen as e:
            sr.status, sr.detail = "ok", f"{e.reason}, übersprungen"
        except EndReached as e:
            sr.status, sr.detail = "ok", str(e)
            raise
        except StepFailed as e:
            sr.status, sr.detail = "fail", str(e)
            sr.screenshot = self._failure_screenshot(run_dir, f"fehler_{phase}_{idx}.png", e.match_region)
            result.status = "fail"
            if is_pre:
                prefix = "Startzustand stimmt nicht: "
            elif phase == "Ablauf":
                prefix = f"Schritt {idx}: "
            else:
                prefix = f"{phase}, Schritt {idx}: "
            result.message = prefix + str(e)
        except (Aborted, pyautogui.FailSafeException) as e:
            self._set_aborted(result, sr, e)
        except Exception as e:
            log.exception("Unerwarteter Fehler")
            sr.status, sr.detail = "fail", f"{type(e).__name__}: {e}"
            result.status, result.message = "fail", sr.detail
        finally:
            sr.duration = time.perf_counter() - t0
            log.info("[%s %s] %s -> %s %s", phase, idx, sr.description, sr.status, sr.detail)
        return sr

    def _run_list(self, result, run_dir, human, phase, steps, is_pre=False, prefix=""):
        """Schritte nacheinander ausführen; nach einem Fehler folgen die übrigen als übersprungen.

        prefix nummeriert verschachtelte Schritte (if_seen/first_seen), z. B. "8." -> 8.1, 8.2.
        """
        for i, step in enumerate(steps):
            if step.action == "repeat":     # verschachtelte Schleife
                ok = self._run_rounds(result, run_dir, human, step.value, step.options["steps"],
                                      phase, f"{prefix}{i + 1}", nested=True)
                if not ok:
                    result.steps += [StepResult(phase, f"{prefix}{n}", s.describe())
                                     for n, s in enumerate(steps[i + 1:], start=i + 2)]
                    return False
                continue
            sr = self._run_step(result, run_dir, human, phase, f"{prefix}{i + 1}", step, is_pre)
            ok = sr.status == "ok"
            if ok and sr.then:
                ok = self._run_list(result, run_dir, human, phase, sr.then, is_pre,
                                    prefix=f"{prefix}{i + 1}.")
            if not ok:
                result.steps += [StepResult(phase, f"{prefix}{n}", s.describe())
                                 for n, s in enumerate(steps[i + 1:], start=i + 2)]
                return False
        return True

    def _run_rounds(self, result, run_dir, human, rep, steps, phase="", prefix="", nested=False):
        """Schritte in Runden wiederholen, solange ein while-Bild erscheint und das until-Bild nicht.

        Auf oberster Ebene (repeat der Sequenz) beendet until den ganzen Ablauf und ein fehlendes
        while-Bild vor Runde 1 ist ein Fehler. Verschachtelt (repeat-Schritt) beendet until nur
        diese Schleife, und null Runden sind in Ordnung. Gibt True zurück, wenn es weitergehen kann.
        """
        threshold = float(rep["threshold"] or self.matching["threshold"])
        token = object()
        if rep["until"]:
            self._ends.append((token, dict(name=rep["until"], threshold=threshold, grayscale=rep["grayscale"])))
        rounds = 0
        try:
            while not rep["max"] or rounds < rep["max"]:
                round_phase = f"{phase} › Runde {rounds + 1}" if nested and phase != "Ablauf" else f"Runde {rounds + 1}"
                sr = StepResult(round_phase, prefix or 0, "while: " + " | ".join(rep["while"]))
                result.steps.append(sr)
                t0 = time.perf_counter()
                try:
                    case, m = self._wait_first([{"if": n, "grayscale": None} for n in rep["while"]],
                                               rep["timeout"], threshold, grayscale=rep["grayscale"])
                except StepFailed:
                    case = m = None
                except EndReached as e:
                    sr.status, sr.detail = "ok", str(e)
                    raise
                except (Aborted, pyautogui.FailSafeException) as e:
                    self._set_aborted(result, sr, e)
                    return False
                finally:
                    sr.duration = time.perf_counter() - t0

                if m is None:
                    names = " / ".join(rep["while"])
                    if rounds == 0 and not nested:
                        sr.status, sr.detail = "fail", f"Bild {names} nicht gefunden"
                        sr.screenshot = self._failure_screenshot(run_dir, "fehler_Runde_1.png")
                        result.status = "fail"
                        result.message = f"Wiederholung: {sr.detail}, keine einzige Runde ausgeführt."
                        return False
                    sr.status, sr.detail = "ok", f"nicht mehr sichtbar, Schleife nach {rounds} Runden beendet"
                    if not nested:
                        result.message = f"{rounds} Runden erfolgreich, danach war {names} nicht mehr sichtbar."
                    return True
                sr.status, sr.detail = "ok", f"'{case['if']}' gefunden (Treffer {m.score:.2f})"
                log.info("[%s] %s gefunden, starte Runde", round_phase, case["if"])
                rounds += 1
                if not self._run_list(result, run_dir, human, round_phase, steps,
                                      prefix=f"{prefix}." if prefix else ""):
                    return False
            if not nested:
                result.message = f"{rounds} Runden erfolgreich (Höchstzahl erreicht)."
            return True
        except EndReached as e:
            if nested and e.owner is not token:
                raise                   # gehört zu einer äußeren Schleife oder ist ein end-Schritt
            log.info("%s", e)
            if nested:
                result.steps[-1].detail = f"{e}, Schleife nach {rounds} Runden beendet"
                return True
            result.message = f"{e} in {result.steps[-1].phase}, Ablauf beendet."
            return True
        finally:
            self._ends = [x for x in self._ends if x[0] is not token]

    def run(self, scenario):
        human_cfg = config.merged_human(self.cfg, scenario.human)
        human = Human(self.control, human_cfg)
        self.matching = {**self.cfg["matching"], **scenario.matching}
        self.matcher.grayscale = self.matching["grayscale"]
        result = RunResult(scenario.name, datetime.now())
        safe_name = re.sub(r"[^\w\-]+", "_", scenario.name)
        run_dir = self.cfg["paths"]["reports"] / f"{result.started:%Y%m%d_%H%M%S}_{safe_name}"
        run_dir.mkdir(parents=True, exist_ok=True)
        t_start = time.perf_counter()

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
            try:
                if self._run_list(result, run_dir, human, "Startzustand", scenario.precondition, is_pre=True):
                    if scenario.repeat:
                        self._run_rounds(result, run_dir, human, scenario.repeat, scenario.steps)
                    elif self._run_list(result, run_dir, human, "Ablauf", scenario.steps):
                        result.message = f"Alle {len(scenario.steps)} Schritte erfolgreich."
                elif not scenario.repeat:
                    result.steps += [StepResult("Ablauf", i + 1, s.describe()) for i, s in enumerate(scenario.steps)]
            except EndReached as e:   # end-Schritt ohne repeat
                result.message = f"{e}, Ablauf beendet."

        self.control.on_resume = None
        result.duration = time.perf_counter() - t_start
        result.report_path = run_dir / "bericht.html"
        report.write(result, result.report_path)
        log.info("Szenario '%s' beendet: %s – %s", scenario.name, result.status, result.message)
        return result
