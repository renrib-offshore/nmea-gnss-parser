#!/usr/bin/env python3
"""
NMEA/GNSS Parser
----------------
Parses NMEA 0183 log files from GPS/GNSS receivers.
Extracts position fixes, calculates statistics, and exports CSV + KML.
Includes ZDA timing analysis with PPS lock inference.

Supports: GGA, RMC, VTG, GSA, ZDA sentences.

Usage:
    python nmea_parser.py <file.nmea> [options]
    python nmea_parser.py --help
"""

import argparse
import csv
import math
import sys
import xml.sax.saxutils as saxutils
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Fix:
    timestamp: Optional[datetime]
    lat: float
    lon: float
    quality: int          # GGA fix quality: 0=invalid, 1=GPS, 2=DGPS, 4=RTK fixed, 5=RTK float
    satellites: int
    hdop: float
    altitude: float       # meters above MSL
    speed_kts: float      # knots (from RMC/VTG)
    heading: float        # degrees true (from RMC/VTG)
    valid: bool           # RMC validity flag


@dataclass
class ZDAEvent:
    timestamp: datetime   # UTC timestamp with microsecond precision


@dataclass
class TimingGap:
    start: datetime
    end: datetime
    duration_s: float
    missed_cycles: int
    kind: str             # "gap" | "forward_jump" | "backward_jump"


QUALITY_LABELS = {
    0: "No fix",
    1: "GPS",
    2: "DGPS",
    3: "PPS",
    4: "RTK Fixed",
    5: "RTK Float",
    6: "Estimated",
    7: "Manual",
    8: "Simulation",
}


# ---------------------------------------------------------------------------
# NMEA checksum
# ---------------------------------------------------------------------------

def verify_checksum(sentence: str) -> bool:
    """Returns True if the NMEA checksum is valid."""
    if "*" not in sentence:
        return True  # no checksum present, accept
    try:
        body, checksum = sentence.lstrip("$").rsplit("*", 1)
        calc = 0
        for ch in body:
            calc ^= ord(ch)
        return calc == int(checksum.strip(), 16)
    except (ValueError, IndexError):
        return False


# ---------------------------------------------------------------------------
# Coordinate conversion
# ---------------------------------------------------------------------------

def nmea_to_decimal(value: str, direction: str) -> Optional[float]:
    """
    Converts NMEA coordinate format (DDDMM.MMMM, N/S/E/W) to decimal degrees.
    Example: '2254.1234', 'S' -> -22.902057
    """
    if not value or not direction:
        return None
    try:
        dot = value.index(".")
        deg = float(value[: dot - 2])
        minutes = float(value[dot - 2:])
        decimal = deg + minutes / 60.0
        if direction in ("S", "W"):
            decimal = -decimal
        return decimal
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# Sentence parsers
# ---------------------------------------------------------------------------

def parse_gga(fields: list[str]) -> dict:
    """$GPGGA,hhmmss.ss,lat,N,lon,E,q,sats,hdop,alt,M,sep,M,,*cs"""
    result = {}
    try:
        result["time_str"] = fields[1]
        result["lat"] = nmea_to_decimal(fields[2], fields[3])
        result["lon"] = nmea_to_decimal(fields[4], fields[5])
        result["quality"] = int(fields[6]) if fields[6] else 0
        result["satellites"] = int(fields[7]) if fields[7] else 0
        result["hdop"] = float(fields[8]) if fields[8] else 99.9
        result["altitude"] = float(fields[9]) if fields[9] else 0.0
    except (ValueError, IndexError):
        pass
    return result


def parse_rmc(fields: list[str]) -> dict:
    """$GPRMC,hhmmss.ss,A,lat,N,lon,E,spd,hdg,ddmmyy,,,A*cs"""
    result = {}
    try:
        result["time_str"] = fields[1]
        result["valid"] = fields[2] == "A"
        result["lat"] = nmea_to_decimal(fields[3], fields[4])
        result["lon"] = nmea_to_decimal(fields[5], fields[6])
        result["speed_kts"] = float(fields[7]) if fields[7] else 0.0
        result["heading"] = float(fields[8]) if fields[8] else 0.0
        result["date_str"] = fields[9]
    except (ValueError, IndexError):
        pass
    return result


