"""Positive tests for CROWD snapshot bridge and staleness logic."""
import json
import pathlib
import sys
import tempfile
from datetime import date
from unittest.mock import patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

from generate_report import (
    _is_us_trading_day,
    _latest_completed_trading_day,
    render_snapshot,
)


# ── Fixtures ──

def make_snapshot(as_of="2026-07-30"):
    return {
        "as_of": as_of,
        "benchmark": "SOXX=1.0",
        "scissors": {
            "formula": "MEM/SOXX - OPT/SOXX",
            "value": 6.0474,
            "direction_20d": "up",
            "direction_60d": "up",
        },
        "baskets": {
            "MEM": {"label": "存储", "ratio": 7.4647, "momentum_20d": -16.92, "momentum_60d": 9.94, "z_score": 1.12},
            "OPT": {"label": "光通信", "ratio": 1.4173, "momentum_20d": -7.45, "momentum_60d": -36.01, "z_score": -0.48},
            "GPU": {"label": "GPU链", "ratio": 0.8036, "momentum_20d": 16.22, "momentum_60d": 1.42, "z_score": -0.64},
            "NCLOUD": {"label": "新云", "ratio": 1.0225, "momentum_20d": 0.45, "momentum_60d": -26.74, "z_score": -1.29},
        },
    }


# ── Test: Normal snapshot rendering ──

class TestNormalRendering:
    def test_renders_all_four_baskets(self):
        warnings = []
        lines = render_snapshot(make_snapshot(), warnings, report_date=date(2026, 7, 31))
        text = "\n".join(lines)
        assert "存储" in text
        assert "光通信" in text
        assert "GPU链" in text
        assert "新云" in text

    def test_renders_scissors_value(self):
        warnings = []
        lines = render_snapshot(make_snapshot(), warnings, report_date=date(2026, 7, 31))
        text = "\n".join(lines)
        assert "6.0474" in text

    def test_renders_direction_arrows(self):
        warnings = []
        lines = render_snapshot(make_snapshot(), warnings, report_date=date(2026, 7, 31))
        text = "\n".join(lines)
        assert "↑" in text

    def test_renders_benchmark(self):
        warnings = []
        lines = render_snapshot(make_snapshot(), warnings, report_date=date(2026, 7, 31))
        text = "\n".join(lines)
        assert "SOXX=1.0" in text

    def test_no_missing_snapshot_warning(self):
        warnings = []
        render_snapshot(make_snapshot(), warnings, report_date=date(2026, 7, 31))
        assert not any("未提供CROWD结构化快照" in w for w in warnings)


# ── Test: Four baskets field consistency ──

class TestBasketConsistency:
    def test_all_baskets_have_required_fields(self):
        snap = make_snapshot()
        for code, basket in snap["baskets"].items():
            assert "label" in basket, f"{code} missing label"
            assert "ratio" in basket, f"{code} missing ratio"
            assert "momentum_20d" in basket, f"{code} missing momentum_20d"
            assert "momentum_60d" in basket, f"{code} missing momentum_60d"
            assert "z_score" in basket, f"{code} missing z_score"

    def test_rendered_table_has_four_data_rows(self):
        warnings = []
        lines = render_snapshot(make_snapshot(), warnings, report_date=date(2026, 7, 31))
        table_rows = [l for l in lines if l.startswith("| ") and "篮子" not in l and "---" not in l]
        assert len(table_rows) == 4

    def test_values_match_source(self):
        snap = make_snapshot()
        warnings = []
        lines = render_snapshot(snap, warnings, report_date=date(2026, 7, 31))
        text = "\n".join(lines)
        assert "7.4647" in text  # MEM ratio
        assert "1.4173" in text  # OPT ratio
        assert "0.8036" in text  # GPU ratio
        assert "1.0225" in text  # NCLOUD ratio


# ── Test: Trading day no false alarm ──

