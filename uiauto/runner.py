"""Führt ein Szenario im Zielfenster aus."""
import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

import cv2
import pyautogui

from . import army, config, negotiation, ocr, report, screen, window
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
        self.params = {}
        self._spots = {}        # bild -> (x, y, w, h) der letzten Fundstelle, für die schnelle Suche
        self._ends = []         # (schleife, find()-Argumente) je until-Bild, bei jeder Bildsuche mitgeprüft

    # ------------------------------------------------------------ Hilfsfunktionen

    def _region(self):
        return window.rect(self.hwnd)

    def _activate(self):
        window.activate(self.hwnd)
        time.sleep(0.2)

    SPOT_MARGIN = 30        # px um die letzte Fundstelle, die bei der schnellen Suche abgesucht werden
    FULL_EVERY = 5          # jede so-vielte Suche über das ganze Fenster (Endbilder, Bild an anderer Stelle)

    def _spot_region(self, region, spot):
        """Kleiner Ausschnitt um die letzte Fundstelle eines Bilds, begrenzt auf das Fenster."""
        x, y, w, h = spot
        m = self.SPOT_MARGIN
        left, top = max(region[0], x - m), max(region[1], y - m)
        right = min(region[0] + region[2], x + w + m)
        bottom = min(region[1] + region[3], y + h + m)
        return left, top, right - left, bottom - top

    def _check_ends(self, region, shot=None):
        """Wirft EndReached, wenn ein until-Bild einer aktiven Schleife im Fenster zu sehen ist."""
        if not self._ends:
            return
        if shot is None:
            shot = screen.grab(region)
        for owner, end_args in self._ends:
            if self.matcher.find(region=region, shot=shot, **end_args):
                raise EndReached(f"Endbild '{end_args['name']}' erkannt", owner)

    def _wait_image(self, name, timeout, threshold, present=True, grayscale=None, skip_if=None, check_end=False):
        """Wartet, bis das Bild erscheint (bzw. bei present=False verschwindet).

        skip_if: zweites Bild; ist es zu sehen (und name nicht), wird NotSeen geworfen.
        Wurde das Bild schon einmal gefunden, wird meist nur um diese Stelle gesucht (schneller);
        jede FULL_EVERY-te Suche und die letzte vor dem Aufgeben gehen über das ganze Fenster.
        check_end: auch ein Treffer der schnellen Suche gilt erst nach einer Prüfung der Endbilder
        im ganzen Fenster (kostet je Treffer 70-150 ms, darum nur auf Wunsch).
        """
        region = self._region()
        end = time.perf_counter() + timeout
        spot = self._spots.get(name) if present and not skip_if else None
        n = 0
        while True:
            self.control.check()
            timed_out = time.perf_counter() >= end
            if spot and not timed_out and n % self.FULL_EVERY != self.FULL_EVERY - 1:
                small = self._spot_region(region, spot)
                m = self.matcher.find(name, small, threshold, shot=screen.grab(small), grayscale=grayscale)
                if m:
                    if check_end:       # z. B. derselbe OK-Knopf im Niederlage-Dialog
                        self._check_ends(region)
                    self._spots[name] = (m.x, m.y, m.w, m.h)
                    return m
            else:
                shot = screen.grab(region)
                self._check_ends(region, shot)
                m = self.matcher.find(name, region, threshold, shot=shot, grayscale=grayscale)
                if (m is not None) == present:
                    if m:
                        self._spots[name] = (m.x, m.y, m.w, m.h)
                    return m
                if skip_if and self.matcher.find(skip_if, region, threshold, shot=shot):
                    raise NotSeen(f"nicht erschienen, stattdessen '{skip_if}' sichtbar")
                if timed_out:
                    if present:
                        best = self.matcher.best_score(name, region, shot=shot, grayscale=grayscale)
                        raise StepFailed(f"Bild '{name}' nicht gefunden nach {timeout:.1f} s "
                                         f"(beste Übereinstimmung {best:.2f}, Schwelle {threshold:.2f})")
                    raise StepFailed(f"Bild '{name}' ist nach {timeout:.1f} s immer noch sichtbar "
                                     f"(Übereinstimmung {m.score:.2f})", (m.x, m.y, m.w, m.h))
            n += 1
            self.control.sleep(self.matching["poll_interval"])
            region = self._region()

    def _wait_first(self, cases, timeout, threshold, grayscale=None):
        """Wartet, bis eines der Bilder erscheint. Gibt (fall, treffer) zurück; bei Gleichstand gewinnt der erste Fall."""
        region = self._region()
        end = time.perf_counter() + timeout
        while True:
            self.control.check()
            shot = screen.grab(region)
            self._check_ends(region, shot)
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
        if isinstance(v, str) and v.startswith("$"):
            v = self.params[v[1:]]                  # gewählte Einstellung, z. B. $klicks

        if a in ("click", "double_click", "right_click", "move"):
            m = self._wait_image(v, timeout, threshold, grayscale=gs, check_end=bool(opts.get("check_end")))
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
            m = self._wait_image(v, timeout, threshold, grayscale=gs, check_end=bool(opts.get("check_end")))
            return f"Treffer {m.score:.2f} bei ({m.x}, {m.y})"
        if a == "drag_over":
            return self._drag_over(v, human, timeout, threshold, gs)
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
        if a == "negotiate":
            return self._negotiate(v, human, timeout, threshold, gs)
        if a == "first_seen":
            case, m = self._wait_first(v, timeout, threshold, grayscale=gs)
            return f"'{case['if']}' erschienen (Treffer {m.score:.2f})", case["then"]
        if a == "click_here":
            # Ausgangsposition bei jedem (Neu-)Start des Schritts: nach einer Pause wegen
            # Mausbewegung wird an der neuen Stelle weitergeklickt
            anchor = tuple(pyautogui.position())
            human.forget_position()
            done = 0
            while v == 0 or done < v:
                human.click_here(anchor)
                done += 1
            return f"{done} Klicks bei {anchor}"
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

    DRAG_PASSES = 5         # so oft werden übrig gebliebene Treffer höchstens erneut abgefahren
    DRAG_SETTLE = 0.4       # s nach einem Durchgang, bis eingesammelte Treffer verschwunden sind

    def _find_all_variants(self, names, region, threshold, shot, grayscale):
        """Treffer aller Bildvarianten; liegt die Mitte eines Treffers in einem besseren, zählt er nicht."""
        found = []
        for m in sorted((m for n in names for m in self.matcher.find_all(n, region, threshold, shot, grayscale)),
                        key=lambda m: -m.score):
            cx, cy = m.center
            if not any(k.x <= cx <= k.x + k.w and k.y <= cy <= k.y + k.h for k in found):
                found.append(m)
        return found

    def _drag_over(self, names, human, timeout, threshold, grayscale):
        """Alle Treffer der Bilder (Varianten desselben Symbols) mit gedrückter linker Maustaste abfahren,
        bis keiner mehr zu sehen ist.

        Gedrückt wird beim ersten Treffer, weiter geht es jeweils zum nächstgelegenen. Nach jedem
        Durchgang wird neu gesucht, die Taste bleibt dabei gedrückt.
        """
        name = " / ".join(names)
        region = self._region()
        end = time.perf_counter() + timeout
        while True:
            self.control.check()
            shot = screen.grab(region)
            found = self._find_all_variants(names, region, threshold, shot, grayscale)
            if found:
                break
            if time.perf_counter() >= end:
                best = max(self.matcher.best_score(n, region, shot=shot, grayscale=grayscale) for n in names)
                raise StepFailed(f"Bild '{name}' nicht gefunden nach {timeout:.1f} s "
                                 f"(beste Übereinstimmung {best:.2f}, Schwelle {threshold:.2f})")
            self.control.sleep(self.matching["poll_interval"])
            region = self._region()

        first_count, passes, pressed = len(found), 0, False
        try:
            while found:
                if passes == self.DRAG_PASSES:
                    m = found[0]
                    raise StepFailed(f"Nach {passes} Durchgängen noch {len(found)}× '{name}' sichtbar",
                                     (m.x, m.y, m.w, m.h))
                passes += 1
                pos = pyautogui.position()
                while found:    # immer zum nächstgelegenen Treffer
                    m = min(found, key=lambda t: (t.center[0] - pos[0]) ** 2 + (t.center[1] - pos[1]) ** 2)
                    found.remove(m)
                    pos = click_point(m, human.cfg)
                    human.move_to(*pos, target_size=min(m.w, m.h))
                    if not pressed:
                        human.mouse_down()
                        pressed = True
                self.control.sleep(self.DRAG_SETTLE)
                found = self._find_all_variants(names, region, threshold, screen.grab(region), grayscale)
        finally:
            if pressed:
                human.mouse_up()
        return f"{first_count} Treffer abgefahren in {passes} {'Durchgang' if passes == 1 else 'Durchgängen'}"

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
        spot_key = "bar:" + "|".join(names)

        def check(frames, area, shot):
            """Leiste mit passenden Füllfarben in diesem Ausschnitt suchen."""
            nonlocal best_fill
            for frame, profiles in frames:
                for m in self.matcher.find_all_masked(frame, area, threshold, shot):
                    x, y = m.x - area[0], m.y - area[1]
                    crop = shot[y:y + m.h, x:x + m.w]
                    share = ocr.bar_fill_ok(profiles, crop)
                    best_fill = max(best_fill, share)
                    if share >= 0.9:
                        self._spots[spot_key] = (m.x, m.y, m.w, m.h, frame)
                        return m, crop
            return None

        # schnell: an der letzten Fundstelle, zuerst mit der Höhenvariante, die zuletzt gepasst hat
        if spot_key in self._spots:
            *box, last_frame = self._spots[spot_key]
            small = self._spot_region(region, box)
            ordered = sorted(bars, key=lambda b: b[0] != last_frame)
            found = check(ordered, small, screen.grab(small))
            if found:
                return found
        while True:
            self.control.check()
            shot = screen.grab(region)
            self._check_ends(region, shot)
            found = check(bars, region, shot)
            if found:
                return found
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

    def _settled_grab(self, region, timeout=1.5, gap=0.15):
        """Screenshot, sobald sich der Bereich zwischen zwei Aufnahmen nicht mehr ändert (Animationen)."""
        prev = screen.grab(region)
        end = time.perf_counter() + timeout
        while time.perf_counter() < end:
            self.control.sleep(gap)
            cur = screen.grab(region)
            if cv2.absdiff(prev, cur).max() < 30:
                return cur
            prev = cur
        return prev

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

            damaged = st.count("damaged")
            self._click_tile(human, reg_a, tile)                     # herausnehmen
            if not self._poll(lambda: n_empty() == 1):
                raise StepFailed(f"Einheit bei Platz {slot + 1} wurde nicht herausgenommen")
            # Pool hat sich verändert (die herausgenommene Einheit kommt dazu): erst wählen, wenn er ruhig ist
            best = self._best_replacement(self._settled_grab(reg_p), portrait)
            if best is None:
                return self._missing(opts, f"Ersatz für Platz {slot + 1} nach dem Herausnehmen nicht mehr gefunden "
                                           f"({summary()})")
            human.think()
            self._click_tile(human, reg_p, best[0])                  # einsetzen
            if not self._poll(lambda: n_empty() == 0):
                raise StepFailed("Ersatz-Einheit wurde nicht in die Armee übernommen")
            replaced += 1
            log.info("Einheit bei Platz %d ersetzt (Ähnlichkeit %.2f)", slot + 1, best[1])
            after = states(self._settled_grab(reg_a))
            if after.count("damaged") >= damaged:   # Ersatz war selbst verwundet: nicht endlos weitertauschen
                bad = [i + 1 for i, s in enumerate(after) if s == "damaged"]
                raise StepFailed(f"Nach dem Ersetzen von Platz {slot + 1} sind immer noch {len(bad)} Einheiten "
                                 f"beschädigt (Plätze {', '.join(map(str, bad))}); der Ersatz war wohl selbst "
                                 f"verwundet, breche ab ({summary()})", reg_a)
        raise StepFailed(f"Nach {replaced} Tauschvorgängen immer noch beschädigte Einheiten, breche ab")

    # ------------------------------------------------------------ Verhandlung

    TABLE_H = 500           # so weit unter der Kopfzeile wird nach der aktuellen Vorschlagszeile gesucht
    MENU_W, MENU_H = 500, 260   # Menü "Ressource auswählen": so weit wird die Leiste seitlich verfolgt, Höhe ab ihr (2 Reihen Güter)
    MAX_ROUNDS = 10
    MENU_TRIES = 3          # so oft wird die Taste 1-5 gedrückt, wenn das Menü nicht erscheint
    MENU_WAIT = 1.5         # s Wartezeit je Versuch (Menü erscheint, Gut wird sichtbar)
    ROUND_PAUSE = 1.2       # s Pause nach dem Erscheinen der neuen Vorschlagszeile, vor der ersten Taste

    def _negotiate(self, imgs, human, timeout, threshold, gs):
        """Vorschläge aus der Tabelle übernehmen, Runde für Runde, bis das Erfolgsbild erscheint.

        Je Runde: für jede Person mit Vorschlag ihre Taste 1-5 drücken (öffnet das Menü wie ein
        Klick auf "Angebot machen"), im Menü das vorgeschlagene Gut wählen, danach "Bezahlen &
        Verhandeln". Ob es eine weitere Runde gibt, zeigt eine neue (tiefere) Zeile in der Tabelle.
        Vor der Suche im Menü fährt die Maus kurz auf dessen Überschriftsleiste: Steht sie noch
        über einem Knopf "Angebot machen", verdeckt dessen Tooltip das Gut.
        """
        table = self.matcher.template(imgs["suggestions"])
        head = f"{imgs['suggestions']} [Kopf]"
        self.matcher.register(head, table[:negotiation.header_rows(table)])

        def read_row():
            h = self._wait_image(head, timeout, threshold, grayscale=gs)
            region = self._region()
            top = h.y + h.h
            area = (h.x, top, h.w, max(1, min(region[1] + region[3], top + self.TABLE_H) - top))
            img = screen.grab(area)
            return area, img, negotiation.current_icons(img)

        def open_menu(person):
            """Taste der Person drücken, bis das Menü erscheint (direkt nach einer Runde reagiert
            das Spiel manchmal noch nicht). Gibt den Treffer der Menü-Überschrift zurück."""
            for attempt in range(self.MENU_TRIES):
                human.press(str(person))
                try:
                    return self._wait_image(imgs["menu"], self.MENU_WAIT if attempt < self.MENU_TRIES - 1 else timeout,
                                            threshold, grayscale=gs)
                except StepFailed:
                    if attempt == self.MENU_TRIES - 1:
                        raise StepFailed(f"Menü 'Ressource auswählen' für Person {person} nach "
                                         f"{self.MENU_TRIES}× Taste {person} nicht erschienen") from None
                    log.info("Verhandlung: Menü für Person %d nicht erschienen, Taste erneut", person)

        def menu_box(title):
            """Bereich unter der grauen Menüleiste (x, y, w, h) und die Leiste selbst (für die Maus)."""
            region = self._region()
            left = max(region[0], title.x - self.MENU_W)
            right = min(region[0] + region[2], title.x + title.w + self.MENU_W)
            row = screen.grab((left, title.y + title.h // 2, right - left, 1))[0]
            a, b = negotiation.menu_span(row, title.x - left, title.x + title.w - left)
            menu = (left + a, title.y, b - a, min(self.MENU_H, region[1] + region[3] - title.y))
            # Maus-Ziel: linkes oder rechtes Ende der Leiste, neben der Überschrift
            side = random.choice([(left + a + 4, title.x - 4), (title.x + title.w + 4, left + b - 4)])
            if side[1] - side[0] < 8:
                side = (title.x, title.x + title.w)
            bar = Match(side[0], title.y, side[1] - side[0], title.h, 1.0)
            return menu, bar

        for rnd in range(1, self.MAX_ROUNDS + 1):
            area, img, icons = read_row()
            if not icons:
                raise StepFailed(f"Runde {rnd}: keine Vorschläge in der Tabelle gefunden")
            for icon in icons:
                title = open_menu(icon.person)
                menu, bar = menu_box(title)
                human.move_to(*click_point(bar, human.cfg), target_size=bar.h)
                good = icon.crop(img)
                found = []

                def seen():         # der Tooltip blendet kurz aus, darum mehrmals versuchen
                    found[:] = [negotiation.find_good(good, screen.grab(menu))]
                    return found[0][0] >= negotiation.SAME_GOOD

                if not self._poll(seen, self.MENU_WAIT):
                    raise StepFailed(f"Runde {rnd}: Gut für Person {icon.person} nicht im Menü gefunden "
                                     f"(beste Übereinstimmung {found[0][0]:.2f}, Schwelle {negotiation.SAME_GOOD:.2f})")
                score, (gx, gy) = found[0]
                human.think()
                human.click(menu[0] + gx, menu[1] + gy, target_size=30)
                self._wait_image(imgs["menu"], timeout, threshold, present=False, grayscale=gs)
                log.info("Verhandlung Runde %d: Person %d Gut gewählt (Übereinstimmung %.2f)", rnd, icon.person, score)
                human.think()

            pay = self._wait_image(imgs["pay"], timeout, threshold, grayscale=gs)
            human.click(*click_point(pay, human.cfg), target_size=min(pay.w, pay.h))
            row_y = icons[0].y
            success = []

            def result():
                if self.matcher.find(imgs["success"], self._region(), threshold, grayscale=gs):
                    success.append(True)
                    return True
                new = negotiation.current_icons(screen.grab(area))
                return bool(new) and new[0].y > row_y + negotiation.SAME_ROW

            if not self._poll(result, timeout):
                raise StepFailed(f"Runde {rnd}: nach dem Bezahlen weder Erfolg noch neue Vorschläge")
            if success:
                return f"Erfolg nach {rnd} Runde{'n' if rnd > 1 else ''}"
            self.control.sleep(self.ROUND_PAUSE)   # das Spiel nimmt Tasten erst kurz nach der neuen Zeile an
        raise StepFailed(f"Nach {self.MAX_ROUNDS} Runden immer noch kein Erfolg, breche ab")

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
                except UserInterference as e:
                    log.info("Mausbewegung durch Benutzer erkannt (%s, Schritt %s %s), pausiere", e, idx, step.action)
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
        max_rounds = rep["max"]
        if isinstance(max_rounds, str):
            max_rounds = self.params[max_rounds[1:]]    # gewählte Einstellung, z. B. $durchlaeufe
        rounds = 0
        try:
            while not max_rounds or rounds < max_rounds:
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

    def run(self, scenario, params=None):
        """params: gewählte Einstellungen {name: wert}; fehlende nehmen die Vorgabe aus der Sequenz."""
        self.params = {name: (params or {}).get(name, spec["default"]) for name, spec in scenario.params.items()}
        if self.params:
            log.info("Einstellungen: %s", self.params)
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
