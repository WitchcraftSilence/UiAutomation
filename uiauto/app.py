"""Steuerfenster und Tray: Szenarien per Hotkey, Fenster oder Menü starten, pausieren, abbrechen, Bilder aufnehmen."""
import logging
import os
import queue
import threading
import tkinter as tk
from logging.handlers import RotatingFileHandler

import pystray
from PIL import Image, ImageDraw

from . import config, scenario as scenario_mod
from .control import Control
from .hotkeys import HotkeyListener
from .recorder import Recorder
from .runner import Runner
from .ui import ControlWindow

log = logging.getLogger("uiauto")

COLORS = {"idle": "#2da44e", "running": "#cf222e", "paused": "#d4a72c"}
TITLES = {"idle": "bereit", "running": "läuft", "paused": "pausiert"}


def _icon_image(color):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, 58, 58), fill=color, outline="white", width=4)
    return img


def _hotkey_order(scenario):
    """Sortierung fürs Menü: nach Hotkey (Zahlen numerisch, also 2 vor 10), ohne Hotkey ans Ende."""
    if not scenario.hotkey:
        return (1, (), scenario.name.lower())
    parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p)
                  for p in reversed(scenario.hotkey.lower().replace(" ", "").split("+")))
    return (0, parts, scenario.name.lower())


