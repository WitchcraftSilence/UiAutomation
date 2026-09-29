"""Startet die Tray-Anwendung ohne Konsolenfenster (Doppelklick oder `pythonw run.pyw`)."""
from uiauto import dpi

dpi.enable()   # vor allen anderen Imports, damit Koordinaten echte Pixel sind

from uiauto.app import App  # noqa: E402

if __name__ == "__main__":
    App().run()
