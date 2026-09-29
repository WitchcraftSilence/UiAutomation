"""Globale Hotkeys über Win32 RegisterHotKey.

Arbeitet mit virtuellen Tastencodes und ist damit unabhängig vom Tastaturlayout
(Strg+Alt+2 wird nicht zu AltGr+2 = '²').
"""
import ctypes
import logging
import threading
from ctypes import wintypes

log = logging.getLogger(__name__)
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MODIFIERS = {"alt": 0x1, "ctrl": 0x2, "strg": 0x2, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000
NAMED_KEYS = {
    "space": 0x20, "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "insert": 0x2D,
    "delete": 0x2E, "pause": 0x13, "esc": 0x1B, "enter": 0x0D, "tab": 0x09,
    **{f"num{i}": 0x60 + i for i in range(10)},
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
}


class HotkeyError(ValueError):
    pass


def parse(combo):
    """'ctrl+alt+1' -> (modifier_flags, vk)."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise HotkeyError(f"Leerer Hotkey: {combo!r}")
    mods = 0
    for p in parts[:-1]:
        if p not in MODIFIERS:
            raise HotkeyError(f"Unbekannte Zusatztaste '{p}' in {combo!r}")
        mods |= MODIFIERS[p]
    key = parts[-1]
    if key in NAMED_KEYS:
        vk = NAMED_KEYS[key]
    elif len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())
    else:
        raise HotkeyError(f"Unbekannte Taste '{key}' in {combo!r}")
    return mods, vk


class HotkeyListener:
    """Registriert Hotkeys in einem eigenen Thread mit Nachrichtenschleife."""

    def __init__(self, bindings, on_error=lambda combo, msg: None):
        # bindings: {"ctrl+alt+1": callback}
        self.bindings = bindings
        self.on_error = on_error
        self._thread = None
        self._thread_id = None
        self._ready = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._loop, name="hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(2)

    def stop(self):
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(2)

    def _loop(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        callbacks = {}
        for i, (combo, cb) in enumerate(self.bindings.items(), start=1):
            try:
                mods, vk = parse(combo)
            except HotkeyError as e:
                self.on_error(combo, str(e))
                continue
            if user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                callbacks[i] = cb
                log.info("Hotkey registriert: %s", combo)
            else:
                self.on_error(combo, f"Hotkey {combo} ist bereits von einem anderen Programm belegt.")
        self._ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in callbacks:
                try:
                    callbacks[msg.wParam]()
                except Exception:
                    log.exception("Fehler im Hotkey-Handler")
        for i in callbacks:
            user32.UnregisterHotKey(None, i)
