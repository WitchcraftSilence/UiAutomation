"""Stopp- und Pause-Steuerung, die vom Runner laufend abgefragt wird."""
import threading
import time


class Aborted(Exception):
    """Ablauf wurde vom Benutzer abgebrochen."""


class Control:
    def __init__(self):
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.on_pause_change = None     # Callback(paused: bool, reason: str)
        self.on_resume = None           # Callback, z. B. Fenster wieder aktivieren

    def reset(self):
        self.stop_event.clear()
        self.pause_event.clear()

    def stop(self):
        self.stop_event.set()

    def pause(self, reason=""):
        if not self.pause_event.is_set():
            self.pause_event.set()
            if self.on_pause_change:
                self.on_pause_change(True, reason)

    def resume(self):
        if self.pause_event.is_set():
            self.pause_event.clear()
            if self.on_pause_change:
                self.on_pause_change(False, "")

    def toggle_pause(self):
        if self.pause_event.is_set():
            self.resume()
        else:
            self.pause("Pause per Hotkey/Menü")

    def check(self):
        """Wirft Aborted bei Stopp; blockiert, solange pausiert ist."""
        if self.stop_event.is_set():
            raise Aborted()
        if self.pause_event.is_set():
            while self.pause_event.is_set():
                if self.stop_event.is_set():
                    raise Aborted()
                time.sleep(0.05)
            if self.on_resume:
                self.on_resume()

    def sleep(self, seconds):
        """Schlafen in kleinen Scheiben, damit Stopp/Pause sofort greifen."""
        end = time.perf_counter() + max(0.0, seconds)
        while True:
            self.check()
            remaining = end - time.perf_counter()
            if remaining <= 0:
                return
            time.sleep(min(0.05, remaining))
