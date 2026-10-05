"""Kleines Steuerfenster mit den Funktionen des Tray-Menüs.

Läuft im Tk-Hauptthread. Andere Threads aktualisieren es nur über App.ui(window.refresh).
Schließen (X) blendet das Fenster aus; das Tray-Icon holt es zurück.
"""
import tkinter as tk
from tkinter import ttk

COLORS = {"idle": "#2da44e", "running": "#cf222e", "paused": "#d4a72c"}
TITLES = {"idle": "bereit", "running": "läuft", "paused": "pausiert"}


class ControlWindow:
    def __init__(self, root, app):
        self.app = app
        self.win = tk.Toplevel(root)
        self.win.title("UI-Automation")
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        self.topmost = tk.BooleanVar(value=True)
        self.win.attributes("-topmost", True)
        self._scenario_buttons = []
        self._hidden_for_record = False

        style = ttk.Style(self.win)
        style.configure("Scenario.TButton", anchor="w", padding=(8, 4))
        style.configure("Hotkey.TLabel", foreground="#6e7781")

        pad = {"padx": 8, "pady": 4}
        outer = ttk.Frame(self.win, padding=8)
        outer.pack(fill="both", expand=True)

        # Status: farbiger Punkt + Text
        head = ttk.Frame(outer)
        head.pack(fill="x", **pad)
        self.dot = tk.Canvas(head, width=16, height=16, highlightthickness=0)
        self.dot.pack(side="left")
        self._dot = self.dot.create_oval(2, 2, 14, 14, fill=COLORS["idle"], outline="")
        self.status = ttk.Label(head, text="bereit", font=("Segoe UI", 10, "bold"))
        self.status.pack(side="left", padx=(6, 0))

        # Sequenzen
        self.list_frame = ttk.LabelFrame(outer, text="Sequenzen", padding=6)
        self.list_frame.pack(fill="x", **pad)

        # Ablauf steuern
        run = ttk.Frame(outer)
        run.pack(fill="x", **pad)
        hk = app.cfg["hotkeys"]
        self.pause_btn = ttk.Button(run, text="Pause", command=self._toggle_pause)
        self.pause_btn.pack(side="left", expand=True, fill="x")
        self.stop_btn = ttk.Button(run, text="Stopp", command=app.control.stop)
        self.stop_btn.pack(side="left", expand=True, fill="x", padx=(6, 0))
        ttk.Label(outer, text=f"Pause {hk['pause']} · Stopp {hk['stop']}", style="Hotkey.TLabel").pack(**pad)

        # Werkzeuge
        tools = ttk.Frame(outer)
        tools.pack(fill="x", **pad)
        ttk.Button(tools, text="Referenzbild aufnehmen", command=app.record).pack(fill="x")
        self.report_btn = ttk.Button(tools, text="Letzten Bericht öffnen", command=app.open_report)
        self.report_btn.pack(fill="x", pady=(4, 0))
        row = ttk.Frame(tools)
        row.pack(fill="x", pady=(4, 0))
        folders = ttk.Menubutton(row, text="Ordner")
        menu = tk.Menu(folders, tearoff=False)
        for label, key in (("Szenarien", "scenarios"), ("Bilder", "images"),
                           ("Berichte", "reports"), ("Logs", "logs")):
            menu.add_command(label=label, command=lambda k=key: app.open_folder(k))
        folders["menu"] = menu
        folders.pack(side="left", expand=True, fill="x")
        ttk.Button(row, text="Neu laden", command=app.reload).pack(side="left", expand=True, fill="x", padx=(6, 0))

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", **pad)
        ttk.Checkbutton(bottom, text="Immer im Vordergrund", variable=self.topmost,
                        command=lambda: self.win.attributes("-topmost", self.topmost.get())).pack(side="left")
        ttk.Button(bottom, text="Beenden", command=app.quit).pack(side="right")

        self.rebuild()

    # ------------------------------------------------------------ Inhalt

    def rebuild(self):
        """Schaltflächen der Sequenzen neu aufbauen (nach dem Laden/Neu laden)."""
        for w in self.list_frame.winfo_children():
            w.destroy()
        self._scenario_buttons = []
        self._param_vars = []
        if not self.app.scenarios:
            ttk.Label(self.list_frame, text="(keine Sequenzen)").pack(anchor="w")
        for s in self.app.scenarios:
            row = ttk.Frame(self.list_frame)
            row.pack(fill="x", pady=1)
            btn = ttk.Button(row, text=s.name, style="Scenario.TButton", width=22,
                             command=lambda s=s: self.app.start_scenario(s))
            btn.pack(side="left", fill="x", expand=True)
            if s.hotkey:
                ttk.Label(row, text=s.hotkey, style="Hotkey.TLabel", width=12).pack(side="left", padx=(6, 0))
            self._scenario_buttons.append(btn)
            for name, spec in s.params.items():
                self._param_row(s, name, spec)
        self.refresh()

    def _param_row(self, scenario, name, spec):
        """Auswahlknöpfe für eine Einstellung unter der Sequenz; die Wahl gilt auch für den Hotkey."""
        row = ttk.Frame(self.list_frame)
        row.pack(fill="x", padx=(12, 0), pady=(0, 3))
        ttk.Label(row, text=f"{name}:", style="Hotkey.TLabel").pack(side="left")
        current = self.app.params_for(scenario)[name]
        var = tk.StringVar(value=str(current))
        by_text = {str(o): o for o in spec["options"]}

        def chosen(*_):
            self.app.param_values[(scenario.name, name)] = by_text[var.get()]
        var.trace_add("write", chosen)
        self._param_vars.append(var)        # Referenz halten, sonst räumt Python die Variable weg
        for o in spec["options"]:
            ttk.Radiobutton(row, text=str(o), value=str(o), variable=var).pack(side="left", padx=(6, 0))

    def refresh(self):
        """Status und aktive/ausgegraute Schaltflächen an den Zustand anpassen."""
        state = self.app.state
        self.dot.itemconfigure(self._dot, fill=COLORS[state])
        text = TITLES[state]
        if state != "idle" and self.app.current:
            text += f": {self.app.current}"
        self.status.configure(text=text)
        startable = "disabled" if state == "running" else "normal"     # bei Pause darf neu gestartet werden
        for b in self._scenario_buttons:
            b.configure(state=startable)
        active = "normal" if state != "idle" else "disabled"
        self.pause_btn.configure(state=active, text="Weiter" if state == "paused" else "Pause")
        self.stop_btn.configure(state=active)
        self.report_btn.configure(state="normal" if self.app.last_report else "disabled")

    def _toggle_pause(self):
        if self.app.state != "idle":
            self.app.control.toggle_pause()

    # ------------------------------------------------------------ Sichtbarkeit

    def show(self):
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

    def hide(self):
        self.win.withdraw()

    def hide_for_record(self):
        """Während der Bildaufnahme ausblenden, damit das Fenster nicht im Screenshot landet."""
        self._hidden_for_record = self.win.state() != "withdrawn"
        self.win.withdraw()

    def restore_after_record(self):
        if self._hidden_for_record:
            self.win.deiconify()
        self._hidden_for_record = False
