#!/usr/bin/env python3
"""
NMEA/GNSS Parser — Graphical Interface
----------------------------------------
Run: python3 gui.py
"""

import subprocess
import sys
import threading
import tkinter as tk
from io import StringIO
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent))
from nmea_parser import (
    analyze_timing, compute_statistics, export_csv, export_kml,
    parse_file, print_report,
)


# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
BG        = "#1e1e2e"
BG2       = "#2a2a3e"
BG3       = "#313145"
FG        = "#cdd6f4"
FG_DIM    = "#7f849c"
GREEN     = "#a6e3a1"
YELLOW    = "#f9e2af"
RED       = "#f38ba8"
BLUE      = "#89b4fa"
MAUVE     = "#cba6f7"
TEAL      = "#94e2d5"
BORDER    = "#45475a"

FONT_UI   = ("Segoe UI", 10)
FONT_MONO = ("Monospace", 9)
FONT_H1   = ("Segoe UI", 12, "bold")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("NMEA / GNSS Parser")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(700, 540)

        self._kml_path: Path | None = None
        self._running = False

        self._build_ui()
        self._center()

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self):
        # ── Top frame: file selection ───────────────────────────────────────
        top = tk.Frame(self, bg=BG, padx=12, pady=10)
        top.pack(fill="x")

        tk.Label(top, text="NMEA / GNSS Parser", font=FONT_H1,
                 bg=BG, fg=MAUVE).grid(row=0, column=0, columnspan=3,
                                        sticky="w", pady=(0, 8))

        tk.Label(top, text="Arquivo NMEA:", font=FONT_UI,
                 bg=BG, fg=FG_DIM).grid(row=1, column=0, sticky="w")
        self.input_var = tk.StringVar()
        tk.Entry(top, textvariable=self.input_var, font=FONT_UI,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", bd=4, width=55).grid(
            row=1, column=1, padx=6, sticky="ew")
        tk.Button(top, text="Abrir…", font=FONT_UI,
                  bg=BG3, fg=FG, activebackground=BORDER,
                  relief="flat", cursor="hand2",
                  command=self._browse_input).grid(row=1, column=2)

        tk.Label(top, text="Salvar em:", font=FONT_UI,
                 bg=BG, fg=FG_DIM).grid(row=2, column=0, sticky="w",
                                         pady=(6, 0))
        self.output_var = tk.StringVar()
        tk.Entry(top, textvariable=self.output_var, font=FONT_UI,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", bd=4, width=55).grid(
            row=2, column=1, padx=6, sticky="ew", pady=(6, 0))
        tk.Button(top, text="Pasta…", font=FONT_UI,
                  bg=BG3, fg=FG, activebackground=BORDER,
                  relief="flat", cursor="hand2",
                  command=self._browse_output).grid(row=2, column=2,
                                                    pady=(6, 0))

        self.include_invalid = tk.BooleanVar(value=False)
        tk.Checkbutton(top, text="Incluir fixes inválidos (qualidade 0)",
                       variable=self.include_invalid,
                       font=FONT_UI, bg=BG, fg=FG_DIM,
                       selectcolor=BG2, activebackground=BG).grid(
            row=3, column=1, sticky="w", pady=(4, 0))

        top.columnconfigure(1, weight=1)

        # ── Action buttons ──────────────────────────────────────────────────
        btn_frame = tk.Frame(self, bg=BG, padx=12, pady=4)
        btn_frame.pack(fill="x")

        self.run_btn = tk.Button(
            btn_frame, text="▶  Processar", font=("Segoe UI", 10, "bold"),
            bg=BLUE, fg=BG, activebackground=TEAL,
            relief="flat", padx=16, pady=6, cursor="hand2",
            command=self._run)
        self.run_btn.pack(side="left", padx=(0, 8))

        self.earth_btn = tk.Button(
            btn_frame, text="🌍  Abrir no Google Earth", font=FONT_UI,
            bg=BG3, fg=FG_DIM, activebackground=BORDER,
            relief="flat", padx=12, pady=6, cursor="hand2",
            state="disabled", command=self._open_kml)
        self.earth_btn.pack(side="left", padx=(0, 8))

        tk.Button(btn_frame, text="Limpar", font=FONT_UI,
                  bg=BG3, fg=FG_DIM, activebackground=BORDER,
                  relief="flat", padx=12, pady=6, cursor="hand2",
                  command=self._clear).pack(side="left")

        # ── Status bar ──────────────────────────────────────────────────────
        status_frame = tk.Frame(self, bg=BG2, padx=12, pady=6)
        status_frame.pack(fill="x")

        self.pps_var    = tk.StringVar(value="PPS: —")
        self.fixes_var  = tk.StringVar(value="Fixes: —")
        self.dist_var   = tk.StringVar(value="Distância: —")
        self.uptime_var = tk.StringVar(value="Uptime GPS: —")

        for var, col in [(self.pps_var, FG), (self.fixes_var, FG),
                         (self.dist_var, FG), (self.uptime_var, FG)]:
            tk.Label(status_frame, textvariable=var, font=FONT_UI,
                     bg=BG2, fg=col).pack(side="left", padx=12)

        # ── Report output ───────────────────────────────────────────────────
        out_frame = tk.Frame(self, bg=BG, padx=12)
        out_frame.pack(fill="both", expand=True, pady=(0, 12))

        self.text = tk.Text(
            out_frame, font=FONT_MONO, bg=BG2, fg=FG,
            insertbackground=FG, relief="flat", bd=0,
            wrap="none", state="disabled")
        self.text.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(out_frame, orient="vertical",
                           command=self.text.yview)
        sb.pack(side="right", fill="y")
        self.text.configure(yscrollcommand=sb.set)

        # colour tags for the report
        self.text.tag_configure("ok",      foreground=GREEN)
        self.text.tag_configure("warn",    foreground=YELLOW)
        self.text.tag_configure("fail",    foreground=RED)
        self.text.tag_configure("header",  foreground=MAUVE)
        self.text.tag_configure("key",     foreground=BLUE)
        self.text.tag_configure("dim",     foreground=FG_DIM)
        self.text.tag_configure("value",   foreground=TEAL)

    # -----------------------------------------------------------------------
    # Actions
    # -----------------------------------------------------------------------

    def _browse_input(self):
        path = filedialog.askopenfilename(
            title="Selecione o arquivo NMEA",
            filetypes=[("NMEA files", "*.nmea *.txt *.log"), ("All files", "*.*")])
        if path:
            self.input_var.set(path)
            # Auto-fill output dir
            if not self.output_var.get():
                self.output_var.set(str(Path(path).parent))

    def _browse_output(self):
        path = filedialog.askdirectory(title="Selecione a pasta de saída")
        if path:
            self.output_var.set(path)

    def _clear(self):
        self._set_text("")
        self.pps_var.set("PPS: —")
        self.fixes_var.set("Fixes: —")
        self.dist_var.set("Distância: —")
        self.uptime_var.set("Uptime GPS: —")
        self._kml_path = None
        self.earth_btn.configure(state="disabled", fg=FG_DIM)

    def _run(self):
        if self._running:
            return
        input_path = self.input_var.get().strip()
        if not input_path:
            messagebox.showwarning("Atenção", "Selecione um arquivo NMEA primeiro.")
            return
        p = Path(input_path)
        if not p.exists():
            messagebox.showerror("Erro", f"Arquivo não encontrado:\n{p}")
            return

        out_str = self.output_var.get().strip()
        out_dir = Path(out_str).resolve() if out_str else p.parent.resolve()

        self._running = True
        self.run_btn.configure(state="disabled", text="Processando…")
        self._clear()
        self._append("Processando: ", "dim")
        self._append(str(p) + "\n", "value")

        def worker():
            try:
                fixes, zda_events, parse_stats = parse_file(
                    p, ignore_invalid=self.include_invalid.get())

                timing = analyze_timing(zda_events)

                if not fixes and not zda_events:
                    self.after(0, lambda: self._append(
                        "\nNenhum dado encontrado no arquivo.\n", "fail"))
                    return

                stats   = compute_statistics(fixes) if fixes else {}
                out_dir.mkdir(parents=True, exist_ok=True)
                stem    = p.stem
                csv_out = out_dir / f"{stem}_fixes.csv"
                kml_out = out_dir / f"{stem}_track.kml"
                if fixes:
                    export_csv(fixes, csv_out)
                    export_kml(fixes, kml_out, name=stem)

                # Capture report text
                old_stdout = sys.stdout
                sys.stdout = buf = StringIO()
                print_report(stats, parse_stats, timing, p,
                             csv_out if fixes else Path("—"),
                             kml_out if fixes else Path("—"))
                sys.stdout = old_stdout
                report = buf.getvalue()

                kml_result = kml_out if fixes else None
                self.after(0, lambda: self._display_report(
                    report, stats, timing, kml_result))

            except Exception as e:
                msg = str(e)
                self.after(0, lambda: self._append(f"\nErro: {msg}\n", "fail"))
            finally:
                self.after(0, self._done)

        threading.Thread(target=worker, daemon=True).start()

    def _done(self):
        self._running = False
        self.run_btn.configure(state="normal", text="▶  Processar")

    def _open_kml(self):
        if self._kml_path and self._kml_path.exists():
            try:
                subprocess.Popen(["xdg-open", str(self._kml_path)])
            except Exception as e:
                messagebox.showerror("Erro", f"Não foi possível abrir o KML:\n{e}")
        else:
            messagebox.showinfo("Aviso", "Arquivo KML não encontrado.\nProcesse um arquivo primeiro.")

    # -----------------------------------------------------------------------
    # Report display
    # -----------------------------------------------------------------------

    def _display_report(self, report: str, stats: dict, timing: dict, kml_out: Path):
        self._set_text("")
        for line in report.splitlines():
            if line.startswith("═") or line.startswith("─"):
                self._append(line + "\n", "dim")
            elif "[OK]" in line:
                self._append(line + "\n", "ok")
            elif "[WARN]" in line:
                self._append(line + "\n", "warn")
            elif "[FAIL]" in line:
                self._append(line + "\n", "fail")
            elif line.strip().isupper() and line.strip():
                self._append(line + "\n", "header")
            elif ":" in line and not line.startswith(" "):
                self._append(line + "\n", "key")
            else:
                self._append(line + "\n")

        # Update status bar
        pps    = timing.get("pps_status", "—") if timing else "—"
        uptime = timing.get("uptime_pct", 0.0)  if timing else 0.0
        color  = GREEN if pps == "LOCKED" else (YELLOW if pps == "DEGRADED" else RED)

        self.pps_var.set(f"PPS: {pps}")
        self.fixes_var.set(f"Fixes: {stats.get('valid_fixes', 0)}")
        dist = stats.get("distance_nm", 0.0)
        self.dist_var.set(f"Distância: {dist:.3f} nm")
        self.uptime_var.set(f"Uptime GPS: {uptime:.1f}%")

        # Update PPS label colour
        for widget in self.nametowidget(".").winfo_children():
            self._set_pps_color(widget, color)

        # Enable Google Earth button
        self._kml_path = kml_out
        self.earth_btn.configure(state="normal", fg=GREEN)

    def _set_pps_color(self, widget, color):
        """Recursively find PPS label and update its colour."""
        try:
            if isinstance(widget, tk.Label) and "PPS:" in str(widget.cget("textvariable")):
                widget.configure(fg=color)
        except Exception:
            pass
        for child in widget.winfo_children():
            self._set_pps_color(child, color)

    # -----------------------------------------------------------------------
    # Text helpers
    # -----------------------------------------------------------------------

    def _set_text(self, content: str):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if content:
            self.text.insert("end", content)
        self.text.configure(state="disabled")

    def _append(self, content: str, tag: str = ""):
        self.text.configure(state="normal")
        if tag:
            self.text.insert("end", content, tag)
        else:
            self.text.insert("end", content)
        self.text.see("end")
        self.text.configure(state="disabled")

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _center(self):
        self.update_idletasks()
        w, h = 780, 580
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")


if __name__ == "__main__":
    app = App()
    app.mainloop()