def parse_vtg(fields: list[str]) -> dict:
    """$GPVTG,hdg,T,hdg,M,spd,N,spd,K,A*cs"""
    result = {}
    try:
        result["heading"] = float(fields[1]) if fields[1] else 0.0
        result["speed_kts"] = float(fields[5]) if fields[5] else 0.0
    except (ValueError, IndexError):
        pass
    return result



# ---------------------------------------------------------------------------
# Distance calculation
# ---------------------------------------------------------------------------

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Returns distance in nautical miles between two decimal-degree points."""
    R = 3440.065  # Earth radius in nautical miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlon = lon2 - lon1
    if dlon > 180:   dlon -= 360  # normalize for antimeridian crossing
    if dlon < -180:  dlon += 360
    dlambda = math.radians(dlon)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ---------------------------------------------------------------------------
# Timestamp builders
# ---------------------------------------------------------------------------

def build_timestamp(time_str: str, date_str: str) -> Optional[datetime]:
    """Builds a datetime from NMEA time (hhmmss.ss) and date (ddmmyy)."""
    try:
        h = int(time_str[0:2])
        m = int(time_str[2:4])
        s = min(int(float(time_str[4:])), 59)  # clamp leap second (60 → 59)
        day = int(date_str[0:2])
        month = int(date_str[2:4])
        year = 2000 + int(date_str[4:6])
        return datetime(year, month, day, h, m, s)
    except (ValueError, IndexError, TypeError):
        return None


def build_zda_timestamp(time_str: str, day: str, month: str, year: str) -> Optional[datetime]:
    """
    Builds a datetime from ZDA fields with sub-second precision.
    ZDA uses full 4-digit year and hhmmss.ss time format.
    """
    try:
        h = int(time_str[0:2])
        m = int(time_str[2:4])
        sec_f = float(time_str[4:])
        s = min(int(sec_f), 59)  # clamp leap second (60 → 59)
        us = int(round((sec_f - int(sec_f)) * 1_000_000))
        return datetime(int(year), int(month), int(day), h, m, s, us)
    except (ValueError, IndexError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_file(path: Path, ignore_invalid: bool = False) -> tuple[list[Fix], list[ZDAEvent], dict]:
    """
    Parses an NMEA log file and returns Fix objects, ZDA events, and statistics.
    Correlates GGA + RMC sentences by timestamp.
    """
    fixes: list[Fix] = []
    zda_events: list[ZDAEvent] = []
    encoding_errors = 0
    stats = {
        "total_sentences": 0,
        "skipped_checksum": 0,
        "skipped_no_fix": 0,
        "sentence_counts": {},
    }

    # Buffers to correlate GGA + RMC within the same second
    gga_buf: dict = {}
    rmc_buf: dict = {}
    vtg_buf: dict = {}
    current_date = ""

    def flush_buffers():
        """Merge buffered sentences into a Fix if we have GGA data."""
        if not gga_buf or gga_buf.get("lat") is None:
            return
        quality = gga_buf.get("quality", 0)
        if quality == 0 and not ignore_invalid:
            stats["skipped_no_fix"] += 1
            return

        ts = None
        if rmc_buf.get("date_str") and gga_buf.get("time_str"):
            ts = build_timestamp(gga_buf["time_str"], rmc_buf["date_str"])
        elif current_date and gga_buf.get("time_str"):
            ts = build_timestamp(gga_buf["time_str"], current_date)

        fix = Fix(
            timestamp=ts,
            lat=gga_buf.get("lat", 0.0),
            lon=gga_buf.get("lon", 0.0),
            quality=quality,
            satellites=gga_buf.get("satellites", 0),
            hdop=gga_buf.get("hdop", 99.9),
            altitude=gga_buf.get("altitude", 0.0),
            speed_kts=vtg_buf.get("speed_kts") or rmc_buf.get("speed_kts", 0.0),
            heading=vtg_buf.get("heading") or rmc_buf.get("heading", 0.0),
            valid=rmc_buf.get("valid", quality > 0),
        )
        fixes.append(fix)

    prev_time = None

    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        for raw_line in f:
            encoding_errors += raw_line.count("\ufffd")
            line = raw_line.strip()
            if not line or not line.startswith("$"):
                continue

            stats["total_sentences"] += 1

            if not verify_checksum(line):
                stats["skipped_checksum"] += 1
                continue

            # Strip checksum
            sentence = line.split("*")[0]
            fields = sentence.split(",")
            talker_msg = fields[0][1:]  # e.g. "GPGGA"
            msg_type = talker_msg[-3:]  # e.g. "GGA"
            stats["sentence_counts"][msg_type] = stats["sentence_counts"].get(msg_type, 0) + 1

            # Track current time to detect new epochs
            cur_time = fields[1] if len(fields) > 1 else ""

            if msg_type == "ZDA":
                try:
                    current_date = f"{fields[2].zfill(2)}{fields[3].zfill(2)}{fields[4][2:]}"
                    ts = build_zda_timestamp(fields[1], fields[2], fields[3], fields[4])
                    if ts:
                        zda_events.append(ZDAEvent(timestamp=ts))
                except IndexError:
                    pass

            elif msg_type == "GGA":
                if cur_time != prev_time and prev_time is not None:
                    flush_buffers()
                    gga_buf.clear(); rmc_buf.clear(); vtg_buf.clear()
                parsed_gga = parse_gga(fields)
                if parsed_gga.get("lat") is not None:  # only update if parse succeeded
                    gga_buf.update(parsed_gga)
                prev_time = cur_time

            elif msg_type == "RMC":
                parsed_rmc = parse_rmc(fields)
                rmc_buf.update(parsed_rmc)
                if parsed_rmc.get("date_str"):
                    current_date = parsed_rmc["date_str"]

            elif msg_type == "VTG":
                vtg_buf.update(parse_vtg(fields))
            # GSA: not parsed (HDOP sourced from GGA)

    flush_buffers()
    if encoding_errors > 0:
        print(f"Warning: {encoding_errors} encoding error(s) in file — some characters were replaced.", file=sys.stderr)
    return fixes, zda_events, stats


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def compute_statistics(fixes: list[Fix]) -> dict:
    """Computes summary statistics from the list of fixes."""
    if not fixes:
        return {}

    valid = [f for f in fixes if f.valid and f.quality > 0]
    invalid = [f for f in fixes if not f.valid or f.quality == 0]

    # Duration
    timestamped = [f for f in fixes if f.timestamp]
    duration = None
    if len(timestamped) >= 2:
        duration = timestamped[-1].timestamp - timestamped[0].timestamp

    # Distance (nautical miles)
    distance_nm = 0.0
    for i in range(1, len(valid)):
        distance_nm += haversine(
            valid[i - 1].lat, valid[i - 1].lon,
            valid[i].lat, valid[i].lon
        )

    # Speed stats
    speeds = [f.speed_kts for f in valid if f.speed_kts > 0]
    avg_speed = sum(speeds) / len(speeds) if speeds else 0.0
    max_speed = max(speeds) if speeds else 0.0

    # HDOP stats
    hdops = [f.hdop for f in valid if f.hdop < 99.0]
    avg_hdop = sum(hdops) / len(hdops) if hdops else 0.0
    max_hdop = max(hdops) if hdops else 0.0

    # Altitude range
    alts = [f.altitude for f in valid]
    alt_min = min(alts) if alts else 0.0
    alt_max = max(alts) if alts else 0.0

    # Fix quality distribution
    quality_dist: dict[int, int] = {}
    for f in fixes:
        quality_dist[f.quality] = quality_dist.get(f.quality, 0) + 1

    return {
        "total_fixes": len(fixes),
        "valid_fixes": len(valid),
        "invalid_fixes": len(invalid),
        "duration": duration,
        "distance_nm": distance_nm,
        "avg_speed_kts": avg_speed,
        "max_speed_kts": max_speed,
        "avg_hdop": avg_hdop,
        "max_hdop": max_hdop,
        "alt_min": alt_min,
        "alt_max": alt_max,
        "quality_dist": quality_dist,
        "start_time": timestamped[0].timestamp if timestamped else None,
        "end_time": timestamped[-1].timestamp if timestamped else None,
    }


# ---------------------------------------------------------------------------
# ZDA timing analysis
# ---------------------------------------------------------------------------

def analyze_timing(events: list[ZDAEvent]) -> dict:
    """
    Analyzes ZDA timestamps to assess timing quality and infer PPS lock status.

    Logic:
    - A GPS receiver with PPS locked emits ZDA at exactly 1.000s intervals.
    - Any deviation from 1.000s indicates timing instability.
    - Gaps > 1.5s mean at least one cycle was missed (signal loss).
    - PPS lock is inferred from the percentage of intervals within ±10ms of 1.000s.
    """
    if len(events) < 2:
        return {"zda_count": len(events), "pps_status": "INSUFFICIENT DATA"}

    EXPECTED    = 1.0     # seconds
    TOLERANCE   = 0.010   # ±10ms — threshold for "PPS locked"
    GAP_THRESH  = 1.5     # seconds — interval above this = missed cycle(s)

    intervals: list[float] = []
    gaps: list[TimingGap] = []

    for i in range(1, len(events)):
        dt = (events[i].timestamp - events[i - 1].timestamp).total_seconds()
        intervals.append(dt)

        if dt < 0:
            gaps.append(TimingGap(
                start=events[i - 1].timestamp,
                end=events[i].timestamp,
                duration_s=dt,
                missed_cycles=0,
                kind="backward_jump",
            ))
        elif dt > GAP_THRESH:
            missed = max(0, round(dt) - 1)
            kind = "forward_jump" if dt > 60 else "gap"
            gaps.append(TimingGap(
                start=events[i - 1].timestamp,
                end=events[i].timestamp,
                duration_s=dt,
                missed_cycles=missed,
                kind=kind,
            ))

    # Statistics on "normal" intervals only (0.5s – 1.5s)
    normal = [iv for iv in intervals if 0.5 <= iv <= 1.5]
    if not normal:
        return {"zda_count": len(events), "pps_status": "NO NORMAL INTERVALS", "gaps": gaps}

    deviations = [abs(iv - EXPECTED) for iv in normal]
    avg_dev_ms  = (sum(deviations) / len(deviations)) * 1000
    max_dev_ms  = max(deviations) * 1000
    locked_pct  = sum(1 for d in deviations if d <= TOLERANCE) / len(deviations) * 100

    # Uptime: ZDA sentences received vs expected for the session duration
    # (computed here so it feeds into PPS status below)
    session_s = (events[-1].timestamp - events[0].timestamp).total_seconds()
    expected_count = round(session_s) + 1
    uptime_pct = min(len(events) / expected_count * 100, 100.0) if expected_count > 0 else 0.0

    # PPS lock inference — both interval quality AND uptime must meet the threshold.
    # A receiver with gaps (PPS absent) cannot be considered LOCKED even if the
    # intervals that did arrive were perfect.
    if locked_pct >= 95 and max_dev_ms <= 50 and uptime_pct >= 95:
        pps_status = "LOCKED"
    elif locked_pct >= 75 and uptime_pct >= 75:
        pps_status = "DEGRADED"
    else:
        pps_status = "UNLOCKED"

    missed_total = sum(g.missed_cycles for g in gaps)

    return {
        "zda_count": len(events),
        "intervals": intervals,
        "gaps": gaps,
        "avg_interval_s": sum(intervals) / len(intervals),
        "avg_deviation_ms": avg_dev_ms,
        "max_deviation_ms": max_dev_ms,
        "locked_pct": locked_pct,
        "pps_status": pps_status,
        "uptime_pct": uptime_pct,
        "expected_count": expected_count,
        "missed_cycles": missed_total,
    }


# ---------------------------------------------------------------------------
# Exporters
# ---------------------------------------------------------------------------

def export_csv(fixes: list[Fix], output_path: Path):
    """Exports all fixes to a CSV file."""
    fieldnames = [
        "timestamp", "latitude", "longitude", "quality", "quality_label",
        "satellites", "hdop", "altitude_m", "speed_kts", "heading_deg", "valid"
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for fix in fixes:
            writer.writerow({
                "timestamp": fix.timestamp.isoformat() if fix.timestamp else "",
                "latitude": f"{fix.lat:.8f}",
                "longitude": f"{fix.lon:.8f}",
                "quality": fix.quality,
                "quality_label": QUALITY_LABELS.get(fix.quality, "Unknown"),
                "satellites": fix.satellites,
                "hdop": f"{fix.hdop:.2f}",
                "altitude_m": f"{fix.altitude:.2f}",
                "speed_kts": f"{fix.speed_kts:.2f}",
                "heading_deg": f"{fix.heading:.1f}",
                "valid": fix.valid,
            })


def export_kml(fixes: list[Fix], output_path: Path, name: str = "GNSS Track"):
    """Exports the track and waypoints to a KML file (opens in Google Earth)."""
    valid = [f for f in fixes if f.valid and f.quality > 0]

    coords = "\n".join(
        f"          {f.lon:.8f},{f.lat:.8f},{f.altitude:.2f}"
        for f in valid
    )

    placemarks = []
    for i, f in enumerate(valid):
        ts = f.timestamp.isoformat() if f.timestamp else f"Fix {i+1}"
        desc = (
            f"Quality: {QUALITY_LABELS.get(f.quality, '?')}\n"
            f"Satellites: {f.satellites}\n"
            f"HDOP: {f.hdop:.2f}\n"
            f"Altitude: {f.altitude:.2f} m\n"
            f"Speed: {f.speed_kts:.1f} kts\n"
            f"Heading: {f.heading:.1f} deg"
        )
        safe_ts   = saxutils.escape(ts)
        safe_desc = saxutils.escape(desc)
        placemarks.append(
            f"""    <Placemark>
      <name>{safe_ts}</name>
      <description>{safe_desc}</description>
      <Point>
        <coordinates>{f.lon:.8f},{f.lat:.8f},{f.altitude:.2f}</coordinates>
      </Point>
    </Placemark>"""
        )

    placemark_block = "\n".join(placemarks)
    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{name}</name>
    <Style id="trackLine">
      <LineStyle>
        <color>ff0000ff</color>
        <width>3</width>
      </LineStyle>
    </Style>
    <Placemark>
      <name>Track</name>
      <styleUrl>#trackLine</styleUrl>
      <LineString>
        <altitudeMode>clampToGround</altitudeMode>
        <coordinates>
{coords}
        </coordinates>
      </LineString>
    </Placemark>
{placemark_block}
  </Document>
</kml>"""

    output_path.write_text(kml, encoding="utf-8")


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def _fmt_duration(seconds: float) -> str:
    """Formats a duration in seconds to a human-readable string (e.g. '10m 03s')."""
    total = int(abs(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{seconds:.3f}s"


def hdop_label(hdop: float) -> str:
    if hdop <= 1.0:   return "Ideal"
    if hdop <= 2.0:   return "Excellent"
    if hdop <= 5.0:   return "Good"
    if hdop <= 10.0:  return "Moderate"
    return "Poor"


def print_report(stats: dict, parse_stats: dict, timing: dict, input_file: Path, csv_out: Path, kml_out: Path):
    """Prints a formatted summary report to the terminal."""
    sep = "─" * 56

    print(f"\n{'═' * 56}")
    print(f"  NMEA/GNSS Parser — Report")
    print(f"{'═' * 56}")
    print(f"  File : {input_file.name}")
    if stats.get("start_time"):
        print(f"  Start: {stats['start_time'].strftime('%Y-%m-%d %H:%M:%S')} UTC")
    if stats.get("end_time"):
        print(f"  End  : {stats['end_time'].strftime('%Y-%m-%d %H:%M:%S')} UTC")
    if stats.get("duration"):
        total_sec = int(stats["duration"].total_seconds())
        h, rem = divmod(total_sec, 3600)
        m, s = divmod(rem, 60)
        print(f"  Duration: {h:02d}h {m:02d}m {s:02d}s")

    print(f"\n{sep}")
    print(f"  SENTENCES")
    print(f"{sep}")
    print(f"  Total parsed : {parse_stats['total_sentences']}")
    print(f"  Bad checksum : {parse_stats['skipped_checksum']}")
    for msg, count in sorted(parse_stats["sentence_counts"].items()):
        print(f"  {msg:<8}     : {count}")

    print(f"\n{sep}")
    print(f"  POSITION FIXES")
    print(f"{sep}")
    print(f"  Total  : {stats.get('total_fixes', 0)}")
    print(f"  Valid  : {stats.get('valid_fixes', 0)}")
    print(f"  Invalid: {stats.get('invalid_fixes', 0)}")

    qd = stats.get("quality_dist", {})
    if qd:
        print(f"\n  Fix quality breakdown:")
        for q, count in sorted(qd.items()):
            label = QUALITY_LABELS.get(q, "?")
            print(f"    [{q}] {label:<12} : {count}")

    print(f"\n{sep}")
    print(f"  NAVIGATION")
    print(f"{sep}")
    dist = stats.get("distance_nm", 0.0)
    print(f"  Distance    : {dist:.3f} nm  ({dist * 1.852:.3f} km)")
    print(f"  Avg speed   : {stats.get('avg_speed_kts', 0.0):.1f} kts")
    print(f"  Max speed   : {stats.get('max_speed_kts', 0.0):.1f} kts")

    print(f"\n{sep}")
    print(f"  QUALITY")
    print(f"{sep}")
    avg_h = stats.get("avg_hdop", 0.0)
    max_h = stats.get("max_hdop", 0.0)
    print(f"  Avg HDOP : {avg_h:.2f}  ({hdop_label(avg_h)})")
    print(f"  Max HDOP : {max_h:.2f}  ({hdop_label(max_h)})")
    print(f"  Alt range: {stats.get('alt_min', 0.0):.1f} m — {stats.get('alt_max', 0.0):.1f} m MSL")

    if timing and timing.get("zda_count", 0) >= 2:
        print(f"\n{sep}")
        print(f"  TIMING / PPS ANALYSIS  (ZDA)")
        print(f"{sep}")

        pps = timing["pps_status"]
        pps_indicator = {"LOCKED": "[OK]", "DEGRADED": "[WARN]", "UNLOCKED": "[FAIL]"}.get(pps, "[?]")
        print(f"  PPS lock status  : {pps_indicator} {pps}")
        print(f"  ZDA sentences    : {timing['zda_count']}  (expected ~{timing.get('expected_count', '?')})")
        print(f"  Uptime           : {timing.get('uptime_pct', 0.0):.1f}%")
        print(f"  Locked intervals : {timing.get('locked_pct', 0.0):.1f}%  (within ±10ms of 1.000s)")
        print(f"  Avg deviation    : {timing.get('avg_deviation_ms', 0.0):.2f} ms")
        print(f"  Max deviation    : {timing.get('max_deviation_ms', 0.0):.2f} ms")

        gaps = timing.get("gaps", [])
        missed = timing.get("missed_cycles", 0)
        if gaps:
            print(f"\n  Timing events detected: {len(gaps)}  (missed cycles: {missed})")
            for g in gaps:
                kind_label = {"gap": "GAP", "forward_jump": "JUMP FWD", "backward_jump": "JUMP BWD"}.get(g.kind, g.kind)
                dur = _fmt_duration(abs(g.duration_s))
                missed_str = f", {g.missed_cycles} missed cycles" if g.missed_cycles else ""
                print(f"    [{kind_label}] {g.start.strftime('%Y-%m-%d %H:%M:%S')} → {g.end.strftime('%H:%M:%S')}"
                      f"  (duration: {dur}{missed_str})")
        else:
            print(f"\n  No timing gaps detected.")

    print(f"\n{sep}")
    print(f"  OUTPUT FILES")
    print(f"{sep}")
    print(f"  CSV : {csv_out}")
    print(f"  KML : {kml_out}")
    print(f"{'═' * 56}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="NMEA/GNSS log parser — extracts fixes, stats, CSV and KML.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python nmea_parser.py samples/sample.nmea
  python nmea_parser.py survey.nmea --output /tmp/results --include-invalid
        """,
    )
    parser.add_argument("input", type=Path, help="NMEA log file (.nmea or .txt)")
    parser.add_argument(
        "--output", "-o", type=Path, default=None,
        help="Output directory (default: same as input file)"
    )
    parser.add_argument(
        "--include-invalid", action="store_true",
        help="Include fixes with quality=0 in output (excluded by default)"
    )
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    out_dir = (args.output if args.output else args.input.parent).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.input.stem

    print(f"Parsing {args.input} ...")
    fixes, zda_events, parse_stats = parse_file(args.input, ignore_invalid=args.include_invalid)

    if not fixes:
        print(
            f"No valid fixes found. Parsed {parse_stats['total_sentences']} sentences "
            f"({parse_stats['skipped_checksum']} bad checksum, "
            f"{parse_stats['skipped_no_fix']} no-fix).",
            file=sys.stderr,
        )
        sys.exit(1)

    stats = compute_statistics(fixes)
    timing = analyze_timing(zda_events)

    csv_out = out_dir / f"{stem}_fixes.csv"
    kml_out = out_dir / f"{stem}_track.kml"

    export_csv(fixes, csv_out)
    export_kml(fixes, kml_out, name=stem)
    print_report(stats, parse_stats, timing, args.input, csv_out, kml_out)


if __name__ == "__main__":
    main()
