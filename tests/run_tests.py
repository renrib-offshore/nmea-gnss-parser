#!/usr/bin/env python3
"""
Test runner for nmea_parser.py
--------------------------------
Runs the parser against each test file and validates the output
against known expected results.

Run: python3 tests/run_tests.py
"""

import sys
from pathlib import Path

# Allow importing nmea_parser from project root
sys.path.insert(0, str(Path(__file__).parent.parent))
from nmea_parser import parse_file, compute_statistics, analyze_timing

NMEA_DIR = Path(__file__).parent / "nmea"

PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
WARN = "\033[93m[WARN]\033[0m"
INFO = "\033[94m[INFO]\033[0m"


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

class TestResult:
    def __init__(self, name: str):
        self.name = name
        self.checks: list[tuple[bool, str]] = []

    def check(self, condition: bool, description: str):
        self.checks.append((condition, description))

    @property
    def passed(self) -> bool:
        return all(ok for ok, _ in self.checks)

    def print(self):
        status = PASS if self.passed else FAIL
        print(f"\n{status} {self.name}")
        for ok, desc in self.checks:
            mark = "  ✓" if ok else "  ✗"
            print(f"{mark} {desc}")


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def test_01_clean():
    r = TestResult("01_clean — Baseline clean data (30 epochs, DGPS)")
    fixes, zda, stats = parse_file(NMEA_DIR / "01_clean.nmea")
    s = compute_statistics(fixes)
    t = analyze_timing(zda)

    r.check(stats["skipped_checksum"] == 0,       "No checksum errors")
    r.check(len(fixes) == 30,                      f"30 fixes parsed (got {len(fixes)})")
    r.check(s["valid_fixes"] == 30,                f"All 30 fixes valid (got {s['valid_fixes']})")
    r.check(s["distance_nm"] > 0,                  f"Non-zero distance ({s['distance_nm']:.3f} nm)")
    r.check(s["avg_hdop"] < 1.5,                   f"Good avg HDOP ({s['avg_hdop']:.2f})")
    r.check(t["pps_status"] == "LOCKED",           f"PPS LOCKED (got {t['pps_status']})")
    r.check(len(t.get("gaps", [])) == 0,           "No timing gaps")
    r.print()
    return r.passed


def test_02_signal_loss():
    r = TestResult("02_signal_loss — GPS loss for 5s, then recovery")
    fixes, zda, stats = parse_file(NMEA_DIR / "02_signal_loss.nmea")
    s = compute_statistics(fixes)

    total = stats["sentence_counts"].get("GGA", 0)
    skipped = stats["skipped_no_fix"]
    r.check(total == 30,                           f"30 GGA sentences (got {total})")
    r.check(skipped == 5,                          f"5 no-fix GGAs skipped (got {skipped})")
    r.check(s["valid_fixes"] == 25,                f"25 valid fixes (got {s['valid_fixes']})")
    r.check(s["invalid_fixes"] == 0,               "0 invalid (quality=0 excluded by default)")
    r.print()
    return r.passed


def test_03_hdop_spike():
    r = TestResult("03_hdop_spike — HDOP spike to 14 (poor geometry)")
    fixes, zda, stats = parse_file(NMEA_DIR / "03_hdop_spike.nmea")
    s = compute_statistics(fixes)

    r.check(len(fixes) == 30,                      f"30 fixes (got {len(fixes)})")
    r.check(s["max_hdop"] > 10.0,                  f"Max HDOP > 10 detected ({s['max_hdop']:.1f})")
    r.check(s["avg_hdop"] < s["max_hdop"],         "Avg HDOP < max HDOP (spike, not constant)")
    r.print()
    return r.passed


def test_04_leap_second():
    r = TestResult("04_leap_second — Timestamp 23:59:60 (leap second)")
    # Must NOT raise an exception
    try:
        fixes, zda, stats = parse_file(NMEA_DIR / "04_leap_second.nmea")
        r.check(True,                              "Parser did not crash on second=60")
    except Exception as e:
        r.check(False,                             f"Parser crashed: {e}")
        r.print()
        return False

    # Leap second should be clamped to :59, so timestamp should be valid
    timestamped = [f for f in fixes if f.timestamp]
    leap_fixes = [f for f in timestamped if f.timestamp.hour == 23
                  and f.timestamp.minute == 59]
    r.check(len(fixes) > 0,                        f"Fixes parsed ({len(fixes)})")
    r.check(len(leap_fixes) > 0,                   f"Fix at 23:59 present (clamped from :60)")
    for f in timestamped:
        r.check(f.timestamp.second <= 59,          f"All seconds ≤ 59 (timestamp: {f.timestamp})")
    r.print()
    return r.passed


def test_05_antimeridian():
    r = TestResult("05_antimeridian — Track crosses ±180° longitude")
    fixes, zda, stats = parse_file(NMEA_DIR / "05_antimeridian.nmea")
    s = compute_statistics(fixes)

    r.check(len(fixes) == 10,                      f"10 fixes (got {len(fixes)})")
    # Distance should be small (crossing is ~0.2° = ~6.5 nm at 18°S)
    # NOT ~20000 nm (what a buggy haversine would return)
    r.check(s["distance_nm"] < 50,                 f"Distance reasonable < 50 nm (got {s['distance_nm']:.2f} nm)")
    r.check(s["distance_nm"] > 0,                  "Non-zero distance")
    r.print()
    return r.passed


