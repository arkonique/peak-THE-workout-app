"""Calculate total training volume from comma-separated count x weight terms."""

from __future__ import annotations

import re
import sys
from decimal import Decimal, InvalidOperation


POSITIVE_INTEGER = re.compile(r"^[0-9]+$")
POSITIVE_NUMBER = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")


def calculate_volume(value: str) -> Decimal:
    """Return the sum of count * weight for input such as ``2x10, 3x20``."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Enter at least one item like 2x10.")

    total = Decimal("0")
    for position, raw_item in enumerate(value.split(","), start=1):
        item = raw_item.strip()
        if not item:
            raise ValueError(f"Item {position} is empty; remove the extra comma.")
        if item.count("x") != 1:
            raise ValueError(f"Item {position} must use the countxweight format, such as 2x10.")

        count_text, weight_text = item.split("x")
        if not POSITIVE_INTEGER.fullmatch(count_text) or int(count_text) < 1:
            raise ValueError(f"Item {position} has a count that must be a positive whole number.")
        if not POSITIVE_NUMBER.fullmatch(weight_text):
            raise ValueError(f"Item {position} has a weight that must be a positive number.")
        try:
            weight = Decimal(weight_text)
        except InvalidOperation as exc:
            raise ValueError(f"Item {position} has a weight that must be a positive number.") from exc
        if weight <= 0:
            raise ValueError(f"Item {position} has a weight that must be greater than zero.")

        total += int(count_text) * weight
    return total


def format_volume(volume: Decimal) -> str:
    if volume == volume.to_integral_value():
        return str(int(volume))
    return format(volume.normalize(), "f")


def main() -> None:
    if len(sys.argv) != 2:
        print('Usage: python calculate_volume.py "2x10,3x20,4x20"', file=sys.stderr)
        raise SystemExit(2)
    try:
        volume = calculate_volume(sys.argv[1])
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    print(format_volume(volume))


if __name__ == "__main__":
    main()
