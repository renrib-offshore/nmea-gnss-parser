#!/usr/bin/env python3
"""
Test file generator for nmea_parser.py
---------------------------------------
Generates NMEA 0183 test files with specific failure scenarios.
Each file targets a different edge case or sensor fault.

Run: python3 tests/generate_tests.py
Output: tests/nmea/*.nmea
"""

import random
from pathlib import Path

OUT_DIR = Path(__file__).parent / "nmea"
OUT_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def cs(sentence: str) -> str:
    """Compute NMEA XOR checksum."""
    body = sentence.lstrip("$").split("*")[0]
    x = 0
    for c in body:
        x ^= ord(c)
    return f"{x:02X}"


def line(sentence: str, corrupt: bool = False) -> str:
    """Return sentence with valid (or deliberately corrupted) checksum."""
    c = cs(sentence)
    if corrupt:
        # Flip last hex digit
        c = c[:-1] + ("0" if c[-1] != "0" else "F")
    return f"{sentence}*{c}"


def zda(hh: int, mm: int, ss: int, day=15, month=4, year=2026) -> str:
    s = f"$GPZDA,{hh:02d}{mm:02d}{ss:02d}.00,{day:02d},{month:02d},{year},00,00"
    return line(s)


def gga(hh: int, mm: int, ss: int,
        lat_dm: str, lat_dir: str, lon_dm: str, lon_dir: str,
        quality: int = 1, sats: int = 9, hdop: float = 1.0,
        alt: float = 15.0, day=15, month=4, year=2026) -> str:
    s = (f"$GPGGA,{hh:02d}{mm:02d}{ss:02d}.00,"
         f"{lat_dm},{lat_dir},{lon_dm},{lon_dir},"
         f"{quality},{sats},{hdop:.1f},{alt:.1f},M,-2.3,M,,")
    return line(s)


def rmc(hh: int, mm: int, ss: int,
        lat_dm: str, lat_dir: str, lon_dm: str, lon_dir: str,
        speed: float = 5.0, heading: float = 45.0,
        valid: str = "A", day=15, month=4, year=2026) -> str:
    date_str = f"{day:02d}{month:02d}{str(year)[2:]}"
    s = (f"$GPRMC,{hh:02d}{mm:02d}{ss:02d}.00,{valid},"
         f"{lat_dm},{lat_dir},{lon_dm},{lon_dir},"
         f"{speed:.1f},{heading:.1f},{date_str},,,A")
    return line(s)


def vtg(heading: float = 45.0, speed_kts: float = 5.0) -> str:
    s = f"$GPVTG,{heading:.1f},T,,M,{speed_kts:.1f},N,{speed_kts*1.852:.1f},K,A"
    return line(s)


# Base position: Rio de Janeiro offshore area
# lat: 22°54'S  lon: 43°10'W
BASE_LAT = ("2254.0000", "S")
BASE_LON = ("04310.0000", "W")

# Antimeridian position: Pacific Ocean near Fiji
ANTI_LAT = ("1800.0000", "S")
ANTI_LON_W = ("17930.0000", "W")   # just west of antimeridian
ANTI_LON_E = ("17930.0000", "E")   # just east of antimeridian (after crossing)


def epoch(hh, mm, ss, lat=None, lon=None, quality=1, sats=9, hdop=1.0,
          alt=15.0, speed=5.5, heading=45.0, valid="A", with_zda=True,
          with_vtg=True, day=15, month=4, year=2026):
    """Return a full epoch (ZDA + GGA + RMC + VTG)."""
    lat_dm, lat_dir = lat or BASE_LAT
    lon_dm, lon_dir = lon or BASE_LON
    lines = []
    if with_zda:
        lines.append(zda(hh, mm, ss, day=day, month=month, year=year))
    lines.append(gga(hh, mm, ss, lat_dm, lat_dir, lon_dm, lon_dir,
                     quality=quality, sats=sats, hdop=hdop, alt=alt))
    lines.append(rmc(hh, mm, ss, lat_dm, lat_dir, lon_dm, lon_dir,
                     speed=speed, heading=heading, valid=valid,
                     day=day, month=month, year=year))
    if with_vtg:
        lines.append(vtg(heading=heading, speed_kts=speed))
    return lines


