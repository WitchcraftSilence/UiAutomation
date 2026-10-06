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
3. Das Steuerfenster (und das Tray-Icon) zeigen den Zustand: grün = bereit, rot = läuft, gelb = pausiert.

Beim Start öffnet sich ein kleines **Steuerfenster** mit allen Sequenzen und den Schaltflächen Pause/Weiter, Stopp, Referenzbild aufnehmen, Letzten Bericht öffnen, Ordner, Neu laden und Beenden. Mit X wird es nur ausgeblendet; Doppelklick auf das Tray-Icon (oder „Fenster anzeigen“) holt es zurück. Während ein Ablauf pausiert ist, kann direkt eine andere Sequenz gestartet werden; die pausierte wird dann beendet.

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
| `click`, `double_click`, `right_click`, `move` | Bild | `offset: [dx, dy]`, `timeout`, `threshold`, `grayscale`, `check_end` |
| `wait_for`, `expect` | Bild | `timeout`, `threshold`, `grayscale`, `check_end` |
| `expect_not` | Bild (wartet, bis es verschwunden ist) | `timeout`, `threshold`, `grayscale` |
| `type` | Text (Umlaute möglich) | |
| `press` | `enter`, `tab`, `ctrl+s`, … | `times` |
| `scroll` | Rasten (negativ = nach unten) | |
| `wait` | Sekunden oder `[min, max]` | |
| `click_here` | Anzahl Klicks (`0` = bis Stopp) | schnell dort klicken, wo die Maus steht (etwa 7–9 Klicks/s; die Maus verrutscht nur selten um 1 px; Tempo und Zittern über `rapid_*` in `human`) |
| `negotiate` | Bilder `suggestions`, `menu`, `pay`, `success` | `timeout`, siehe unten |
| `end` | optionaler Grund, z. B. `end: Niederlage` (oder nur `- end`) | beendet den Ablauf sofort erfolgreich, meist in `if_seen`/`then` |

Jede Aktion kann zusätzlich eine `note` als Kommentar im Bericht bekommen.

Vor jedem Schritt macht das Tool eine Denkpause (`think_time`). Mit `think` bekommt ein einzelner Schritt eine eigene Pause, z. B. für schnelle Tastenfolgen:

```yaml
  - press: r
  - press: b
    think: [0.05, 0.12]   # Sekunden oder [min, max]; 0 = keine Denkpause
```

`offset` wird von der Bildmitte aus gemessen, entweder in Pixeln (`[150, 0]`) oder in Vielfachen der Bildgröße: `[0, 1.5h]` heißt 1,5 × Bildhöhe nach unten, `[-0.5w, 0]` eine halbe Bildbreite nach links. Negative Werte zeigen nach links bzw. oben.

### Optionale Schritte

```yaml
  - if_seen: hinweis.png   # erscheint das Bild innerhalb von timeout …
    timeout: 3
    skip_if: weiter.png    # optional: ist dieses Bild zu sehen, nicht länger warten
    then:                  # … laufen diese Schritte, sonst geht es ohne Fehler weiter
      - press: esc
      - expect_not: hinweis.png
```

Ohne `timeout` wartet `if_seen` die vollen 10 s, bevor es aufgibt. Darum sollte man einen kurzen Wert angeben. Oder man gibt mit `skip_if` ein Bild an, an dem man erkennt, dass das optionale Bild nicht mehr kommt. Sind beide zu sehen, gewinnt das `if_seen`-Bild.

### Zähler lesen

```yaml
  - if_counter: Leiste.png       # Bild der ganzen Leiste; mehrere Farbschemata: [a.png, b.png]
    when: x >= total - 2         # Bedingung mit x und total
    timeout: 2
    then:
      - end: Ziel erreicht
```

