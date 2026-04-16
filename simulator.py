#!/usr/bin/env python3
"""
NMEA Simulator
--------------
Generates and broadcasts synthetic NMEA 0183 sentences via UDP.
Designed to test the NMEA/GNSS Parser Live Monitor.

Usage: python3 simulator.py
"""

import math
import random
import socket
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta, timezone
from tkinter import ttk


# ---------------------------------------------------------------------------
# Colour palette (matches the parser)
# ---------------------------------------------------------------------------
BG     = "#1e1e2e"
BG2    = "#2a2a3e"
BG3    = "#313145"
FG     = "#cdd6f4"
FG_DIM = "#7f849c"
GREEN  = "#a6e3a1"
YELLOW = "#f9e2af"
RED    = "#f38ba8"
BLUE   = "#89b4fa"
MAUVE  = "#cba6f7"
TEAL   = "#94e2d5"
BORDER = "#45475a"
ORANGE = "#fab387"

FONT_UI   = ("Segoe UI", 10)
FONT_MONO = ("Monospace", 9)
FONT_H1   = ("Segoe UI", 12, "bold")
FONT_BOLD = ("Segoe UI", 10, "bold")


# ---------------------------------------------------------------------------
# NMEA helpers
# ---------------------------------------------------------------------------

def nmea_checksum(body: str) -> str:
    cs = 0
    for c in body:
        cs ^= ord(c)
    return f"{cs:02X}"


def build_sentence(body: str) -> bytes:
    return f"${body}*{nmea_checksum(body)}\r\n".encode("ascii")


def decimal_to_nmea_lat(deg: float) -> tuple[str, str]:
    d = int(abs(deg))
    m = (abs(deg) - d) * 60
    return f"{d:02d}{m:09.6f}", ("N" if deg >= 0 else "S")


def decimal_to_nmea_lon(deg: float) -> tuple[str, str]:
    d = int(abs(deg))
    m = (abs(deg) - d) * 60
    return f"{d:03d}{m:09.6f}", ("E" if deg >= 0 else "W")


def move_position(lat: float, lon: float,
                  speed_kts: float, heading_deg: float,
                  dt: float) -> tuple[float, float]:
    """Advances lat/lon by speed and heading over dt seconds."""
    if speed_kts == 0:
        return lat, lon
    dist_rad = (speed_kts * dt / 3600) / 3440.065
    hdg = math.radians(heading_deg)
    lat_r = math.radians(lat)
    lon_r = math.radians(lon)
    new_lat_r = math.asin(
        math.sin(lat_r) * math.cos(dist_rad) +
        math.cos(lat_r) * math.sin(dist_rad) * math.cos(hdg)
    )
    new_lon_r = lon_r + math.atan2(
        math.sin(hdg) * math.sin(dist_rad) * math.cos(lat_r),
        math.cos(dist_rad) - math.sin(lat_r) * math.sin(new_lat_r)
    )
    return math.degrees(new_lat_r), math.degrees(new_lon_r)


# ---------------------------------------------------------------------------
# Simulation state
# ---------------------------------------------------------------------------

SCENARIOS: dict[str, dict] = {
    "vessel": {"quality": 1, "sats": 9,  "hdop": 1.2, "speed": 5.0, "heading": 45.0,  "alt": 4.0},
    "static": {"quality": 2, "sats": 11, "hdop": 0.9, "speed": 0.0, "heading": 0.0,   "alt": 12.5},
    "rtk":    {"quality": 4, "sats": 14, "hdop": 0.7, "speed": 1.5, "heading": 112.0, "alt": 8.2},
}


class SimState:
    def __init__(self):
        self.lat      = 56.20000   # North Sea — typical offshore area
        self.lon      = 2.50000
        self.scenario = "vessel"
        self._faults: dict[str, float | None] = {}
        self._lock = threading.Lock()

    def set_scenario(self, name: str):
        with self._lock:
            self.scenario = name

    def inject_fault(self, name: str, duration: float | None):
        with self._lock:
            self._faults[name] = (time.time() + duration) if duration else None

    def clear_fault(self, name: str):
        with self._lock:
            self._faults.pop(name, None)

    def has_fault(self, name: str) -> bool:
        with self._lock:
            if name not in self._faults:
                return False
            expiry = self._faults[name]
            if expiry is not None and time.time() > expiry:
                del self._faults[name]
                return False
            return True

    def active_faults(self) -> list[str]:
        with self._lock:
            expired = [k for k, v in self._faults.items()
                       if v is not None and time.time() > v]
            for k in expired:
                del self._faults[k]
            return list(self._faults.keys())

    def base(self) -> dict:
        with self._lock:
            return dict(SCENARIOS[self.scenario])

    def advance(self, dt: float):
        b = self.base()
        if b["speed"] > 0 and not self.has_fault("signal_loss"):
            with self._lock:
                self.lat, self.lon = move_position(
                    self.lat, self.lon, b["speed"], b["heading"], dt)


