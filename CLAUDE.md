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
- Der Hauptthread führt die Tk-`mainloop` aus. Das Hauptfenster ist unsichtbar. Sichtbar ist das Steuerfenster `ui.ControlWindow` (`Toplevel`); andere Threads aktualisieren es nur über `App.ui(window.refresh)` bzw. `window.rebuild`. X blendet es aus, und der Standard-Eintrag im Tray-Menü holt es zurück. Während der Bildaufnahme wird es ausgeblendet (`Recorder.on_done`). Er wird für das Aufnahmewerkzeug (`recorder.py`) und für Dialoge gebraucht. Andere Threads rufen Tk nur über `App.ui(fn)` auf, eine Queue, die alle 50 ms abgefragt wird.
- `pystray` läuft in einem eigenen Thread. `_setup` lädt die Szenarien und registriert die Hotkeys erst, wenn das Icon existiert.
- `hotkeys.py` hat einen eigenen Thread mit Win32-Nachrichtenschleife (`RegisterHotKey`). Er arbeitet mit virtuellen Tastencodes und ist deshalb unabhängig vom Tastaturlayout. `reload()` stoppt den Listener und startet ihn neu.
- Jeder Ablauf läuft in einem Daemon-Thread (`Runner.run`). Es läuft immer nur einer gleichzeitig. Ob etwas läuft, entscheidet `App.state` (`_is_running`), nicht `thread.is_alive()`: Das Menü wird in `_set_state` neu aufgebaut, während der Thread noch lebt. Sonst bleibt es nach dem Ende auf „läuft“ stehen.

**Stopp und Pause** (`control.py`): Der Runner und `Human` rufen ständig `control.check()` auf. Bei Stopp wirft das `Aborted`, bei Pause blockiert es. Wartezeiten müssen deshalb über `control.sleep()` laufen, nicht über `time.sleep()`, sonst greifen Stopp und Pause nicht sofort. Nach einer Pause ruft der Runner über `on_resume` `human.forget_position()` auf und holt Firefox wieder nach vorn.

**Eingriff durch den Benutzer**: `Human` merkt sich, wo die Maus nach der letzten eigenen Bewegung sein müsste (`_expected`). Weicht die tatsächliche Position um mehr als `interference_tolerance` davon ab, wirft `Human` die Ausnahme `UserInterference`. Der Runner pausiert daraufhin und wiederholt nach „Weiter“ denselben Schritt. `pyautogui.FAILSAFE` (Maus in einer Bildschirmecke) gilt als Abbruch.

**Ablauf eines Szenarios** (`runner.py`): Zielfenster über Fensterklasse und Titel finden (`window.py`), aktivieren, dann erst `precondition` (mit kurzem `precondition_timeout`, ohne Denkpause), danach `steps`. Mit `repeat:` laufen die `steps` in Runden (`_run_rounds`), solange das `while`-Bild erscheint. `repeat` kann auch als Schritt verschachtelt werden (`_run_list` ruft `_run_rounds(nested=True)` auf). Die `until`-Bilder aller aktiven Schleifen liegen als Stapel in `Runner._ends` und werden in `_wait_image`/`_wait_first` bei jedem Screenshot mitgeprüft. Sie werfen `EndReached(owner=schleife)`. Jede Schleife fängt nur ihr eigenes `EndReached`, ein `end`-Schritt (`owner=None`) beendet immer den ganzen Ablauf. Beim ersten Fehler bricht der Ablauf ab: Fehler-Screenshot, gegebenenfalls mit markiertem Treffer, danach der HTML-Bericht (`report.py`). Bilder werden nur im Rechteck des Zielfensters gesucht, nicht auf dem ganzen Bildschirm.

**Schnelle Suche** (`Runner._spots`): Jede Fundstelle wird gemerkt. `_wait_image` sucht danach zunächst nur in einem Ausschnitt um diese Stelle (`SPOT_MARGIN`). Jede `FULL_EVERY`-te Suche und die letzte vor einem Timeout laufen über das ganze Fenster, und nur dort werden die `until`-Endbilder geprüft. `_find_bar` macht dasselbe für Leisten und merkt sich zusätzlich die passende Höhenvariante. Der maskierte Farbvergleich über das ganze Fenster kostet etwa 300 ms pro Variante, um die Fundstelle nur wenige ms.

**Bilderkennung** (`matcher.py`): OpenCV `TM_CCOEFF_NORMED`, standardmäßig in Graustufen. Der Cache ist nach Pfad und mtime geschlüsselt, neu aufgenommene Bilder werden also ohne Neustart erkannt.