Die Leiste ist ein Fortschrittsbalken: hell = erreicht, dunkel = offen. Das Tool findet sie über die Teile, die sich nie ändern (Rahmen und Hintergrund des Symbolfelds links), und prüft dann, ob die Leiste nur aus den beiden Füllfarben der Vorlage besteht. Beschriftung, Symbol und Füllstand dürfen also wechseln, Leisten in anderen Farben (z. B. anderer Gilden) werden ignoriert. Die Vorlage muss deshalb eine **teilweise gefüllte** Leiste zeigen. Dann liest es rechts den Zähler `x/total` mit der Windows-Texterkennung und führt `then` nur aus, wenn die Bedingung zutrifft. Der Zähler wird mehrfach in verschiedenen Vergrößerungen gelesen und nur ein Ergebnis angenommen, das die Mehrheit bestätigt. Ist er nicht sicher lesbar, wird der Schritt übersprungen (steht im Bericht) und beim nächsten Mal erneut geprüft. Darum besser `>=` als `==` verwenden.

In `when` erlaubt: `x`, `total`, ganze Zahlen, `+ - * //` und Vergleiche (`>= > <= < == !=`).

### Beschädigte Einheiten tauschen

```yaml
  - replace_damaged:
      army: Armee.png        # Bild des Armee-Bereichs; gefunden über die oberen 25 px (Überschrift)
      pool: Verfuegbar.png   # Bild der verfügbaren Einheiten; gefunden über die oberen 40 px (Reiter)
      empty: Leer.png        # optional: Bild eines leeren Felds; solche Felder werden nur aufgefüllt
    if_missing:              # optional: kein gesunder Ersatz -> diese Schritte (sonst Fehler)
      - end: kein Ersatz
```

Für jede Einheit der Armee, deren Lebensbalken nicht voll ist (letztes Segment nicht grün), sucht das Tool unter den verfügbaren Einheiten eine derselben Art mit vollem Balken. Nur wenn es einen Ersatz gibt, nimmt es die beschädigte Einheit heraus (Klick) und setzt den Ersatz ein (Klick). Nach jedem Klick prüft es, ob sich die Armee wirklich verändert hat. Die Einheitenart wird über das Porträt verglichen und muss nicht vorher aufgenommen werden. Leere Felder (mit `empty`) werden zuerst aufgefüllt, mit einer gesunden Einheit, deren Art schon in der Armee steht.

### Verhandlung (Gildenexpedition)

```yaml
  - negotiate:
      suggestions: Vorschlag.png  # Vorschlagstabelle; gesucht wird nur die Kopfzeile "Person 1 … Person 5"
      menu: Ressource.png         # Überschrift "Ressource auswählen" des Auswahlmenüs
      pay: Bezahlen.png           # Knopf "Bezahlen & Verhandeln"
      success: Erfolg.png         # erscheint bei gewonnener Verhandlung; nur den Schriftzug aufnehmen, ohne Hintergrund
    timeout: 5
```

Je Runde liest das Tool die aktuelle (unterste) Zeile der Vorschlagstabelle. Sie ist die einzige mit kräftigen Güter-Symbolen auf schwarzem Kreis, ältere Zeilen sind blass. Für jede Person mit Vorschlag drückt es deren Taste 1–5 (wirkt wie ein Klick auf „Angebot machen“), sucht das vorgeschlagene Gut im Menü und klickt darauf. Bevor es das Gut sucht, fährt die Maus kurz auf die Überschriftsleiste des Menüs, denn über einem Knopf „Angebot machen“ würde dessen Tooltip das Gut verdecken. Erscheint das Menü nicht, drückt es die Taste bis zu dreimal. Danach klickt es auf „Bezahlen & Verhandeln“. Erscheint dann das Erfolgsbild, endet der Schritt. Erscheint stattdessen eine neue Zeile, folgt die nächste Runde, höchstens 10 Runden. Die Güter werden zur Laufzeit zwischen Tabelle und Menü verglichen und müssen nicht einzeln aufgenommen werden. Das Erfolgsfenster schließen die Schritte danach, siehe `scenarios/Verhandlung.yaml`.

### Einstellungen im Steuerfenster

```yaml
params:
  klicks:
    options: [5, 10, 25, 50]   # erscheinen als Auswahlknöpfe unter der Sequenz
    default: 25                # optional, sonst die erste Möglichkeit

steps:
  - click_here: $klicks        # $name setzt den gewählten Wert ein
```

