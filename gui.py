#!/usr/bin/env python3
"""
NMEA/GNSS Parser — Graphical Interface
----------------------------------------
Run: python3 gui.py
"""

import os
import queue
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from io import StringIO
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).parent))
from nmea_parser import (
    ZDAEvent, analyze_timing, build_zda_timestamp,
    compute_statistics, export_csv, export_kml,
    hdop_label, parse_file, parse_gga, parse_rmc, parse_vtg,
    print_report, verify_checksum, QUALITY_LABELS,
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
FONT_BOLD = ("Segoe UI", 10, "bold")


# ---------------------------------------------------------------------------
# Cross-platform file opener
# ---------------------------------------------------------------------------

def open_file(path: Path):
    """Opens a file with the default system application (Windows/macOS/Linux)."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        raise RuntimeError(str(e))


def _now() -> str:
    return datetime.utcnow().strftime("%H:%M:%S UTC")


# ---------------------------------------------------------------------------
# Floating PPS Indicator
# ---------------------------------------------------------------------------

class FloatIndicator(tk.Toplevel):
    """Small draggable borderless always-on-top PPS status indicator."""

    def __init__(self, master, on_close=None):
        super().__init__(master)
        # overrideredirect MUST be the very first call — before any widgets or geometry
        self.overrideredirect(True)
        self.configure(bg=BG3)

        self._on_close_cb = on_close
        self._drag_x = 0
        self._drag_y = 0

        # ── Drag/title bar ──────────────────────────────────────────────────
        bar = tk.Frame(self, bg=BORDER, height=18, cursor="fleur")
        bar.pack(fill="x")
        bar.bind("<ButtonPress-1>", self._drag_start)
        bar.bind("<B1-Motion>",     self._drag_move)

        tk.Label(bar, text="PPS Monitor", font=("Segoe UI", 8),
                 bg=BORDER, fg=FG_DIM).pack(side="left", padx=6)
        tk.Button(bar, text="×", font=("Segoe UI", 9, "bold"),
                  bg=BORDER, fg=FG_DIM, activebackground=RED,
                  relief="flat", bd=0, padx=5, cursor="hand2",
                  command=self._close_btn).pack(side="right")

        # ── Content ─────────────────────────────────────────────────────────
        inner = tk.Frame(self, bg=BG3, padx=14, pady=8)
        inner.pack(fill="both", expand=True)
        inner.bind("<ButtonPress-1>", self._drag_start)
        inner.bind("<B1-Motion>",     self._drag_move)

        self._pps_var    = tk.StringVar(value="PPS  ● INACTIVE")
        self._sig_var    = tk.StringVar(value="SIG  ● —")
        self._detail_var = tk.StringVar(value="no data")

        self._pps_lbl = tk.Label(inner, textvariable=self._pps_var,
                                 font=FONT_BOLD, bg=BG3, fg=FG, anchor="w")
        self._pps_lbl.pack(fill="x")
        self._pps_lbl.bind("<ButtonPress-1>", self._drag_start)
        self._pps_lbl.bind("<B1-Motion>",     self._drag_move)

        self._sig_lbl = tk.Label(inner, textvariable=self._sig_var,
                                 font=FONT_BOLD, bg=BG3, fg=FG, anchor="w")
        self._sig_lbl.pack(fill="x")
        self._sig_lbl.bind("<ButtonPress-1>", self._drag_start)
        self._sig_lbl.bind("<B1-Motion>",     self._drag_move)

        self._detail_lbl = tk.Label(inner, textvariable=self._detail_var,
                                    font=("Segoe UI", 9), bg=BG3, fg=FG_DIM, anchor="w")
        self._detail_lbl.pack(fill="x")
        self._detail_lbl.bind("<ButtonPress-1>", self._drag_start)
        self._detail_lbl.bind("<B1-Motion>",     self._drag_move)

        # ── Position relative to parent window ──────────────────────────────
        master.update_idletasks()
        x = master.winfo_x() + master.winfo_width() - 175
        y = master.winfo_y() + 10
        self.geometry(f"165x98+{x}+{y}")
        self.update_idletasks()   # ensure content is rendered before showing

    # ── Drag ────────────────────────────────────────────────────────────────

    def _drag_start(self, event):
        self._drag_x = event.x_root - self.winfo_x()
        self._drag_y = event.y_root - self.winfo_y()

    def _drag_move(self, event):
        self.geometry(f"+{event.x_root - self._drag_x}+{event.y_root - self._drag_y}")

    def _close_btn(self):
        if self._on_close_cb:
            self._on_close_cb()
        self.destroy()

    # ── Status update ────────────────────────────────────────────────────────

    def update_status(self, pps: str, signal: str, quality: int, satellites: int):
        """
        pps    : LOCKED | DEGRADED | UNLOCKED | INACTIVE
        signal : OK | LOSS | INACTIVE
        """
        pps_colors = {"LOCKED": GREEN, "DEGRADED": YELLOW, "UNLOCKED": RED}
        sig_colors = {"OK": GREEN, "LOSS": RED}

        pps_color = pps_colors.get(pps, FG)
        sig_color = sig_colors.get(signal, FG_DIM)

        self._pps_var.set(f"PPS  ● {pps}")
        self._sig_var.set(f"SIG  ● {signal}")
        self._pps_lbl.configure(fg=pps_color)
        self._sig_lbl.configure(fg=sig_color)

        if pps == "INACTIVE":
            self._detail_var.set("no data")
        else:
            self._detail_var.set(f"Q{quality}  {satellites} sat")


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("NMEA / GNSS Parser")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(720, 580)

        # File analysis state
        self._kml_path: Path | None = None
        self._running = False

        # Live monitor state
        self._udp_socket: socket.socket | None = None
        self._udp_stop = threading.Event()
        self._live_queue: queue.Queue = queue.Queue()
        self._live_running = False
        self._live_log_file = None
        self._live_zda_buf: deque = deque(maxlen=120)
        self._live_gga: dict = {}
        self._live_rmc: dict = {}
        self._live_vtg: dict = {}
        self._live_prev_time: str = ""
        self._live_sentence_count = 0
        self._live_quality: int = 0
        self._live_satellites: int = 0
        self._last_zda_time: float = 0.0
        self._last_gga_time: float = 0.0
        self._logged_gap_count: int = 0   # timing events already written to live log
        self._float_win: FloatIndicator | None = None

        self._build_ui()
        self._center()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self):
        # Title bar
        title_frame = tk.Frame(self, bg=BG, padx=12, pady=8)
        title_frame.pack(fill="x")
        tk.Label(title_frame, text="NMEA / GNSS Parser", font=FONT_H1,
                 bg=BG, fg=MAUVE).pack(side="left")

        # Notebook style
        style = ttk.Style()
        style.theme_use("default")
        style.configure("TNotebook",       background=BG,  borderwidth=0)
        style.configure("TNotebook.Tab",   background=BG3, foreground=FG_DIM,
                        padding=[14, 5],   font=FONT_UI)
        style.map("TNotebook.Tab",
                  background=[("selected", BG2)],
                  foreground=[("selected", FG)])

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        file_tab = tk.Frame(self.nb, bg=BG)
        live_tab = tk.Frame(self.nb, bg=BG)
        self.nb.add(file_tab, text="  File Analysis  ")
        self.nb.add(live_tab, text="  Live Monitor  ")

        self._build_file_tab(file_tab)
        self._build_live_tab(live_tab)

    # ── File Analysis tab ────────────────────────────────────────────────────

    def _build_file_tab(self, parent):
        top = tk.Frame(parent, bg=BG, padx=12, pady=10)
        top.pack(fill="x")

        tk.Label(top, text="NMEA File:", font=FONT_UI,
                 bg=BG, fg=FG_DIM).grid(row=0, column=0, sticky="w")
        self.input_var = tk.StringVar()
        tk.Entry(top, textvariable=self.input_var, font=FONT_UI,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", bd=4, width=55).grid(
            row=0, column=1, padx=6, sticky="ew")
        tk.Button(top, text="Open…", font=FONT_UI,
                  bg=BG3, fg=FG, activebackground=BORDER,
                  relief="flat", cursor="hand2",
                  command=self._browse_input).grid(row=0, column=2)

        tk.Label(top, text="Save to:", font=FONT_UI,
                 bg=BG, fg=FG_DIM).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.output_var = tk.StringVar()
        tk.Entry(top, textvariable=self.output_var, font=FONT_UI,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", bd=4, width=55).grid(
            row=1, column=1, padx=6, sticky="ew", pady=(6, 0))
        tk.Button(top, text="Folder…", font=FONT_UI,
                  bg=BG3, fg=FG, activebackground=BORDER,
                  relief="flat", cursor="hand2",
                  command=self._browse_output).grid(row=1, column=2, pady=(6, 0))

        self.include_invalid = tk.BooleanVar(value=False)
        tk.Checkbutton(top, text="Include invalid fixes (quality 0)",
                       variable=self.include_invalid,
                       font=FONT_UI, bg=BG, fg=FG_DIM,
                       selectcolor=BG2, activebackground=BG).grid(
            row=2, column=1, sticky="w", pady=(4, 0))
        top.columnconfigure(1, weight=1)

        # Action buttons
        btn_frame = tk.Frame(parent, bg=BG, padx=12, pady=4)
        btn_frame.pack(fill="x")

        self.run_btn = tk.Button(
            btn_frame, text="▶  Process", font=FONT_BOLD,
            bg=BLUE, fg=BG, activebackground=TEAL,
            relief="flat", padx=16, pady=6, cursor="hand2",
            command=self._run)
        self.run_btn.pack(side="left", padx=(0, 8))

        self.earth_btn = tk.Button(
            btn_frame, text="🌍  Open in Google Earth", font=FONT_UI,
            bg=BG3, fg=FG_DIM, activebackground=BORDER,
            relief="flat", padx=12, pady=6, cursor="hand2",
            state="disabled", command=self._open_kml)
        self.earth_btn.pack(side="left", padx=(0, 8))

        tk.Button(btn_frame, text="Clear", font=FONT_UI,
                  bg=BG3, fg=FG_DIM, activebackground=BORDER,
                  relief="flat", padx=12, pady=6, cursor="hand2",
                  command=self._clear).pack(side="left")

        tk.Button(btn_frame, text="?  Help", font=FONT_UI,
                  bg=BG3, fg=MAUVE, activebackground=BORDER,
                  relief="flat", padx=12, pady=6, cursor="hand2",
                  command=self._show_help).pack(side="right")

        # Status bar
        status_frame = tk.Frame(parent, bg=BG2, padx=12, pady=6)
        status_frame.pack(fill="x")

        self.pps_var    = tk.StringVar(value="PPS: —")
        self.fixes_var  = tk.StringVar(value="Fixes: —")
        self.dist_var   = tk.StringVar(value="Distance: —")
        self.uptime_var = tk.StringVar(value="Uptime GPS: —")

        for var in [self.pps_var, self.fixes_var, self.dist_var, self.uptime_var]:
            tk.Label(status_frame, textvariable=var, font=FONT_UI,
                     bg=BG2, fg=FG).pack(side="left", padx=12)

        # Report output
        out_frame = tk.Frame(parent, bg=BG, padx=12)
        out_frame.pack(fill="both", expand=True, pady=(4, 10))

        self.text = tk.Text(
            out_frame, font=FONT_MONO, bg=BG2, fg=FG,
            insertbackground=FG, relief="flat", bd=0,
            wrap="none", state="disabled")
        self.text.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(out_frame, orient="vertical", command=self.text.yview)
        sb.pack(side="right", fill="y")
        self.text.configure(yscrollcommand=sb.set)

        self.text.tag_configure("ok",     foreground=GREEN)
        self.text.tag_configure("warn",   foreground=YELLOW)
        self.text.tag_configure("fail",   foreground=RED)
        self.text.tag_configure("header", foreground=MAUVE)
        self.text.tag_configure("key",    foreground=BLUE)
        self.text.tag_configure("dim",    foreground=FG_DIM)
        self.text.tag_configure("value",  foreground=TEAL)

    # ── Live Monitor tab ─────────────────────────────────────────────────────

    def _build_live_tab(self, parent):
        # Connection row
        conn = tk.Frame(parent, bg=BG, padx=12, pady=10)
        conn.pack(fill="x")

        tk.Label(conn, text="UDP Port:", font=FONT_UI,
                 bg=BG, fg=FG_DIM).pack(side="left")
        self.udp_port_var = tk.StringVar(value="10110")
        tk.Entry(conn, textvariable=self.udp_port_var, font=FONT_UI,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", bd=4, width=8).pack(side="left", padx=(6, 14))

        self.conn_btn = tk.Button(
            conn, text="⬤  Connect", font=FONT_BOLD,
            bg=GREEN, fg=BG, activebackground=TEAL,
            relief="flat", padx=14, pady=5, cursor="hand2",
            command=self._toggle_connection)
        self.conn_btn.pack(side="left", padx=(0, 18))

        self.live_log_var = tk.BooleanVar(value=False)
        tk.Checkbutton(conn, text="Log to file",
                       variable=self.live_log_var,
                       font=FONT_UI, bg=BG, fg=FG_DIM,
                       selectcolor=BG2, activebackground=BG,
                       command=self._toggle_log).pack(side="left", padx=(0, 14))

        self.show_float_var = tk.BooleanVar(value=False)
        tk.Checkbutton(conn, text="Show floating indicator",
                       variable=self.show_float_var,
                       font=FONT_UI, bg=BG, fg=FG_DIM,
                       selectcolor=BG2, activebackground=BG,
                       command=self._toggle_float).pack(side="left")

        # Data panels
        panels = tk.Frame(parent, bg=BG, padx=12)
        panels.pack(fill="x")
        panels.columnconfigure(0, weight=1)
        panels.columnconfigure(1, weight=1)

        def section(title, row, col):
            f = tk.Frame(panels, bg=BG2, padx=10, pady=8)
            f.grid(row=row, column=col, padx=(0, 8), pady=(0, 8), sticky="nsew")
            tk.Label(f, text=title, font=FONT_BOLD,
                     bg=BG2, fg=MAUVE).pack(anchor="w", pady=(0, 4))
            return f

        def field(parent, label) -> tuple[tk.StringVar, tk.Label]:
            row = tk.Frame(parent, bg=BG2)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=label, font=FONT_UI,
                     bg=BG2, fg=FG_DIM, width=16, anchor="w").pack(side="left")
            var = tk.StringVar(value="—")
            lbl = tk.Label(row, textvariable=var, font=FONT_UI,
                           bg=BG2, fg=TEAL, anchor="w")
            lbl.pack(side="left")
            return var, lbl

        pos = section("POSITION", 0, 0)
        self.lv_lat,  _              = field(pos, "Latitude")
        self.lv_lon,  _              = field(pos, "Longitude")
        self.lv_alt,  _              = field(pos, "Altitude")
        self.lv_fix,  _              = field(pos, "Fix type")

        nav = section("NAVIGATION", 0, 1)
        self.lv_speed, _             = field(nav, "Speed")
        self.lv_hdg,   _             = field(nav, "Heading")
        self.lv_sats,  _             = field(nav, "Satellites")
        self.lv_hdop,  _             = field(nav, "HDOP")

        tim = section("TIMING / PPS", 1, 0)
        self.lv_pps,      self._lv_pps_lbl = field(tim, "PPS status")
        self.lv_avgdev,   _                 = field(tim, "Avg deviation")
        self.lv_maxdev,   _                 = field(tim, "Max deviation")
        self.lv_zda,      _                 = field(tim, "ZDA received")
        self.lv_zda_time, _                 = field(tim, "NMEA time")

        ses = section("SESSION", 1, 1)
        self.lv_uptime,   _          = field(ses, "Uptime")
        self.lv_nmea_cnt, _          = field(ses, "Sentences")
        self.lv_updated,  _          = field(ses, "Last update")
        self.lv_logfile,  _          = field(ses, "Log file")

        # Status log
        log_frame = tk.Frame(parent, bg=BG, padx=12)
        log_frame.pack(fill="both", expand=True, pady=(0, 8))

        self.live_log = tk.Text(
            log_frame, font=FONT_MONO, bg=BG2, fg=FG_DIM,
            relief="flat", bd=0, height=5, state="disabled")
        self.live_log.pack(side="left", fill="both", expand=True)

        sb2 = ttk.Scrollbar(log_frame, orient="vertical", command=self.live_log.yview)
        sb2.pack(side="right", fill="y")
        self.live_log.configure(yscrollcommand=sb2.set)

    # -----------------------------------------------------------------------
    # File Analysis — actions
    # -----------------------------------------------------------------------

    def _browse_input(self):
        path = filedialog.askopenfilename(
            title="Select NMEA file",
            filetypes=[("NMEA files", "*.nmea *.txt *.log"), ("All files", "*.*")])
        if path:
            self.input_var.set(path)
            if not self.output_var.get():
                self.output_var.set(str(Path(path).parent))

    def _browse_output(self):
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self.output_var.set(path)

    def _clear(self):
        self._set_text("")
        self.pps_var.set("PPS: —")
        self.fixes_var.set("Fixes: —")
        self.dist_var.set("Distance: —")
        self.uptime_var.set("Uptime GPS: —")
        self._kml_path = None
        self.earth_btn.configure(state="disabled", fg=FG_DIM)

    def _run(self):
        if self._running:
            return
        input_path = self.input_var.get().strip()
        if not input_path:
            messagebox.showwarning("Warning", "Please select an NMEA file first.")
            return
        p = Path(input_path)
        if not p.exists():
            messagebox.showerror("Error", f"File not found:\n{p}")
            return

        out_str = self.output_var.get().strip()
        out_dir = Path(out_str).resolve() if out_str else p.parent.resolve()

        self._running = True
        self.run_btn.configure(state="disabled", text="Processing…")
        self._clear()
        self._append("Processing: ", "dim")
        self._append(str(p) + "\n", "value")

        def worker():
            try:
                fixes, zda_events, parse_stats = parse_file(
                    p, ignore_invalid=self.include_invalid.get())
                timing = analyze_timing(zda_events)

                if not fixes and not zda_events:
                    self.after(0, lambda: self._append(
                        "\nNo data found in file.\n", "fail"))
                    return

                stats   = compute_statistics(fixes) if fixes else {}
                out_dir.mkdir(parents=True, exist_ok=True)
                stem    = p.stem
                csv_out = out_dir / f"{stem}_fixes.csv"
                kml_out = out_dir / f"{stem}_track.kml"
                if fixes:
                    export_csv(fixes, csv_out)
                    export_kml(fixes, kml_out, name=stem)

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
                self.after(0, lambda: self._append(f"\nError: {msg}\n", "fail"))
            finally:
                self.after(0, self._done)

        threading.Thread(target=worker, daemon=True).start()

    def _done(self):
        self._running = False
        self.run_btn.configure(state="normal", text="▶  Process")

    def _open_kml(self):
        if self._kml_path and self._kml_path.exists():
            try:
                open_file(self._kml_path)
            except Exception as e:
                messagebox.showerror("Error", f"Could not open KML file:\n{e}")
        else:
            messagebox.showinfo("Warning", "KML file not found.\nPlease process a file first.")

    # -----------------------------------------------------------------------
    # File Analysis — report display
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

        pps    = timing.get("pps_status", "—") if timing else "—"
        uptime = timing.get("uptime_pct", 0.0)  if timing else 0.0
        color  = GREEN if pps == "LOCKED" else (YELLOW if pps == "DEGRADED" else RED)

        self.pps_var.set(f"PPS: {pps}")
        self.fixes_var.set(f"Fixes: {stats.get('valid_fixes', 0)}")
        dist = stats.get("distance_nm", 0.0)
        self.dist_var.set(f"Distance: {dist:.3f} nm")
        self.uptime_var.set(f"Uptime GPS: {uptime:.1f}%")

        for widget in self.nametowidget(".").winfo_children():
            self._set_pps_color(widget, color)

        self._kml_path = kml_out
        self.earth_btn.configure(state="normal", fg=GREEN)

    def _set_pps_color(self, widget, color):
        try:
            if isinstance(widget, tk.Label) and "PPS:" in str(widget.cget("textvariable")):
                widget.configure(fg=color)
        except Exception:
            pass
        for child in widget.winfo_children():
            self._set_pps_color(child, color)

    # -----------------------------------------------------------------------
    # Live Monitor — connection
    # -----------------------------------------------------------------------

    def _toggle_connection(self):
        if self._live_running:
            self._disconnect_udp()
        else:
            self._connect_udp()

    def _connect_udp(self):
        try:
            port = int(self.udp_port_var.get().strip())
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            messagebox.showerror("Error", "Invalid UDP port number.")
            return

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", port))
            sock.settimeout(1.0)
        except OSError as e:
            messagebox.showerror("Error", f"Could not bind to UDP port {port}:\n{e}")
            return

        self._udp_socket = sock
        self._udp_stop.clear()
        self._live_running = True
        self._live_sentence_count = 0
        self._live_zda_buf.clear()
        self._live_gga.clear()
        self._live_rmc.clear()
        self._live_vtg.clear()
        self._live_prev_time = ""
        self._last_gga_time = 0.0

        self.conn_btn.configure(text="⬤  Disconnect", bg=RED, fg=BG)
        self._live_log_event(f"[{_now()}]  Listening on UDP port {port}…")

        threading.Thread(target=self._udp_reader, daemon=True).start()
        self._poll_live_queue()

    def _disconnect_udp(self):
        self._udp_stop.set()
        self._live_running = False
        if self._udp_socket:
            try:
                self._udp_socket.close()
            except Exception:
                pass
            self._udp_socket = None
        if self._live_log_file:
            self._close_log()
        self._last_zda_time = 0.0
        self._last_gga_time = 0.0
        self._logged_gap_count = 0
        self.conn_btn.configure(text="⬤  Connect", bg=GREEN, fg=BG)
        self._live_log_event(f"[{_now()}]  Disconnected.")
        # Reset live display and floating indicator
        self.lv_pps.set("—")
        self._lv_pps_lbl.configure(fg=TEAL)
        if self._float_win and self._float_win.winfo_exists():
            self._float_win.update_status("INACTIVE", "INACTIVE", 0, 0)

    def _udp_reader(self):
        """Daemon thread — reads UDP packets and queues NMEA lines."""
        while not self._udp_stop.is_set():
            try:
                data, _ = self._udp_socket.recvfrom(4096)
                lines = data.decode("ascii", errors="replace").splitlines()
                for line in lines:
                    line = line.strip()
                    if line.startswith("$"):
                        self._live_queue.put(line)
            except socket.timeout:
                continue
            except Exception:
                break

    def _poll_live_queue(self):
        """GUI thread — drains queue and refreshes display every 100 ms."""
        try:
            for _ in range(50):
                line = self._live_queue.get_nowait()
                self._process_live_sentence(line)
        except queue.Empty:
            pass

        now = time.time()

        # ZDA timeout: if no ZDA for >5s, force PPS UNLOCKED
        pps_status = None
        if (self._live_running and self._last_zda_time > 0
                and now - self._last_zda_time > 5.0):
            pps_status = "UNLOCKED"
            self.lv_pps.set("UNLOCKED")
            self._lv_pps_lbl.configure(fg=RED)

        # GGA timeout: if no valid position for >5s, flag signal loss
        sig_status = None
        if self._live_running and self._last_gga_time > 0:
            sig_status = "LOSS" if now - self._last_gga_time > 5.0 else "OK"

        # Push combined state to floating indicator
        if (self._float_win and self._float_win.winfo_exists()
                and (pps_status or sig_status)):
            self._float_win.update_status(
                pps_status or self.lv_pps.get(),
                sig_status or "OK",
                self._live_quality,
                self._live_satellites)

        if self._live_running:
            self.after(100, self._poll_live_queue)

    # -----------------------------------------------------------------------
    # Live Monitor — sentence processing
    # -----------------------------------------------------------------------

    def _process_live_sentence(self, line: str):
        if not verify_checksum(line):
            return

        self._live_sentence_count += 1

        if self._live_log_file:
            try:
                self._live_log_file.write(line + "\r\n")
            except Exception:
                pass

        sentence = line.split("*")[0]
        fields   = sentence.split(",")
        msg_type = fields[0][1:][-3:]
        cur_time = fields[1] if len(fields) > 1 else ""

        if msg_type == "GGA":
            if cur_time != self._live_prev_time and self._live_prev_time:
                self._flush_live_fix()
            parsed = parse_gga(fields)
            if parsed.get("lat") is not None:
                self._live_gga.update(parsed)
            self._live_prev_time = cur_time

        elif msg_type == "RMC":
            self._live_rmc.update(parse_rmc(fields))

        elif msg_type == "VTG":
            self._live_vtg.update(parse_vtg(fields))

        elif msg_type == "ZDA":
            try:
                ts = build_zda_timestamp(fields[1], fields[2], fields[3], fields[4])
                if ts:
                    self._last_zda_time = time.time()
                    self._live_zda_buf.append(ZDAEvent(timestamp=ts))
                    self.lv_zda_time.set(ts.strftime("%H:%M:%S UTC"))
                    self._update_pps_live()
            except (IndexError, ValueError):
                pass

    def _flush_live_fix(self):
        """Merges buffered GGA/RMC/VTG and updates live display fields."""
        if not self._live_gga or self._live_gga.get("lat") is None:
            return

        q    = self._live_gga.get("quality",   0)
        sats = self._live_gga.get("satellites", 0)
        hdop = self._live_gga.get("hdop",       99.9)
        alt  = self._live_gga.get("altitude",   0.0)
        lat  = self._live_gga.get("lat",        0.0)
        lon  = self._live_gga.get("lon",        0.0)
        spd  = self._live_vtg.get("speed_kts") or self._live_rmc.get("speed_kts", 0.0)
        hdg  = self._live_vtg.get("heading")   or self._live_rmc.get("heading",   0.0)

        self._live_quality    = q
        self._live_satellites = sats
        # Only refresh the signal timer on a valid fix (quality > 0).
        # quality=0 means no satellite lock — signal loss condition.
        if q > 0:
            self._last_gga_time = time.time()

        self.lv_lat.set(f"{lat:.8f}°")
        self.lv_lon.set(f"{lon:.8f}°")
        self.lv_alt.set(f"{alt:.2f} m MSL")
        self.lv_fix.set(f"[{q}] {QUALITY_LABELS.get(q, '?')}")
        self.lv_speed.set(f"{spd:.1f} kts")
        self.lv_hdg.set(f"{hdg:.1f}°")
        self.lv_sats.set(str(sats))
        self.lv_hdop.set(f"{hdop:.2f}  ({hdop_label(hdop)})")
        self.lv_nmea_cnt.set(str(self._live_sentence_count))
        self.lv_updated.set(_now())

        self._live_gga.clear()
        self._live_rmc.clear()
        self._live_vtg.clear()

    def _update_pps_live(self):
        """Recomputes PPS status from the rolling ZDA buffer and updates UI."""
        if len(self._live_zda_buf) < 2:
            return
        timing = analyze_timing(list(self._live_zda_buf))
        pps    = timing.get("pps_status", "—")
        avgdev = timing.get("avg_deviation_ms", 0.0)
        maxdev = timing.get("max_deviation_ms", 0.0)
        uptime = timing.get("uptime_pct",       0.0)
        zcount = timing.get("zda_count",        0)

        color = GREEN if pps == "LOCKED" else (YELLOW if pps == "DEGRADED" else RED)

        self.lv_pps.set(pps)
        self._lv_pps_lbl.configure(fg=color)
        self.lv_avgdev.set(f"{avgdev:.2f} ms")
        self.lv_maxdev.set(f"{maxdev:.2f} ms")
        self.lv_zda.set(str(zcount))
        self.lv_uptime.set(f"{uptime:.1f}%")

        # Log new timing events (gaps / jumps) that weren't logged yet
        gaps = timing.get("gaps", [])
        kind_labels = {
            "gap":           ("[GAP]",      "warn"),
            "forward_jump":  ("[JUMP FWD]", "warn"),
            "backward_jump": ("[JUMP BWD]", "warn"),
        }
        for gap in gaps[self._logged_gap_count:]:
            label, tag = kind_labels.get(gap.kind, (f"[{gap.kind.upper()}]", ""))
            start_s = gap.start.strftime("%H:%M:%S")
            end_s   = gap.end.strftime("%H:%M:%S")
            msg = f"[{_now()}]  {label}  {start_s} → {end_s}  ({gap.duration_s:.1f}s)"
            self._live_log_event(msg)
        self._logged_gap_count = len(gaps)

        if self._float_win and self._float_win.winfo_exists():
            now = time.time()
            sig = ("LOSS" if (self._last_gga_time > 0 and now - self._last_gga_time > 5.0)
                   else ("OK" if self._last_gga_time > 0 else "—"))
            self._float_win.update_status(pps, sig, self._live_quality, self._live_satellites)

    # -----------------------------------------------------------------------
    # Live Monitor — log file
    # -----------------------------------------------------------------------

    def _toggle_log(self):
        if self.live_log_var.get():
            path = filedialog.asksaveasfilename(
                title="Save NMEA log",
                defaultextension=".nmea",
                filetypes=[("NMEA files", "*.nmea"), ("All files", "*.*")])
            if path:
                try:
                    self._live_log_file = open(path, "w", encoding="ascii")
                    self.lv_logfile.set(Path(path).name)
                    self._live_log_event(f"[{_now()}]  Logging to: {path}")
                except OSError as e:
                    messagebox.showerror("Error", f"Could not open log file:\n{e}")
                    self.live_log_var.set(False)
            else:
                self.live_log_var.set(False)
        else:
            self._close_log()

    def _close_log(self):
        if self._live_log_file:
            try:
                self._live_log_file.close()
            except Exception:
                pass
            self._live_log_file = None
            self.lv_logfile.set("—")
            self._live_log_event(f"[{_now()}]  Log file closed.")

    # -----------------------------------------------------------------------
    # Live Monitor — floating indicator
    # -----------------------------------------------------------------------

    def _toggle_float(self):
        if self.show_float_var.get():
            if self._float_win is None or not self._float_win.winfo_exists():
                self._float_win = FloatIndicator(self, on_close=self._on_float_closed)
        else:
            if self._float_win and self._float_win.winfo_exists():
                self._float_win.destroy()
            self._float_win = None

    def _on_float_closed(self):
        """Called when user closes the floating indicator via the × button."""
        self.show_float_var.set(False)
        self._float_win = None

    # -----------------------------------------------------------------------
    # Live Monitor — status log
    # -----------------------------------------------------------------------

    def _live_log_event(self, msg: str):
        self.live_log.configure(state="normal")
        self.live_log.insert("end", msg + "\n")
        self.live_log.see("end")
        self.live_log.configure(state="disabled")

    # -----------------------------------------------------------------------
    # Help
    # -----------------------------------------------------------------------

    def _show_help(self):
        win = tk.Toplevel(self)
        win.title("NMEA/GNSS Parser — Help")
        win.configure(bg=BG)
        win.resizable(True, True)
        win.minsize(660, 520)

        frame = tk.Frame(win, bg=BG, padx=14, pady=10)
        frame.pack(fill="both", expand=True)

        txt = tk.Text(frame, font=FONT_MONO, bg=BG2, fg=FG,
                      relief="flat", bd=0, wrap="word", state="normal")
        txt.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
        sb.pack(side="right", fill="y")
        txt.configure(yscrollcommand=sb.set)

        txt.tag_configure("h1",   foreground=MAUVE, font=("Segoe UI", 11, "bold"))
        txt.tag_configure("h2",   foreground=BLUE,  font=("Segoe UI", 10, "bold"))
        txt.tag_configure("key",  foreground=TEAL)
        txt.tag_configure("ok",   foreground=GREEN)
        txt.tag_configure("warn", foreground=YELLOW)
        txt.tag_configure("fail", foreground=RED)
        txt.tag_configure("dim",  foreground=FG_DIM)

        def h1(t):  txt.insert("end", t + "\n", "h1")
        def h2(t):  txt.insert("end", t + "\n", "h2")
        def sep():  txt.insert("end", "─" * 62 + "\n", "dim")
        def line(label, desc, tag="key"):
            txt.insert("end", f"  {label:<24}", tag)
            txt.insert("end", desc + "\n")
        def body(t): txt.insert("end", t + "\n")
        def blank(): txt.insert("end", "\n")

        h1("NMEA/GNSS Parser — User Manual")
        blank()

        # ── File Analysis ────────────────────────────────────────────────────
        h2("FILE ANALYSIS — HOW TO USE")
        sep()
        body("  1. Click  Open…   to select an NMEA log file (.nmea / .txt / .log)")
        body("  2. Optionally choose an output folder with  Folder…")
        body("  3. Check  Include invalid fixes  to also export quality=0 fixes")
        body("  4. Click  ▶ Process  to run the analysis")
        body("  5. After processing,  🌍 Open in Google Earth  opens the KML track")
        blank()

        h2("FILE INFORMATION")
        sep()
        line("File",     "Name of the NMEA file processed")
        line("Start",    "UTC timestamp of the first valid position fix")
        line("End",      "UTC timestamp of the last valid position fix")
        line("Duration", "Total recording duration  (hh:mm:ss)")
        blank()

        h2("SENTENCES")
        sep()
        line("Total parsed",  "Total NMEA sentences read from the file")
        line("Bad checksum",  "Sentences with invalid checksum — corrupted or truncated;\n"
                              "                         discarded, not used in analysis")
        line("GGA",  "Global Positioning System Fix Data\n"
                     "                         provides position, quality and altitude")
        line("RMC",  "Recommended Minimum Navigation\n"
                     "                         provides position, speed, heading and date")
        line("VTG",  "Track Made Good and Ground Speed")
        line("ZDA",  "Time & Date — used for PPS timing analysis")
        blank()

        h2("POSITION FIXES")
        sep()
        line("Total",   "All fixes found (valid + invalid)")
        line("Valid",   "Fixes with quality > 0 and RMC valid flag\n"
                        "                         used for all navigation calculations")
        line("Invalid", "Fixes with quality = 0 or flagged invalid by receiver")
        blank()
        body("  Fix quality types:")
        txt.insert("end", "    [0] No fix     ", "fail")
        body(": no satellite lock — position unreliable")
        txt.insert("end", "    [1] GPS        ", "key")
        body(": standard autonomous GPS fix")
        txt.insert("end", "    [2] DGPS       ", "key")
        body(": Differential GPS — corrected by ground reference station")
        txt.insert("end", "    [4] RTK Fixed  ", "ok")
        body(": Real-Time Kinematic, fixed solution — centimetre-level accuracy")
        txt.insert("end", "    [5] RTK Float  ", "warn")
        body(": RTK floating ambiguity — decimetre-level accuracy")
        blank()

        h2("NAVIGATION")
        sep()
        line("Distance",  "Total distance travelled in nautical miles (nm) and km")
        line("Avg speed", "Average speed over ground, in knots (kts)")
        line("Max speed", "Maximum instantaneous speed recorded, in knots (kts)")
        blank()

        h2("QUALITY")
        sep()
        body("  HDOP — Horizontal Dilution of Precision")
        body("  Measures how satellite geometry affects horizontal accuracy. Lower is better:")
        txt.insert("end", "    Ideal     <= 1.0  ", "ok");   body(": optimal satellite geometry")
        txt.insert("end", "    Excellent <= 2.0  ", "ok");   body(": suitable for all applications")
        txt.insert("end", "    Good      <= 5.0  ", "ok");   body(": reliable for general navigation")
        txt.insert("end", "    Moderate  <= 10.0 ", "warn"); body(": marginal for precise work")
        txt.insert("end", "    Poor      > 10.0  ", "fail"); body(": unreliable — check satellite visibility")
        blank()
        line("Avg HDOP",  "Mean HDOP over the entire session")
        line("Max HDOP",  "Worst HDOP recorded during the session")
        line("Alt range", "Altitude span from minimum to maximum, in metres above MSL")
        blank()

        h2("TIMING / PPS ANALYSIS  (ZDA)")
        sep()
        body("  Uses ZDA sentences to assess timing integrity and infer PPS lock.")
        body("  A GPS receiver with PPS active emits exactly one ZDA per second.")
        body("  Any deviation from 1.000 s intervals indicates timing instability.")
        blank()
        body("  PPS lock status:")
        txt.insert("end", "    [OK]   LOCKED    ", "ok");   body(": >=95% of intervals within ±10ms — timing stable")
        txt.insert("end", "    [WARN] DEGRADED  ", "warn"); body(": 75–94% within ±10ms — minor instability")
        txt.insert("end", "    [FAIL] UNLOCKED  ", "fail"); body(": <75% within ±10ms — PPS not reliable")
        blank()
        line("ZDA sentences",    "ZDA messages received during the session")
        line("Expected",         "Expected count based on session duration (~1/second)")
        line("Uptime",           "Percentage of expected ZDA messages actually received")
        line("Locked intervals", "Percentage of 1s intervals within ±10ms of 1.000s")
        line("Avg deviation",    "Average timing error in milliseconds")
        line("Max deviation",    "Worst single timing error recorded in milliseconds")
        blank()
        body("  Timing failure events (reported with date, time and duration):")
        txt.insert("end", "    [GAP]      ", "fail")
        body(": ZDA messages missing for >1.5s — possible signal loss\n"
             "                 shows start time, end time and outage duration")
        txt.insert("end", "    [JUMP FWD] ", "warn")
        body(": time jumped forward >60s — receiver reset or UTC step")
        txt.insert("end", "    [JUMP BWD] ", "warn")
        body(": time went backwards — clock correction or data corruption")
        blank()

        # ── Live Monitor ─────────────────────────────────────────────────────
        h2("LIVE MONITOR — HOW TO USE")
        sep()
        body("  Displays real-time NMEA data received via UDP.")
        body("  Compatible with 4DNav, QINSy, Navisuite, HYPACK and any")
        body("  Serial-to-Network tool broadcasting NMEA sentences over UDP.")
        blank()
        body("  1. Enter the UDP port (default: 10110 — NMEA over IP standard)")
        body("  2. Click  Connect  to start listening")
        body("  3. All panels update automatically as data arrives")
        body("  4. Click  Disconnect  to stop")
        blank()

        h2("LIVE MONITOR — PANELS")
        sep()
        body("  POSITION")
        line("  Latitude / Longitude", "Current position in decimal degrees")
        line("  Altitude",             "Height above Mean Sea Level in metres")
        line("  Fix type",             "GGA quality code and label (see fix types above)")
        blank()
        body("  NAVIGATION")
        line("  Speed",      "Speed over ground in knots")
        line("  Heading",    "True heading in degrees")
        line("  Satellites", "Number of satellites currently in use")
        line("  HDOP",       "Current horizontal dilution of precision")
        blank()
        body("  TIMING / PPS  (rolling analysis — last 120 ZDA sentences, ~2 minutes)")
        line("  PPS status",    "LOCKED / DEGRADED / UNLOCKED")
        line("  Avg deviation", "Rolling average timing error in milliseconds")
        line("  Max deviation", "Worst timing error in the current window")
        line("  ZDA received",  "Total ZDA sentences received this session")
        blank()
        body("  SESSION")
        line("  Uptime",     "Percentage of expected ZDA messages received")
        line("  Sentences",  "Total NMEA sentences received this session")
        line("  Last update","UTC time of the most recent position fix")
        line("  Log file",   "Name of the active log file (if logging enabled)")
        blank()

        h2("LIVE MONITOR — OPTIONS")
        sep()
        line("  Log to file",
             "Saves all received NMEA sentences to a .nmea file in real time.\n"
             "                         The file can later be analysed in File Analysis.")
        blank()
        line("  Show floating indicator",
             "Opens a small always-on-top draggable window\n"
             "                         showing PPS status and fix quality at a glance.")
        blank()

        h2("FLOATING INDICATOR")
        sep()
        body("  Compact overlay that stays on top of all other applications.")
        body("  Can be dragged freely to any position on screen.")
        blank()
        body("  PPS line — timing integrity from ZDA sentences:")
        txt.insert("end", "    Green  ", "ok");   body("— LOCKED   : PPS stable, timing reliable")
        txt.insert("end", "    Yellow ", "warn"); body("— DEGRADED : minor instability, monitor closely")
        txt.insert("end", "    Red    ", "fail"); body("— UNLOCKED : PPS not reliable, check receiver")
        blank()
        body("  SIG line — position data availability (GGA):")
        txt.insert("end", "    Green  ", "ok");   body("— OK   : GGA received within last 5 seconds")
        txt.insert("end", "    Red    ", "fail"); body("— LOSS : no position data for >5 seconds")
        blank()
        body("  Also shows fix quality code (Q0–Q5) and number of active satellites.")
        blank()

        txt.configure(state="disabled")
        txt.see("1.0")

        w, h = 700, 580
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        win.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

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
        w, h = 820, 640
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    def _on_close(self):
        self._disconnect_udp()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
