from __future__ import annotations

import unittest
from decimal import Decimal

from calculate_volume import calculate_volume, format_volume


class CalculateVolumeTests(unittest.TestCase):
    def test_integer_volume_with_or_without_spaces(self) -> None:
        self.assertEqual(calculate_volume("2x10,3x20,4x20"), Decimal("160"))
        self.assertEqual(calculate_volume("2x10, 3x20, 4x20"), Decimal("160"))

    def test_decimal_weights_are_exact(self) -> None:
        result = calculate_volume("3x22.5, 2x10")
        self.assertEqual(result, Decimal("87.5"))
        self.assertEqual(format_volume(result), "87.5")

    def test_invalid_items_have_short_actionable_errors(self) -> None:
        cases = {
            "": "Enter at least one item",
            "2x10,": "Item 2 is empty",
            "2*10": "Item 1 must use",
            "2.5x10": "count that must be a positive whole number",
            "2xweight": "weight that must be a positive number",
            "0x10": "count that must be a positive whole number",
            "2x0": "weight that must be greater than zero",
        }
        for value, expected in cases.items():
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, expected):
                calculate_volume(value)


if __name__ == "__main__":
    unittest.main()
