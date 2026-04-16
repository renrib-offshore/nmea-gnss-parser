# NMEA/GNSS Parser

A professional NMEA 0183 log parser built in pure Python (no external dependencies).  
Designed for marine, offshore, and survey applications.

## Features

- Parses **GGA, RMC, VTG, GSA, ZDA** sentences
- Validates NMEA checksums
- Converts NMEA coordinates to decimal degrees
- Calculates distance (nautical miles & km), speed, and duration
- HDOP quality assessment
- Fix quality breakdown (GPS, DGPS, RTK Fixed/Float)
- Exports **CSV** (all fix data) and **KML** (track for Google Earth)
- Clean terminal report

## Usage

```bash
python nmea_parser.py <file.nmea>

# Custom output directory
python nmea_parser.py survey.nmea --output ./results

# Include invalid fixes (quality=0) in output
python nmea_parser.py survey.nmea --include-invalid
```

## Example Output

```
════════════════════════════════════════════════════════
  NMEA/GNSS Parser — Report
════════════════════════════════════════════════════════
  File : sample.nmea
  Start: 2026-04-15 12:00:00 UTC
  End  : 2026-04-15 12:04:30 UTC
  Duration: 00h 04m 30s

  SENTENCES
  ─────────────────────────────────────────────────────
  Total parsed : 39  |  Bad checksum : 0
  GGA: 10  |  RMC: 10  |  VTG: 9  |  ZDA: 10

  POSITION FIXES
  ─────────────────────────────────────────────────────
  Total: 10  |  Valid: 9  |  Invalid: 1
  [1] GPS: 7  |  [2] DGPS: 2  |  [5] RTK Float: 1

  NAVIGATION
  ─────────────────────────────────────────────────────
  Distance  : 1.428 nm  (2.645 km)
  Avg speed : 5.9 kts  |  Max speed: 6.5 kts

  QUALITY
  ─────────────────────────────────────────────────────
  Avg HDOP : 1.01 (Excellent)  |  Max HDOP: 1.20
  Alt range: 14.4 m — 15.4 m MSL
════════════════════════════════════════════════════════
```

## Requirements

- Python 3.10+
- No third-party libraries required

## Output Files

| File | Description |
|------|-------------|
| `<name>_fixes.csv` | All fixes with coordinates, quality, speed, heading |
| `<name>_track.kml` | Track + waypoints, opens in Google Earth |

## Author

**Renato Ribeiro**  
Computer Engineer | Electronics Technician | Senior Offshore Surveyor  
10+ years of subsea positioning and sensor integration experience.  
[LinkedIn](https://linkedin.com/in/renatoribeiro32854870)