def write(filename: str, lines: list[str], bom: bool = False):
    path = OUT_DIR / filename
    content = "\n".join(lines) + "\n"
    if bom:
        path.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))
    else:
        path.write_text(content, encoding="utf-8")
    print(f"  Created: {path.name}  ({len(lines)} lines)")


# ---------------------------------------------------------------------------
# Test 01 — Clean baseline (1 Hz, 30 seconds, good signal)
# ---------------------------------------------------------------------------
def gen_01_clean():
    lines = []
    lats = ["2254.0000", "2253.9500", "2253.9000", "2253.8500",
            "2253.8000", "2253.7500", "2253.7000", "2253.6500",
            "2253.6000", "2253.5500", "2253.5000", "2253.4500",
            "2253.4000", "2253.3500", "2253.3000", "2253.2500",
            "2253.2000", "2253.1500", "2253.1000", "2253.0500",
            "2253.0000", "2252.9500", "2252.9000", "2252.8500",
            "2252.8000", "2252.7500", "2252.7000", "2252.6500",
            "2252.6000", "2252.5500"]
    lons = ["04310.0000", "04309.9000", "04309.8000", "04309.7000",
            "04309.6000", "04309.5000", "04309.4000", "04309.3000",
            "04309.2000", "04309.1000", "04309.0000", "04308.9000",
            "04308.8000", "04308.7000", "04308.6000", "04308.5000",
            "04308.4000", "04308.3000", "04308.2000", "04308.1000",
            "04308.0000", "04307.9000", "04307.8000", "04307.7000",
            "04307.6000", "04307.5000", "04307.4000", "04307.3000",
            "04307.2000", "04307.1000"]
    for i in range(30):
        lines += epoch(12, 0, i,
                       lat=(lats[i], "S"), lon=(lons[i], "W"),
                       quality=2, sats=11, hdop=0.9, alt=15.0,
                       speed=5.5, heading=45.0)
    write("01_clean.nmea", lines)


# ---------------------------------------------------------------------------
# Test 02 — GPS signal loss and recovery
# (quality drops to 0 for 5 seconds, then recovers as GPS, then DGPS)
# ---------------------------------------------------------------------------
def gen_02_signal_loss():
    lines = []
    # 0–9s: good DGPS
    for i in range(10):
        lines += epoch(12, 0, i, quality=2, sats=10, hdop=0.9)
    # 10–14s: signal lost (quality=0, RMC invalid)
    for i in range(10, 15):
        lines.append(zda(12, 0, i))
        lines.append(gga(12, 0, i, *BASE_LAT, *BASE_LON,
                         quality=0, sats=0, hdop=99.9, alt=0.0))
        lines.append(rmc(12, 0, i, *BASE_LAT, *BASE_LON,
                         speed=0.0, heading=0.0, valid="V"))
    # 15–19s: reacquiring (GPS only, high HDOP)
    for i in range(15, 20):
        lines += epoch(12, 0, i, quality=1, sats=4, hdop=4.5)
    # 20–29s: recovered DGPS
    for i in range(20, 30):
        lines += epoch(12, 0, i, quality=2, sats=10, hdop=0.9)
    write("02_signal_loss.nmea", lines)


