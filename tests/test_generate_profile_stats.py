#!/usr/bin/env python3
"""Tests for the dependency-free profile statistics generator."""

from __future__ import annotations

import importlib.util
import sys
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "generate_profile_stats.py"
ASSETS = Path(__file__).resolve().parent.parent / "assets"
SPEC = importlib.util.spec_from_file_location("generate_profile_stats", SCRIPT)
assert SPEC and SPEC.loader
generator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = generator
SPEC.loader.exec_module(generator)


class ProfileStatsTests(unittest.TestCase):
    def test_previous_year_clamps_leap_day(self):
        self.assertEqual(generator.previous_year(date(2024, 2, 29)), date(2023, 2, 28))

    def test_month_keys_cover_thirteen_calendar_months(self):
        keys = generator.month_keys(date(2025, 8, 22), date(2026, 8, 22))
        self.assertEqual(len(keys), 13)
        self.assertEqual(keys[0], (2025, 8))
        self.assertEqual(keys[-1], (2026, 8))

    def test_contribution_series_filters_to_requested_window(self):
        days = [
            {"date": "2025-08-21", "contributionCount": 99},
            {"date": "2025-08-22", "contributionCount": 2},
            {"date": "2026-08-22", "contributionCount": 3},
            {"date": "2026-08-23", "contributionCount": 99},
        ]
        total, series = generator.contribution_series(
            days, date(2025, 8, 22), date(2026, 8, 22)
        )
        self.assertEqual(total, 5)
        self.assertEqual(series[0][1], 2)
        self.assertEqual(series[-1][1], 3)

    def test_language_shares_exclude_non_source_categories(self):
        shares = generator.language_shares(
            {
                "Python": 900,
                "TypeScript": 60,
                "HTML": 500,
                "Jupyter Notebook": 800,
                "SCSS": 50,
                "Dockerfile": 20,
            }
        )
        self.assertEqual([item.name for item in shares], ["Python", "TypeScript"])
        self.assertEqual([item.percent for item in shares], [93.8, 6.2])

    def test_language_shares_roll_smaller_languages_into_other(self):
        shares = generator.language_shares(
            {"Python": 70, "TypeScript": 10, "PowerShell": 8, "JavaScript": 7, "C++": 5}
        )
        self.assertEqual(
            [item.name for item in shares],
            ["Python", "TypeScript", "PowerShell", "Other"],
        )
        self.assertEqual(sum(item.bytes for item in shares), 100)

    def test_language_bar_allocation_preserves_card_width_and_gaps(self):
        shares = generator.language_shares(
            {"Python": 904, "TypeScript": 66, "PowerShell": 21, "JavaScript": 9}
        )
        self.assertEqual(sum(generator.allocate_bar_widths(shares)), 806)

    def test_generated_svgs_are_valid_and_theme_structures_match(self):
        start, end = date(2025, 8, 22), date(2026, 8, 22)
        days = [
            {"date": "2025-08-22", "contributionCount": 5},
            {"date": "2026-08-22", "contributionCount": 8},
        ]
        total, series = generator.contribution_series(days, start, end)
        shares = generator.language_shares(
            {"Python": 904, "TypeScript": 66, "PowerShell": 21, "JavaScript": 9}
        )
        documents = {}
        for kind, factory in {
            "activity": lambda theme: generator.activity_svg(theme, total, series, start, end),
            "languages": lambda theme: generator.language_svg(theme, shares),
        }.items():
            dark = factory("dark")
            light = factory("light")
            ET.fromstring(dark)
            ET.fromstring(light)
            documents[(kind, "dark")] = dark
            documents[(kind, "light")] = light
            dark_root = ET.fromstring(dark)
            light_root = ET.fromstring(light)
            self.assertEqual(
                [element.tag for element in dark_root.iter()],
                [element.tag for element in light_root.iter()],
            )

        self.assertIn("13 contributions", documents[("activity", "dark")])
        self.assertIn("Python leads at 90.4%", documents[("languages", "light")])
        self.assertNotIn("Jupyter Notebook", documents[("languages", "dark")])

    def test_write_if_changed_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "card.svg"
            self.assertTrue(generator.write_if_changed(path, "one\n"))
            self.assertFalse(generator.write_if_changed(path, "one\n"))
            self.assertTrue(generator.write_if_changed(path, "two\n"))

    def test_checked_in_generated_cards_are_valid_and_currently_consistent(self):
        roots = {}
        text = {}
        for kind in ("activity", "languages"):
            for theme in ("dark", "light"):
                source = (ASSETS / f"{kind}-{theme}.svg").read_text(encoding="utf-8")
                roots[(kind, theme)] = ET.fromstring(source)
                text[(kind, theme)] = source

            self.assertEqual(
                [element.tag for element in roots[(kind, "dark")].iter()],
                [element.tag for element in roots[(kind, "light")].iter()],
            )

        self.assertNotIn("public contributions", text[("activity", "dark")])
        self.assertRegex(text[("activity", "dark")], r"\d+ contributions · last 12 months")
        self.assertEqual(roots[("activity", "dark")].get("height"), "220")
        self.assertEqual(roots[("languages", "dark")].get("height"), "172")

        percentages = []
        for element in roots[("languages", "dark")].iter():
            match = re.fullmatch(r"(\d+\.\d)%", element.text or "")
            if match:
                percentages.append(float(match.group(1)))
        self.assertGreaterEqual(len(percentages), 1)
        self.assertLessEqual(len(percentages), 4)
        self.assertAlmostEqual(sum(percentages), 100.0, delta=0.2)


if __name__ == "__main__":
    unittest.main()