Die Auswahl gilt für den Start über das Fenster und über den Hotkey. Sie bleibt beim Neu laden erhalten, aber nicht über einen Neustart des Tools hinweg.

### Mehrere Schleifen nacheinander

`repeat` darf auch als Schritt in `steps` stehen. So lassen sich mehrere Schleifen hintereinander (oder ineinander) bauen:

```yaml
steps:
  - repeat:                # Schleife 1
      while: kampf.png
      until: niederlage.png
    steps:                 # auf gleicher Höhe wie "repeat:", nicht darunter
      - press: a
  - repeat:                # Schleife 2, beginnt, wenn Schleife 1 fertig ist
      while: [held_a.png, held_b.png]
    steps:
      - press: b
  - press: esc             # danach geht es normal weiter
```

Unterschiede zum `repeat` der ganzen Sequenz:
- `until` beendet nur diese Schleife, danach geht es mit dem nächsten Schritt weiter.
- Erscheint das `while`-Bild gar nicht, läuft die Schleife null Runden, und das ist kein Fehler.
- `end` beendet weiterhin den ganzen Ablauf, ebenso das `until` einer äußeren Schleife.

### Eines von mehreren Bildern

```yaml
  - first_seen:            # wartet, bis eines der Bilder erscheint (bis timeout)
      - if: variante_a.png
        then:
          - press: d
      - if: variante_b.png
        grayscale: false   # optional pro Bild
        then:
          - press: a
    timeout: 10
```

Es laufen nur die Schritte des Bildes, das zuerst erscheint. Sind mehrere gleichzeitig zu sehen, gewinnt das weiter oben stehende. Erscheint keines, schlägt der Schritt fehl.

### Wiederholen

```yaml
repeat:
  while: bild.png   # vor jeder Runde: ist das Bild zu sehen, laufen die steps erneut
                    # oder mehrere: while: [a.png, b.png], dann reicht eines davon
  until: ende.png   # optional: erscheint dieses Bild, endet der Ablauf sofort erfolgreich
  timeout: 5        # so lange wird vor jeder Runde darauf gewartet (Standard 5 s)
  max: 50           # optional, 0 = unbegrenzt
  grayscale: false  # optional, siehe unten
  threshold: 0.9    # optional
```

Die Wiederholung endet erfolgreich, sobald das `while`-Bild nicht mehr erscheint oder das `until`-Bild auftaucht. Das `until`-Bild wird bei jeder Suche über das ganze Fenster mitgeprüft, also auch mitten in einer Runde. Findet ein Schritt sein Bild schnell an der gemerkten Stelle wieder, entfällt diese Prüfung. Steht derselbe Knopf auch im Enddialog (z. B. OK im Niederlage-Fenster), gibt man dem Schritt `check_end: true`. Dann wird vor dem Treffer noch einmal nach dem `until`-Bild gesucht, was 70–150 ms kostet:

```yaml
  - wait_for: ok.png
    check_end: true   # ok.png steht auch im Niederlage-Dialog
  - press: esc
```

Fehlt das `while`-Bild schon vor der ersten Runde, gilt der Ablauf als fehlgeschlagen.

### Farbe statt Graustufen

Standardmäßig wird in Graustufen verglichen. Das ist robust, aber Bilder, die sich nur in der Farbe unterscheiden (z. B. ein aktiver orangefarbener und ein inaktiver grauer Knopf), gelten dann als gleich. Für diese Bilder gibt man `grayscale: false` an:

```yaml
  - click: knopf_aktiv.png
    grayscale: false          # nur dieser Schritt vergleicht in Farbe

repeat:
  while: start.png
  until: ende.png
  grayscale: false            # gilt für while und until
```

Soll eine ganze Sequenz in Farbe vergleichen, geht auch ein Block `matching: {grayscale: false}` am Anfang der Datei. Er überschreibt alle Werte aus `matching` in `config.yaml` für diese Sequenz.

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