# ---------------------------------------------------------------------------
# Test 03 — HDOP spike (poor satellite geometry for 8 seconds)
# ---------------------------------------------------------------------------
def gen_03_hdop_spike():
    lines = []
    for i in range(10):
        lines += epoch(12, 0, i, quality=1, sats=9, hdop=1.1)
    # Spike: HDOP 8–14 (satellite blocked, multipath)
    hdops = [1.1, 3.2, 8.4, 11.7, 14.3, 13.8, 9.1, 4.2, 1.8, 1.1]
    sats   = [9,   7,   5,   4,    3,    3,    5,   7,   8,   9  ]
    for i, (h, s) in enumerate(zip(hdops, sats)):
        lines += epoch(12, 0, i + 10, quality=1, sats=s, hdop=h)
    for i in range(20, 30):
        lines += epoch(12, 0, i, quality=2, sats=10, hdop=0.9)
    write("03_hdop_spike.nmea", lines)


# ---------------------------------------------------------------------------
# Test 04 — Leap second (23:59:60 UTC — last second of the day)
# Sentence contains second=60, which must NOT crash the parser.
# ---------------------------------------------------------------------------
def gen_04_leap_second():
    lines = []
    # Normal run approaching midnight
    for ss in range(55, 60):
        lines += epoch(23, 59, ss, with_zda=True)
    # THE leap second: time field = 235960.00
    leap_gga = "$GPGGA,235960.00,2254.0000,S,04310.0000,W,1,09,1.0,15.0,M,-2.3,M,,"
    leap_rmc = "$GPRMC,235960.00,A,2254.0000,S,04310.0000,W,5.5,45.0,150426,,,A"
    leap_zda = "$GPZDA,235960.00,15,04,2026,00,00"
    lines.append(line(leap_zda))
    lines.append(line(leap_gga))
    lines.append(line(leap_rmc))
    # Back to normal: 00:00:00 next day
    for ss in range(0, 5):
        lines += epoch(0, 0, ss, with_zda=True, day=16)
    write("04_leap_second.nmea", lines)


# ---------------------------------------------------------------------------
# Test 05 — Antimeridian crossing (−179° W → +179° E)
# Tests haversine distance calculation across ±180° longitude.
# ---------------------------------------------------------------------------
def gen_05_antimeridian():
    lines = []
    # Approach from west side
    west_lons = ["17958.0000", "17959.0000", "17959.5000",
                 "17959.8000", "17959.9000"]
    # Cross and move east
    east_lons = ["17959.9000", "17959.8000", "17959.5000",
                 "17959.0000", "17958.0000"]
    for i, lon in enumerate(west_lons):
        lines += epoch(12, 0, i, lat=("1800.0000", "S"), lon=(lon, "W"),
                       speed=8.0, heading=90.0)
    for i, lon in enumerate(east_lons):
        lines += epoch(12, 0, i + 5, lat=("1800.0000", "S"), lon=(lon, "E"),
                       speed=8.0, heading=90.0)
    write("05_antimeridian.nmea", lines)


# ---------------------------------------------------------------------------
# Test 06 — PPS drift / timing jitter
# ZDA intervals vary ±50ms around 1.000s — DEGRADED status expected.
# ---------------------------------------------------------------------------
def gen_06_pps_drift():
    lines = []
    from datetime import datetime, timedelta
    base = datetime(2026, 4, 15, 12, 0, 0)
    # Inject GGA/RMC at start and end only
    lines += epoch(12, 0, 0)
    lines += epoch(12, 0, 29)
    # Remove the epoch lines; rebuild with just ZDAs that drift
    lines = []
    lines += epoch(12, 0, 0)
    random.seed(42)
    t = base
    for i in range(30):
        jitter_ms = random.randint(-50, 50)  # ±50ms jitter
        t += timedelta(seconds=1, milliseconds=jitter_ms)
        hh, mm, ss = t.hour, t.minute, t.second
        ms = t.microsecond // 1000
        s = f"$GPZDA,{hh:02d}{mm:02d}{ss:02d}.{ms:03d},15,04,2026,00,00"
        lines.append(line(s))
    lines += epoch(12, 0, 29)
    write("06_pps_drift.nmea", lines)


