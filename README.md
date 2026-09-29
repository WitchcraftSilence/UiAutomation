# UiAutomation

UI-Tests per Bilderkennung mit menschenähnlicher Maus und Tastatur. Das Tool läuft im Tray, ohne Konsolenfenster.

## Installation

```powershell
winget install Python.Python.3.12
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Start: `.venv\Scripts\pythonw.exe run.pyw`. Am besten legt man dafür eine Verknüpfung an.

## Bedienung

1. Firefox von Hand an die gewünschte Stelle bringen.
2. Die Sequenz per Hotkey (z. B. `Strg+Alt+1`) oder über das Tray-Menü starten.
3. Das Tray-Icon zeigt den Zustand: grün = bereit, rot = läuft, gelb = pausiert.

| Hotkey | Funktion |
|---|---|
| `Strg+Alt+F10` | Referenzbild aufnehmen (Bereich aufziehen, Namen eingeben; der Name landet in der Zwischenablage) |
| `Strg+Alt+F11` | Pause / Weiter |
| `Strg+Alt+F12` | Stopp |
| Maus in eine Bildschirmecke | Not-Aus |

Bewegt man während eines Ablaufs die Maus, **pausiert** der Ablauf automatisch. Mit „Weiter“ holt er Firefox wieder nach vorn und macht dort weiter.

## Sequenzen

Sequenzen sind YAML-Dateien unter `scenarios/`, siehe `scenarios/beispiel.yaml.example`.

| Aktion | Wert | Optionen |
|---|---|---|
| `click`, `double_click`, `right_click`, `move` | Bild | `offset: [dx, dy]`, `timeout`, `threshold` |
| `wait_for`, `expect` | Bild | `timeout`, `threshold` |
| `expect_not` | Bild (wartet, bis es verschwunden ist) | `timeout`, `threshold` |
| `type` | Text (Umlaute möglich) | |
| `press` | `enter`, `tab`, `ctrl+s`, … | `times` |
| `scroll` | Rasten (negativ = nach unten) | |
| `wait` | Sekunden oder `[min, max]` | |

Jede Aktion kann zusätzlich eine `note` als Kommentar im Bericht bekommen.

## Menschenähnliches Verhalten (`human` in config.yaml, pro Sequenz überschreibbar)

- **Klickpunkt**: zufällig im gefundenen Bild, normalverteilt um die Mitte. Der Rand wird nie getroffen.
- **Mausweg**: gekrümmte Bézier-Kurve mit einem Geschwindigkeitsprofil wie beim Menschen (langsam anfahren, schnell in der Mitte, langsam abbremsen) und leichtem Zittern.
- **Dauer**: nach Fitts' Gesetz, also länger bei weiten Wegen und kleinen Zielen. Gelegentlich schießt die Maus leicht übers Ziel hinaus und korrigiert.
- **Klick**: kurzes Verweilen vor dem Klick, variable Haltedauer der Taste.
- **Tippen**: unregelmäßiges Tempo mit gelegentlichem Stocken, nach Leerzeichen und Satzzeichen etwas langsamer.
- **Denkpause** vor jedem Schritt.

## Ergebnisse

- Bericht: `reports/<zeit>_<sequenz>/bericht.html`, bei Fehlern mit Screenshot.
- Log: `logs/uiauto.log`.

Referenzbilder sollte man mit derselben Windows-Skalierung, demselben Firefox-Zoom und demselben Theme aufnehmen, mit denen später getestet wird.