def test_06_pps_drift():
    r = TestResult("06_pps_drift — ZDA jitter ±50ms (DEGRADED or LOCKED)")
    fixes, zda, stats = parse_file(NMEA_DIR / "06_pps_drift.nmea")
    t = analyze_timing(zda)

    r.check(t["zda_count"] >= 20,                  f"ZDA sentences captured ({t['zda_count']})")
    r.check(t["avg_deviation_ms"] > 0,             f"Non-zero deviation detected ({t['avg_deviation_ms']:.2f} ms)")
    r.check(t["pps_status"] in ("LOCKED", "DEGRADED", "UNLOCKED"),
                                                   f"PPS status is valid ({t['pps_status']})")
    r.print()
    return r.passed


def test_07_pps_loss():
    r = TestResult("07_pps_loss — ZDA gap of 8s and backward time jump")
    fixes, zda, stats = parse_file(NMEA_DIR / "07_pps_loss.nmea")
    t = analyze_timing(zda)

    gaps = t.get("gaps", [])
    gap_kinds = {g.kind for g in gaps}
    r.check(len(gaps) > 0,                         f"At least one timing event detected ({len(gaps)})")
    r.check("gap" in gap_kinds or "forward_jump" in gap_kinds,
                                                   f"Gap detected (kinds: {gap_kinds})")
    r.check("backward_jump" in gap_kinds,          f"Backward jump detected (kinds: {gap_kinds})")
    r.print()
    return r.passed


def test_08_bad_checksums():
    r = TestResult("08_bad_checksums — Mixed valid/corrupted checksums")
    fixes, zda, stats = parse_file(NMEA_DIR / "08_bad_checksums.nmea")

    bad = stats["skipped_checksum"]
    total = stats["total_sentences"]
    r.check(bad > 0,                               f"Bad checksums detected and skipped ({bad})")
    r.check(bad < total,                           f"Some valid sentences parsed ({total - bad}/{total})")
    r.check(len(fixes) > 0,                        f"Valid fixes still extracted ({len(fixes)})")
    r.print()
    return r.passed


def test_09_bom():
    r = TestResult("09_bom — UTF-8 BOM at start of file")
    try:
        fixes, zda, stats = parse_file(NMEA_DIR / "09_bom.nmea")
        r.check(True,                              "Parser did not crash on BOM file")
        r.check(len(fixes) > 0,                    f"Fixes extracted despite BOM ({len(fixes)})")
        r.check(stats["skipped_checksum"] == 0,    "No checksum errors caused by BOM")
    except Exception as e:
        r.check(False,                             f"Parser crashed: {e}")
    r.print()
    return r.passed


def test_10_mixed_talkers():
    r = TestResult("10_mixed_talkers — GP / GN / GL talker prefixes")
    fixes, zda, stats = parse_file(NMEA_DIR / "10_mixed_talkers.nmea")

    r.check(len(fixes) == 10,                      f"10 fixes from mixed talkers (got {len(fixes)})")
    r.check(stats["skipped_checksum"] == 0,        "No checksum errors")
    r.print()
    return r.passed


def test_11_empty():
    r = TestResult("11_empty — Empty file (must fail gracefully)")
    try:
        fixes, zda, stats = parse_file(NMEA_DIR / "11_empty.nmea")
        r.check(len(fixes) == 0,                   "No fixes returned from empty file")
        r.check(stats["total_sentences"] == 0,     "Zero sentences parsed")
        r.check(True,                              "No exception raised")
    except Exception as e:
        r.check(False,                             f"Unexpected exception: {e}")
    r.print()
    return r.passed


def test_12_zda_only():
    r = TestResult("12_zda_only — Only ZDA sentences (timing without position)")
    fixes, zda, stats = parse_file(NMEA_DIR / "12_zda_only.nmea")
    t = analyze_timing(zda)

    r.check(len(fixes) == 0,                       "No position fixes (expected)")
    r.check(t["zda_count"] == 30,                  f"30 ZDA events captured (got {t['zda_count']})")
    r.check(t["pps_status"] == "LOCKED",           f"PPS LOCKED on clean 1Hz ZDA (got {t['pps_status']})")
    r.check(len(t.get("gaps", [])) == 0,           "No timing gaps")
    r.print()
    return r.passed


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_01_clean,
    test_02_signal_loss,
    test_03_hdop_spike,
    test_04_leap_second,
    test_05_antimeridian,
    test_06_pps_drift,
    test_07_pps_loss,
    test_08_bad_checksums,
    test_09_bom,
    test_10_mixed_talkers,
    test_11_empty,
    test_12_zda_only,
]

if __name__ == "__main__":
    print("=" * 60)
    print("  NMEA Parser — Test Suite")
    print("=" * 60)

    results = [t() for t in TESTS]
    passed = sum(results)
    total = len(results)

    print(f"\n{'=' * 60}")
    print(f"  Result: {passed}/{total} tests passed")
    if passed == total:
        print(f"  {PASS} All tests passed.")
    else:
        failed = [TESTS[i].__name__ for i, ok in enumerate(results) if not ok]
        print(f"  {FAIL} Failed: {', '.join(failed)}")
    print("=" * 60)

    sys.exit(0 if passed == total else 1)