**Konfiguration** (`config.py`): Die `DEFAULTS` in `config.py` sind maßgeblich. `config.yaml` überschreibt sie per Deep-Merge, und die Blöcke `human:` (`merged_human`) und `matching:` (`Runner.run` setzt `self.matching` und `matcher.grayscale`) eines Szenarios überschreiben sie noch einmal. Zusätzlich kann jeder Bildschritt (und `repeat`) `grayscale` einzeln setzen. Das wird bis `Matcher.find(grayscale=...)` durchgereicht, `None` bedeutet die Voreinstellung. Pfade werden relativ zum Projektroot aufgelöst und beim Laden angelegt. Neue Einstellungen gehören zuerst in `DEFAULTS`.

**Neue Aktion hinzufügen**: in `scenario.py` in `ACTIONS` eintragen (Wert ist `True`, wenn der Wert ein Bildname ist), dort die Validierung in `_parse_step` ergänzen, dann `Runner._execute` erweitern und die Tabelle in der README nachziehen. Neue Schrittoptionen gehören in `OPTIONS`, sonst lehnt der Parser sie ab. `if_seen` und `first_seen` (Liste von `{if, then}`-Fällen) haben verschachtelte Schritte. Der Parser verarbeitet sie rekursiv, `_execute` gibt dafür `(detail, then_schritte)` zurück, und `Runner._run_list` führt sie aus und nummeriert sie als `9.1`, `9.2`.

**Zähler lesen** (`ocr.py`, Aktion `if_counter`): Windows-OCR über die `winrt-*`-Pakete. Die Leiste ist ein Fortschrittsbalken: Der „dunkle Kasten“ rechts ist der offene Teil und kein festes Element. `Runner._find_bar` vergleicht maskiert nur Rahmen und Symbolfeld-Rand (`ocr.bar_frame`, `Matcher.find_all_masked`, Score = 1 − mittlerer Farbabstand/100), auch mit ±1 px Höhe (`ocr.height_variants`, der Browser rundet). Danach prüft `ocr.bar_fill_ok`, ob jede Spalte eine der beiden Füllfarben der Vorlage hat (`fill_profiles`). Der Zähler wird in der Live-Leiste nach der letzten Textlücke gesucht (`counter_x_live`) und nur in den Textzeilen ohne Rahmen gelesen. `read_fraction` liest in mehreren Vergrößerungen und verlangt `MIN_VOTES` übereinstimmende Lesungen. Bedingungen in `when` werden per `ast` gegen eine Whitelist geprüft, nie mit `eval`.

**Einheiten tauschen** (`army.py`, Aktion `replace_damaged`): `find_tiles` findet Kacheln über die grünen Stücke der Lebensbalken. „Voll“ heißt, dass das letzte Segment ganz rechts grün ist. Ein Grün-Anteil über die ganze Breite taugt nicht, weil die Trennstriche dunkel sind. Gleiche Einheitenart wird über den oberen Porträtteil verglichen (`similarity`, ohne Abzeichen und „x8“). Für die Armee kommen die Plätze als feste Positionen aus dem Armee-Bild, ihren Zustand liefert `army.slot_state`. `find_tiles` würde Einheiten mit fast leerem Balken (unter 12 px Grün) übersehen und taugt nur für die verfügbaren Einheiten, wo ohnehin nur volle Balken interessieren. `Runner._replace_damaged` füllt zuerst leere Felder (`army.find_empty`, Vorlage aus `empty:`) mit einer Einheit, deren Art schon in der Armee steht. Danach tauscht es beschädigte Einheiten. Es prüft vor dem Herausnehmen, ob es Ersatz gibt, und kontrolliert nach jedem Klick die Kachelanzahl.

## Windows-Eigenheiten, die man leicht bricht

- `dpi.enable()` muss in `run.pyw` **vor** dem Import von pyautogui, mss und tkinter aufgerufen werden. Sonst sind Screenshot- und Klickkoordinaten bei Skalierung ≠ 100 % verschoben.
- Bilder werden über `screen.imread`/`screen.imwrite` gelesen und geschrieben (`np.fromfile`/`imdecode`), weil `cv2.imread` unter Windows an Pfaden mit Umlauten scheitert.
- `mss`-Instanzen sind nicht threadübergreifend nutzbar, deshalb gibt es in `screen._sct()` eine Instanz pro Thread.
- Text wird mit `pynput` getippt, weil das Unicode unabhängig vom Layout unterstützt. `press` nutzt pyautogui-Tastennamen (`pyautogui.KEYBOARD_KEYS`, wird beim Laden geprüft).
- `window.activate` umgeht die Fokussperre von Windows mit dem Trick über `AttachThreadInput`.
- Referenzbilder funktionieren nur mit derselben Windows-Skalierung, demselben Firefox-Zoom und demselben Theme, mit denen sie aufgenommen wurden.