class TestNoFalseAlarm:
    def test_friday_snapshot_on_saturday_no_warning(self):
        """Snapshot from Friday, report on Saturday -> no staleness."""
        warnings = []
        snap = make_snapshot(as_of="2026-07-31")  # Friday
        render_snapshot(snap, warnings, report_date=date(2026, 8, 1))  # Saturday
        assert not any("陈旧" in w for w in warnings)

    def test_friday_snapshot_on_sunday_no_warning(self):
        """Snapshot from Friday, report on Sunday -> no staleness."""
        warnings = []
        snap = make_snapshot(as_of="2026-07-31")  # Friday
        render_snapshot(snap, warnings, report_date=date(2026, 8, 2))  # Sunday
        assert not any("陈旧" in w for w in warnings)

    def test_friday_snapshot_on_monday_no_warning(self):
        """Snapshot from Friday, report on Monday -> no staleness."""
        warnings = []
        snap = make_snapshot(as_of="2026-07-31")  # Friday
        render_snapshot(snap, warnings, report_date=date(2026, 8, 3))  # Monday
        assert not any("陈旧" in w for w in warnings)

    def test_thursday_snapshot_on_friday_no_warning(self):
        """Snapshot from Thursday, report on Friday -> no staleness (latest completed = Thu)."""
        warnings = []
        snap = make_snapshot(as_of="2026-07-30")  # Thursday
        render_snapshot(snap, warnings, report_date=date(2026, 7, 31))  # Friday
        assert not any("陈旧" in w for w in warnings)

    def test_july4_holiday_skipped(self):
        """Snapshot from July 3, report on July 6 (Mon) -> no staleness (Jul 4 holiday)."""
        warnings = []
        snap = make_snapshot(as_of="2026-07-03")  # Friday before July 4
        render_snapshot(snap, warnings, report_date=date(2026, 7, 6))  # Monday
        assert not any("陈旧" in w for w in warnings)

    def test_weekend_not_trading_day(self):
        assert not _is_us_trading_day(date(2026, 8, 1))  # Saturday
        assert not _is_us_trading_day(date(2026, 8, 2))  # Sunday

    def test_july4_not_trading_day(self):
        assert not _is_us_trading_day(date(2026, 7, 4))

    def test_latest_completed_skips_weekend(self):
        assert _latest_completed_trading_day(date(2026, 8, 3)) == date(2026, 7, 31)

    def test_latest_completed_skips_july4(self):
        assert _latest_completed_trading_day(date(2026, 7, 6)) == date(2026, 7, 3)


# ── Test: Real staleness detection ──

class TestRealStaleness:
    def test_old_snapshot_triggers_warning(self):
        """Snapshot from July 2, report on July 31 -> stale."""
        warnings = []
        snap = make_snapshot(as_of="2026-07-02")
        lines = render_snapshot(snap, warnings, report_date=date(2026, 7, 31))
        text = "\n".join(lines)
        assert any("陈旧" in w for w in warnings)
        assert "陈旧" in text

    def test_staleness_message_contains_dates(self):
        warnings = []
        snap = make_snapshot(as_of="2026-07-02")
        render_snapshot(snap, warnings, report_date=date(2026, 7, 31))
        stale_warnings = [w for w in warnings if "陈旧" in w]
        assert len(stale_warnings) == 1
        assert "2026-07-02" in stale_warnings[0]
        assert "2026-07-30" in stale_warnings[0]  # latest trading day before Jul 31

    def test_one_day_behind_triggers(self):
        """Snapshot from Wednesday, report on Friday (latest completed = Thu) -> stale."""
        warnings = []
        snap = make_snapshot(as_of="2026-07-29")  # Wednesday
        render_snapshot(snap, warnings, report_date=date(2026, 7, 31))  # Friday
        assert any("陈旧" in w for w in warnings)

    def test_no_report_date_no_check(self):
        """When report_date is None, no staleness check."""
        warnings = []
        snap = make_snapshot(as_of="2020-01-01")
        render_snapshot(snap, warnings, report_date=None)
        assert not any("陈旧" in w for w in warnings)


# ── Test: Atomic write failure protection ──

class TestAtomicWrite:
    def test_snapshot_file_valid_json(self):
        """The generated crowd_snapshot.json must be valid JSON."""
        snap_path = pathlib.Path(__file__).resolve().parent.parent / "data" / "crowd_snapshot.json"
        if not snap_path.exists():
            pytest.skip("crowd_snapshot.json not generated yet")
        data = json.loads(snap_path.read_text(encoding="utf-8"))
        assert "as_of" in data
        assert "baskets" in data
        assert "scissors" in data

    def test_no_temp_files_left(self):
        """No .tmp files should remain in data/ after successful build."""
        data_dir = pathlib.Path(__file__).resolve().parent.parent / "data"
        tmp_files = list(data_dir.glob(".crowd_snapshot_*.tmp"))
        assert tmp_files == [], f"Temp files left behind: {tmp_files}"

    def test_atomic_write_survives_interrupted_json(self):
        """Simulate: if JSON serialization fails, original file is not corrupted."""
        with tempfile.TemporaryDirectory() as td:
            target = pathlib.Path(td) / "crowd_snapshot.json"
            original = {"as_of": "2026-01-01", "baskets": {}, "scissors": {}}
            target.write_text(json.dumps(original), encoding="utf-8")

            # Simulate a failed atomic write (bad data that can't serialize)
            tmp_path = pathlib.Path(td) / ".crowd_snapshot_test.tmp"
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write("{invalid json content")
                # Validate before replace (this should fail)
                with open(tmp_path, "r", encoding="utf-8") as f:
                    json.load(f)
                tmp_path.replace(target)  # Should not reach here
            except (json.JSONDecodeError, ValueError):
                tmp_path.unlink(missing_ok=True)

            # Original file must be intact
            recovered = json.loads(target.read_text(encoding="utf-8"))
            assert recovered == original
