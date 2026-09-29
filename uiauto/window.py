"""Zielfenster (Firefox) finden, in den Vordergrund holen und seine Position ermitteln."""
import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
dwmapi = ctypes.windll.dwmapi

DWMWA_EXTENDED_FRAME_BOUNDS = 9
SW_RESTORE = 9

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class WindowError(Exception):
    pass


def _text(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def find(window_class, title_contains=""):
    """Oberstes sichtbares Fenster mit passender Klasse und Titel (Z-Reihenfolge)."""
    found = []

    def callback(hwnd, _):
        if (user32.IsWindowVisible(hwnd)
                and _class(hwnd) == window_class
                and _text(hwnd)
                and title_contains.lower() in _text(hwnd).lower()):
            found.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    if not found:
        hint = f" mit Titel '{title_contains}'" if title_contains else ""
        raise WindowError(f"Kein Fenster der Klasse {window_class}{hint} gefunden.")
    return found[0]


def rect(hwnd):
    """Sichtbarer Fensterbereich in Bildschirm-Pixeln: (left, top, width, height)."""
    r = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                                    ctypes.byref(r), ctypes.sizeof(r)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def title(hwnd):
    return _text(hwnd)


def activate(hwnd, timeout=2.0):
    """Fenster in den Vordergrund holen (mit AttachThreadInput-Trick gegen die Fokussperre)."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    if user32.GetForegroundWindow() == hwnd:
        return

    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    own_thread = kernel32.GetCurrentThreadId()
    attached = fg_thread and fg_thread != own_thread and user32.AttachThreadInput(own_thread, fg_thread, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(own_thread, fg_thread, False)

    end = time.time() + timeout
    while time.time() < end:
        if user32.GetForegroundWindow() == hwnd:
            return
        time.sleep(0.05)
    raise WindowError("Fenster konnte nicht in den Vordergrund geholt werden.")


def is_foreground(hwnd):
    return user32.GetForegroundWindow() == hwnd