# ---------------------------------------------------------------------------
# NMEA sentence builder
# ---------------------------------------------------------------------------

def build_sentences(state: SimState, now: datetime,
                    zda_offset: float = 0.0) -> list[bytes]:
    sentences: list[bytes] = []
    base = state.base()

    time_str = now.strftime("%H%M%S.00")
    date_str = now.strftime("%d%m%y")

    quality = base["quality"]
    sats    = base["sats"]
    hdop    = base["hdop"]
    alt     = base["alt"]
    speed   = base["speed"]
    heading = base["heading"]

    # Apply faults to parameters
    if state.has_fault("signal_loss"):
        quality, sats, hdop = 0, 0, 99.9

    if state.has_fault("hdop_spike"):
        sats = max(3, sats - random.randint(4, 6))
        hdop = round(random.uniform(7.5, 12.0), 1)

    if state.has_fault("fix_downgrade"):
        quality = min(quality, 1)

    lat_str, lat_dir = decimal_to_nmea_lat(state.lat)
    lon_str, lon_dir = decimal_to_nmea_lon(state.lon)
    speed_kmh = speed * 1.852

    # GGA — always sent
    gga = (f"GPGGA,{time_str},"
           f"{lat_str},{lat_dir},{lon_str},{lon_dir},"
           f"{quality},{sats:02d},{hdop:.2f},{alt:.1f},M,0.0,M,,")
    sentences.append(build_sentence(gga))

    # RMC + VTG — skipped during signal loss
    if not state.has_fault("signal_loss"):
        validity = "A" if quality > 0 else "V"
        rmc = (f"GPRMC,{time_str},{validity},"
               f"{lat_str},{lat_dir},{lon_str},{lon_dir},"
               f"{speed:.3f},{heading:.1f},{date_str},,,A")
        sentences.append(build_sentence(rmc))

        vtg = (f"GPVTG,{heading:.1f},T,{heading:.1f},M,"
               f"{speed:.3f},N,{speed_kmh:.3f},K,A")
        sentences.append(build_sentence(vtg))

    # ZDA — skipped during PPS loss; offset applied for time jumps
    if not state.has_fault("pps_loss"):
        zda_time = now + timedelta(seconds=zda_offset)
        zda = (f"GPZDA,{zda_time.strftime('%H%M%S.00')},"
               f"{zda_time.strftime('%d')},"
               f"{zda_time.strftime('%m')},"
               f"{zda_time.strftime('%Y')},00,00")
        sentences.append(build_sentence(zda))

    return sentences


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class SimulatorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("NMEA Simulator")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(580, 520)

        self._state        = SimState()
        self._running      = False
        self._sock: socket.socket | None = None
        self._sent_count   = 0

        self._build_ui()
        self._center()

    # -----------------------------------------------------------------------
    # UI
    # -----------------------------------------------------------------------

    def _build_ui(self):
        # ── Title ────────────────────────────────────────────────────────────
        tf = tk.Frame(self, bg=BG, padx=12, pady=8)
        tf.pack(fill="x")
        tk.Label(tf, text="NMEA Simulator", font=FONT_H1,
                 bg=BG, fg=MAUVE).pack(side="left")

        # ── Connection row ───────────────────────────────────────────────────
        conn = tk.Frame(self, bg=BG, padx=12, pady=4)
        conn.pack(fill="x")

        tk.Label(conn, text="Target IP:", font=FONT_UI,
                 bg=BG, fg=FG_DIM).pack(side="left")
        self.ip_var = tk.StringVar(value="127.0.0.1")
        tk.Entry(conn, textvariable=self.ip_var, font=FONT_UI,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", bd=4, width=14).pack(side="left", padx=(4, 12))

        tk.Label(conn, text="Port:", font=FONT_UI,
                 bg=BG, fg=FG_DIM).pack(side="left")
        self.port_var = tk.StringVar(value="10110")
        tk.Entry(conn, textvariable=self.port_var, font=FONT_UI,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", bd=4, width=7).pack(side="left", padx=(4, 16))

        tk.Label(conn, text="Rate: 1 Hz", font=FONT_UI,
                 bg=BG, fg=FG_DIM).pack(side="left", padx=(0, 16))

        self.start_btn = tk.Button(
            conn, text="▶  Start", font=FONT_BOLD,
            bg=GREEN, fg=BG, activebackground=TEAL,
            relief="flat", padx=14, pady=5, cursor="hand2",
            command=self._start)
        self.start_btn.pack(side="left", padx=(0, 6))

        self.stop_btn = tk.Button(
            conn, text="■  Stop", font=FONT_BOLD,
            bg=BG3, fg=FG_DIM, activebackground=BORDER,
            relief="flat", padx=14, pady=5, cursor="hand2",
            state="disabled", command=self._stop)
        self.stop_btn.pack(side="left")

        # ── Main area ────────────────────────────────────────────────────────
        mid = tk.Frame(self, bg=BG, padx=12, pady=8)
        mid.pack(fill="both", expand=True)
        mid.columnconfigure(0, weight=1)
        mid.columnconfigure(1, weight=1)

        # Scenario panel
        sc = tk.Frame(mid, bg=BG2, padx=12, pady=10)
        sc.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        tk.Label(sc, text="SCENARIO", font=FONT_BOLD,
                 bg=BG2, fg=MAUVE).pack(anchor="w", pady=(0, 6))

        self.scenario_var = tk.StringVar(value="vessel")
        for label, val in [
            ("Vessel  (moving, GPS)",    "vessel"),
            ("Static  (fixed, DGPS)",    "static"),
            ("RTK Survey  (RTK Fixed)",  "rtk"),
        ]:
            tk.Radiobutton(sc, text=label, variable=self.scenario_var,
                           value=val, font=FONT_UI, bg=BG2, fg=FG,
                           selectcolor=BG3, activebackground=BG2,
                           command=self._change_scenario).pack(anchor="w", pady=2)

        tk.Frame(sc, bg=BORDER, height=1).pack(fill="x", pady=8)

        tk.Label(sc, text="Live position", font=FONT_UI,
                 bg=BG2, fg=FG_DIM).pack(anchor="w")
        self.pos_var = tk.StringVar(value="—")
        tk.Label(sc, textvariable=self.pos_var, font=FONT_MONO,
                 bg=BG2, fg=TEAL, justify="left").pack(anchor="w", pady=(2, 0))

        tk.Frame(sc, bg=BORDER, height=1).pack(fill="x", pady=8)

        self.status_var = tk.StringVar(value="● Stopped")
        self.status_lbl = tk.Label(sc, textvariable=self.status_var,
                                   font=FONT_BOLD, bg=BG2, fg=FG_DIM)
        self.status_lbl.pack(anchor="w")

        # Fault panel
        ft = tk.Frame(mid, bg=BG2, padx=12, pady=10)
        ft.grid(row=0, column=1, sticky="nsew")

        tk.Label(ft, text="INJECT FAULT", font=FONT_BOLD,
                 bg=BG2, fg=MAUVE).pack(anchor="w", pady=(0, 6))

        self._fault_btns: dict[str, tk.Button] = {}

        fault_defs = [
            ("pps_loss",      "PPS Loss",           30,   RED,    "Stops ZDA for 30s → PPS UNLOCKED"),
            ("signal_loss",   "Signal Loss",         15,   RED,    "Quality=0, no RMC/VTG for 15s"),
            ("hdop_spike",    "HDOP Spike",          20,   YELLOW, "Reduces sats, HDOP 7–12 for 20s"),
            ("fix_downgrade", "Fix Downgrade",       20,   YELLOW, "Forces quality to GPS (1) for 20s"),
            ("jump_forward",  "Time Jump +90s",      None, ORANGE, "Single ZDA jump forward 90 seconds"),
            ("jump_backward", "Time Jump −5s",       None, ORANGE, "Single ZDA jump backward 5 seconds"),
        ]

        for fid, label, dur, color, tip in fault_defs:
            dur_str = f"  [{dur}s]" if dur else "  [pulse]"
            btn = tk.Button(ft,
                            text=f"{label}{dur_str}",
                            font=FONT_UI, bg=BG3, fg=color,
                            activebackground=BORDER,
                            relief="flat", padx=8, pady=5,
                            cursor="hand2", anchor="w",
                            command=lambda f=fid, d=dur: self._inject(f, d))
            btn.pack(fill="x", pady=2)
            self._fault_btns[fid] = btn

            # Tooltip on hover
            btn.bind("<Enter>", lambda e, t=tip: self._show_tip(t))
            btn.bind("<Leave>", lambda e: self._show_tip(""))

        self.tip_var = tk.StringVar(value="")
        tk.Label(ft, textvariable=self.tip_var, font=("Segoe UI", 8),
                 bg=BG2, fg=FG_DIM, wraplength=200,
                 justify="left").pack(anchor="w", pady=(6, 0))

        # ── Event log ────────────────────────────────────────────────────────
        log_frame = tk.Frame(self, bg=BG, padx=12)
        log_frame.pack(fill="x", expand=False, pady=(0, 10))

        tk.Label(log_frame, text="Event log", font=FONT_UI,
                 bg=BG, fg=FG_DIM).pack(anchor="w")

        self.log_txt = tk.Text(log_frame, font=FONT_MONO, bg=BG2, fg=FG_DIM,
                               relief="flat", bd=0, height=7, state="disabled")
        self.log_txt.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_txt.yview)
        sb.pack(side="right", fill="y")
        self.log_txt.configure(yscrollcommand=sb.set)

        self.log_txt.tag_configure("ok",   foreground=GREEN)
        self.log_txt.tag_configure("warn", foreground=YELLOW)
        self.log_txt.tag_configure("fail", foreground=RED)
        self.log_txt.tag_configure("info", foreground=TEAL)

    # -----------------------------------------------------------------------
    # Controls
    # -----------------------------------------------------------------------

    def _start(self):
        ip = self.ip_var.get().strip()
        try:
            port = int(self.port_var.get().strip())
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            self._log("Invalid port number.", "fail")
            return

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._running    = True
        self._sent_count = 0

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal", bg=RED, fg=BG)
        self.status_var.set("● Running")
        self.status_lbl.configure(fg=GREEN)
        self._log(f"Started  →  {ip}:{port}", "ok")

        threading.Thread(
            target=self._send_loop, args=(ip, port), daemon=True).start()
        self._update_ui()

    def _stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled", bg=BG3, fg=FG_DIM)
        self.status_var.set("● Stopped")
        self.status_lbl.configure(fg=FG_DIM)
        self._log("Stopped.", "info")

    def _change_scenario(self):
        name = self.scenario_var.get()
        self._state.set_scenario(name)
        labels = {"vessel": "Vessel", "static": "Static", "rtk": "RTK Survey"}
        self._log(f"Scenario changed: {labels[name]}", "info")

    def _inject(self, fault_id: str, duration):
        if not self._running:
            self._log("Start the simulator first.", "warn")
            return

        if fault_id in ("jump_forward", "jump_backward"):
            self._state.inject_fault(fault_id, 0.5)
            label = "+90s forward" if fault_id == "jump_forward" else "−5s backward"
            self._log(f"Time jump injected: {label}", "warn")
        else:
            if self._state.has_fault(fault_id):
                self._state.clear_fault(fault_id)
                self._log(f"Fault cleared: {fault_id.replace('_', ' ')}", "ok")
            else:
                self._state.inject_fault(fault_id, duration)
                self._log(
                    f"Fault injected: {fault_id.replace('_', ' ')}"
                    + (f" ({duration}s)" if duration else ""), "fail")

    def _show_tip(self, text: str):
        self.tip_var.set(text)

    # -----------------------------------------------------------------------
    # Send loop (daemon thread)
    # -----------------------------------------------------------------------

    def _send_loop(self, ip: str, port: int):
        while self._running:
            t0  = time.time()
            now = datetime.now(timezone.utc).replace(tzinfo=None)

            # Consume single-pulse time jumps
            zda_offset = 0.0
            if self._state.has_fault("jump_forward"):
                zda_offset = 90.0
                self._state.clear_fault("jump_forward")
            elif self._state.has_fault("jump_backward"):
                zda_offset = -5.0
                self._state.clear_fault("jump_backward")

            for sentence in build_sentences(self._state, now, zda_offset):
                try:
                    self._sock.sendto(sentence, (ip, port))
                except Exception:
                    return

            self._sent_count += 1
            self._state.advance(1.0)

            time.sleep(max(0.0, 1.0 - (time.time() - t0)))

    # -----------------------------------------------------------------------
    # UI refresh (main thread)
    # -----------------------------------------------------------------------

    def _update_ui(self):
        if not self._running:
            return

        self.pos_var.set(
            f"Lat: {self._state.lat:+.6f}°\n"
            f"Lon: {self._state.lon:+.6f}°\n"
            f"Sent: {self._sent_count} sets"
        )

        active = self._state.active_faults()
        for fid, btn in self._fault_btns.items():
            btn.configure(relief="sunken" if fid in active else "flat",
                          bg=BORDER if fid in active else BG3)

        self.after(500, self._update_ui)

    # -----------------------------------------------------------------------
    # Log
    # -----------------------------------------------------------------------

    def _log(self, msg: str, tag: str = ""):
        ts = datetime.utcnow().strftime("%H:%M:%S")
        self.log_txt.configure(state="normal")
        self.log_txt.insert("end", f"[{ts}]  {msg}\n", tag)
        self.log_txt.see("end")
        self.log_txt.configure(state="disabled")

    # -----------------------------------------------------------------------

    def _center(self):
        self.update_idletasks()
        w, h = 620, 560
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")


if __name__ == "__main__":
    app = SimulatorApp()
    app.mainloop()
