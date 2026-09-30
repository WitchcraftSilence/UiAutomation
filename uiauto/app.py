"""Tray-Anwendung: Szenarien per Hotkey oder Menü starten, pausieren, abbrechen, Bilder aufnehmen."""
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

log = logging.getLogger("uiauto")

COLORS = {"idle": "#2da44e", "running": "#cf222e", "paused": "#d4a72c"}
TITLES = {"idle": "bereit", "running": "läuft", "paused": "pausiert"}


def _icon_image(color):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, 58, 58), fill=color, outline="white", width=4)
    return img


class App:
    def __init__(self):
        self.cfg = config.load()
        self._setup_logging()
        self.control = Control()
        self.control.on_pause_change = self._on_pause_change
        self.runner = Runner(self.cfg, self.control, self.notify)
        self.scenarios = []
        self.last_report = None
        self.state = "idle"
        self._run_thread = None
        self._start_lock = threading.Lock()
        self._ui_queue = queue.Queue()
        self.hotkeys = None

        # Tk läuft im Hauptthread (für Aufnahmewerkzeug und Dialoge), unsichtbar
        self.root = tk.Tk()
        self.root.withdraw()
        self.recorder = Recorder(self.root, self.cfg["paths"]["images"], self.notify)

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

    def notify(self, title, text):
        log.info("Meldung: %s – %s", title, text)
        try:
            self.icon.notify(text, title)
        except Exception:
            log.exception("Benachrichtigung fehlgeschlagen")

    def _set_state(self, state):
        self.state = state
        self.icon.icon = _icon_image(COLORS[state])
        self.icon.title = f"UI-Automation – {TITLES[state]}"
        self.icon.update_menu()     # Menüeinträge (aktiv/ausgegraut, Pause/Weiter) neu auswerten

    # ------------------------------------------------------------ Szenarien

    def load_scenarios(self):
        self.scenarios, errors = scenario_mod.load_all(self.cfg["paths"]["scenarios"],
                                                       self.cfg["paths"]["images"])
        for e in errors:
            log.error(e)
        if errors:
            self.notify("Fehler in Szenarien", "\n".join(errors)[:250])
        log.info("%d Szenarien geladen", len(self.scenarios))

    def start_scenario(self, scenario):
        # Hotkey- und Tray-Thread können gleichzeitig starten wollen
        with self._start_lock:
            if self._is_running():
                self.notify("Läuft bereits", "Erst den laufenden Ablauf beenden oder abbrechen.")
                return
            self._set_state("running")
        self._run_thread = threading.Thread(target=self._run, args=(scenario,), daemon=True)
        self._run_thread.start()

    def _run(self, scenario):
        try:
            result = self.runner.run(scenario)
            self.last_report = result.report_path
            title = {"ok": "✔ Erfolgreich", "fail": "✖ Fehlgeschlagen",
                     "aborted": "■ Abgebrochen"}[result.status]
            self.notify(f"{title}: {scenario.name}", result.message[:250])
        except Exception as e:
            log.exception("Ablauf abgestürzt")
            self.notify("Interner Fehler", str(e)[:250])
        finally:
            self._set_state("idle")

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
                    self.notify("Hotkey doppelt", f"{s.hotkey} ({s.name}) ist schon vergeben.")
                    continue
                bindings[s.hotkey] = self._starter(s)
        self.hotkeys = HotkeyListener(bindings, lambda combo, msg: self.notify("Hotkey-Fehler", msg))
        self.hotkeys.start()

    def _starter(self, s):
        return lambda: self.start_scenario(s)

    def record(self):
        if self._is_running():
            self.notify("Aufnahme nicht möglich", "Während ein Ablauf läuft, keine Aufnahme.")
            return
        # kurz warten, bis Tray-Menü bzw. Hotkey-Tasten weg sind
        self.ui(lambda: self.root.after(300, self.recorder.start))

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
                             enabled=lambda item: not self._is_running())
            for s in self.scenarios
        ] or [pystray.MenuItem("(keine Szenarien)", None, enabled=False)]
        return pystray.Menu(
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
