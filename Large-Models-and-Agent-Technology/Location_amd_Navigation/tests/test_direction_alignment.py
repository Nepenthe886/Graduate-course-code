"""Direction distance self-check must match server heading semantics."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.bt_defs import _heading_aligned_gain  # noqa: E402


class HeadingAlignedGainTest(unittest.TestCase):
    def test_pure_north_counts(self):
        gain = _heading_aligned_gain({"x": 0.0, "y": 0.0}, {"x": 0.0, "y": 20.0}, 0.0)
        self.assertAlmostEqual(gain, 20.0)

    def test_pure_east_counts(self):
        gain = _heading_aligned_gain({"x": 0.0, "y": 0.0}, {"x": 20.0, "y": 0.0}, 90.0)
        self.assertAlmostEqual(gain, 20.0)

    def test_detour_diagonal_not_counted(self):
        # Flight log of Relief UAV 08: detour 9.7 deg west of north.
        gain = _heading_aligned_gain({"x": 686.0, "y": 623.0},
                                     {"x": 678.61, "y": 666.06}, 0.0)
        self.assertEqual(gain, 0.0)

    def test_small_deviation_counts(self):
        gain = _heading_aligned_gain({"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 20.0}, 0.0)
        self.assertGreater(gain, 19.0)

    def test_backward_not_counted(self):
        gain = _heading_aligned_gain({"x": 0.0, "y": 0.0}, {"x": 0.0, "y": -20.0}, 0.0)
        self.assertEqual(gain, 0.0)


if __name__ == "__main__":
    unittest.main()
