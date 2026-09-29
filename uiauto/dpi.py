"""DPI-Awareness setzen, damit Screenshot- und Klick-Koordinaten echte Pixel sind.

Muss aufgerufen werden, bevor pyautogui, mss oder tkinter importiert werden.
"""
import ctypes


def enable():
    # Per-Monitor v2 (Windows 10 1703+), sonst schrittweise Fallbacks
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass
