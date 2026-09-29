# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projekt

Tray-Anwendung (nur Windows) für UI-Tests per Bilderkennung in Firefox, mit menschenähnlicher Maus- und Tastatureingabe. Code, Kommentare, Meldungen, Berichte und Doku sind auf Deutsch, und das soll so bleiben.

## Befehle

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pythonw.exe run.pyw      # Start ohne Konsole
.venv\Scripts\python.exe run.pyw       # Start mit Konsole (Tracebacks sichtbar)
```

Es gibt keine Tests, keinen Linter und keinen Build. Laufzeitfehler landen in `logs/uiauto.log`, Ergebnisse eines Ablaufs in `reports/<zeit>_<sequenz>/bericht.html`.

Ein Szenario lässt sich ohne GUI prüfen:
`.venv\Scripts\python.exe -c "from uiauto import config, scenario; c=config.load(); print(scenario.load_all(c['paths']['scenarios'], c['paths']['images']))"`

## Architektur

**Threads** (`app.py`):
- Der Hauptthread führt eine unsichtbare Tk-`mainloop` aus. Er wird für das Aufnahmewerkzeug (`recorder.py`) und für Dialoge gebraucht. Andere Threads rufen Tk nur über `App.ui(fn)` auf, eine Queue, die alle 50 ms abgefragt wird.
- `pystray` läuft in einem eigenen Thread. `_setup` lädt die Szenarien und registriert die Hotkeys erst, wenn das Icon existiert.
- `hotkeys.py` hat einen eigenen Thread mit Win32-Nachrichtenschleife (`RegisterHotKey`). Er arbeitet mit virtuellen Tastencodes und ist deshalb unabhängig vom Tastaturlayout. `reload()` stoppt den Listener und startet ihn neu.
- Jeder Ablauf läuft in einem Daemon-Thread (`Runner.run`). Es läuft immer nur einer gleichzeitig.

**Stopp und Pause** (`control.py`): Der Runner und `Human` rufen ständig `control.check()` auf. Bei Stopp wirft das `Aborted`, bei Pause blockiert es. Wartezeiten müssen deshalb über `control.sleep()` laufen, nicht über `time.sleep()`, sonst greifen Stopp und Pause nicht sofort. Nach einer Pause ruft der Runner über `on_resume` `human.forget_position()` auf und holt Firefox wieder nach vorn.

**Eingriff durch den Benutzer**: `Human` merkt sich, wo die Maus nach der letzten eigenen Bewegung sein müsste (`_expected`). Weicht die tatsächliche Position um mehr als `interference_tolerance` davon ab, wirft `Human` die Ausnahme `UserInterference`. Der Runner pausiert daraufhin und wiederholt nach „Weiter“ denselben Schritt. `pyautogui.FAILSAFE` (Maus in einer Bildschirmecke) gilt als Abbruch.

**Ablauf eines Szenarios** (`runner.py`): Zielfenster über Fensterklasse und Titel finden (`window.py`), aktivieren, dann erst `precondition` (mit kurzem `precondition_timeout`, ohne Denkpause), danach `steps`. Beim ersten Fehler bricht der Ablauf ab: Fehler-Screenshot, gegebenenfalls mit markiertem Treffer, danach der HTML-Bericht (`report.py`). Bilder werden nur im Rechteck des Zielfensters gesucht, nicht auf dem ganzen Bildschirm.

**Bilderkennung** (`matcher.py`): OpenCV `TM_CCOEFF_NORMED`, standardmäßig in Graustufen. Der Cache ist nach Pfad und mtime geschlüsselt, neu aufgenommene Bilder werden also ohne Neustart erkannt.

**Konfiguration** (`config.py`): Die `DEFAULTS` in `config.py` sind maßgeblich. `config.yaml` überschreibt sie per Deep-Merge, und der Block `human:` eines Szenarios überschreibt sie noch einmal (`merged_human`). Pfade werden relativ zum Projektroot aufgelöst und beim Laden angelegt. Neue Einstellungen gehören zuerst in `DEFAULTS`.

**Neue Aktion hinzufügen**: in `scenario.py` in `ACTIONS` eintragen (Wert ist `True`, wenn der Wert ein Bildname ist), dort die Validierung in `_parse_step` ergänzen, dann `Runner._execute` erweitern und die Tabelle in der README nachziehen. Neue Schrittoptionen gehören in `OPTIONS`, sonst lehnt der Parser sie ab.

## Windows-Eigenheiten, die man leicht bricht

- `dpi.enable()` muss in `run.pyw` **vor** dem Import von pyautogui, mss und tkinter aufgerufen werden. Sonst sind Screenshot- und Klickkoordinaten bei Skalierung ≠ 100 % verschoben.
- Bilder werden über `screen.imread`/`screen.imwrite` gelesen und geschrieben (`np.fromfile`/`imdecode`), weil `cv2.imread` unter Windows an Pfaden mit Umlauten scheitert.
- `mss`-Instanzen sind nicht threadübergreifend nutzbar, deshalb gibt es in `screen._sct()` eine Instanz pro Thread.
- Text wird mit `pynput` getippt, weil das Unicode unabhängig vom Layout unterstützt. `press` nutzt pyautogui-Tastennamen (`pyautogui.KEYBOARD_KEYS`, wird beim Laden geprüft).
- `window.activate` umgeht die Fokussperre von Windows mit dem Trick über `AttachThreadInput`.
- Referenzbilder funktionieren nur mit derselben Windows-Skalierung, demselben Firefox-Zoom und demselben Theme, mit denen sie aufgenommen wurden.