# ---------------------------------------------------------------------------
# Test 07 — PPS loss: gap then backward time jump
# ---------------------------------------------------------------------------
def gen_07_pps_loss():
    lines = []
    # 10 good seconds
    for i in range(10):
        lines.append(zda(12, 0, i))
    lines += epoch(12, 0, 0)
    # Gap: 8 seconds missing
    for i in range(18, 25):
        lines.append(zda(12, 0, i))
    # Backward jump: clock went back 3 seconds (GPS receiver reset)
    for i in range(22, 30):
        lines.append(zda(12, 0, i))
    lines += epoch(12, 0, 29)
    write("07_pps_loss.nmea", lines)


# ---------------------------------------------------------------------------
# Test 08 — Bad checksums (mixed valid and corrupted sentences)
# ---------------------------------------------------------------------------
def gen_08_bad_checksums():
    lines = []
    for i in range(15):
        corrupt_gga = (i % 3 == 1)   # every 3rd GGA corrupted
        corrupt_rmc = (i % 5 == 2)   # every 5th RMC corrupted
        lines.append(zda(12, 0, i))
        s_gga = (f"$GPGGA,{12:02d}{0:02d}{i:02d}.00,"
                 f"2254.0000,S,04310.0000,W,1,09,1.0,15.0,M,-2.3,M,,")
        s_rmc = (f"$GPRMC,{12:02d}{0:02d}{i:02d}.00,A,"
                 f"2254.0000,S,04310.0000,W,5.5,45.0,150426,,,A")
        lines.append(line(s_gga, corrupt=corrupt_gga))
        lines.append(line(s_rmc, corrupt=corrupt_rmc))
    write("08_bad_checksums.nmea", lines)


# ---------------------------------------------------------------------------
# Test 09 — UTF-8 BOM at start of file
# Parser must handle BOM without breaking startswith("$") detection.
# ---------------------------------------------------------------------------
def gen_09_bom():
    lines = []
    for i in range(10):
        lines += epoch(12, 0, i, quality=1, sats=8, hdop=1.2)
    write("09_bom.nmea", lines, bom=True)


# ---------------------------------------------------------------------------
# Test 10 — Mixed talker prefixes (GP, GN, GL)
# Real receivers often mix GPGGA, GNGGA, GLGSV etc.
# ---------------------------------------------------------------------------
def gen_10_mixed_talkers():
    lines = []
    talkers = ["GP", "GN", "GL", "GN", "GP", "GN", "GP", "GL", "GN", "GP"]
    for i, talker in enumerate(talkers):
        lines.append(zda(12, 0, i))
        s_gga = (f"${talker}GGA,{12:02d}{0:02d}{i:02d}.00,"
                 f"2254.0000,S,04310.0000,W,1,09,1.0,15.0,M,-2.3,M,,")
        s_rmc = (f"${talker}RMC,{12:02d}{0:02d}{i:02d}.00,A,"
                 f"2254.0000,S,04310.0000,W,5.5,45.0,150426,,,A")
        lines.append(line(s_gga))
        lines.append(line(s_rmc))
    write("10_mixed_talkers.nmea", lines)


# ---------------------------------------------------------------------------
# Test 11 — Empty file
# Parser must return a clear error, not crash.
# ---------------------------------------------------------------------------
def gen_11_empty():
    write("11_empty.nmea", [])


# ---------------------------------------------------------------------------
# Test 12 — ZDA only (no GGA/RMC)
# Timing analysis should work; position fixes = 0.
# ---------------------------------------------------------------------------
def gen_12_zda_only():
    lines = []
    for i in range(30):
        lines.append(zda(12, 0, i))
    write("12_zda_only.nmea", lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Generating test files...")
    gen_01_clean()
    gen_02_signal_loss()
    gen_03_hdop_spike()
    gen_04_leap_second()
    gen_05_antimeridian()
    gen_06_pps_drift()
    gen_07_pps_loss()
    gen_08_bad_checksums()
    gen_09_bom()
    gen_10_mixed_talkers()
    gen_11_empty()
    gen_12_zda_only()
    print(f"\nDone. Files in: {OUT_DIR}")