class App:
    def __init__(self):
        self.cfg = config.load()
        self._setup_logging()
        self.control = Control()
        self.control.on_pause_change = self._on_pause_change
        self.runner = Runner(self.cfg, self.control, self.notify)
        self.scenarios = []
        self.last_report = None
        self.current = None             # Name der laufenden/pausierten Sequenz
        self.param_values = {}          # (sequenz, einstellung) -> im Fenster gewählter Wert
        self.state = "idle"
        self._run_thread = None
        self._notify_timer = None
        self._start_lock = threading.Lock()
        self._ui_queue = queue.Queue()
        self.hotkeys = None

        # Tk läuft im Hauptthread: unsichtbares Hauptfenster, Steuerfenster, Aufnahmewerkzeug, Dialoge
        self.root = tk.Tk()
        self.root.withdraw()
        self.window = ControlWindow(self.root, self)
        self.recorder = Recorder(self.root, self.cfg["paths"]["images"], self.notify,
                                 on_done=self.window.restore_after_record)

        self.icon = pystray.Icon("uiauto", _icon_image(COLORS["idle"]), "UI-Automation – bereit")

    # ------------------------------------------------------------ Infrastruktur

    def _setup_logging(self):
        handler = RotatingFileHandler(self.cfg["paths"]["logs"] / "uiauto.log",
                                      maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.addHandler(handler)

    def ui(self, fn):
        """fn im Tk-Hauptthread ausführen."""
        self._ui_queue.put(fn)

    def _poll_ui(self):
        while True:
            try:
                fn = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                log.exception("Fehler im UI-Thread")
        self.root.after(50, self._poll_ui)

    def notify(self, title, text, error=False):
        """Windows-Meldung am Tray-Icon, die nach kurzer Zeit von selbst wieder verschwindet."""
        log.info("Meldung: %s – %s", title, text)
        cfg = self.cfg["notifications"]
        seconds = cfg["error_seconds"] if error else cfg["seconds"]
        if seconds <= 0:
            return
        try:
            if self._notify_timer:
                self._notify_timer.cancel()
            self.icon.notify(text, title)
            self._notify_timer = threading.Timer(seconds, self._remove_notification)
            self._notify_timer.daemon = True
            self._notify_timer.start()
        except Exception:
            log.exception("Benachrichtigung fehlgeschlagen")

    def _remove_notification(self):
        try:
            self.icon.remove_notification()
        except Exception:
            log.exception("Benachrichtigung entfernen fehlgeschlagen")

    def _set_state(self, state):
        self.state = state
        self.icon.icon = _icon_image(COLORS[state])
        self.icon.title = f"UI-Automation – {TITLES[state]}"
        self.icon.update_menu()     # Menüeinträge (aktiv/ausgegraut, Pause/Weiter) neu auswerten
        self.ui(self.window.refresh)

    # ------------------------------------------------------------ Szenarien

    def load_scenarios(self):
        scenarios, errors = scenario_mod.load_all(self.cfg["paths"]["scenarios"],
                                                  self.cfg["paths"]["images"])
        self.scenarios = sorted(scenarios, key=_hotkey_order)
        for e in errors:
            log.error(e)
        if errors:
            self.notify("Fehler in Szenarien", "\n".join(errors)[:250], error=True)
        log.info("%d Szenarien geladen", len(self.scenarios))
        self.ui(self.window.rebuild)

    def start_scenario(self, scenario):
        # Hotkey- und Tray-Thread können gleichzeitig starten wollen
        with self._start_lock:
            if self.state == "paused":
                # pausierten Ablauf beenden wie mit Stopp, dann den neuen starten
                log.info("Pausierten Ablauf beenden, starte '%s'", scenario.name)
                self.control.stop()
                if self._run_thread:
                    self._run_thread.join(5)
                if self._run_thread and self._run_thread.is_alive():
                    self.notify("Start nicht möglich", "Der pausierte Ablauf ließ sich nicht beenden.", error=True)
                    return
            elif self._is_running():
                self.notify("Läuft bereits", "Erst den laufenden Ablauf beenden oder abbrechen.")
                return
            self.current = scenario.name
            self._set_state("running")
            self._run_thread = threading.Thread(target=self._run, args=(scenario,), daemon=True)
            self._run_thread.start()

    def _run(self, scenario):
        try:
            result = self.runner.run(scenario, self.params_for(scenario))
            self.last_report = result.report_path
            title = {"ok": "✔ Erfolgreich", "fail": "✖ Fehlgeschlagen",
                     "aborted": "■ Abgebrochen"}[result.status]
            self.notify(f"{title}: {scenario.name}", result.message[:250], error=result.status == "fail")
        except Exception as e:
            log.exception("Ablauf abgestürzt")
            self.notify("Interner Fehler", str(e)[:250], error=True)
        finally:
            self._set_state("idle")

    def params_for(self, scenario):
        """Im Fenster gewählte Einstellungen einer Sequenz, sonst die Vorgaben."""
        return {name: self.param_values.get((scenario.name, name), spec["default"])
                for name, spec in scenario.params.items()}

    def _on_pause_change(self, paused, reason):
        if self.state == "idle":
            return
        self._set_state("paused" if paused else "running")
        if paused:
            self.notify("Pausiert", reason)

    def _is_running(self):
        # Nach dem Zustand, nicht nach dem Thread: der lebt am Ende noch kurz weiter,
        # während das Menü schon neu aufgebaut wird.
        return self.state != "idle"

    # ------------------------------------------------------------ Hotkeys & Menü

    def _start_hotkeys(self):
        hk = self.cfg["hotkeys"]
        bindings = {
            hk["stop"]: self.control.stop,
            hk["pause"]: lambda: self._is_running() and self.control.toggle_pause(),
            hk["record"]: self.record,
        }
        for s in self.scenarios:
            if s.hotkey:
                if s.hotkey in bindings:
                    self.notify("Hotkey doppelt", f"{s.hotkey} ({s.name}) ist schon vergeben.", error=True)
                    continue
                bindings[s.hotkey] = self._starter(s)
        self.hotkeys = HotkeyListener(bindings, lambda combo, msg: self.notify("Hotkey-Fehler", msg, error=True))
        self.hotkeys.start()

    def _starter(self, s):
        return lambda: self.start_scenario(s)

    def record(self):
        if self._is_running():
            self.notify("Aufnahme nicht möglich", "Während ein Ablauf läuft, keine Aufnahme.")
            return
        # Steuerfenster ausblenden und kurz warten, bis Menü bzw. Hotkey-Tasten weg sind
        def start():
            self.window.hide_for_record()
            self.root.after(300, self.recorder.start)
        self.ui(start)

    def reload(self):
        if self.hotkeys:
            self.hotkeys.stop()
        self.load_scenarios()
        self._start_hotkeys()
        self.icon.menu = self._build_menu()
        self.icon.update_menu()
        self.notify("Neu geladen", f"{len(self.scenarios)} Szenarien")

    def open_report(self):
        if self.last_report:
            os.startfile(self.last_report)

    def open_folder(self, key):
        os.startfile(self.cfg["paths"][key])

    def quit(self):
        self.control.stop()
        if self.hotkeys:
            self.hotkeys.stop()
        self.icon.stop()
        self.ui(self.root.quit)

    def _build_menu(self):
        hk = self.cfg["hotkeys"]
        scenario_items = [
            pystray.MenuItem(f"{s.name}\t{s.hotkey}" if s.hotkey else s.name, self._starter(s),
                             enabled=lambda item: self.state != "running")   # auch bei Pause
            for s in self.scenarios
        ] or [pystray.MenuItem("(keine Szenarien)", None, enabled=False)]
        return pystray.Menu(
            pystray.MenuItem("Fenster anzeigen", lambda: self.ui(self.window.show), default=True),
            pystray.Menu.SEPARATOR,
            *scenario_items,
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda item: ("Weiter" if self.state == "paused" else "Pause") + f"\t{hk['pause']}",
                             lambda: self.control.toggle_pause(),
                             enabled=lambda item: self._is_running()),
            pystray.MenuItem(f"Stopp\t{hk['stop']}", lambda: self.control.stop(),
                             enabled=lambda item: self._is_running()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"Referenzbild aufnehmen\t{hk['record']}", lambda: self.record()),
            pystray.MenuItem("Letzten Bericht öffnen", lambda: self.open_report(),
                             enabled=lambda item: self.last_report is not None),
            pystray.MenuItem("Ordner", pystray.Menu(
                pystray.MenuItem("Szenarien", lambda: self.open_folder("scenarios")),
                pystray.MenuItem("Bilder", lambda: self.open_folder("images")),
                pystray.MenuItem("Berichte", lambda: self.open_folder("reports")),
                pystray.MenuItem("Logs", lambda: self.open_folder("logs")),
            )),
            pystray.MenuItem("Szenarien neu laden", lambda: self.reload()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Beenden", lambda: self.quit()),
        )

    # ------------------------------------------------------------ Start

    def _setup(self, icon):
        # läuft, sobald das Tray-Icon existiert, damit Meldungen beim Laden sichtbar sind
        icon.visible = True
        self.load_scenarios()
        self._start_hotkeys()
        icon.menu = self._build_menu()
        icon.update_menu()

    def run(self):
        threading.Thread(target=self.icon.run, kwargs={"setup": self._setup},
                         name="tray", daemon=True).start()
        self.root.after(50, self._poll_ui)
        self.root.mainloop()
